import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def never_call_twelvedata(monkeypatch):
    """Tests, CI and the Streamlit app must never reach api.twelvedata.com (paid credits on a shared key)."""
    import requests

    real = requests.adapters.HTTPAdapter.send

    def guarded(self, request, *a, **k):
        assert "twelvedata.com" not in (request.url or ""), f"tests must not call Twelve Data: {request.url}"
        return real(self, request, *a, **k)

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", guarded)
