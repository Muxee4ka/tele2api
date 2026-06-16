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

import datetime
import random
import string
from typing import Dict, List, Optional, Tuple, Union

# curl_cffi вместо requests — для импер­сонации TLS мобильного приложения
# (см. docstring модуля).
from curl_cffi import requests

__all__ = ['Tele2Api']

# --- Сетевые константы -------------------------------------------------------

DEFAULT_HOST = 'api.t2.ru'          # личный кабинет и Маркет
AUTH_HOST = 'api.t2.ru'             # /auth (Keycloak) и /api/validation
AUTH_REALM = 'tele2-b2c'            # realm Keycloak (НЕ t2-b2c)
CLIENT_ID = 'digital-suite-web-app'
IMPERSONATE = 'chrome131_android'   # TLS-профиль curl_cffi, проходящий NGENIX

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


def _request_id(length: int = 40) -> str:
    """Случайный X-Request-Id — t2 ожидает его в каждом запросе."""
    alphabet = string.ascii_letters + string.digits
    return ''.join(random.choice(alphabet) for _ in range(length))


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
        :return: ``'OK'`` либо текст ошибки.
        """
        data = {'sender': 'Tele2'}
        if operation is not None:
            data['operation'] = operation
        response = self._post(self.url_validation, json=data)
        if not self._ok(response):
            return response.json().get('detail')
        return 'OK'

    def authorization(self, sms_code: str,
                      password_type: str = 'sms_code') -> Union[Tuple[str, str], str]:
        """Авторизоваться и сохранить токены.

        :param sms_code: одноразовый SMS-код либо постоянный пароль.
        :param password_type: ``'sms_code'`` или ``'password'``.
        :return: пара ``(access_token, refresh_token)`` либо текст ошибки.
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
            return response.json()['error_description']
        return self._store_tokens(response.json())

    def update_token(self, refresh_token: Optional[str] = None) -> Union[Tuple[str, str], str]:
        """Обновить access-токен по refresh-токену.

        :param refresh_token: refresh-токен; по умолчанию — сохранённый в клиенте.
        :return: пара ``(access_token, refresh_token)`` либо текст ошибки.
        """
        response = self._post(self.url_auth, data={
            'client_id': CLIENT_ID,
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token or self.refresh_token,
        }, headers={'Content-Type': 'application/x-www-form-urlencoded'})
        if not self._ok(response):
            return response.json().get('error_description', response.text)
        return self._store_tokens(response.json())

    def reset_password(self) -> str:
        """Запросить новый постоянный пароль.

        :return: ``'OK'`` либо ``False``.
        """
        response_option = self._get(self.url_reset_option)
        self._post(self.url_reset_pass, json={})
        if not self._ok(response_option):
            return self._ok(response_option)
        return 'OK'

    # --- Данные абонента -----------------------------------------------------

    def get_balance(self, subscriber: Optional[str] = None) -> Optional[float]:
        """Баланс в рублях (``None`` при ошибке)."""
        response = self._get(f'{self._sub(subscriber)}/balance')
        if self._ok(response):
            return response.json()['data']['value']
        return None

    def get_rests(self, subscriber: Optional[str] = None) -> Dict[str, int]:
        """Остатки, доступные для продажи на Маркете.

        :return: ``{'data': ГБ, 'voice': минуты, 'sms': штуки}``.
        """
        response = self._get(f'{self._sub(subscriber)}/rests',
                             params={'includePackageDescription': 'true'})
        rests = response.json()['data']['rests']
        sellable = [r for r in rests if r['type'] == 'tariff' and not r['rollover']]

        def total(uom: str) -> int:
            return int(sum(r['remain'] for r in sellable if r['uom'] == uom))

        return {
            'data': total('mb') // 1024,
            'voice': total('min'),
            'sms': total('pcs'),  # SMS приходят в единицах 'pcs'
        }

    def get_rests_detailed(self, subscriber: Optional[str] = None) -> Union[List[dict], str]:
        """Полная детализация остатков по пакетам (без схлопывания в суммы).

        В отличие от :meth:`get_rests`, возвращает каждый пакет как есть. Полезные
        поля каждого элемента: ``type`` (``'tariff'``/``'service'``/…),
        ``trafficType`` (``data``/``voice``/``sms``), ``remain`` и ``initial``
        (остаток и исходный объём в ``uom``), ``uom`` (``mb``/``min``/``pcs``),
        ``rollover`` (перенос остатка), а с ``includePackageDescription`` — текстовое
        описание пакета и срок действия.

        :return: список пакетов остатков либо код ошибки.
        """
        response = self._get(f'{self._sub(subscriber)}/rests',
                             params={'includePackageDescription': 'true'})
        if not self._ok(response):
            return self._status(response)
        return response.json()['data']['rests']

    def get_rests_rollover(self, subscriber: Optional[str] = None) -> Dict[str, int]:
        """Перенесённые с прошлых периодов остатки (rollover).

        :meth:`get_rests` их исключает (на Маркете продаётся только основной
        пакет тарифа, не перенос). Здесь — суммарный перенос по всем пакетам,
        у которых ``rollover=True`` (включая ``type='service'``).

        :return: ``{'data': ГБ, 'voice': минуты, 'sms': штуки}``.
        """
        response = self._get(f'{self._sub(subscriber)}/rests',
                             params={'includePackageDescription': 'true'})
        rolled = [r for r in response.json()['data']['rests'] if r.get('rollover')]

        def total(uom: str) -> int:
            return int(sum(r['remain'] for r in rolled if r['uom'] == uom))

        return {
            'data': total('mb') // 1024,
            'voice': total('min'),
            'sms': total('pcs'),  # SMS приходят в единицах 'pcs'
        }

    def get_profile(self, subscriber: Optional[str] = None) -> Optional[dict]:
        """Профиль абонента (``None`` при ошибке)."""
        response = self._get(f'{self._sub(subscriber)}/profile')
        if self._ok(response):
            return response.json()['data']
        return None

    def get_status(self, subscriber: Optional[str] = None) -> str:
        """Статус SIM-карты (``'ACTIVATED'`` / ``'SUSPENDED'`` и т.п.)."""
        response = self._get(f'{self._sub(subscriber)}/status')
        if not self._ok(response):
            return self._status(response)
        data = response.json()['data']
        # обычно строка-статус, но на части аккаунтов — объект {'status': ...}
        return data['status'] if isinstance(data, dict) else data

    def set_status(self, status: str, subscriber: Optional[str] = None) -> str:
        """Заблокировать/разблокировать SIM-карту.

        :param status: ``'SUSPENDED'`` (заблокировать) или ``'ACTIVATED'``.
        :param subscriber: номер абонента; по умолчанию — основной номер.
        :return: ``'OK'`` либо код ошибки.
        """
        response = self._put(f'{self._sub(subscriber)}/status', json=status)
        if not self._ok(response):
            return self._status(response)
        return 'OK'

    def get_tariff(self, subscriber: Optional[str] = None) -> Union[dict, str]:
        """Текущий тариф абонента.

        Возвращает словарь с данными тарифа: обычно название (``frontName`` /
        ``tariffName``), абонентская плата (``abonentFee``), идентификатор
        (``tariffId``/``id``), дата ближайшего списания и описание пакетов,
        входящих в тариф. Точный набор полей зависит от тарифа.

        :return: данные тарифа либо код ошибки.
        """
        response = self._get(f'{self._sub(subscriber)}/tariff')
        if not self._ok(response):
            return self._status(response)
        return response.json()['data']

    def get_charges(self, month: Optional[str] = None,
                    subscriber: Optional[str] = None) -> Union[list, str]:
        """Расходы за месяц (в т.ч. на поднятие лотов «Маркет t2: Поднятие лота»).

        :param month: ``'YYYY-MM'``; по умолчанию — текущий месяц.
        :param subscriber: номер абонента; по умолчанию — основной номер.
        :return: данные расходов либо код ошибки.
        """
        if month is None:
            month = datetime.date.today().strftime('%Y-%m')
        response = self._get(f'{self._sub(subscriber)}/siteKHABAROVSK/charges',
                             params={'month': month},
                             headers={'x-api-version': '2'})
        if not self._ok(response):
            return self._status(response)
        return response.json()['data']

    def get_slaves(self, subscriber: Optional[str] = None) -> Optional[list]:
        """Список привязанных номеров (``None`` при ошибке)."""
        response = self._get(f'{self._sub(subscriber)}/numbers/slaves')
        if self._ok(response):
            return response.json()
        return None

    # --- Маркет --------------------------------------------------------------

    def get_active_lots(self, subscriber: Optional[str] = None) -> Optional[List[dict]]:
        """Список активных лотов (``None`` при ошибке)."""
        response = self._get(f'{self._sub(subscriber)}/exchange/lots/created')
        if self._ok(response):
            return [lot for lot in response.json()['data'] if lot['status'] == 'active']
        return None

    def create_lot(self, traffic_type: str, value: int, amount: int,
                   emojis: Union[str, List[str]] = 'None',
                   subscriber: Optional[str] = None) -> str:
        """Создать новый лот.

        :param traffic_type: ``'voice'``, ``'data'`` или ``'sms'``.
        :param value: объём лота (минуты / ГБ / SMS).
        :param amount: цена лота в рублях.
        :param emojis: ``'None'`` — без эмодзи; ``'random'`` — три случайных;
            либо список значений из :data:`EMOJIS`.
        :param subscriber: номер абонента; по умолчанию — основной номер.
        :return: id созданного лота либо код ошибки.
        """
        market_url = f'{self._sub(subscriber)}/exchange/lots/created'
        response = self._put(market_url, json={
            'trafficType': traffic_type,
            'cost': {'amount': amount, 'currency': 'rub'},
            'volume': {'value': value, 'uom': TRAFFIC_UOM.get(traffic_type, 'gb')},
        })
        if not self._ok(response):
            return self._status(response)

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
                  subscriber: Optional[str] = None) -> str:
        """Изменить цену лота.

        :param id_lot: id лота.
        :param amount: новая цена в рублях.
        :param subscriber: номер абонента; по умолчанию — основной номер.
        :return: ``'OK'`` либо код ошибки.
        """
        response = self._patch(
            f'{self._sub(subscriber)}/exchange/lots/created/{id_lot}',
            json={'cost': {'amount': amount, 'currency': 'rub'}},
        )
        if not self._ok(response):
            return self._status(response)
        return 'OK'

    def premium_lot(self, id_lot: str, subscriber: Optional[str] = None) -> str:
        """Поднять лот в топ выдачи («ракета», стоит 5 руб.).

        :param id_lot: id лота.
        :param subscriber: номер абонента; по умолчанию — основной номер.
        :return: ``'OK'`` либо код ошибки.
        """
        response = self._put(f'{self._sub(subscriber)}/exchange/lots/premium',
                             json={'lotId': id_lot})
        if not self._ok(response):
            return self._status(response)
        return 'OK'

    def delete_lot(self, id_lot: str, subscriber: Optional[str] = None) -> str:
        """Снять лот с продажи.

        :param id_lot: id лота.
        :param subscriber: номер абонента; по умолчанию — основной номер.
        :return: ``'OK'`` либо код ошибки.
        """
        response = self._delete(
            f'{self._sub(subscriber)}/exchange/lots/created/{id_lot}')
        if not self._ok(response):
            return self._status(response)
        return 'OK'

    def get_lot_position(self, traffic_type: str, value: int, amount: int,
                         limit: int = 666,
                         subscriber: Optional[str] = None) -> Union[List[dict], str]:
        """Лоты с заданными параметрами в публичной выдаче Маркета (по порядку).

        :param traffic_type: ``'voice'``, ``'data'`` или ``'sms'``.
        :param value: объём лота.
        :param amount: цена лота.
        :param limit: глубина выборки.
        :param subscriber: номер абонента; по умолчанию — основной номер.
        :return: список лотов либо код ошибки.
        """
        response = self._get(f'{self._sub(subscriber)}/exchange/lots', params={
            'trafficType': traffic_type,
            'volume': value,
            'cost': amount,
            'offset': 0,
            'limit': limit,
        })
        if not self._ok(response):
            return self._status(response)
        return response.json()['data']

    def bought_lot(self, sms_code: str, lot: dict) -> str:
        """Купить лот на Маркете.

        :param sms_code: код подтверждения покупки.
        :param lot: словарь лота (из :meth:`get_lot_position`).
        :return: ``'OK'`` либо код ошибки.
        """
        response = self._put(f'{self.bought_api}?validationCode={sms_code}', json={
            'volume': {'value': lot['volume']['value'], 'uom': lot['volume']['uom']},
            'cost': {'amount': lot['cost']['amount'], 'currency': 'rub'},
            'lotId': lot['id'],
            'hash': lot['hash'],
            'trafficType': lot['trafficType'],
        })
        if not self._ok(response):
            return self._status(response)
        return 'OK'

    # --- Услуги --------------------------------------------------------------

    def get_services(self, status: Optional[str] = None) -> Union[List[dict], str]:
        """Услуги абонента: подключённые и доступные для подключения.

        Каждый элемент обычно содержит ``billingServiceId``/``id``, ``name``,
        ``abonentFee`` (стоимость), ``status``/``isActive`` (подключена ли услуга)
        и описание. ``billingServiceId`` подходит для подключения/отключения через
        :meth:`mixx_update_subscribe` и прочие операции с услугами.

        :param status: необязательный фильтр на стороне API (например
            ``'connected'`` — только подключённые); по умолчанию — все услуги.
        :return: список услуг либо код ошибки.
        """
        params = {'status': status} if status else None
        response = self._get(self.service_api, params=params)
        if not self._ok(response):
            return self._status(response)
        data = response.json()['data']
        # ответ может прийти как список услуг или как {'services': [...]}
        if isinstance(data, dict):
            return data.get('services', data)
        return data

    def mixx_update_subscribe(self, action: str = 'enable') -> Union[dict, str]:
        """Включить/выключить подписку MIXX.

        :param action: ``'enable'`` или ``'disable'``.
        :return: ``'OK'`` либо тело ответа с ошибкой.
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
            return response.json()
        return 'OK'
