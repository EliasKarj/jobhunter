# Tehtävä: arviointipaneeli jobhunterin CV-räätälöintiin

## Lähtötilanne: tämä on laajennus, ei uusi ominaisuus

Jobhunter on paikallinen Python-sovellus. Se hakee ilmoituksia Duunitorilta ja Joblystä, pisteyttää ne (avainsana + semanttinen + kielimallin sopivuus) ja tarjoaa React-käyttöliittymän. CV:n räätälöinti on jo olemassa, ja tämä tehtävä lisää sen ympärille agenttipaneelin, joka arvioi ja parantaa tuloksen ennen kuin näen sen.

**Lue ensin** (aja `graphify query "..."` ennen grep- tai tiedostohakuja, ks. projektin CLAUDE.md):

| Mitä | Missä |
|---|---|
| CV:n jäsennys, räätälöinti, yhden sivun rajaus, suomenkielinen pohja | `rag/cvtailor.py` (`parse`, `outline`, `tailor`, `apply_plan`, `fit_one_page`, `tailor_url`) |
| Väitteiden tarkistus säännöillä | `rag/verify.py` (`check`, `TECH_TERMS`, `ad_terms`), `cvtailor.check_summary`, `cvtailor.check_translation` |
| Tyyli ja kliseet | `rag/style.py` (`CLICHES`, `STYLE`, `find`, `polish`) |
| Kielimallirajapinta | `rag/llm.py` (`get_llm`, `ClaudeLLM`, `OllamaLLM`, `FakeLLM`, `LLMError`, `complete_json(schema)`) |
| Rinnakkaisajon malli | `rag/llmscore.py` (`ThreadPoolExecutor`, `SCORE_WORKERS`) |
| Tallennetut CV:t ja käsin muokkaus | `server/drafts.py` (`get_cv`, `save_cv`, `merge_cv_edit`, `cvs.json`) |
| API, SSE ja paikallisuussuoja | `server/app.py` (`/api/cv*`, `/api/chat` SSE:n mallina, Host- ja `X-Requested-With`-tarkistus) |
| CV-näkymä, muokkaus ja PDF | `frontend/src/views/CvView.tsx` (PDF selaimen tulostuksella), `frontend/src/api.ts` (SSE-apu) |
| Lähteet | `data/cv.md` (englanti, totuuden lähde), `data/cv_fi.md` (tarkistettu suomenkielinen pohja) |

Kerro suunnitelmasi lyhyesti ennen isompia muutoksia. Älä kirjoita olemassa olevaa uusiksi: paneeli kutsuu nykyistä `tailor()`-putkea ja laajentaa sitä.

## Ehdottomat säännöt (käyttäjän omat, eivät neuvoteltavissa)

1. **Vain CV:ssä olevat taidot ja kokemukset.** Ei keksittyjä taitoja, projekteja, vuosia, nimikkeitä tai lukuja.
2. **Yksi sivu.** CV:n pituus on aina yksi A4-sivu (`fit_one_page`), ei 1–2 sivua.
3. **Ei automaattista lähettämistä** mihinkään portaaliin.
4. **Luonteva kieli.** Ei tekoälyltä kuulostavia fraaseja ("this candidate brings…", "highly motivated", "hakijalla on…"), ei kolmatta persoonaa. `rag/style.py` määrittelee kiellot.
5. CV:n tekstistä ei poisteta eikä siihen lisätä "AI-assisted"-tyyppisiä merkintöjä.

## Keskeinen suunnittelupäätös: räätälöijä ei kirjoita CV:tä vapaasti

Nykyinen räätälöinti on **ekstraktiivinen**: malli valitsee järjestyksen, pois jätettävät kohdat ja korostettavat taidot sekä kirjoittaa 2–3 lauseen profiilitekstin. Koodi kokoaa CV:n alkuperäisistä riveistä. Siksi keksitty sisältö voi päätyä vain profiilitekstiin, ja se tarkistetaan säännöillä.

Paneeli säilyttää tämän. Räätälöijä saa lisäksi rajatun oikeuden **muotoilla yksittäisiä rivejä uudelleen**:

- Jokainen uudelleenmuotoilu viittaa yhden lähderivin tunnisteeseen (`S3E2B1` jne.). Lähdefakta on siis rakenteesta tuleva tunniste, ei mallin oma väite.
- Koodi hylkää muotoilun ennen paneelia, jos siinä on tekniikka, luku tai nimi, jota lähderivillä tai muualla CV:ssä ei ole. Tarkistukseen käytetään samaa logiikkaa kuin `check_translation` ja `verify.check`. Hylätty rivi palaa alkuperäiseksi, ja syy kirjataan.
- Nimikkeitä, työnantajia, päivämääriä, koulutusta ja kielitaitoa ei saa muotoilla uudelleen. Ne tulevat aina lähteestä sellaisinaan.

