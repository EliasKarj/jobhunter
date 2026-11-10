#!/usr/bin/env python3
"""jobhunter - hakee työpaikkoja ja pisteyttää ne profile.json:n kriteereillä.

Käyttö:
  python jobhunter.py search            # hae Duunitorista, pisteytä, kirjoita raportti
  python jobhunter.py score jobs.json   # pisteytä valmis ilmoituslista (ei verkkoa)
"""
from __future__ import annotations
from datadir import DATA, EXAMPLES

import argparse
import csv
import datetime as dt
import hashlib
import html as htmllib
import json
import re
import ssl
import sys
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict, field
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

BASE = Path(__file__).resolve().parent
UA = "jobhunter/1.0 (henkilökohtainen työnhakutyökalu)"

# Pisteytyksen maksimit (yhteensä 100)
MAX_LOCATION = 20
MAX_SKILLS = 35
MAX_LEVEL = 30
MAX_FIELD = 15


@dataclass
class Job:
    title: str
    url: str = ""
    company: str = ""
    location: str = ""
    text: str = ""
    source: str = ""
    detail: dict = field(default_factory=dict)  # jäsennellyt lisätiedot ilmoitussivua varten


# ---------------------------------------------------------------- HTML-apurit

class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._buf = []

    def handle_data(self, data):
        if self._href is not None:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            label = " ".join("".join(self._buf).split())
            self.links.append((self._href, label))
            self._href = None


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    return " ".join(p.parts)


# ------------------------------------------------------------------- Haku

