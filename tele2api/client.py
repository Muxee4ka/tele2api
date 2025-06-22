from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
import os
import pickle

import requests


HEADERS: Dict[str, str] = {
    "Tele2-User-Agent": '"mytele2-app/4.17.0"; "unknown"; "Android/11"; "Build/165135449"',
    "User-Agent": "okhttp/4.9.2",
MAIN_API = "https://msk.t2.ru/api/subscribers/"
URL_VALIDATION = "https://msk.t2.ru/api/validation/number/"
URL_AUTH = "https://msk.t2.ru/auth/realms/tele2-b2c/protocol/openid-connect/token"
    "https://msk.t2.ru/auth/realms/tele2-b2c/credential-management/reset-options?username="
    "https://msk.t2.ru/auth/realms/tele2-b2c/credential-management/reset-password?username="
                    self.access_token = data.get("access_token", "")
                    self.refresh_token = data.get("refresh_token", "")
                    if self.access_token:
                        self.session.headers["Authorization"] = f"Bearer {self.access_token}"
            except Exception:
                pass
        if not self.access_token:
            print("Requesting SMS code...")
            self.get_sms_code()
            sms_code = input("Enter SMS code: ")
            result = self.authorization(sms_code)
            if isinstance(result, tuple):
                if self.token_file:
                    try:
                        with open(self.token_file, "wb") as fh:
                            pickle.dump({"access_token": self.access_token, "refresh_token": self.refresh_token}, fh)
                    except Exception:
                        pass
            else:
                raise RuntimeError(f"Authorization failed: {result}")
    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7,de;q=0.6,fr;q=0.5',
    "Cache-Control": "max-age=0",
    'Tele2-User-Agent': '"mytele2-app/4.17.0"; "unknown"; "Android/11"; "Build/165135449"',
    'X-API-Version': '1',
    'User-Agent': 'okhttp/4.9.2',
    'Accept-Encoding': 'gzip, deflate',
    'Accept': 'application/json, text/plain, */*',
    'Content-Type': 'application/json',
    'Connection': 'keep-alive'
}

MAIN_API = "https://msk.t2.ru/api/subscribers/"
URL_VALIDATION = "https://msk.t2.ru/api/validation/number/"
URL_AUTH = "https://msk.t2.ru/auth/realms/tele2-b2c/protocol/openid-connect/token"
URL_RESET_OPTION = (
    "https://msk.t2.ru/auth/realms/tele2-b2c/credential-management/reset-options?username="
)
URL_RESET_PASS = (
    "https://msk.t2.ru/auth/realms/tele2-b2c/credential-management/reset-password?username="
)


def _is_success(response: requests.Response) -> bool:
    """Return ``True`` if response status code is 200."""

    return response.status_code == 200