Faktapohja on `parse(data/cv.md)` (suomeksi `data/cv_fi.md`) tunnisteineen. Erillistä faktalistaa tai välimuistia ei tarvita, koska jäsennys on deterministinen ja nopea. Mallille faktat annetaan `outline()`-muodossa.

## Kulku

1. Käyttäjä painaa CV-näkymässä "Räätälöi ja arvioi" -painiketta. Ajo käynnistyy vain pyynnöstä, ei koskaan automaattisesti.
2. **Räätälöijä** tuottaa suunnitelman (laajennettu `PLAN_SCHEMA`). Koodi kokoaa CV:n, ajaa sääntötarkistukset ja rajaa yhdelle sivulle.
3. **Sääntötarkistus ennen paneelia:** `check_summary`, rivimuotoilujen tarkistus ja `style.find`. Löydökset menevät paneelille ja räätälöijälle valmiina faktoina, joten mallin ei tarvitse arvata niitä.
4. **Paneeli** (4 arvioijaa) arvioi kootun CV:n rinnakkain.
5. Jos faktantarkistaja hylkää tai mikä tahansa arvioija löytää `blocking`-ongelman, räätälöijä saa löydökset ja tekee uuden suunnitelman. Korjauskierroksia on **enintään 2**.
6. **Varma lopetus:** jos faktaongelma jää kahden kierroksen jälkeen, ongelmallinen profiiliteksti tai rivi korvataan alkuperäisellä. Tämä vastaa nykyistä käytöstä, ja tila on `needs_review` jäljellä olevien huomioiden kanssa. Ajo ei siis koskaan pääty keksittyyn sisältöön eikä jää silmukkaan.
7. Tulos tallennetaan nykyiseen `cvs.json`-rakenteeseen (`save_cv`), joten nykyinen muokkaus ja PDF toimivat sellaisinaan. Ajon historia ja paneelin tulokset tallennetaan SQLiteen (ks. Tallennus).

## Agentit

Jokainen agentti on oma `complete_json`-kutsunsa `rag/llm.py`:n kautta, ja sillä on oma system prompt tiedostossa `rag/prompts/<agentti>.md`. Kaikki agentit saavat syötteinä:

- `job_posting`: otsikko, yritys, kaupunki ja `clean_text`
- `source_facts`: `outline()`
- `tailored_cv`: koottu CV tunnisteineen, ja korjauskierroksilla myös `panel_feedback`
- `rule_findings`: sääntötarkistuksen tulokset

Kirjoita CV:n kieli ilmoituksen mukaan (`fi` tai `en`, kuten nykyisin). Hakijasta puhutaan neutraalisti ("the candidate", ei "he" tai "she").

### 1. Räätälöijä (tailor)

```
You plan how to tailor a one-page CV to a job posting. Code assembles the CV
from the source lines; you choose what to show and how to word it.

Hard rules:
- Use ONLY facts in source_facts. Never invent skills, tools, employers,
  dates, titles, certifications or numbers. If the posting asks for something
  the candidate lacks, do not add it. At most emphasise adjacent real
  experience honestly (e.g. Docker Compose for a Kubernetes ask, without
  claiming Kubernetes).
- You may reorder sections, drop lines, pick focus skills, write the 2-3
  sentence summary, and reword individual bullet lines. Every rewording
  references exactly one source line id and must not add anything that line
  (or the rest of the CV) does not say.
- Never reword titles, employers, dates, education or languages.
- Mirror the posting's terminology only where it truthfully applies.
- Write like the candidate wrote it: concrete, plain, first person or
  CV-style without pronouns. Never refer to "the candidate" / "hakija".
- Position the candidate as a student/junior. Do not inflate seniority.

On revision rounds you receive panel_feedback and rule_findings. Fix every
blocking issue. For a suggestion you do not apply, give a one-line reason.

Output JSON matching TailorPlan.
```

### 2. Faktantarkistaja (fact_checker): veto-oikeus

