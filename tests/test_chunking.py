from rag.chunking import chunk_text, header
from rag.clean import clean_text, job_id
from rag.ingest import job_chunks, job_meta

AD = """Haemme junioritasoista sovelluskehittäjää tiimiimme.
Työskentelet sekä back endin että front endin parissa. Saat tueksesi kokeneet kollegat.
Hakijalta edellytämme
• Soveltuvaa teknisen alan koulutusta
• Back end -kehityksen perusosaamista (esim. Python ja Flask)
• Front end -kehityksen perusosaamista (HTML5, CSS3, JavaScript, esim. React)
• Relaatiotietokantojen (esim. PostgreSQL) ja SQL-kielen perusosaamista
Tarjoamme sinulle
• Merkityksellisen työn
• Hybridityömallin
• Kattavan työterveyshuollon"""


def test_short_text_is_one_chunk():
    chunks = chunk_text("Lyhyt ilmoitus.", 1000, 150)
    assert len(chunks) == 1
    assert chunks[0].text == "Lyhyt ilmoitus."


def test_empty_text_gives_no_chunks():
    assert chunk_text("", 1000, 150) == []
    assert chunk_text("\n\n  \n", 1000, 150) == []


def test_chunks_respect_size_and_offsets():
    for size, overlap in [(120, 30), (200, 50), (500, 100)]:
        chunks = chunk_text(AD, size, overlap)
        assert len(chunks) > 1
        for c in chunks:
            assert 0 < len(c.text) <= size
            assert c.text.strip()
            assert AD[c.start:c.end] == c.text  # lähdeviite osuu alkuperäiseen kohtaan


def test_chunks_cover_whole_text_in_order():
    chunks = chunk_text(AD, 150, 40)
    assert chunks[0].start == 0
    assert chunks[-1].end == len(AD)
    for a, b in zip(chunks, chunks[1:]):
        assert b.start > a.start  # etenee aina
        assert b.start <= a.end + 1  # ei aukkoja (rivinvaihto saa jäädä väliin)


def test_overlap_repeats_whole_lines_within_limit():
    chunks = chunk_text(AD, 150, 80)
    overlapping = [(a, b) for a, b in zip(chunks, chunks[1:]) if b.start < a.end]
    assert overlapping, "päällekkäisyyttä pitäisi syntyä"
    for a, b in overlapping:
        assert a.end - b.start <= 80
        assert AD[b.start - 1] == "\n" or b.start == 0  # alkaa rivin alusta


def test_zero_overlap_has_no_shared_text():
    chunks = chunk_text(AD, 150, 0)
    for a, b in zip(chunks, chunks[1:]):
        assert b.start >= a.end


def test_overlong_line_is_split_by_sentence_and_words():
    long = " ".join(["Tämä on pitkä lause, jossa on paljon sanoja."] * 40)
    chunks = chunk_text(long, 200, 40)
    assert all(len(c.text) <= 200 for c in chunks)
    assert chunks[-1].end == len(long)
    nospace = "a" * 450
    assert [len(c.text) for c in chunk_text(nospace, 200, 0)] == [200, 200, 50]


def test_overlap_must_be_smaller_than_size():
    import pytest
    with pytest.raises(ValueError):
        chunk_text(AD, 100, 100)


def test_job_chunks_have_header_and_title_chunk():
    meta = job_meta({"title": "Junior Developer", "company": "Firma Oy", "location": "Vaasa",
                     "url": "https://duunitori.fi/tyopaikat/tyo/firma-junior-20698022", "source": "duunitori",
                     "text": AD + "\n\nHakuaika umpeutuu 30.11.2026"})
    chunks = job_chunks(meta, 300, 50)
    assert chunks[0]["kind"] == "title" and chunks[0]["text"] == "Junior Developer | Firma Oy | Vaasa"
    assert all(c["text"].startswith("Junior Developer | Firma Oy | Vaasa\n") for c in chunks[1:])
    assert [c["idx"] for c in chunks] == list(range(len(chunks)))
    assert "Hakuaika umpeutuu" not in meta["clean_text"]
    assert meta["job_id"] == "duunitori:20698022"


def test_clean_removes_contacts_and_repeated_lines():
    t = ("Lisätietoja: matti.meikalainen@firma.fi, puh. 040 123 4567 tai www.firma.fi/ura\n"
         "Tämä on pitkä toistuva alatunnisterivi sivulla\nTämä on pitkä toistuva alatunnisterivi sivulla")
    c = clean_text(t)
    assert "@" not in c and "4567" not in c and "www." not in c
    assert c.count("alatunnisterivi") == 1


def test_cv_skip_sections(tmp_path):
    from rag.profile import cv_chunks
    cv = tmp_path / "cv.md"
    body = "Pitkä kuvaus tehtävistä ja vastuista, joka ylittää kuusikymmentä merkkiä varmasti tässä."
    cv.write_text(f"## Security Guard | Vartiopalvelu Oy\n{body}\n## Projektit\nReact-sovellus: {body}\n", encoding="utf-8")
    assert [c.source_ref for c in cv_chunks(cv)] == ["cv.md#Security Guard | Vartiopalvelu Oy", "cv.md#Projektit"]
    assert [c.source_ref for c in cv_chunks(cv, skip=("vartiopalvelu",))] == ["cv.md#Projektit"]


def test_job_id_fallback_is_stable():
    assert job_id("https://example.com/ura/foo") == job_id("https://example.com/ura/foo")
    assert job_id("https://example.com/a") != job_id("https://example.com/b")
    assert header("A", "", "Vaasa") == "A | Vaasa"