@dataclass
class Tele2Api:
    """Client for Tele2 personal account API."""

    phone_number: str
    access_token: str = ""
    refresh_token: str = ""
    session: requests.Session = field(default_factory=requests.Session, init=False)

    def __post_init__(self) -> None:
        base_api = f"{MAIN_API}{self.phone_number}"
        self.market_api = f"{base_api}/exchange/lots/created"
        self.bought_api = f"{base_api}/exchange/lots/bought"
        self.rests_api = f"{base_api}/rests"
        self.profile_api = f"{base_api}/profile"
        self.balance_api = f"{base_api}/balance"
        data: Dict[str, str] = {"sender": "t2.ru"}
        self.url_validation = URL_VALIDATION + self.phone_number
        self.url_auth = URL_AUTH
        self.url_reset_option = URL_RESET_OPTION + self.phone_number
        self.url_reset_pass = URL_RESET_PASS + self.phone_number
        self.session.headers.update({"Authorization": f"Bearer {self.access_token}", **HEADERS})

    # ------------------------------------------------------------------
    # Context manager helpers
    # ------------------------------------------------------------------
    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "Tele2Api":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # API methods
    # ------------------------------------------------------------------
    def get_sms_code(self, operation: Optional[str] = None) -> str:
        """Request a one-time SMS code for authorization."""

        data: Dict[str, str] = {"sender": "t2.ru"}
        if operation:
            data["operation"] = operation
        response = self.session.post(self.url_validation, json=data)
        if not _is_success(response):
            return response.json().get("detail", "error")
        return "OK"

    def reset_password(self) -> str:
        """Reset the permanent password via SMS."""

        response_option = self.session.get(self.url_reset_option)
        self.session.post(self.url_reset_pass, json={})
        if not _is_success(response_option):
            return str(response_option.status_code)
        return "OK"

    def authorization(self, sms_code: str, password_type: str = "sms_code") -> Union[str, Tuple[str, str]]:
        """Authorize using an SMS code or a permanent password."""

        payload = {
            "client_id": "digital-suite-web-app",
            "grant_type": "password",
            "username": self.phone_number,
            "password": sms_code,
            "password_type": password_type,
        }
        self.session.headers["Content-Type"] = "application/x-www-form-urlencoded"
        response = self.session.post(self.url_auth, data=payload, verify=False)
        if _is_success(response):
            data = response.json()
            self.access_token = data["access_token"]
            self.refresh_token = data["refresh_token"]
            self.session.headers["Authorization"] = f"Bearer {self.access_token}"
            return self.access_token, self.refresh_token
        return response.json().get("error_description", "error")

    def refresh_access_token(self, refresh_token: str) -> Union[str, Tuple[str, str]]:
        """Refresh authorization token using ``refresh_token``."""

        payload = {
            "client_id": "digital-suite-web-app",
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        response = self.session.post(self.url_auth, data=payload)
        if _is_success(response):
            data = response.json()
            self.access_token = data["access_token"]
            self.refresh_token = data["refresh_token"]
            self.session.headers["Authorization"] = f"Bearer {self.access_token}"
            return self.access_token, self.refresh_token
        return response.json().get("error_description", "error")

    def get_balance(self) -> Optional[int]:
        """Return current balance."""

        response = self.session.get(self.balance_api)
        if _is_success(response):
            return response.json()["data"]["value"]
        return None

    def get_rests(self) -> Dict[str, int]:
        """Return remaining data and voice minutes available for sale."""

        response = self.session.get(self.rests_api)
        data = response.json()
        rests = list(data["data"]["rests"])
        sellable = [r for r in rests if r["type"] == "tariff" and not r["rollover"]]
        return {
            "data": int(sum(r["remain"] for r in sellable if r["uom"] == "mb") / 1024),
            "voice": int(sum(r["remain"] for r in sellable if r["uom"] == "min")),
        }

    def create_lot(
        self,
        traffic_type: str,
        value: int,
        amount: int,
        emojis: Union[str, List[str], None] = None,
    ) -> str:
        """Create a new market lot."""

        payload = {
            "trafficType": traffic_type,
            "cost": {"amount": amount, "currency": "rub"},
            "volume": {"value": value, "uom": "min" if traffic_type == "voice" else "gb"},
        }
        response = self.session.put(self.market_api, json=payload)
        if not _is_success(response):
            return response.json()["meta"]["status"]
        lot_id = response.json()["data"]["id"]
        if emojis:
            if emojis == "random":
                all_emojis = ["cat", "scream", "bomb", "rich", "zipped", "tongue", "cool", "devil"]
                list_emojis = random.sample(all_emojis, k=3)
            elif isinstance(emojis, str):
                list_emojis = [emojis]
            else:
                list_emojis = list(emojis)
            self.session.patch(
                f"{self.market_api}/{lot_id}",
                json={"showSellerName": True, "emojis": list_emojis, "cost": {"amount": amount, "currency": "rub"}},
            )
        return lot_id

    def patch_lot(self, lot_id: str, amount: int) -> str:
        """Change lot price."""

        response = self.session.patch(
            f"{self.market_api}/{lot_id}",
            json={"cost": {"amount": amount, "currency": "rub"}},
        )
        if not _is_success(response):
            return response.json()["meta"]["status"]
        return "OK"

    def bought_lot(self, sms_code: str, lot: Dict[str, any]) -> str:
        """Purchase a lot."""

        response = self.session.put(
            f"{self.bought_api}?validationCode={sms_code}",
            json={
                "volume": {"value": lot["volume"]["value"], "uom": lot["volume"]["uom"]},
                "cost": {"amount": lot["cost"]["amount"], "currency": "rub"},
                "lotId": lot["id"],
                "hash": lot["hash"],
                "trafficType": lot["trafficType"],
            },
        )
        if not _is_success(response):
            return response.json()["meta"]["status"]
        return "OK"

    def delete_lot(self, lot_id: str) -> str:
        """Remove a lot from the market."""

        response = self.session.delete(f"{self.market_api}/{lot_id}")
        if not _is_success(response):
            return response.json()["meta"]["status"]
        return "OK"

    def get_active_lots(self) -> Optional[List[Dict[str, any]]]:
        """Return list of active lots."""

        response = self.session.get(self.market_api)
        if _is_success(response):
            data = response.json()
            lots = list(data["data"])
            return [lot for lot in lots if lot.get("status") == "active"]
        return None

    def mixx_update_subscribe(self, action: str = "enable") -> str:
        """Enable or disable mixx notifications."""

        json_data = {
            "operationType": "change_service",
            "changedServices": [
                {
                    "billingServiceId": "31299",
                    "action": action,
                }
            ],
        }
        self.session.post(f"{self.service_api}/notifications/check", json=json_data)
        if action == "enable":
            response = self.session.put(f"{self.service_api}/31299")
        else:
            response = self.session.delete(f"{self.service_api}/31299")
        if not _is_success(response):
            return response.json()
        return "OK"