def _ssl_context():
    """Käytä certifi-varmenteita, jos asennettu (korjaa Windowsin vanhentuneet juurivarmenteet)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


# Pyyntöväli palvelimittain. Jobly pyytää robots.txt:ssä 10 s viivettä (Crawl-delay: 10).
HOST_MIN_INTERVAL = {"www.jobly.fi": 10.0, "jobly.fi": 10.0}
WORKERS = 1
_rl_lock = threading.Lock()
_rl_next: dict[str, float] = {}


def _throttle(url: str, delay: float) -> None:
    """Varaa seuraava sallittu pyyntöhetki palvelimelle, vaikka säikeitä olisi useita."""
    host = urllib.parse.urlsplit(url).netloc
    interval = HOST_MIN_INTERVAL.get(host, delay / max(1, WORKERS))
    with _rl_lock:
        slot = max(time.monotonic(), _rl_next.get(host, 0.0))
        _rl_next[host] = slot + interval
    wait = slot - time.monotonic()
    if wait > 0:
        time.sleep(wait)


def http_get(url: str, cache_dir: Path, delay: float, max_age_h: float = 12) -> str:
    cache_dir.mkdir(exist_ok=True)
    f = cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".html")
    if f.exists() and (time.time() - f.stat().st_mtime) < max_age_h * 3600:
        return f.read_text(encoding="utf-8", errors="replace")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "fi,en;q=0.8"})
    for attempt in range(4):
        _throttle(url, delay)
        try:
            with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as r:
                html = r.read().decode("utf-8", errors="replace")
            break
        except urllib.error.HTTPError as e:
            # palvelin pyytää hidastamaan (429) tai on hetken ylikuormittunut: odota ja yritä uudelleen
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(10 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, ssl.SSLError, OSError):
            # ohimenevä verkkovirhe (aikakatkaisu, SSL-kättely): yritä uudelleen hetken päästä
            if attempt < 3:
                time.sleep(3 * (attempt + 1))
                continue
            raise
    f.write_text(html, encoding="utf-8")
    return html


_JOB_PATH = re.compile(r"^/tyopaikat/tyo/[^/?#]+/?$")
_JUNK_LABELS = {"lisää", "tallenna", "suosikki", "suosikit", "jaa", "avaa", "lue lisää", "hae", "hae työpaikkaa"}


def _job_link(href: str, label: str):
    """Palauta (koko url, otsikko) vain varsinaiselle ilmoituslinkille.
    Hylkää toimintolinkit (esim. 'Lisää suosikkeihin'), jotta haku ei koskaan avaa niitä."""
    if not href or "/tyopaikat/tyo/" not in href:
        return None
    path = urllib.parse.urlsplit(href).path
    if not _JOB_PATH.match(path):
        return None
    if " ".join(label.lower().split()) in _JUNK_LABELS:
        return None
    full = urllib.parse.urljoin("https://duunitori.fi", path.rstrip("/"))
    return full, htmllib.unescape(label)


def search_duunitori(profile: dict, cache: Path, delay: float) -> list[Job]:
    s = profile["search"]
    found: dict[str, Job] = {}
    # Duunitori ei enää suodata palvelimella hakusanalla: jokainen hakusana palautti samat mainospaikat
    # (tarkistettu 9.11.2026). Hakusanapyynnöt ohitetaan oletuksena; ilmoitukset tulevat laajasta hausta.
    for q in (s["queries"] if s.get("duunitori_queries") else []):
        for area in s["areas"]:
            url = "https://duunitori.fi/tyopaikat?" + urllib.parse.urlencode({"haku": q, "alue": area})
            try:
                html = http_get(url, cache, delay)
            except Exception as e:  # noqa: BLE001
                print(f"! haku epäonnistui {url}: {e}", file=sys.stderr)
                continue
            p = _Links()
            p.feed(html)
            n = 0
            for href, label in p.links:
                lk = _job_link(href, label)
                if not lk:
                    continue
                full, label = lk
                if full in found:
                    continue
                found[full] = Job(title=label or full.rsplit("/", 1)[-1], url=full,
                                  location=area, source="duunitori")
                n += 1
                if n >= s.get("max_per_query", 15):
                    break
            print(f"  duunitori | {area:9} | {q}: {n} uutta ilmoitusta", file=sys.stderr)
    if s.get("duunitori_mode", "sitemap") == "sitemap":  # oletus: kevyt sivukartta; vanha selaus vain erikseen (crawl)
        sitemap_duunitori(profile, cache, delay, found)
    elif s.get("broad", {}).get("enabled"):
        crawl_duunitori(profile, cache, delay, found)
    return list(found.values())


_PAGE_PARAM = re.compile(r"[?&](sivu|page|p)=\d+")


def _title_relevant(title: str, profile: dict) -> bool:
    """Karkea otsikkoseula laajassa haussa, ettei tuhansia aiheeseen kuulumattomia sivuja ladata."""
    t = title.lower()
    terms = (profile.get("field_title_terms", []) + profile.get("it_title_terms", [])
             + profile.get("level_positive", []) + ["it", "ict", "tieto", "data", "digi", "tech", "koodi", "ohjelm"])
    for x in terms:
        x = x.lower()
        if len(x) <= 3:
            if re.search(r"(?<![a-zäö0-9])" + re.escape(x) + r"(?![a-zäö0-9])", t):
                return True
        elif x in t:
            return True
    return False


def _title_excluded(title: str, profile: dict) -> bool:
    t = title.lower()
    for x in profile.get("exclude_title_terms", []):
        if (_term(x).search(t) if len(x) <= 4 else x.lower() in t):
            return True
    return False


def _crawl_area(area: str, profile: dict, cache: Path, delay: float) -> dict:
    cfg = profile["search"].get("broad", {})
    max_pages = int(cfg.get("max_pages", 30))
    out: dict[str, Job] = {}
    seen_area: set[str] = set()
    param = "sivu"
    total = 0
    page = 0
    fails = 0
    for page in range(1, max_pages + 1):
        params = {"alue": area}
        if page > 1:
            params[param] = page
        url = "https://duunitori.fi/tyopaikat?" + urllib.parse.urlencode(params)
        try:
            html = http_get(url, cache, delay)
            fails = 0
        except urllib.error.HTTPError as e:
            print(f"! sivu epäonnistui {url}: {e}", file=sys.stderr)
            break  # 404 = lista loppui
        except Exception as e:  # noqa: BLE001
            print(f"! sivu epäonnistui {url}: {e}", file=sys.stderr)
            fails += 1
            if fails >= 2:
                break
            continue  # ohita yksittäinen epäonnistunut sivu
        p = _Links()
        p.feed(html)
        if page == 1:
            m = _PAGE_PARAM.search(" ".join(h for h, _ in p.links if h))
            if m:
                param = m.group(1)
        new = 0
        for href, label in p.links:
            lk = _job_link(href, label)
            if not lk:
                continue
            full, title = lk
            if full in seen_area:
                continue
            seen_area.add(full)
            new += 1
            total += 1
            if _title_excluded(title, profile):
                continue  # ei ladata sivua, jonka pisteytys hylkäisi joka tapauksessa
            if cfg.get("title_prefilter", False) and not _title_relevant(title, profile):
                continue
            out[full] = Job(title=title or full.rsplit("/", 1)[-1], url=full,
                            location=area, source="duunitori")
        if new == 0:
            break
    print(f"  duunitori laaja | {area:9} | {total} ilmoitusta käyty läpi ({page} sivua), {len(out)} lisätty luettavaksi",
          file=sys.stderr)
    return out


def crawl_duunitori(profile: dict, cache: Path, delay: float, found: dict) -> None:
    """Laaja haku: käy alueiden kaikki ilmoitukset läpi ilman hakusanaa, sivu sivulta.
    Alueet haetaan rinnakkain; sivut alueen sisällä peräkkäin (loppu tiedetään vasta kun uusia ei tule)."""
    s = profile["search"]
    max_jobs = int(s.get("broad", {}).get("max_jobs", 1500))
    global WORKERS
    WORKERS = max(1, min(int(s.get("workers", 4)), len(s["areas"])))
    workers = WORKERS
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda a: _crawl_area(a, profile, cache, delay), s["areas"]))
    lists = [list(out.items()) for out in results]
    i = 0
    while len(found) < max_jobs and any(i < len(l) for l in lists):
        for l in lists:  # kierros alueilta, ettei raja syö viimeisiä alueita
            if i < len(l) and len(found) < max_jobs and l[i][0] not in found:
                found[l[i][0]] = l[i][1]
        i += 1


# ------------------------------------------------------------- Duunitori: sivukartta
# Duunitori julkaisee kaikki voimassa olevat ilmoitukset sivukartassa (sitemap-jobentry.xml, ~80 osaa × 200,
# uusimmat ensin). Koko Suomen lista tulee ~80 pyynnöllä, ja vain otsikkosuodattimen läpäisevät ilmoitussivut
# luetaan. Kevyempi kuin alueiden selaus (~350 listasivua + jokainen uusi ilmoitus), ja kattaa myös etätyöt.
SITEMAP = "https://duunitori.fi/sitemap-jobentry.xml"
_ASCII = str.maketrans("äöåéü", "aoaeu")
_SLUG_TAIL = re.compile(r"-[a-z]{3,6}-\d{6,}$")          # ...-scsav-20717318 (lähdekoodi ja ilmoitusnumero)

# Otsikkosuodattimen lisätermit (profiilin ala-, IT- ja tasotermien lisäksi). Mitattu nykyistä aineistoa vastaan.
SITEMAP_TERMS = [
    "ai", "qa", "ict", "it", "bi", "sap", "erp", "crm", "net", "sql", "aws", "gis", "ux", "ui", "soc", "noc",
    "data", "security", "cyber", "kyber", "tietoturv", "engineer", "insinoori", "analyst", "analyyt", "kehittaja",
    "developer", "ohjelm", "software", "sovellus", "jarjestelm", "verkko", "network", "cloud", "pilvi", "devops",
    "tieto", "digi", "tech", "automaatio", "automation", "embedded", "sulautet", "test", "python", "java", "linux",
    "azure", "web", "frontend", "backend", "fullstack", "full stack", "koodi", "programm", "machine learning",
    "koneoppi", "tekoaly", "robot", "trainee", "harjoittel", "kesatyo", "opinnayte", "diplomityo", "thesis",
    "intern", "junior", "opiskelija", "tukihenkilo", "helpdesk", "service desk", "kayttotuki", "kayttajatuki",
    "lahituki", "mikrotuki", "platform", "integra", "architect", "arkkitehti", "consultant", "konsultti",
    "product owner", "scrum", "game", "peli", "mobile", "mobiili", "electronic", "elektroniik", "fpga",
    "firmware", "quantum", "kvantti", "gnss", "navigation", "solution", "ratkaisu", "specialist", "asiantuntija",
    "avoin hakemus", "avoin haku", "open applica",
]


def _ascii(s: str) -> str:
    return (s or "").lower().translate(_ASCII)


def slug_title(url: str) -> str:
    """Ilmoitusosoitteen loppuosa luettavaksi: .../tekninen-kiinteistonhoitaja-vantaa-scsav-20717318 ->
    'tekninen kiinteistonhoitaja vantaa'. Ääkköset puuttuvat osoitteista."""
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    return _SLUG_TAIL.sub("", slug).replace("-", " ").strip()


@lru_cache(maxsize=8)
def _slug_terms(key: tuple) -> re.Pattern:
    terms = sorted({_ascii(t).replace("-", " ").strip() for t in key if t and t.strip()}, key=len, reverse=True)
    parts = [rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])" if len(t) <= 3 else re.escape(t) for t in terms]
    return re.compile("|".join(parts))


def slug_relevant(text: str, profile: dict) -> bool:
    """Otsikkosuodatin sivukartan osoitteille: lyhyet termit (it, ai, qa) vain kokonaisina sanoina,
    pidemmät myös yhdyssanan osana (ohjelmistokehittaja)."""
    key = tuple(profile.get("field_title_terms", []) + profile.get("it_title_terms", [])
                + profile.get("level_positive", []) + profile["search"].get("sitemap_terms", SITEMAP_TERMS))
    return bool(_slug_terms(key).search(_ascii(text)))


def sitemap_urls(cache: Path, delay: float, max_pages: int = 120) -> list[str]:
    """Kaikki sivukartan ilmoitusosoitteet. Osat luetaan, kunnes osa on tyhjä."""
    out: list[str] = []
    for p in range(1, max_pages + 1):
        url = SITEMAP + (f"?p={p}" if p > 1 else "")
        try:
            xml = http_get(url, cache, delay, max_age_h=6)
        except urllib.error.HTTPError as e:
            if e.code != 404:       # 404 = sivukartta loppui (osien määrä vaihtelee päivittäin)
                print(f"! sivukartta {url}: {e}", file=sys.stderr)
            break
        except Exception as e:  # noqa: BLE001
            print(f"! sivukartta {url}: {e}", file=sys.stderr)
            break
        locs = re.findall(r"<loc>\s*(https://duunitori\.fi/tyopaikat/tyo/[^<\s]+)\s*</loc>", xml)
        if not locs:
            break
        out += locs
    return list(dict.fromkeys(out))


def sitemap_duunitori(profile: dict, cache: Path, delay: float, found: dict) -> None:
    urls = sitemap_urls(cache, delay)
    kept = 0
    for u in urls:
        if u in found:
            continue
        t = slug_title(u)
        if slug_relevant(t, profile):
            found[u] = Job(title=t, url=u, source="duunitori")
            kept += 1
    print(f"  duunitori sivukartta | {len(urls)} ilmoitusta koko Suomesta, otsikkosuodatin päästi {kept}", file=sys.stderr)


def search_jobly(profile: dict, cache: Path, delay: float) -> list[Job]:
    """Jobly: haku ?search=..., ilmoituslinkit muotoa /tyopaikka/<slug>-<id>.
    Alue-parametria ei ole varmistettu, joten alue päätellään ilmoituksen tekstistä pisteytyksessä."""
    s = profile["search"]
    found: dict[str, Job] = {}
    for q in s["queries"]:
        url = "https://www.jobly.fi/tyopaikat?" + urllib.parse.urlencode({"search": q})
        try:
            html = http_get(url, cache, delay)
        except urllib.error.HTTPError as e:
            print(f"! Jobly: {e}", file=sys.stderr)
            if e.code in (401, 403):
                print("! Jobly estää automaattiset haut (403). Lopetetaan Joblyn haku eikä yritetä uudelleen.",
                      file=sys.stderr)
                break
            continue
        except Exception as e:  # noqa: BLE001
            print(f"! haku epäonnistui {url}: {e}", file=sys.stderr)
            continue
        p = _Links()
        p.feed(html)
        n = 0
        for href, label in p.links:
            if "/tyopaikka/" not in href:
                continue
            full = urllib.parse.urljoin("https://www.jobly.fi", href.split("?")[0])
            if full in found:
                continue
            slug = full.rsplit("/", 1)[-1]
            found[full] = Job(title=label or slug, url=full, source="jobly")
            n += 1
            if n >= s.get("max_per_query", 15):
                break
    return list(found.values())


KUNTAREKRY = "https://www.kuntarekry.fi"


def search_kuntarekry(profile: dict, cache: Path, delay: float) -> list[Job]:
    """Kuntarekry (kunnat, hyvinvointialueet, kuntayhtymät): sivuston oma julkinen hakuosoite palauttaa JSONin
    (?format=json, sama jota sivun haku käyttää). robots.txt sallii kaiken (tarkistettu 9.11.2026).
    Yksi pyyntö ammattiluokkaa kohden koko Suomesta; pisteytys hoitaa alueen ja tason."""
    s = profile["search"]
    found: dict[str, Job] = {}
    for cat in s.get("kuntarekry_categories", ["it-tietohallinto"]):
        url = f"{KUNTAREKRY}/fi/tyopaikat/{cat}/?format=json&includeTotal=1&limit=200"
        try:
            data = json.loads(http_get(url, cache, delay, max_age_h=6))
        except Exception as e:  # noqa: BLE001
            print(f"! Kuntarekry ({cat}): {e}", file=sys.stderr)
            continue
        n = 0
        for it in data.get("jobs") or []:
            full = urllib.parse.urljoin(KUNTAREKRY, str(it.get("url") or ""))
            if not it.get("url") or full in found:
                continue
            tags = [t for t in it.get("location_tags") or [] if t]
            found[full] = Job(title=str(it.get("title") or "").strip(), url=full,
                              company=str(it.get("organisation_title") or "").strip(),
                              location=", ".join(tags) or str(it.get("region") or ""), source="kuntarekry")
            n += 1
        print(f"  kuntarekry | {cat}: {n} ilmoitusta (yhteensä {data.get('total')})", file=sys.stderr)
    return list(found.values())


SOURCES = {"duunitori": search_duunitori, "jobly": search_jobly, "kuntarekry": search_kuntarekry}


# ---------------------------------------------------------- Ilmoituksen tiedot
_EMPLOYMENT = {"FULL_TIME": "Kokoaikainen", "PART_TIME": "Osa-aikainen", "INTERN": "Harjoittelu",
               "CONTRACTOR": "Toimeksianto", "TEMPORARY": "Määräaikainen", "VOLUNTEER": "Vapaaehtoistyö"}
_UNIT = {"MONTH": "kk", "HOUR": "h", "YEAR": "v", "WEEK": "vko", "DAY": "pv"}


def _ld_date(s: str, plus_hours: int = 0) -> str:
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")) + dt.timedelta(hours=plus_hours)
        if d.year > 2100:  # "ei päättymispäivää" -merkintä (esim. 2650)
            return ""
        return d.date().isoformat()
    except ValueError:
        return ""


def _plain(desc: str) -> str:
    if "<" in desc:
        desc = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", desc)
        desc = re.sub(r"(?i)<li[^>]*>", "- ", desc)
        desc = re.sub(r"<[^>]+>", "", desc)
    desc = htmllib.unescape(desc)
    # Kuntarekry poistaa kappalejaot lähteessä ("…kehittämistä?Korkeakoulutuksen"): rivinvaihto välimerkin ja ison
    # alkukirjaimen väliin. CamelCase-sanoihin (JavaScript, PostgreSQL) ei kosketa.
    desc = re.sub(r"([.?!:])([A-ZÅÄÖ][a-zåäö])", r"\1\n\2", desc)
    lines = [" ".join(x.split()) for x in desc.replace("\r", "").split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def _money(v) -> str:
    try:
        return f"{float(v):,.0f}".replace(",", " ")
    except (TypeError, ValueError):
        return ""


def _salary(item: dict) -> str:
    try:
        val = item["baseSalary"]["value"]
        unit = _UNIT.get(str(val.get("unitText", "")).upper(), "")
        lo, hi, one = _money(val.get("minValue")), _money(val.get("maxValue")), _money(val.get("value"))
        amount = f"{lo}–{hi}" if lo and hi and lo != hi else (lo or hi or one)
        return f"{amount} €/{unit}" if amount and unit else (f"{amount} €" if amount else "")
    except (KeyError, TypeError, AttributeError):
        return ""


def parse_posting(html: str) -> dict:
    """Lue sivun JobPosting-rakenne (JSON-LD). Palauttaa tyhjän sanakirjan, jos sitä ei ole."""
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if not (isinstance(item, dict) and item.get("@type") == "JobPosting"):
                continue
            org = item.get("hiringOrganization") if isinstance(item.get("hiringOrganization"), dict) else {}
            locs = item.get("jobLocation")
            places = []
            for loc in locs if isinstance(locs, list) else [locs]:
                try:
                    a = loc["address"]
                    places.append(str(a.get("addressLocality") or a.get("streetAddress") or "").strip())
                except (KeyError, TypeError, AttributeError):
                    pass
            name = str(org.get("name", "")).strip()
            return {
                "description": _plain(str(item.get("description", ""))),
                "company": "" if name.lower() in ("", "ei tietoa") else name,
                "company_url": str(org.get("sameAs", "")) if str(org.get("sameAs", "")).startswith("http") else "",
                "posted": _ld_date(item.get("datePosted", ""), 3),
                "deadline": _ld_date(item.get("validThrough", ""), 3),
                "employment": _EMPLOYMENT.get(str(item.get("employmentType", "")).upper(), ""),
                "places": [p for p in dict.fromkeys(places) if p],
                "salary": _salary(item),
            }
    return {}


_JUNK_H1 = {"selain ei ole tuettu", "browser not supported", "webbläsaren stöds inte"}


def enrich(job: Job, cache: Path, delay: float, max_age_h: float = 168) -> None:
    """Hae ilmoituksen koko teksti pisteytystä varten."""
    try:
        html = http_get(job.url, cache, delay, max_age_h)
    except Exception as e:  # noqa: BLE001
        print(f"! ilmoitus ei auennut {job.url}: {e}", file=sys.stderr)
        return
    job.text = html_to_text(html)
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
    if h1:
        t = " ".join(re.sub(r"<[^>]+>", " ", h1.group(1)).split())
        if t and t.lower() not in _JUNK_H1:   # Kuntarekryn ensimmäinen h1 on selaimen tukiviesti
            job.title = htmllib.unescape(t)
    m = re.search(r"sijaitsee kunnassa ([^,.\n]+)", job.text)
    if m:
        job.location = m.group(1).strip()
    m = re.search(r"on julkaissut ([^.\n]+)\.", job.text)
    if m:
        job.company = m.group(1).strip()
    d = parse_posting(html)
    if d.get("description"):
        # Siisti kuvaus pisteytykseen ja ilmoitussivulle: sivun valikot ja alatunniste eivät sotke tekstiä
        body = d.pop("description")
        if d.get("deadline"):
            y, mo, da = d["deadline"].split("-")
            body += f"\n\nHakuaika umpeutuu {da}.{mo}.{y}"
        job.text = body
        if not job.company and d.get("company"):
            job.company = d["company"]
        if not job.location and d.get("places"):    # sivukarttahaku: ei hakualuetta, paikkakunta ilmoituksesta
            job.location = ", ".join(d["places"])
        job.detail = d


# ------------------------------------------------------------- Pisteytys

@lru_cache(maxsize=2048)
def _term(term: str) -> re.Pattern:
    """Sanaosuma. Lopussa oleva * sallii suomen taivutuspäätteet: 'pilvi*' osuu 'pilvipalveluihin'."""
    t = term.lower()
    start = r"(?<![a-zäöå0-9])"
    if t.endswith("*"):
        return re.compile(start + re.escape(t[:-1]) + r"[a-zäöå0-9-]*")
    return re.compile(start + re.escape(t) + r"(?![a-zäöå0-9])")


def load_profile(path) -> dict:
    """Lue profile.json ja sen vieressä oleva skills.json (osaamisprofiili), jos se on olemassa."""
    path = Path(path)
    example = EXAMPLES / "profile.json"
    if not path.exists() and example.exists():   # tuore klooni: oma profiili esimerkistä (profile.json ei ole repossa)
        path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    prof = json.loads(path.read_text(encoding="utf-8"))
    sk = path.parent / "skills.json"
    if sk.exists():
        data = json.loads(sk.read_text(encoding="utf-8"))
        prof["skill_list"] = [
            {"name": s["name"], "terms": [s["name"], *s.get("aliases", [])], "weight": float(s.get("weight", 1))}
            for s in data.get("skills", []) if s.get("name")
        ]
    comp = path.parent / "companies.json"
    prof["watch_companies"] = []
    if comp.exists():
        try:
            prof["watch_companies"] = [c["name"].lower() for c in json.loads(comp.read_text(encoding="utf-8")).get("companies", [])
                                       if c.get("name")]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return prof


def _deadline(text: str):
    m = re.search(r"umpeutuu\s+(\d{2})\.(\d{2})\.(\d{4})", text)
    if not m:
        return None
    d, mo, y = map(int, m.groups())
    try:
        return dt.date(y, mo, d)
    except ValueError:
        return None


def score_job(job: Job, prof: dict, today: dt.date) -> dict:
    title = job.title.lower()
    body = job.text.lower()
    # sivukarttahaussa paikkakunta tulee vain ilmoitussivun tiedoista (detail.places), ei hakualueesta
    places = " ".join((job.detail or {}).get("places") or [])
    place = f"{job.location} {places} {title}".lower()
    reasons: list[str] = []
    res = {"score": 0.0, "status": "ok", "reasons": reasons, "area": None,
           "parts": {"alue": 0.0, "osaaminen": 0.0, "taso": 0.0, "ala": 0.0}}

    # 1) Poissulkevat otsikkotermit
    for t in prof["exclude_title_terms"]:
        # lyhyet termit (hr, plc) sanarajoilla, pidemmät osumaan myös yhdyssanoissa
        hit = _term(t).search(title) if len(t) <= 4 else t.lower() in title
        if hit:
            res["status"] = "excluded"
            reasons.append(f"Poissuljettu: otsikossa '{t}'")
            return res

    # 1b) Estetyt työnantajat (asetuksissa)
    comp = (job.company or "").lower()
    for c in prof.get("block_companies", []):
        if c and c.lower() in comp:
            res["status"] = "excluded"
            reasons.append(f"Poissuljettu: työnantaja '{c}'")
            return res

    # 2) Vanhentunut hakuaika
    dl = _deadline(job.text)
    res["deadline"] = dl.isoformat() if dl else None
    if dl:
        if dl < today:
            res["status"] = "expired"
            reasons.append(f"Hakuaika päättyi {dl.strftime('%d.%m.%Y')}")
            return res
        reasons.append(f"Hakuaika päättyy {dl.strftime('%d.%m.%Y')}")

    # 3) Alue (0-20)
    loc_pts, loc_name = 0.0, None
    for scope in (place, body[:2000]):
        for loc in prof["locations"]:
            if any(a in scope for a in loc["aliases"]) and loc["weight"] > loc_pts:
                loc_pts, loc_name = float(loc["weight"]), loc["name"]
        if loc_name:
            break
    # Täysi etätyö: paikkakunnalla ei ole väliä, joten ilmoitus saa aluepisteet hakualueen ulkopuoltakin
    anywhere = prof.get("remote_anywhere") or {}
    if not loc_name and anywhere.get("weight") and any(
            _term(t).search(place + " " + body[:3000]) for t in anywhere.get("terms", [])):
        loc_pts, loc_name = float(anywhere["weight"]), "Etätyö (koko Suomi)"
    if any(_term(t).search(place + " " + body[:3000]) for t in prof.get("remote_terms", [])):
        loc_pts = min(MAX_LOCATION, loc_pts + prof.get("remote_bonus", 0))
        reasons.append("Etä-/hybridimahdollisuus")
    if loc_name:
        reasons.append(f"Alue: {loc_name} (+{loc_pts:.0f})")
    else:
        reasons.append("Alue ei osu hakualueisiin")
    res["parts"]["alue"] = loc_pts
    res["area"] = loc_name

    # 4) Osaaminen (0-35)
    raw, hits = 0.0, []
    blob = title + " " + body
    skill_list = prof.get("skill_list")
    if skill_list is None:  # vanha muoto: pelkkä sana -> paino
        skill_list = [{"name": k, "terms": [k], "weight": w} for k, w in prof.get("skills", {}).items()]
    for sk in skill_list:
        if sk["weight"] > 0 and any(_term(t).search(blob) for t in sk["terms"]):
            raw += sk["weight"]  # jokainen taito lasketaan vain kerran, vaikka aliaksia osuisi useita
            hits.append(sk["name"])
    skill_pts = MAX_SKILLS * min(1.0, raw / prof.get("skills_target", 12))
    if hits:
        reasons.append("Osaaminen: " + ", ".join(hits))
    res["parts"]["osaaminen"] = skill_pts

    # 5) Taso (0-30): harjoittelu/junior hyvä, senior huono
    lvl = 12.0
    pos_total = 0.0
    for t in prof["level_positive"]:
        if t in title:
            pos_total += 8
            reasons.append(f"Taso+: '{t}' otsikossa")
        elif t in body:
            pos_total += 3
    lvl += min(pos_total, 18)
    title_only = set(prof.get("level_title_only", []))
    for t in prof["level_negative"]:
        if t in title:
            lvl -= 12
            reasons.append(f"Taso-: '{t}' otsikossa")
        elif t in body and t not in title_only:  # esim. "manager" tai "päällikkö" mainitaan usein vain yhteystiedoissa
            lvl -= 5
            reasons.append(f"Taso-: '{t}' tekstissä")
    # Lievä vaatimus (noin 2 vuotta): vahvoilla projekteilla kannattaa hakea, joten pieni vähennys ja vain kerran
    soft = next((t for t in prof.get("level_soft_negative", []) if t in body), None)
    if soft:
        lvl -= 3
        reasons.append(f"Taso~: '{soft}' (lievä kokemusvaatimus)")
    # pitkät termit osuvat myös yhdyssanoihin (esim. "IT-palvelujohtaja"), lyhyet vain sanarajoilla
    senior_title = [t for t in prof.get("title_senior_terms", [])
                    if (t.lower() in title if len(t) >= 6 else _term(t).search(title))]
    for t in senior_title:
        lvl -= 12
        reasons.append(f"Taso-: '{t}' otsikossa")
    lvl = max(0.0, min(float(MAX_LEVEL), lvl))
    res["parts"]["taso"] = lvl

    # 6) Ala (0-15)
    if any(t in title for t in prof["field_title_terms"]):
        field = float(MAX_FIELD)
        reasons.append("Ala: ohjelmistotehtävä otsikossa")
    elif any(t in title for t in prof.get("it_title_terms", [])):
        field = 11.0
        reasons.append("Ala: yleinen IT-tehtävä otsikossa")
    elif sum(body.count(t) for t in ("ohjelmisto", "software", "ohjelmoin")) >= 3:
        field = 7.0
        reasons.append("Ala: ohjelmistoa tekstissä")
    elif sum(body.count(t) for t in prof.get("it_body_terms", [])) >= 6:
        field = 5.0
        reasons.append("Ala: IT-aihetta tekstissä")
    else:
        field = 0.0
        reasons.append("Ala: ei selvästi ohjelmistotyötä")
    res["parts"]["ala"] = field

    total = sum(res["parts"].values())
    # Seurattu yritys (Yritykset-näkymä): pieni bonus, jos työ on IT-alaa. Ennen ylärajoja, joten ne pätevät edelleen.
    comp = (job.company or "").lower()
    res["watch"] = any(w and (w in comp or (len(comp) > 3 and comp in w)) for w in prof.get("watch_companies", []))
    if res["watch"] and field > 0:
        bonus = float(prof.get("watch_bonus", 4))
        total = min(100.0, total + bonus)
        reasons.append(f"Seurattu yritys (+{bonus:g})")
    # Otsikossa senior-tasoa tarkoittava termi: pisteet katetaan, ettei hyvä alue/osaaminen nosta kärkeen
    if senior_title or any(t in title for t in prof["level_negative"]):
        if total > 40:
            reasons.append("Pisteet rajattu 40:een (vaatimustaso liian korkea)")
        total = min(total, 40.0)
    # Ilmoitus vaatii selvästi 3+ vuoden kokemusta: ei nouse vahvimpien osumien joukkoon
    yrs = [t for t in prof.get("years_cap_terms", []) if t in body]
    if yrs and not senior_title:
        if total > 55:
            reasons.append(f"Pisteet rajattu 55:een (vaatii kokemusta: '{yrs[0]}')")
        total = min(total, 55.0)
    # Ei IT-alan työtä lainkaan: alue ja perustaso eivät saa nostaa paikkaa listalle
    if field == 0.0:
        if total > 15:
            reasons.append("Pisteet rajattu 15:een (ei IT-alan työtä)")
        total = min(total, 15.0)
    res["score"] = round(total, 1)
    return res


def collect_jobs(prof: dict, delay: float = 1.5, progress=print, checkpoint=None) -> list[Job]:
    """Hae kaikki lähteet ja lue ilmoitusten tekstit. progress(str) saa tilaviestit."""
    cache = DATA / "cache"
    jobs: list[Job] = []
    seen: set[str] = set()
    for name in prof["search"].get("sources", ["duunitori"]):
        if name not in SOURCES:
            print(f"! tuntematon lähde: {name}", file=sys.stderr)
            continue
        progress(f"Haetaan lähteestä {name}")
        for j in SOURCES[name](prof, cache, delay):
            if j.url not in seen:
                seen.add(j.url)
                jobs.append(j)
    progress(f"Löytyi {len(jobs)} ilmoitusta, luetaan tekstejä")
    global WORKERS
    workers = max(1, int(prof["search"].get("workers", 4)))
    WORKERS = workers
    with ThreadPoolExecutor(max_workers=workers) as ex:
        # map palauttaa järjestyksessä, joten jobs[:i] on aina luettu
        # sivukarttatilassa samat ilmoitukset tulevat joka päivä: luettu sivu kelpaa 30 päivää (ilmoitus harvoin muuttuu,
        # ja poistunut ilmoitus putoaa sivukartasta), muuten viikon
        age = float(prof["search"].get("detail_max_age_h", 720 if prof["search"].get("duunitori_mode", "sitemap") == "sitemap" else 168))
        for i, _ in enumerate(ex.map(lambda j: enrich(j, cache, delay, age), jobs), 1):
            if i % 5 == 0 or i == len(jobs):
                progress(f"Luetaan ilmoituksia {i}/{len(jobs)}")
            if checkpoint and i % 50 == 0 and i < len(jobs):
                checkpoint(jobs[:i])  # tallenna välitulokset
    return jobs


def score_all(jobs: list[Job], prof: dict, today: dt.date | None = None) -> list[dict]:
    today = today or dt.date.today()
    rows = []
    for j in jobs:
        r = score_job(j, prof, today)
        r["job"] = asdict(j)
        rows.append(r)
    return _dedupe(rows)


def _dedupe(rows: list[dict]) -> list[dict]:
    """Sama ilmoitus useassa kaupungissa (eri URL): pidä parhaiten pisteytetty, mainitse muut alueet."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        j = r["job"]
        key = (" ".join(j["title"].lower().split()), j.get("company", "").lower().strip())
        groups.setdefault(key, []).append(r)
    out = []
    for key, g in groups.items():
        if not key[0] or len(g) == 1:
            out.extend(g)
            continue
        g.sort(key=lambda r: -r["score"])
        best = g[0]
        others = sorted({x["area"] for x in g[1:] if x["area"] and x["area"] != best["area"]})
        if others:
            best["reasons"].append("Sama ilmoitus myös: " + ", ".join(others))
        out.append(best)
    return out