```
You verify a tailored CV against the candidate's source facts. Rule-based
checks have already run; their findings are in rule_findings. Your job is
what rules cannot see: meaning.

Check every claim in tailored_cv: skills, tools, responsibilities, scope,
team role, outcomes, education, language levels. Classify each problem:
- "fabricated": not supported by source_facts at all -> blocking
- "exaggerated": stronger than the source (e.g. "led" vs "participated",
  "production system" vs "course project", "expert" vs "used") -> blocking
- "merged_contexts": combines facts from different projects into one claim
  (e.g. "built React apps with Python and PostgreSQL" when those were
  separate projects) -> blocking
- "vague_but_ok": acceptable but could be more precise -> suggestion

Quote the exact phrase from tailored_cv and the source line id (or state that
none exists). When in doubt, flag it. Do not judge style or fit.

Output JSON matching ReviewOutput. verdict = "reject" if any blocking issue.
```

### 3. Rekrytoija ja ATS (recruiter)

```
You are an experienced Finnish IT recruiter doing a 30-second first screen,
plus an ATS keyword check.

Evaluate:
- Which must-have and nice-to-have requirements are visibly covered, and
  which important keywords are missing. Only suggest keywords the candidate
  truthfully has per source_facts. Never suggest adding skills they lack.
- Is the fit for this role clear in 30 seconds? Is the most relevant content
  at the top of the page?
- Language quality and naturalness. Flag phrases that sound machine-written
  or generic (they could apply to any applicant) as suggestions.
- Anything that would cause a quick rejection.

Output JSON matching ReviewOutput. Use blocking only for issues that would
likely get the CV discarded.
```

### 4. Tekninen haastattelija (tech_interviewer)

```
You are a technical interviewer for this role. Decide which claims in the
tailored CV you would probe, and whether they would hold up based on
source_facts. Many projects are student or learning projects; the candidate
must be able to explain what they personally did.

Flag claims that sound stronger than the candidate could defend in a
technical conversation and suggest more defensible wording. List 3-5 likely
interview questions this CV invites, so the candidate can prepare.

Output JSON matching ReviewOutput, with interview_questions filled.
```

### 5. Esihenkilö (hiring_manager)

```
You are the hiring manager for this role. Judge overall fit and positioning:
- Is the positioning right for a student/junior applicant?
- Does the page tell a coherent story of why this person suits this role?
- Is non-IT work history (for example customer service, retail, logistics,
  security or military service) framed as a strength where relevant
  (responsibility, reliability, working under pressure), without
  overstating it?
- What single change would most improve the candidate's chances?

Output JSON matching ReviewOutput.
```

## JSON-skeemat

Validoi Pydanticilla (v2 on jo asennettu). Anna malli-API:lle sama skeema JSON Schemana `complete_json`:n kautta. Jos validointi epäonnistuu, yritä kerran uudelleen ja liitä virheilmoitus viestiin.

```python
class Rewrite(BaseModel):
    source_id: str          # yksi lähderivin tunniste, esim. "S3E2B1"
    text: str
    reason: str

class TailorPlan(BaseModel):       # nykyinen PLAN_SCHEMA + rewrites + declined
    section_order: list[str]
    drop: list[str]
    focus_skills: list[str]
    summary: str
    rewrites: list[Rewrite] = []
    declined_suggestions: list[str] = []   # "ehdotus: syy", jota ei toteutettu

class Finding(BaseModel):
    severity: Literal["blocking", "suggestion"]
    category: str
    quote: str | None       # tarkka lainaus tailored_cv:stä
    source_id: str | None   # lähderivi, johon löydös liittyy
    issue: str
    fix: str

class ReviewOutput(BaseModel):
    agent: str
    verdict: Literal["approve", "reject"]
    score: int = Field(ge=1, le=10)
    findings: list[Finding]
    interview_questions: list[str] = []
    summary: str            # 1-2 lausetta
```

Muutoslista ("mitä muuttui ja mihin faktaan se perustuu") **lasketaan koodissa** suunnitelmasta ja lähteestä: pois jätetyt, uudelleenjärjestetyt, uudelleenmuotoillut (ennen/jälkeen ja `source_id`) ja profiiliteksti. Malli ei raportoi sitä itse, joten lista ei voi valehdella.

## Orkestrointi

