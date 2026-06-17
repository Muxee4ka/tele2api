"""Клиент API маркета t2 (бывш. Tele2).

Позволяет из Python авторизоваться по SMS-коду или паролю и работать с личным
кабинетом и Маркетом: баланс, остатки, лоты (создание, поднятие «ракетой»,
удаление), статус SIM-карты, расходы и т.д.

Почему curl_cffi, а не requests
-------------------------------
В 2024 Tele2 ребрендировался в t2 (домен ``my.tele2.ru`` → ``my.t2.ru``), а API
переехал на ``api.t2.ru``. Инфраструктура закрыта анти-ботом NGENIX: эндпоинты
логина (``/auth/*``) и запроса SMS (``/api/validation/*``) пропускают запрос
только если он неотличим от запроса мобильного приложения. Для этого нужно
одновременно:

1. TLS-рукопожатие как у okhttp/Android — его воспроизводит ``curl_cffi`` с
   профилем :data:`IMPERSONATE`. Обычный ``requests``/``urllib`` (TLS OpenSSL)
   получает ``403 Forbidden``.
2. Заголовок ``Tele2-User-Agent`` — выставляется автоматически (см. :data:`HEADERS`).

Благодаря этому полный цикл «запрос SMS → логин → вызовы API» работает без
браузера, как делает официальное приложение. ``access_token`` живёт ~4 часа,
затем обновляется через :meth:`Tele2Api.update_token`.

Исключения
----------
Все методы поднимают исключение вместо возврата строки с кодом ошибки:

- :exc:`Tele2ApiError` — базовый класс; атрибут ``code`` содержит код ошибки API.
- :exc:`Tele2AuthError` — ошибки авторизации (неверный SMS-код, истёкший токен).
- :exc:`Tele2LotError` — ошибки операций с лотами Маркета.
- :exc:`Tele2ServiceError` — ошибки подключения / отключения услуг.

Пример
------
>>> api = Tele2Api('79991234567')
>>> api.get_sms_code()
'OK'
>>> api.authorization('123456')          # код из SMS
('eyJ...', 'eyJ...')
>>> api.get_balance()
251.4
"""

import base64
import datetime
import json
import random
import string
from typing import Dict, List, Optional, Tuple

from curl_cffi import requests

__all__ = [
    'Tele2Api',
    'Tele2ApiError', 'Tele2AuthError', 'Tele2LotError', 'Tele2ServiceError',
    'Service',
]

# --- Сетевые константы -------------------------------------------------------

DEFAULT_HOST = 'api.t2.ru'          # личный кабинет и Маркет
AUTH_HOST = 'api.t2.ru'             # /auth (Keycloak) и /api/validation
AUTH_REALM = 'tele2-b2c'            # realm Keycloak (НЕ t2-b2c)
CLIENT_ID = 'digital-suite-web-app'
# firefox133 проходит NGENIX; chrome131_android заблокирован (#10).
# Профиль следует пересматривать при появлении 403.
IMPERSONATE = 'firefox133'

HEADERS = {
    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7,de;q=0.6,fr;q=0.5',
    'Cache-Control': 'max-age=0',
    'Tele2-User-Agent': '"mytele2-app/4.14.0"; "unknown"; "Android/11"; "Build/164755374"',
    'X-API-Version': '1',
    'User-Agent': 'okhttp/4.2.0',
    'Accept-Encoding': 'gzip, deflate',
    'Accept': 'application/json, text/plain, */*',
    'Content-Type': 'application/json',
    'Connection': 'keep-alive',
}

# --- Доменные константы ------------------------------------------------------

# Тип трафика лота -> единица измерения объёма
TRAFFIC_UOM = {'voice': 'min', 'data': 'gb', 'sms': 'sms'}

# Доступные эмодзи продавца на Маркете
EMOJIS = ['cat', 'scream', 'bomb', 'rich', 'zipped', 'tongue', 'cool', 'devil']

# billingServiceId подписки MIXX
MIXX_SERVICE_ID = '31299'


# --- Исключения (#6) ---------------------------------------------------------

class Tele2ApiError(Exception):
    """Базовое исключение клиента.

    :param message: текстовое описание ошибки.
    :param code: код ошибки из поля ``meta.status`` ответа t2.
    """

    def __init__(self, message: str, code: str = ''):
        super().__init__(message)
        self.code = code or message


class Tele2AuthError(Tele2ApiError):
    """Ошибка авторизации: неверный SMS-код, истёкший refresh-токен и т.п."""


class Tele2LotError(Tele2ApiError):
    """Ошибка операции с лотом Маркета."""


class Tele2ServiceError(Tele2ApiError):
    """Ошибка подключения или отключения услуги."""


# --- Вспомогательные функции -------------------------------------------------