# --------------------------------------------------------------- Raportit

def write_reports(rows: list[dict], out_dir: Path, top: int) -> tuple[Path, Path]:
    out_dir.mkdir(exist_ok=True)
    stamp = dt.date.today().isoformat()
    md, cs = out_dir / f"tyopaikat_{stamp}.md", out_dir / f"tyopaikat_{stamp}.csv"

    ranked = [r for r in rows if r["status"] == "ok"]
    ranked.sort(key=lambda r: r["score"], reverse=True)
    shown = ranked[:top] if top else ranked

    lines = [f"# Työpaikat paremmuusjärjestyksessä ({stamp})", "",
             f"Pisteytettyjä: {len(ranked)} | poissuljettuja: "
             f"{sum(r['status'] == 'excluded' for r in rows)} | vanhentuneita: "
             f"{sum(r['status'] == 'expired' for r in rows)}", "",
             "| # | Pisteet | Työpaikka | Yritys | Alue |", "|---|---|---|---|---|"]
    for i, r in enumerate(shown, 1):
        j = r["job"]
        lines.append(f"| {i} | **{r['score']}** | [{j['title']}]({j['url']}) | {j['company']} | {j['location']} |")
    lines += ["", "## Perustelut", ""]
    for i, r in enumerate(shown, 1):
        p = r["parts"]
        lines.append(f"**{i}. {r['job']['title']}** - {r['score']}/100 "
                     f"(alue {p['alue']:.0f}, osaaminen {p['osaaminen']:.0f}, "
                     f"taso {p['taso']:.0f}, ala {p['ala']:.0f})")
        lines += [f"- {x}" for x in r["reasons"]]
        lines.append("")
    md.write_text("\n".join(lines), encoding="utf-8")

    with cs.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["sija", "pisteet", "otsikko", "yritys", "alue", "url", "perustelut"])
        for i, r in enumerate(ranked, 1):
            j = r["job"]
            w.writerow([i, r["score"], j["title"], j["company"], j["location"], j["url"],
                        " | ".join(r["reasons"])])
    return md, cs


