import pytest
from rag import llm, style


def test_finds_ai_phrases_but_not_plain_words():
    t = ("A highly motivated student who brings a strong foundation in DevOps, complemented by a diverse background. "
         "This candidate is passionate about cloud.")
    found = [x.lower() for x in style.find(t)]
    for p in ["highly motivated", "brings a strong", "strong foundation", "complemented by", "this candidate", "passionate about"]:
        assert p in found, p
    assert style.find("Fourth-year IT student who built a CI/CD pipeline with GitHub Actions.") == []
    assert style.find("Olen erittäin motivoitunut ja minulla on vahva pohja, odotan innolla.", "fi")
    assert style.find("Rakensin Lenkkiloki-sovelluksen ja sen CI/CD-putken.", "fi") == []


def test_polish_rewrites_once_and_keeps_better_version():
    texts = {"a": "Highly motivated student passionate about DevOps.", "b": "Built a GPX analyzer in JavaScript."}
    fake = llm.FakeLLM([{"a": "IT student who works with DevOps tools."}])
    out, left = style.polish(texts, fake, "en")
    assert out["a"] == "IT student who works with DevOps tools." and out["b"] == texts["b"]
    assert left == {} and len(fake.calls) == 1
    assert "highly motivated" in fake.calls[0]["messages"][0]["content"].lower()   # malli saa löydetyt fraasit


def test_polish_rejects_worse_or_truncated_rewrite():
    texts = {"a": "Highly motivated student with experience in Docker, Linux and CI/CD pipelines."}
    out, left = style.polish(texts, llm.FakeLLM([{"a": "Student."}]), "en")   # liian lyhyt: tietoa katosi
    assert out == texts and left["a"] == ["Highly motivated"]
    out, _ = style.polish(texts, llm.FakeLLM([llm.LLMError("Ollama ei vastaa")]), "en")
    assert out == texts                                                          # virhe ei kaada generointia


def test_rewritten_text_is_still_fact_checked():
    from rag import package
    from tests.test_generative import _grounding
    fake = llm.FakeLLM([{"cover_letter": "Olen erittäin motivoitunut ja osaan Linuxia.", "why_apply": "Kiinnostava tehtävä Firma Oy:ssä.",
                         "strengths": "CI/CD-osaaminen."},
                        {"cover_letter": "Osaan Linuxia ja Kubernetesia."}])       # uudelleenkirjoitus lisää väitteen
    p = package.build_package(_grounding(), fake, "fi", "Aino", cv_full="")
    cl = next(s for s in p["sections"] if s["key"] == "cover_letter")
    assert cl["text"] == "Osaan Linuxia ja Kubernetesia." and cl["verify"]["flagged"] == 1   # keksitty Kubernetes liputetaan
    assert cl["style"] == []


def test_third_person_is_caught_in_inflected_forms():
    assert style.find("Hakijalla on kokemusta Pythonista.", "fi")
    assert style.find("Hakijan projektit olivat laajoja.", "fi")


@pytest.mark.parametrize("text,hit", [
    ("Neljännen vuoden insinööriopiskelija, joka valmistuu kesällä 2027.", "joka valmistuu"),
    ("Opiskelija. Rakentaa Python- ja PostgreSQL-pohjaisia dataputkia.", "Rakentaa"),
    ("Opiskelee tietotekniikkaa ESAMKissa.", "Opiskelee"),
])
def test_finnish_third_person_is_found(text, hit):
    assert hit in style.third_person_fi(text)
    assert any("kolmas persoona" in f for f in style.find(text, "fi"))


@pytest.mark.parametrize("text", [
    "Neljännen vuoden insinööriopiskelija, valmistun kesällä 2027. Rakennan Python-sovelluksia.",
    "Tein sovelluksen, joka kerää dataa taustalla. Olen tehnyt React-käyttöliittymiä.",
    "Konttisoin sovelluksen Docker Composella.",
])
def test_first_person_finnish_is_not_flagged(text):
    assert style.third_person_fi(text) == []
