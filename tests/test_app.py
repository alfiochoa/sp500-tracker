import os
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent


def _md(at):
    return " ".join(m.value for m in at.markdown)


def test_deployed_app_shows_only_real_data_no_simulated_wording():
    os.environ.pop("SP500_SYNTHETIC", None)
    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=90).run()
    assert not at.exception
    text = _md(at)
    assert "real end-of-day closes from Twelve Data" in text and "not investment advice" in text.lower()
    assert "Wikipedia (CC BY-SA 4.0)" in text and "symbols fresh" in text
    for bad in ("SIMULATED", "SYNTHETIC", "demo prices", "503"):
        assert bad.lower() not in text.lower().replace("~303", ""), bad


def test_synthetic_mode_is_loudly_labelled(monkeypatch):
    monkeypatch.setenv("SP500_SYNTHETIC", "1")
    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=90).run()
    assert not at.exception
    assert any("SYNTHETIC TEST DATA" in m.value for m in at.markdown)


def test_refresh_without_key_changes_nothing(monkeypatch, capsys):
    import importlib.util
    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
    spec = importlib.util.spec_from_file_location("refresh", ROOT / "scripts/refresh.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main([]) == 0
    assert "no data fetched" in capsys.readouterr().out
