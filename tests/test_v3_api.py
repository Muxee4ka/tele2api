"""Тесты для API v3.0.0: типизированные исключения (#6), экспирация токена (#7),
маркет-хелперы (#8), нормализация данных (#9), профиль impersonate (#10).

Все вызовы замоканы — сеть не используется.
"""
import base64
import json
import time

import pytest

from tele2api import (
    Tele2Api,
    Tele2ApiError,
    Tele2AuthError,
    Tele2LotError,
    Tele2ServiceError,
    Service,
)
from tele2api.tele2_api import _parse_money, IMPERSONATE

MASTER = "79990000000"


class _FakeResp:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = text

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _api():
    return Tele2Api(MASTER, access_token="t")


def _make_jwt(exp):
    """Собрать неподписанный JWT с заданным ``exp`` (claim в payload)."""
    def b64(d):
        raw = json.dumps(d).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return f"{b64({'alg': 'none'})}.{b64({'exp': exp})}.sig"


# --- #6: типизированные исключения -------------------------------------------

def test_create_lot_returns_id_on_success(monkeypatch):
    api = _api()
    monkeypatch.setattr(api, "_put", lambda url, **k: _FakeResp(200, {"data": {"id": "LOT-1"}}))
    assert api.create_lot("data", 5, 100) == "LOT-1"


def test_create_lot_raises_lot_error(monkeypatch):
    api = _api()
    monkeypatch.setattr(api, "_put", lambda url, **k: _FakeResp(400, {"meta": {"status": "LOT_LIMIT"}}))
    with pytest.raises(Tele2LotError) as exc:
        api.create_lot("data", 5, 100)
    assert exc.value.code == "LOT_LIMIT"


def test_connect_service_raises_service_error(monkeypatch):
    api = _api()
    monkeypatch.setattr(api, "_post", lambda url, **k: _FakeResp(200, {}))
    monkeypatch.setattr(api, "_put", lambda url, **k: _FakeResp(403, {"meta": {"status": "NOT_ALLOWED"}}))
    with pytest.raises(Tele2ServiceError) as exc:
        api.connect_service("99999")
    assert exc.value.code == "NOT_ALLOWED"


def test_exception_hierarchy():
    assert issubclass(Tele2AuthError, Tele2ApiError)
    assert issubclass(Tele2LotError, Tele2ApiError)
    assert issubclass(Tele2ServiceError, Tele2ApiError)


def test_status_falls_back_to_text_when_not_json():
    api = _api()
    resp = _FakeResp(503, body=ValueError("not json"), text="  SSO_NOT_CONFIGURED  ")
    assert api._status(resp) == "SSO_NOT_CONFIGURED"


# --- #7: экспирация токена ----------------------------------------------------

def test_token_expires_at_none_without_token():
    api = Tele2Api(MASTER)
    assert api.token_expires_at is None
    assert api.is_token_expired() is True


def test_token_expires_at_parses_exp():
    exp = int(time.time()) + 3600
    api = Tele2Api(MASTER, access_token=_make_jwt(exp))
    assert int(api.token_expires_at.timestamp()) == exp


def test_is_token_expired_margin():
    api = Tele2Api(MASTER, access_token=_make_jwt(int(time.time()) + 3600))
    assert api.is_token_expired(margin=300) is False
    assert api.is_token_expired(margin=4000) is True  # окно больше срока жизни


def test_ensure_token_refreshes_when_near_expiry(monkeypatch):
    api = Tele2Api(MASTER, access_token=_make_jwt(int(time.time()) + 60))
    called = []
    monkeypatch.setattr(api, "update_token", lambda *a, **k: called.append(True))
    api.ensure_token(margin=300)
    assert called == [True]


def test_ensure_token_skips_when_fresh(monkeypatch):
    api = Tele2Api(MASTER, access_token=_make_jwt(int(time.time()) + 3600))
    called = []
    monkeypatch.setattr(api, "update_token", lambda *a, **k: called.append(True))
    api.ensure_token(margin=300)
    assert called == []


# --- #8: маркет-хелперы -------------------------------------------------------

def _lot(lot_id, amount, ttype="data", value=5):
    return {"id": lot_id, "trafficType": ttype, "status": "active",
            "volume": {"value": value, "uom": "gb"}, "cost": {"amount": amount, "currency": "rub"}}


def _listing(item_id, amount, my=False):
    return {"id": item_id, "my": my, "cost": {"amount": amount, "currency": "rub"}}


def test_undercut_no_competitors(monkeypatch):
    api = _api()
    lot = _lot("L1", 100)
    monkeypatch.setattr(api, "get_lot_position", lambda *a, **k: [_listing("L1", 100, my=True)])
    res = api.undercut_lot(lot)
    assert res == {"changed": False, "old_price": 100, "new_price": 100, "reason": "no_competitors"}


def test_undercut_already_cheapest(monkeypatch):
    api = _api()
    lot = _lot("L1", 100)
    monkeypatch.setattr(api, "get_lot_position", lambda *a, **k: [_listing("L1", 100), _listing("C1", 150)])
    res = api.undercut_lot(lot)
    assert res["changed"] is False and res["reason"] == "already_cheapest"