def _request_id(length: int = 40) -> str:
    """Случайный X-Request-Id — t2 ожидает его в каждом запросе."""
    alphabet = string.ascii_letters + string.digits
    return ''.join(random.choice(alphabet) for _ in range(length))


def _decode_jwt_payload(token: str) -> dict:
    """Декодировать payload JWT без проверки подписи (#7)."""
    parts = token.split('.')
    if len(parts) != 3:
        return {}
    padded = parts[1] + '=' * (4 - len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return {}


def _parse_money(value) -> Optional[float]:
    """Нормализовать денежное значение t2 к ``float`` (#9).

    t2 возвращает деньги как ``{"amount": N, "currency": "rub"}`` или
    голым числом. Функция приводит оба варианта к ``float``.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return float(value.get('amount', 0))
    return float(value)


# --- Модель услуги (#9) ------------------------------------------------------

class Service:
    """Типизированная обёртка над записью об услуге t2.

    Ключевая особенность: статус подключения нужно проверять через
    ``status == 'CONNECTED'``, а не ``showDisconnectButton`` — последний t2
    выставляет примерно у 100 доступных, но не подключённых услуг.
    """

    def __init__(self, data: dict):
        self._data = data

    @property
    def billing_id(self) -> str:
        return self._data.get('billingServiceId') or self._data.get('id', '')

    @property
    def name(self) -> str:
        return self._data.get('name', '')

    @property
    def fee(self) -> Optional[float]:
        return _parse_money(self._data.get('abonentFee'))

    @property
    def is_connected(self) -> bool:
        return self._data.get('status') == 'CONNECTED'

    @property
    def can_disconnect(self) -> bool:
        # Снимать услугу можно только если t2 явно это разрешает; поле
        # showDisconnectButton для этого НЕ годится (t2 выставляет его и у ~100
        # доступных, но не подключённых услуг) — см. #9.
        status = self._data.get('disconnectionAvailabilityStatus') or {}
        return status.get('canDisconnect') is True

    def __repr__(self) -> str:
        return (
            f'Service(id={self.billing_id!r}, name={self.name!r}, '
            f'connected={self.is_connected})'
        )


# --- Основной клиент ---------------------------------------------------------

class Tele2Api:
    """Клиент API одного абонента t2.

    :param phone_number: номер в формате ``79991234567``.
    :param access_token: сохранённый access-токен (если уже авторизован).
    :param refresh_token: сохранённый refresh-токен.
    :param host: хост личного кабинета и Маркета.
    :param auth_host: хост авторизации (Keycloak) и запроса SMS.
    :param impersonate: TLS-профиль curl_cffi для прохождения NGENIX.
    """

    def __init__(self, phone_number: str, access_token: str = '', refresh_token: str = '',
                 host: str = DEFAULT_HOST, auth_host: str = AUTH_HOST,
                 impersonate: str = IMPERSONATE):
        self._phone_number = phone_number
        self._host = host
        self._impersonate = impersonate
        self.access_token = access_token
        self.refresh_token = refresh_token

        base_url = f'https://{host}'
        auth_url = f'https://{auth_host}'
        base_api = f'{base_url}/api/subscribers/{phone_number}'

        # Личный кабинет и данные абонента
        self.balance_api = f'{base_api}/balance'
        self.rests_api = f'{base_api}/rests'
        self.profile_api = f'{base_api}/profile'
        self.status_api = f'{base_api}/status'
        self.tariff_api = f'{base_api}/tariff'
        self.charges_api = f'{base_api}/siteKHABAROVSK/charges'
        self.slaves_api = f'{base_api}/numbers/slaves'
        self.service_api = f'{base_api}/services'

        # Маркет
        self.market_api = f'{base_api}/exchange/lots/created'
        self.bought_api = f'{base_api}/exchange/lots/bought'
        self.premium_api = f'{base_api}/exchange/lots/premium'
        self.public_market_api = f'{base_api}/exchange/lots'

        # Авторизация (за NGENIX — проходят только с импер­сонацией + Tele2-User-Agent)
        self.url_validation = f'{auth_url}/api/validation/number/{phone_number}'
        self.url_auth = f'{auth_url}/auth/realms/{AUTH_REALM}/protocol/openid-connect/token'
        self.url_reset_option = (f'{auth_url}/auth/realms/{AUTH_REALM}'
                                 f'/credential-management/reset-options?username={phone_number}')
        self.url_reset_pass = (f'{auth_url}/auth/realms/{AUTH_REALM}'
                               f'/credential-management/reset-password?username={phone_number}')

        self.session = requests.Session(impersonate=impersonate)
        self.session.headers.update({'Authorization': f'Bearer {access_token}', **HEADERS})

    def __repr__(self) -> str:
        return f'Tele2Api(phone_number={self._phone_number!r})'

    # --- Низкоуровневые помощники --------------------------------------------

    def _request(self, method: str, url: str, **kwargs):
        """Запрос к API с автоматическим X-Request-Id."""
        headers = {'X-Request-Id': _request_id()}
        headers.update(kwargs.pop('headers', None) or {})
        return self.session.request(method, url, headers=headers, **kwargs)

    def _get(self, url: str, **kwargs):
        return self._request('GET', url, **kwargs)

    def _post(self, url: str, **kwargs):
        return self._request('POST', url, **kwargs)

    def _put(self, url: str, **kwargs):
        return self._request('PUT', url, **kwargs)

    def _patch(self, url: str, **kwargs):
        return self._request('PATCH', url, **kwargs)

    def _delete(self, url: str, **kwargs):
        return self._request('DELETE', url, **kwargs)

    @staticmethod
    def _ok(response) -> bool:
        return response.status_code == 200

    @staticmethod
    def _status(response) -> str:
        """Код ошибки из ``meta`` тела ответа.

        Если тело не JSON (например, страница NGENIX или ``SSO_NOT_CONFIGURED``),
        возвращает текст ответа либо HTTP-код.
        """
        try:
            return response.json()['meta']['status']
        except (ValueError, KeyError, TypeError):
            return response.text.strip() or f'HTTP {response.status_code}'

    def _raise(self, response, exc_class: type = Tele2ApiError) -> None:
        code = self._status(response)
        raise exc_class(code, code)

    def _store_tokens(self, payload: dict) -> Tuple[str, str]:
        """Сохранить токены из ответа Keycloak и обновить заголовок сессии."""
        self.access_token = payload['access_token']
        self.refresh_token = payload['refresh_token']
        self.session.headers['Authorization'] = f'Bearer {self.access_token}'
        return self.access_token, self.refresh_token

    def _sub(self, subscriber: Optional[str] = None) -> str:
        """Base subscriber URL for ``subscriber`` (defaults to the master number)."""
        return f'https://{self._host}/api/subscribers/{subscriber or self._phone_number}'

    # --- Авторизация ---------------------------------------------------------

    def get_sms_code(self, operation: Optional[str] = None) -> str:
        """Запросить одноразовый SMS-код для авторизации.

        :param operation: необязательный тип операции.
        :return: ``'OK'``.
        :raises Tele2ApiError: при ошибке отправки SMS.
        """
        data = {'sender': 'Tele2'}
        if operation is not None:
            data['operation'] = operation
        response = self._post(self.url_validation, json=data)
        if not self._ok(response):
            detail = response.json().get('detail', self._status(response))
            raise Tele2ApiError(detail, detail)
        return 'OK'

    def authorization(self, sms_code: str,
                      password_type: str = 'sms_code') -> Tuple[str, str]:
        """Авторизоваться и сохранить токены.

        :param sms_code: одноразовый SMS-код либо постоянный пароль.
        :param password_type: ``'sms_code'`` или ``'password'``.
        :return: пара ``(access_token, refresh_token)``.
        :raises Tele2AuthError: при неверном коде или другой ошибке авторизации.
        """
        data = {
            'client_id': CLIENT_ID,
            'grant_type': 'password',
            'username': self._phone_number,
            'password': sms_code,
            'password_type': password_type,
        }
        response = self._post(self.url_auth, data=data,
                              headers={'Content-Type': 'application/x-www-form-urlencoded'})
        if not self._ok(response):
            msg = response.json().get('error_description', self._status(response))
            raise Tele2AuthError(msg, msg)
        return self._store_tokens(response.json())

    def update_token(self, refresh_token: Optional[str] = None) -> Tuple[str, str]:
        """Обновить access-токен по refresh-токену.

        :param refresh_token: refresh-токен; по умолчанию — сохранённый в клиенте.
        :return: пара ``(access_token, refresh_token)``.
        :raises Tele2AuthError: если refresh-токен истёк или недействителен.
        """
        response = self._post(self.url_auth, data={
            'client_id': CLIENT_ID,
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token or self.refresh_token,
        }, headers={'Content-Type': 'application/x-www-form-urlencoded'})
        if not self._ok(response):
            msg = response.json().get('error_description', response.text)
            raise Tele2AuthError(msg, msg)
        return self._store_tokens(response.json())

    def reset_password(self) -> None:
        """Запросить новый постоянный пароль.

        :raises Tele2ApiError: при ошибке.
        """
        response_option = self._get(self.url_reset_option)
        if not self._ok(response_option):
            self._raise(response_option)
        response = self._post(self.url_reset_pass, json={})
        if not self._ok(response):
            self._raise(response)

    # --- Токен: экспирация (#7) ----------------------------------------------

    @property
    def token_expires_at(self) -> Optional[datetime.datetime]:
        """UTC-момент истечения access-токена или ``None``, если токен отсутствует."""
        if not self.access_token:
            return None
        exp = _decode_jwt_payload(self.access_token).get('exp')
        if exp is None:
            return None
        return datetime.datetime.fromtimestamp(exp, tz=datetime.timezone.utc)

    def is_token_expired(self, margin: int = 300) -> bool:
        """Вернуть ``True``, если токен истёк или истечёт в ближайшие ``margin`` секунд."""
        exp = self.token_expires_at
        if exp is None:
            return True
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        return now >= exp - datetime.timedelta(seconds=margin)

    def ensure_token(self, margin: int = 300) -> None:
        """Обновить access-токен, если до его истечения осталось менее ``margin`` секунд.

        :raises Tele2AuthError: если обновление не удалось.
        """
        if self.is_token_expired(margin):
            self.update_token()

    # --- Данные абонента -----------------------------------------------------

    def get_balance(self, subscriber: Optional[str] = None) -> float:
        """Баланс в рублях.

        :raises Tele2ApiError: при ошибке.
        """
        response = self._get(f'{self._sub(subscriber)}/balance')
        if not self._ok(response):
            self._raise(response)
        return response.json()['data']['value']

    def get_rests(self, subscriber: Optional[str] = None) -> Dict[str, int]:
        """Остатки, доступные для продажи на Маркете.

        :return: ``{'data': ГБ, 'voice': минуты, 'sms': штуки}``.
        :raises Tele2ApiError: при ошибке.
        """
        response = self._get(f'{self._sub(subscriber)}/rests',
                             params={'includePackageDescription': 'true'})
        if not self._ok(response):
            self._raise(response)
        rests = response.json()['data']['rests']
        sellable = [r for r in rests if r['type'] == 'tariff' and not r['rollover']]

        def total(uom: str) -> int:
            return int(sum(r['remain'] for r in sellable if r['uom'] == uom))

        return {
            'data': total('mb') // 1024,
            'voice': total('min'),
            'sms': total('pcs'),  # SMS приходят в единицах 'pcs'
        }

    def get_rests_detailed(self, subscriber: Optional[str] = None) -> List[dict]:
        """Полная детализация остатков по пакетам (без схлопывания в суммы).

        В отличие от :meth:`get_rests`, возвращает каждый пакет как есть. Полезные
        поля каждого элемента: ``type`` (``'tariff'``/``'service'``/…),
        ``trafficType`` (``data``/``voice``/``sms``), ``remain`` и ``initial``
        (остаток и исходный объём в ``uom``), ``uom`` (``mb``/``min``/``pcs``),
        ``rollover`` (перенос остатка), а с ``includePackageDescription`` — текстовое
        описание пакета и срок действия.

        :raises Tele2ApiError: при ошибке.
        """
        response = self._get(f'{self._sub(subscriber)}/rests',
                             params={'includePackageDescription': 'true'})
        if not self._ok(response):
            self._raise(response)
        return response.json()['data']['rests']

    def get_rests_rollover(self, subscriber: Optional[str] = None) -> Dict[str, int]:
        """Перенесённые с прошлых периодов остатки (rollover).

        :meth:`get_rests` их исключает (на Маркете продаётся только основной
        пакет тарифа, не перенос). Здесь — суммарный перенос по всем пакетам,
        у которых ``rollover=True`` (включая ``type='service'``).

        :return: ``{'data': ГБ, 'voice': минуты, 'sms': штуки}``.
        :raises Tele2ApiError: при ошибке.
        """
        response = self._get(f'{self._sub(subscriber)}/rests',
                             params={'includePackageDescription': 'true'})
        if not self._ok(response):
            self._raise(response)
        rolled = [r for r in response.json()['data']['rests'] if r.get('rollover')]

        def total(uom: str) -> int:
            return int(sum(r['remain'] for r in rolled if r['uom'] == uom))

        return {
            'data': total('mb') // 1024,
            'voice': total('min'),
            'sms': total('pcs'),  # SMS приходят в единицах 'pcs'
        }

    def get_profile(self, subscriber: Optional[str] = None) -> dict:
        """Профиль абонента.

        :raises Tele2ApiError: при ошибке.
        """
        response = self._get(f'{self._sub(subscriber)}/profile')
        if not self._ok(response):
            self._raise(response)
        return response.json()['data']

    def get_status(self, subscriber: Optional[str] = None) -> str:
        """Статус SIM-карты (``'ACTIVATED'`` / ``'SUSPENDED'`` и т.п.).

        :raises Tele2ApiError: при ошибке.
        """
        response = self._get(f'{self._sub(subscriber)}/status')
        if not self._ok(response):
            self._raise(response)
        data = response.json()['data']
        # обычно строка-статус, но на части аккаунтов — объект {'status': ...}
        return data['status'] if isinstance(data, dict) else data

    def set_status(self, status: str, subscriber: Optional[str] = None) -> None:
        """Заблокировать/разблокировать SIM-карту.

        :param status: ``'SUSPENDED'`` (заблокировать) или ``'ACTIVATED'``.
        :param subscriber: номер абонента; по умолчанию — основной номер.
        :raises Tele2ApiError: при ошибке.
        """
        response = self._put(f'{self._sub(subscriber)}/status', json=status)
        if not self._ok(response):
            self._raise(response)

    def get_tariff(self, subscriber: Optional[str] = None) -> dict:
        """Текущий тариф абонента.

        Возвращает словарь с данными тарифа: обычно название (``frontName`` /
        ``tariffName``), абонентская плата (``abonentFee``), идентификатор
        (``tariffId``/``id``), дата ближайшего списания и описание пакетов,
        входящих в тариф. Точный набор полей зависит от тарифа.

        :raises Tele2ApiError: при ошибке.
        """
        response = self._get(f'{self._sub(subscriber)}/tariff')
        if not self._ok(response):
            self._raise(response)
        return response.json()['data']

    def get_charges(self, month: Optional[str] = None,
                    subscriber: Optional[str] = None) -> list:
        """Расходы за месяц (в т.ч. на поднятие лотов «Маркет t2: Поднятие лота»).

        :param month: ``'YYYY-MM'``; по умолчанию — текущий месяц.
        :param subscriber: номер абонента; по умолчанию — основной номер.
        :raises Tele2ApiError: при ошибке.
        """
        if month is None:
            month = datetime.date.today().strftime('%Y-%m')
        response = self._get(f'{self._sub(subscriber)}/siteKHABAROVSK/charges',
                             params={'month': month},
                             headers={'x-api-version': '2'})
        if not self._ok(response):
            self._raise(response)
        return response.json()['data']

    def get_slaves(self, subscriber: Optional[str] = None) -> list:
        """Список привязанных номеров.

        :raises Tele2ApiError: при ошибке.
        """
        response = self._get(f'{self._sub(subscriber)}/numbers/slaves')
        if not self._ok(response):
            self._raise(response)
        return response.json()

    # --- Маркет --------------------------------------------------------------

    def get_active_lots(self, subscriber: Optional[str] = None) -> List[dict]:
        """Список активных лотов.

        :raises Tele2ApiError: при ошибке.
        """
        response = self._get(f'{self._sub(subscriber)}/exchange/lots/created')
        if not self._ok(response):
            self._raise(response)
        return [lot for lot in response.json()['data'] if lot['status'] == 'active']

    def create_lot(self, traffic_type: str, value: int, amount: int,
                   emojis: object = 'None',
                   subscriber: Optional[str] = None) -> str:
        """Создать новый лот.

        :param traffic_type: ``'voice'``, ``'data'`` или ``'sms'``.
        :param value: объём лота (минуты / ГБ / SMS).
        :param amount: цена лота в рублях.
        :param emojis: ``'None'`` — без эмодзи; ``'random'`` — три случайных;
            либо список значений из :data:`EMOJIS`.
        :param subscriber: номер абонента; по умолчанию — основной номер.
        :return: id созданного лота.
        :raises Tele2LotError: при ошибке.
        """
        market_url = f'{self._sub(subscriber)}/exchange/lots/created'
        response = self._put(market_url, json={
            'trafficType': traffic_type,
            'cost': {'amount': amount, 'currency': 'rub'},
            'volume': {'value': value, 'uom': TRAFFIC_UOM.get(traffic_type, 'gb')},
        })
        if not self._ok(response):
            self._raise(response, Tele2LotError)

        id_lot = response.json()['data']['id']
        if emojis != 'None':
            selected = random.choices(EMOJIS, k=3) if emojis == 'random' else emojis
            self._patch(f'{market_url}/{id_lot}', json={
                'showSellerName': True,
                'emojis': selected,
                'cost': {'amount': amount, 'currency': 'rub'},
            })
        return id_lot

    def patch_lot(self, id_lot: str, amount: int,
                  subscriber: Optional[str] = None) -> None:
        """Изменить цену лота.

        :param id_lot: id лота.
        :param amount: новая цена в рублях.
        :param subscriber: номер абонента; по умолчанию — основной номер.
        :raises Tele2LotError: при ошибке.
        """
        response = self._patch(
            f'{self._sub(subscriber)}/exchange/lots/created/{id_lot}',
            json={'cost': {'amount': amount, 'currency': 'rub'}},
        )
        if not self._ok(response):
            self._raise(response, Tele2LotError)

    def premium_lot(self, id_lot: str, subscriber: Optional[str] = None) -> None:
        """Поднять лот в топ выдачи («ракета», стоит 5 руб.).

        :param id_lot: id лота.
        :param subscriber: номер абонента; по умолчанию — основной номер.
        :raises Tele2LotError: при ошибке.
        """
        response = self._put(f'{self._sub(subscriber)}/exchange/lots/premium',
                             json={'lotId': id_lot})
        if not self._ok(response):
            self._raise(response, Tele2LotError)

    def delete_lot(self, id_lot: str, subscriber: Optional[str] = None) -> None:
        """Снять лот с продажи.

        :param id_lot: id лота.
        :param subscriber: номер абонента; по умолчанию — основной номер.
        :raises Tele2LotError: при ошибке.
        """
        response = self._delete(
            f'{self._sub(subscriber)}/exchange/lots/created/{id_lot}')
        if not self._ok(response):
            self._raise(response, Tele2LotError)

    def get_lot_position(self, traffic_type: str, value: int, amount: int,
                         limit: int = 666,
                         subscriber: Optional[str] = None) -> List[dict]:
        """Лоты с заданными параметрами в публичной выдаче Маркета (по порядку).

        :param traffic_type: ``'voice'``, ``'data'`` или ``'sms'``.
        :param value: объём лота.
        :param amount: цена лота.
        :param limit: глубина выборки.
        :param subscriber: номер абонента; по умолчанию — основной номер.
        :raises Tele2ApiError: при ошибке.
        """
        response = self._get(f'{self._sub(subscriber)}/exchange/lots', params={
            'trafficType': traffic_type,
            'volume': value,
            'cost': amount,
            'offset': 0,
            'limit': limit,
        })
        if not self._ok(response):
            self._raise(response)
        return response.json()['data']

    def bought_lot(self, sms_code: str, lot: dict) -> None:
        """Купить лот на Маркете.

        :param sms_code: код подтверждения покупки.
        :param lot: словарь лота (из :meth:`get_lot_position`).
        :raises Tele2LotError: при ошибке.
        """
        response = self._put(f'{self.bought_api}?validationCode={sms_code}', json={
            'volume': {'value': lot['volume']['value'], 'uom': lot['volume']['uom']},
            'cost': {'amount': lot['cost']['amount'], 'currency': 'rub'},
            'lotId': lot['id'],
            'hash': lot['hash'],
            'trafficType': lot['trafficType'],
        })
        if not self._ok(response):
            self._raise(response, Tele2LotError)

    # --- Маркет: помощники (#8) ----------------------------------------------

    def get_my_lot_position(self, lot: dict,
                            subscriber: Optional[str] = None) -> int:
        """Позиция лота в публичной выдаче (1-based, 0 — не найден).

        :param lot: словарь лота с ключами ``trafficType``, ``volume``, ``cost``, ``id``.
        :raises Tele2ApiError: при ошибке запроса.
        """
        listings = self.get_lot_position(
            lot['trafficType'], lot['volume']['value'], lot['cost']['amount'],
            subscriber=subscriber,
        )
        for i, item in enumerate(listings, 1):
            if item['id'] == lot['id']:
                return i
        return 0

    def undercut_lot(self, lot: dict, step: int = 1, min_amount: int = 1,
                     subscriber: Optional[str] = None) -> dict:
        """Опустить цену лота ниже ближайшего конкурента.

        Ищет лоты с такими же объёмом и ценой в публичной выдаче, исключает
        собственные лоты (по полю ``my`` или совпадению ``id``), выбирает
        минимальную цену среди конкурентов и выставляет ``min(конкуренты) - step``,
        не ниже ``min_amount`` и не выше текущей цены.

        :return: ``{'changed', 'old_price', 'new_price', 'reason'}``.
        :raises Tele2ApiError: при ошибке запроса позиции.
        :raises Tele2LotError: при ошибке обновления лота.
        """
        listings = self.get_lot_position(
            lot['trafficType'], lot['volume']['value'], lot['cost']['amount'],
            subscriber=subscriber,
        )
        old_price = lot['cost']['amount']
        competitors = [
            item['cost']['amount'] for item in listings
            if not item.get('my') and item['id'] != lot['id']
        ]
        if not competitors:
            return {'changed': False, 'old_price': old_price,
                    'new_price': old_price, 'reason': 'no_competitors'}
        new_price = max(min(competitors) - step, min_amount)
        if new_price >= old_price:
            return {'changed': False, 'old_price': old_price,
                    'new_price': old_price, 'reason': 'already_cheapest'}
        self.patch_lot(lot['id'], new_price, subscriber)
        return {'changed': True, 'old_price': old_price,
                'new_price': new_price, 'reason': 'undercut'}

    def undercut_all_lots(self, step: int = 1, min_amount: int = 1,
                          traffic_type: Optional[str] = None,
                          subscriber: Optional[str] = None) -> List[dict]:
        """Применить :meth:`undercut_lot` ко всем активным лотам.

        :param traffic_type: если задан, обрабатываются только лоты этого типа.
        :return: список результатов ``undercut_lot`` с добавленным полем ``lot_id``.
        """
        lots = self.get_active_lots(subscriber)
        results = []
        for lot in lots:
            if traffic_type and lot['trafficType'] != traffic_type:
                continue
            try:
                result = self.undercut_lot(lot, step, min_amount, subscriber)
                result['lot_id'] = lot['id']
                results.append(result)
            except Tele2ApiError as e:
                results.append({
                    'lot_id': lot['id'], 'changed': False,
                    'reason': str(e), 'error': True,
                })
        return results

    def premium_all_lots(self, traffic_type: Optional[str] = None,
                         subscriber: Optional[str] = None) -> dict:
        """Поднять все активные лоты в топ.

        :param traffic_type: если задан, поднимаются только лоты этого типа.
        :return: ``{'ok': [id, ...], 'failed': [id, ...], 'errors': {id: reason}}``.
        """
        lots = self.get_active_lots(subscriber)
        ok: List[str] = []
        failed: List[str] = []
        errors: dict = {}
        for lot in lots:
            if traffic_type and lot['trafficType'] != traffic_type:
                continue
            try:
                self.premium_lot(lot['id'], subscriber)
                ok.append(lot['id'])
            except Tele2LotError as e:
                failed.append(lot['id'])
                errors[lot['id']] = str(e)
        return {'ok': ok, 'failed': failed, 'errors': errors}

    def delete_all_lots(self, traffic_type: Optional[str] = None,
                        subscriber: Optional[str] = None) -> dict:
        """Удалить все активные лоты.

        :param traffic_type: если задан, удаляются только лоты этого типа.
        :return: ``{'ok': [id, ...], 'failed': [id, ...], 'errors': {id: reason}}``.
        """
        lots = self.get_active_lots(subscriber)
        ok: List[str] = []
        failed: List[str] = []
        errors: dict = {}
        for lot in lots:
            if traffic_type and lot['trafficType'] != traffic_type:
                continue
            try:
                self.delete_lot(lot['id'], subscriber)
                ok.append(lot['id'])
            except Tele2LotError as e:
                failed.append(lot['id'])
                errors[lot['id']] = str(e)
        return {'ok': ok, 'failed': failed, 'errors': errors}

    def create_lots(self, traffic_type: str, volumes: List[int], amount: int,
                    emojis: object = 'None',
                    subscriber: Optional[str] = None) -> dict:
        """Создать несколько лотов одного типа.

        :param volumes: список объёмов (минуты / ГБ / SMS).
        :return: ``{'ok': [lot_id, ...], 'failed': [volume, ...], 'errors': {volume: reason}}``.
        """
        ok: List[str] = []
        failed: List[int] = []
        errors: dict = {}
        for volume in volumes:
            try:
                lot_id = self.create_lot(traffic_type, volume, amount, emojis, subscriber)
                ok.append(lot_id)
            except Tele2LotError as e:
                failed.append(volume)
                errors[volume] = str(e)
        return {'ok': ok, 'failed': failed, 'errors': errors}

    # --- Услуги --------------------------------------------------------------

    def get_services(self, status: Optional[str] = None,
                     subscriber: Optional[str] = None) -> List[dict]:
        """Услуги абонента: подключённые и доступные для подключения.

        Каждый элемент обычно содержит ``billingServiceId``/``id``, ``name``,
        ``abonentFee`` (стоимость), ``status``/``isActive`` (подключена ли услуга)
        и описание. ``billingServiceId`` подходит для подключения/отключения через
        :meth:`connect_service` и прочие операции с услугами.

        Для проверки статуса используйте :meth:`get_services_catalogue` и
        ``Service.is_connected``, а не ``showDisconnectButton``.

        :param status: необязательный фильтр на стороне API (например
            ``'connected'`` — только подключённые); по умолчанию — все услуги.
        :raises Tele2ApiError: при ошибке.
        """
        params = {'status': status} if status else None
        response = self._get(f'{self._sub(subscriber)}/services', params=params)
        if not self._ok(response):
            self._raise(response)
        data = response.json()['data']
        # ответ может прийти как список услуг или как {'services': [...]}
        if isinstance(data, dict):
            return data.get('services', data)
        return data

    def get_services_catalogue(self, status: Optional[str] = None,
                               subscriber: Optional[str] = None) -> List[Service]:
        """Услуги абонента в виде типизированных объектов :class:`Service` (#9).

        Удобнее :meth:`get_services`: ``Service.is_connected`` проверяет
        ``status == 'CONNECTED'`` (а не ненадёжный ``showDisconnectButton``), а
        ``Service.can_disconnect`` — ``disconnectionAvailabilityStatus.canDisconnect``.

        :raises Tele2ApiError: при ошибке.
        """
        return [Service(s) for s in self.get_services(status, subscriber)]

    def connect_service(self, billing_id: str,
                        subscriber: Optional[str] = None) -> None:
        """Подключить услугу по её ``billingServiceId``.

        :param billing_id: ``billingServiceId`` услуги (из :meth:`get_services`).
        :param subscriber: номер абонента; по умолчанию — основной номер.
        :raises Tele2ServiceError: при ошибке.
        """
        svc = f'{self._sub(subscriber)}/services'
        self._post(f'{svc}/notifications/check', json={
            'operationType': 'change_service',
            'changedServices': [{'billingServiceId': billing_id, 'action': 'enable'}],
        })
        response = self._put(f'{svc}/{billing_id}')
        if not self._ok(response):
            self._raise(response, Tele2ServiceError)

    def disconnect_service(self, billing_id: str,
                           subscriber: Optional[str] = None) -> None:
        """Отключить услугу по её ``billingServiceId``.

        :param billing_id: ``billingServiceId`` услуги (из :meth:`get_services`).
        :param subscriber: номер абонента; по умолчанию — основной номер.
        :raises Tele2ServiceError: при ошибке.
        """
        svc = f'{self._sub(subscriber)}/services'
        self._post(f'{svc}/notifications/check', json={
            'operationType': 'change_service',
            'changedServices': [{'billingServiceId': billing_id, 'action': 'disable'}],
        })
        response = self._delete(f'{svc}/{billing_id}')
        if not self._ok(response):
            self._raise(response, Tele2ServiceError)

    def mixx_update_subscribe(self, action: str = 'enable') -> None:
        """Включить/выключить подписку MIXX.

        :param action: ``'enable'`` или ``'disable'``.
        :raises Tele2ServiceError: при ошибке.
        :raises ValueError: если ``action`` не ``'enable'`` и не ``'disable'``.
        """
        self._post(f'{self.service_api}/notifications/check', json={
            'operationType': 'change_service',
            'changedServices': [{'billingServiceId': MIXX_SERVICE_ID, 'action': action}],
        })
        if action == 'enable':
            response = self._put(f'{self.service_api}/{MIXX_SERVICE_ID}')
        elif action == 'disable':
            response = self._delete(f'{self.service_api}/{MIXX_SERVICE_ID}')
        else:
            raise ValueError("action должен быть 'enable' или 'disable'")
        if not self._ok(response):
            self._raise(response, Tele2ServiceError)

    # --- Нормализация данных (#9) --------------------------------------------

    def get_charges_flat(self, month: Optional[str] = None,
                         subscriber: Optional[str] = None) -> List[dict]:
        """Расходы за месяц в виде плоского списка строк ``{name, amount, category}``.

        Разворачивает вложенную структуру ``subGroups → consumingServices``
        из :meth:`get_charges` в простой список. ``category`` совпадает с полем
        ``type`` группы (``'SUBSCRIPTION_FEE'``, ``'SMS_MMS'``, ``'CONTENT'`` и т.д.).

        :raises Tele2ApiError: при ошибке запроса.
        """
        raw = self.get_charges(month, subscriber)
        items = []
        for group in raw:
            category = group.get('type', '')
            for sub in group.get('subGroups', []):
                for svc in sub.get('consumingServices', []):
                    items.append({
                        'name': svc.get('name', ''),
                        'amount': _parse_money(svc.get('cost')),
                        'category': category,
                    })
        return items

    def get_actual_fee(self, subscriber: Optional[str] = None) -> Optional[float]:
        """Фактическая абонентская плата за текущий месяц.

        Тариф хранит прайс-листовую цену; реальная (со скидками) берётся из
        расходов текущего месяца по категории ``SUBSCRIPTION_FEE``. При
        недоступности расходов возвращает ``abonentFee`` из тарифа.

        :return: сумма в рублях или ``None``.
        """
        try:
            for item in self.get_charges_flat(subscriber=subscriber):
                if item['category'] == 'SUBSCRIPTION_FEE':
                    return item['amount']
        except Tele2ApiError:
            pass
        try:
            tariff = self.get_tariff(subscriber)
            return _parse_money(tariff.get('abonentFee'))
        except Tele2ApiError:
            return None
