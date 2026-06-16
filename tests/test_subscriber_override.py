"""Tests for per-subscriber URL addressing."""
import pytest
from tele2api.tele2_api import Tele2Api

MASTER = "79990000000"
SLAVE = "79991112233"


class _FakeResp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


def _patch_get(api, seen):
    def _fake_get(url, **kwargs):
        seen["url"] = url
        return _FakeResp(200, {"data": {"value": 1.0}})
    api._get = _fake_get


def test_subscriber_override_changes_url(monkeypatch):
    api = Tele2Api(MASTER, access_token="t")
    seen = {}
    monkeypatch.setattr(api, "_get", lambda url, **k: seen.update({"url": url}) or _FakeResp(200, {"data": {"value": 1.0}}))

    api.get_balance(subscriber=SLAVE)
    assert f"/subscribers/{SLAVE}/balance" in seen["url"]

    api.get_balance()
    assert f"/subscribers/{MASTER}/balance" in seen["url"]


def test_sub_helper_default_is_master():
    api = Tele2Api(MASTER, access_token="t")
    assert api._sub() == f"https://api.t2.ru/api/subscribers/{MASTER}"


def test_sub_helper_override():
    api = Tele2Api(MASTER, access_token="t")
    assert api._sub(SLAVE) == f"https://api.t2.ru/api/subscribers/{SLAVE}"


@pytest.mark.parametrize("method,kwargs,suffix,body", [
    ("get_balance", {}, "/balance", {"data": {"value": 1.0}}),
    ("get_rests", {}, "/rests", {"data": {"rests": []}}),
    ("get_rests_detailed", {}, "/rests", {"data": {"rests": []}}),
    ("get_rests_rollover", {}, "/rests", {"data": {"rests": []}}),
    ("get_profile", {}, "/profile", {"data": {}}),
    ("get_status", {}, "/status", {"data": "ACTIVATED"}),
    ("get_tariff", {}, "/tariff", {"data": {}}),
    ("get_slaves", {}, "/numbers/slaves", {}),
    ("get_active_lots", {}, "/exchange/lots/created", {"data": []}),
])
def test_get_methods_use_subscriber_url(monkeypatch, method, kwargs, suffix, body):
    api = Tele2Api(MASTER, access_token="t")
    seen = {}

    def fake_get(url, **k):
        seen["url"] = url
        return _FakeResp(200, body)

    monkeypatch.setattr(api, "_get", fake_get)
    getattr(api, method)(subscriber=SLAVE, **kwargs)
    assert f"/subscribers/{SLAVE}{suffix}" in seen["url"]