- Uusi moduuli `rag/panel.py`. Se käyttää `cvtailor`-funktioita eikä kopioi niitä.
- Paneelikutsut rinnakkain `ThreadPoolExecutor`illa, kuten `llmscore.py`:ssä. `rag/llm.py` on synkroninen, eikä sille tarvita async-clientia.
- **Hyväksyntäehto:** kaikilla `verdict == "approve"` eikä yhtään `blocking`-löydöstä. Faktantarkistajan hylkäys estää aina hyväksynnän. Toisen arvioijan blocking-löydös, joka vaatisi taidon lisäämistä, hylätään koodissa ja kirjataan.
- Korjauskierroksella räätälöijä saa kaikkien arvioijien löydökset ja sääntölöydökset yhdistettyinä ja duplikaatit poistettuina.
- **Mallit:** räätälöijä käyttää `service.llm("writing")`, eli `CLAUDE_MODEL_WRITING` tai `CLAUDE_MODEL`. Paneeli käyttää omaa asetusta `PANEL_MODEL`, jonka tyhjä arvo tarkoittaa `CLAUDE_MODEL`ia. Oletuksia (`claude-haiku-5-5`) ei vaihdeta.
- **Ollama:** paneeli toimii myös `LLM_PROVIDER=ollama`-asetuksella, mutta kutsut tehdään peräkkäin, koska yksi näytönohjain ei hyödy rinnakkaisuudesta. Käyttöliittymä varoittaa, että ajo kestää minuutteja.
- **Virheet:** `LLMError`-uudelleenyritys eksponentiaalisella backoffilla, enintään 3 yritystä. Jos yksi arvioija kaatuu lopullisesti, ajo jatkuu ilman sitä tilassa `needs_review`. Faktantarkistajan kaatuminen tarkoittaa aina `needs_review`-tilaa.
- **Token-rajat:** `ClaudeLLM`:n ajattelu (effort) kuluttaa `max_tokens`-rajaa, ja liian pieni raja katkaisee JSONin. Käytä vähintään 4000:aa.
- Ajo on taustatehtävä (säie), eikä se ole HTTP-pyynnön sisällä.

## Edistymisloki

**Backend:**
- `POST /api/cv/runs` (`url`, `lang`) luo ajon, käynnistää sen taustalla ja palauttaa heti `run_id`:n. Paikallisuussuoja (`X-Requested-With: jobhunter`) koskee myös tätä reittiä.
- Orkestroija kirjaa jokaisen vaiheen tauluun `run_events` (run_id, seq, aikaleima, tyyppi, viesti, data JSON).
- `GET /api/cv/runs/{run_id}/events` lähettää tapahtumat SSE:nä (`text/event-stream`). Se tukee `Last-Event-ID`-otsaketta, joten jo tapahtuneet toistetaan uudelleenyhdistettäessä. Heartbeat-kommentti lähtee 15 sekunnin välein. Käytä `/api/chat`-reitin `_sse`-apua mallina.
- `GET /api/cv/runs/{run_id}` palauttaa tilan ja kaikki tapahtumat JSONina pollausta varten.
- `POST /api/cv/runs/{run_id}/cancel` asettaa peruutuslipun. Ajo tarkistaa sen vaiheiden välillä, ja käynnissä olevien kutsujen tulokset hylätään. Säikeitä ei tapeta väkisin.

**Tapahtumatyypit:**
`run_started`, `facts_loaded` (lähde `cv.md` / `cv_fi.md`), `tailor_started` / `tailor_finished` (kierros, muutosten määrä), `rules_checked` (löydökset), `review_started` / `review_finished` arvioijittain (tuomio, pisteet, blocking-määrä), `revision_started` (kierros N/2, hylänneet arvioijat), `fallback_applied` (mikä korvattiin alkuperäisellä), `retry` (syy, yritys N/3), `run_finished` (approved / needs_review, kesto, tokenit, arvioitu hinta), `run_failed` (selkokielinen virhe), `run_cancelled`.

**Frontend** (`CvView.tsx` ja tarvittaessa uusi komponentti, nykyinen tyyli ja teemat):
- Vaihepalkki: Räätälöinti → Sääntötarkistus → Paneeli → (Korjaus) → Valmis.
- Arvioijat omina kortteinaan, jotka vaihtuvat tilasta "arvioi…" tuomioon ja pisteisiin.
- Aikaleimallinen loki, automaattinen vieritys (ei, jos käyttäjä on vierittänyt ylös) ja kulunut aika.
- Peruuta-painike.
- Valmiin ajon jälkeen CV näkyy nykyisessä näkymässä, ja paneelin tulokset ovat sivupalkissa.
- Aiemmat ajot listataan ilmoituksen yhteydessä, ja niiden loki on luettavissa.

## Tallennus

