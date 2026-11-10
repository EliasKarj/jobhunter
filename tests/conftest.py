import os
import sys
import tempfile
from pathlib import Path

# Testit eivät koskaan näe käyttäjän omaa dataa: oma tyhjä datakansio ennen kuin mikään moduuli lataa datadirin.
os.environ["JOBHUNTER_DATA"] = tempfile.mkdtemp(prefix="jobhunter-test-")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_configure(config):
    config.addinivalue_line("markers", "live: oikeaa Claude API:a vastaan (aja: pytest -m live)")


def pytest_collection_modifyitems(config, items):
    """live-testit ajetaan vain, kun ne valitaan erikseen (-m live): tavallinen ajo ei tee API-kutsuja."""
    import pytest
    if "live" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="oikea API: aja pytest -m live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _archive_to_tmp(tmp_path, monkeypatch):
    """Testit eivät koskaan kirjoita oikeaan CV-arkistoon."""
    from server import archive, pdf
    monkeypatch.setattr(archive, "ARCHIVE", tmp_path / "archive" / "cv")
    monkeypatch.setattr(pdf, "ENABLED", False)          # ei selainta testeissä


@pytest.fixture(autouse=True)
def _style_to_tmp(tmp_path, monkeypatch):
    """Testit eivät lue käyttäjän omia tyylinäytteitä tai tyyliprofiilia."""
    from rag import stylesamples, styleprofile
    monkeypatch.setattr(stylesamples, "PATH", tmp_path / "style_samples.json")
    monkeypatch.setattr(styleprofile, "PATH", tmp_path / "style_profile.json")
