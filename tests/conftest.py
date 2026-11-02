"""Testler için ortak yardımcılar. Hiçbir test ağa çıkmaz."""

import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def make_response(body: str = "", status: int = 200, json_body=None) -> requests.Response:
    """Gerçek requests.Response üretir (raise_for_status vb. çalışsın diye)."""
    r = requests.Response()
    r.status_code = status
    r.encoding = "utf-8"
    if json_body is not None:
        import json as _json
        r._content = _json.dumps(json_body).encode("utf-8")
        r.headers["Content-Type"] = "application/json"
    else:
        r._content = body.encode("utf-8")
    return r


class FakeSession:
    """requests.Session yerine geçer; çağrıları kaydeder."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def _next(self):
        return self.responses.pop(0) if self.responses else make_response("")

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._next()

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._next()


@pytest.fixture
def fake_session():
    return FakeSession


@pytest.fixture
def response_factory():
    return make_response


@pytest.fixture
def profile():
    """storage'da tutulan kullanıcı profili."""
    return {
        "name": "Ali",
        "surname": "Yılmaz",
        "tc": "10000000146",
        "phone": "5321234567",
        "boib": "BOIB-9",
        "attendant": False,
        "attendant_tc": "",
        "attendant_name": "",
        "birth_day": "5",
        "birth_month": "3",
        "birth_year": "1990",
    }


@pytest.fixture
def store(tmp_path):
    from cryptography.fernet import Fernet
    from gsbot.storage import Storage
    return Storage(str(tmp_path / "t.db"), Fernet.generate_key().decode())


@pytest.fixture
def fixture_row():
    return {"branch": "1", "id": "9876", "text": "Galatasaray - Fenerbahçe"}