def run(jobs: list[Job], prof: dict, out_dir: Path, top: int, show_all: bool, mode: str | None = None) -> None:
    today = dt.date.today()
    rows = []
    for j in jobs:
        r = score_job(j, prof, today)
        r["job"] = asdict(j)
        rows.append(r)
    if mode != "keyword":
        # semantic/hybrid: RAG-indeksi (rag/); oletuksena .env:n SCORING_MODE, joka on yleensä keyword
        from rag import config as rag_config
        from rag.scoring import apply_mode
        s = rag_config.load()
        rows = apply_mode(rows, s.with_(scoring_mode=mode) if mode else s)
    md, cs = write_reports(rows, out_dir, 0 if show_all else top)
    ok = sorted((r for r in rows if r["status"] == "ok"), key=lambda r: r["score"], reverse=True)
    print(f"\nTop {min(top, len(ok))}:")
    for i, r in enumerate(ok[:top], 1):
        print(f"{i:>2}. {r['score']:>5}  {r['job']['title'][:60]}  [{r['job']['location']}]")
    print(f"\nRaportit: {md}\n          {cs}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default=str(DATA / "profile.json"))
    ap.add_argument("--out", default=str(DATA / "results"))
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--all", action="store_true", help="näytä kaikki, älä vain top N")
    ap.add_argument("--mode", choices=["keyword", "semantic", "hybrid"],
                    help="pisteytystapa (oletus .env:n SCORING_MODE); keyword = pelkkä avainsanapisteytys")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="hae Duunitorista ja pisteytä")
    s.add_argument("--delay", type=float, default=1.5, help="sekuntia pyyntöjen välillä")
    c = sub.add_parser("score", help="pisteytä JSON-tiedosto")
    c.add_argument("file")
    a = ap.parse_args()

    prof = load_profile(a.profile)
    if a.cmd == "score":
        data = json.loads(Path(a.file).read_text(encoding="utf-8"))
        jobs = [Job(**d) for d in data]
    else:
        jobs = collect_jobs(prof, a.delay)
    run(jobs, prof, Path(a.out), a.top, a.all, a.mode)


if __name__ == "__main__":
    main()
