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


@pytest.mark.parametrize("http_method,svc_method,action_suffix", [
    ("_put", "connect_service", "enable"),
    ("_delete", "disconnect_service", "disable"),
])
def test_service_toggle_url_and_check(monkeypatch, http_method, svc_method, action_suffix):
    api = Tele2Api(MASTER, access_token="t")
    calls = []

    def fake_post(url, **k):
        calls.append(("post", url, k))
        return _FakeResp(200, {})

    def fake_action(url, **k):
        calls.append(("action", url))
        return _FakeResp(200, {})

    monkeypatch.setattr(api, "_post", fake_post)
    monkeypatch.setattr(api, http_method, fake_action)

    result = getattr(api, svc_method)("99999", subscriber=SLAVE)

    assert result is None
    check_url = calls[0][1]
    assert f"/subscribers/{SLAVE}/services/notifications/check" in check_url
    body = calls[0][2].get("json", {})
    assert body["changedServices"][0]["action"] == action_suffix
    action_url = calls[1][1]
    assert f"/subscribers/{SLAVE}/services/99999" in action_url


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
