from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_app_renders_offline_with_demo_label():
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "streamlit_app.py"), default_timeout=60).run()
    assert not at.exception
    assert any("not investment advice" in m.value.lower() for m in at.markdown)
    assert any("SIMULATED" in m.value and "scraped from Wikipedia" in m.value for m in at.markdown)


def test_refresh_without_key_says_live_mode_is_off(monkeypatch, capsys):
    import importlib.util
    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
    spec = importlib.util.spec_from_file_location("refresh", Path(__file__).resolve().parent.parent / "scripts/refresh.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr("sys.argv", ["refresh.py"])
    assert mod.main() == 0
    assert "LIVE MODE IS OFF" in capsys.readouterr().out


def test_partial_live_coverage_is_labelled_and_universes_are_separate():
    root = Path(__file__).resolve().parent.parent
    if not (root / "data/live_prices.csv.gz").exists():
        return
    at = AppTest.from_file(str(root / "streamlit_app.py"), default_timeout=60).run()
    assert not at.exception
    assert any("Live prices cover" in m.value and "of 503 symbols" in m.value for m in at.markdown)
    uni = at.radio[0]
    assert uni.label == "Price universe" and len(uni.options) == 2
    at = uni.set_value(uni.options[1]).run()  # switch to the simulated universe
    assert not at.exception and any("SIMULATED, " in m.value for m in at.markdown)