def test_undercut_drops_price(monkeypatch):
    api = _api()
    lot = _lot("L1", 100)
    patched = {}
    monkeypatch.setattr(api, "get_lot_position", lambda *a, **k: [_listing("L1", 100), _listing("C1", 80)])
    monkeypatch.setattr(api, "patch_lot", lambda lid, amount, sub=None: patched.update(id=lid, amount=amount))
    res = api.undercut_lot(lot, step=1, min_amount=1)
    assert res == {"changed": True, "old_price": 100, "new_price": 79, "reason": "undercut"}
    assert patched == {"id": "L1", "amount": 79}


def test_undercut_clamps_to_min_amount(monkeypatch):
    api = _api()
    lot = _lot("L1", 100)
    monkeypatch.setattr(api, "get_lot_position", lambda *a, **k: [_listing("C1", 1)])
    monkeypatch.setattr(api, "patch_lot", lambda *a, **k: None)
    res = api.undercut_lot(lot, step=5, min_amount=1)
    assert res["new_price"] == 1  # 1 - 5 = -4, зажато до min_amount


def test_get_my_lot_position(monkeypatch):
    api = _api()
    lot = _lot("L2", 100)
    monkeypatch.setattr(api, "get_lot_position",
                        lambda *a, **k: [_listing("C1", 90), _listing("L2", 100), _listing("C2", 110)])
    assert api.get_my_lot_position(lot) == 2
    monkeypatch.setattr(api, "get_lot_position", lambda *a, **k: [_listing("C1", 90)])
    assert api.get_my_lot_position(lot) == 0


def test_premium_all_lots_aggregates(monkeypatch):
    api = _api()
    monkeypatch.setattr(api, "get_active_lots", lambda sub=None: [_lot("A", 10), _lot("B", 20)])

    def fake_premium(lot_id, sub=None):
        if lot_id == "B":
            raise Tele2LotError("BOOM", "BOOM")
    monkeypatch.setattr(api, "premium_lot", fake_premium)
    res = api.premium_all_lots()
    assert res["ok"] == ["A"] and res["failed"] == ["B"] and res["errors"]["B"] == "BOOM"


def test_delete_all_lots_filters_traffic_type(monkeypatch):
    api = _api()
    monkeypatch.setattr(api, "get_active_lots",
                        lambda sub=None: [_lot("A", 10, ttype="data"), _lot("V", 5, ttype="voice")])
    deleted = []
    monkeypatch.setattr(api, "delete_lot", lambda lid, sub=None: deleted.append(lid))
    res = api.delete_all_lots(traffic_type="voice")
    assert deleted == ["V"] and res["ok"] == ["V"]


# --- #9: нормализация данных --------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ({"amount": 5, "currency": "rub"}, 5.0),
    (7, 7.0),
    (3.5, 3.5),
    (None, None),
])
def test_parse_money(value, expected):
    assert _parse_money(value) == expected


def test_service_model():
    connected = Service({"billingServiceId": "1", "name": "X", "status": "CONNECTED",
                         "abonentFee": {"amount": 50, "currency": "rub"},
                         "disconnectionAvailabilityStatus": {"canDisconnect": True}})
    assert connected.is_connected is True
    assert connected.can_disconnect is True
    assert connected.fee == 50.0
    assert connected.billing_id == "1"


def test_service_can_disconnect_ignores_show_button():
    # showDisconnectButton выставлен, но услуга не подключена и снять её нельзя
    svc = Service({"id": "2", "name": "Y", "status": "AVAILABLE", "showDisconnectButton": True})
    assert svc.is_connected is False
    assert svc.can_disconnect is False


def test_get_charges_flat(monkeypatch):
    api = _api()
    raw = [{
        "type": "SUBSCRIPTION_FEE",
        "subGroups": [{"consumingServices": [
            {"name": "Тариф", "cost": {"amount": 300, "currency": "rub"}},
        ]}],
    }, {
        "type": "CONTENT",
        "subGroups": [{"consumingServices": [{"name": "Подписка", "cost": 99}]}],
    }]
    monkeypatch.setattr(api, "get_charges", lambda month=None, subscriber=None: raw)
    flat = api.get_charges_flat()
    assert flat == [
        {"name": "Тариф", "amount": 300.0, "category": "SUBSCRIPTION_FEE"},
        {"name": "Подписка", "amount": 99.0, "category": "CONTENT"},
    ]


def test_get_actual_fee_from_charges(monkeypatch):
    api = _api()
    monkeypatch.setattr(api, "get_charges_flat",
                        lambda subscriber=None: [{"name": "T", "amount": 250.0, "category": "SUBSCRIPTION_FEE"}])
    assert api.get_actual_fee() == 250.0


def test_get_actual_fee_falls_back_to_tariff(monkeypatch):
    api = _api()
    monkeypatch.setattr(api, "get_charges_flat", lambda subscriber=None: [])
    monkeypatch.setattr(api, "get_tariff", lambda subscriber=None: {"abonentFee": {"amount": 400, "currency": "rub"}})
    assert api.get_actual_fee() == 400.0


# --- #10: профиль impersonate -------------------------------------------------

def test_default_impersonate_is_working_profile():
    assert IMPERSONATE == "firefox133"
    assert _api()._impersonate == "firefox133"


def test_impersonate_override():
    api = Tele2Api(MASTER, access_token="t", impersonate="chrome131_android")
    assert api._impersonate == "chrome131_android"
