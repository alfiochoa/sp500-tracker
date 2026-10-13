from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_app_renders_offline_with_demo_label():
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "streamlit_app.py"), default_timeout=60).run()
    assert not at.exception
    assert any("not investment advice" in m.value.lower() for m in at.markdown)
    assert any("DEMO DATA" in m.value for m in at.markdown)