SQLite (`data/rag.sqlite`, sama `Store` kuin muulla sovelluksella): taulut `cv_runs` (run_id, url, lang, aikaleima, tila, mallit, tokenit, hinta) ja `run_events`. Jokaisen kierroksen suunnitelma, koottu CV ja paneelin tulokset tallennetaan tapahtumien dataan. Lopullinen CV menee `save_cv`:llä tiedostoon `cvs.json`.

## Käyttöliittymä

- Lopullinen CV nykyisellä yhden sivun pohjalla ja tila: hyväksytty / vaatii tarkistusta.
- Muutokset alkuperäiseen: koodin laskema lista, jossa uudelleenmuotoilut näkyvät ennen/jälkeen-pareina lähderivin kanssa.
- Paneelin arviot arvioijittain: pisteet, tuomio ja löydökset. Jäljelle jääneet blocking-löydökset näkyvät ylimpänä.
- Haastattelukysymykset omana osionaan, ja ne voi kopioida.
- Käsin muokkaus ja PDF toimivat kuten nyt (`merge_cv_edit`, selaimen tulostus). WeasyPrintiä ei lisätä.

## API ja kustannukset

- `ANTHROPIC_API_KEY` tulee `.env`:stä, eikä sitä tulosteta lokiin tai käyttöliittymään.
- Lokita tokenit jokaisesta kutsusta (input, output ja välimuistiosumat, jos käytössä). Laske hinta mallin hinnaston mukaan. Hinnat ovat konfiguraatiossa, eivät koodissa.
- Arvio enimmäismäärästä: 1 räätälöijä + 4 arvioijaa per kierros, enintään 3 kierrosta eli 15 kutsua. Mittaa toteutunut kustannus 3–5 oikealla ajolla ja kirjaa se README:hen.
- Syötteen alkuosa (system prompt, faktat, ilmoitus) on sama kaikilla arvioijilla. Käytä Clauden prompt cachingia, jos se on helppo lisätä `ClaudeLLM`:ään.

## Yksityisyys

Claudelle lähtee ilmoitus ja CV:n sisältö ilman yhteystietorivejä (`translation_units` / `outline` eivät sisällä niitä), kuten nykyisessä räätälöinnissä. Nimen lähteminen on hyväksytty. Päivitä README:n taulukko "Mitä Claudelle lähtee".

## Testit

Testit käyttävät `FakeLLM`:ää. Tavallinen testiajo ei tee oikeita API-kutsuja.

- Hyväksyntä ensimmäisellä kierroksella, korjaus toisella, kahden kierroksen raja ja `fallback_applied`.
- Faktantarkistajan veto, vaikka muut hyväksyvät.
- Virheellinen JSON (yksi uusi yritys), arvioijan lopullinen kaatuminen ja peruutus kesken paneelin.
- Rivimuotoilun sääntöhylkäys: uusi tekniikka, uusi luku sekä nimikkeen tai päivämäärän muuttaminen.
- Muutoslista lasketaan oikein suunnitelmasta.
- Tapahtumien järjestys ja sisältö sekä uudelleenyhdistäminen `Last-Event-ID`:llä.
- **Hallusinaatiotesti** (`pytest -m live`, oikea API): syötä faktantarkistajalle CV, johon on lisätty keksitty taito, venytetty nimike ja kahden projektin yhdistävä väite. Varmista, että se hylkää kaikki kolme.
- Nykyiset testit (104) menevät edelleen läpi.

## Rajaukset

- Ei automaattista lähettämistä mihinkään.
- Hakemuskirje ei kuulu tähän vaiheeseen. Rakenna `rag/panel.py` kuitenkin niin, että saman putken voi myöhemmin ajaa hakemuspaketille (`rag/package.py`): arvioijat ja hyväksyntäehto ovat yleisiä, ja räätälöijä on vaihdettava osa.
- Pelkkä räätälöinti ilman paneelia säilyy nopeana ja oletuksena. Paneeli on erillinen painike.

## Valmiin määritelmä

- Paneeliajo käynnistyy yhdellä painalluksella valitulle ilmoitukselle ja näkyy reaaliaikaisesti.
- Keksitty sisältö ei koskaan päädy lopulliseen CV:hen: säännöt ja faktantarkistaja estävät sen, ja testit todistavat sen.
- Kaikki testit menevät läpi, ja `npm run build` onnistuu.
- README:ssä on lyhyt osio: Mermaid-kaavio agenttien kulusta, asetukset (`PANEL_MODEL`) ja mitattu kustannus per ajo.
- `graphify update .` on ajettu muutosten jälkeen.
