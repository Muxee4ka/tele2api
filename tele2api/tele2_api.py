import json
import random

import requests

from tele2_config import HEADERS, URL_AUTH, URL_VALIDATION, URL_RESET_PASS, URL_RESET_OPTION, MAIN_API


def _get_status_code(response):
    return response.status_code == 200


class Tele2Api:
    def __init__(self, phone_number: str, access_token: str = '', refresh_token: str = ''):
        self._phone_number = phone_number
        base_api = MAIN_API + phone_number
        self.market_api = f'{base_api}/exchange/lots/created'
        self.bought_api = f'{base_api}/exchange/lots/bought'
        self.rests_api = f'{base_api}/rests'
        self.profile_api = f'{base_api}/profile'
        self.balance_api = f'{base_api}/balance'
        self.service_api = f'{base_api}/services'
        self.url_validation = URL_VALIDATION + self._phone_number
        self.url_auth = URL_AUTH
        self.url_reset_option = URL_RESET_OPTION + self._phone_number
        self.url_reset_pass = URL_RESET_PASS + self._phone_number
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.session = requests.Session()
        self.session.headers = {
            'Authorization': f'Bearer {self.access_token}',
            **HEADERS
        }

    def get_sms_code(self, operation=None):
        """
        Получение одноразового sms-кода для авторизации
        :return:
        """
        data = {"sender": "Tele2"}
        if operation is not None:
            data['operation'] = operation
        data = json.dumps(data)
        response = self.session.post(self.url_validation, data=data)
        if not _get_status_code(response):
            return response.json().get('detail')
        return 'OK'

    def reset_password(self):
        """
        Получение нового постоянного пароля
        :return:
        """
        data = json.dumps({})
        response_option = self.session.get(self.url_reset_option)
        response_pass = self.session.post(self.url_reset_pass, data=data)
        if not _get_status_code(response_option):
            return _get_status_code(response_option)
        return 'OK'

    def authorization(self, sms_code: str, password_type: str = 'sms_code' or 'password'):
        """
        Авторизация
        :param sms_code: одноразовый sms-код, либо постоянный пароль для входа
        :param password_type: выбор способа авторизации: 'sms_code' или 'password'
        :return: Получаем access_token и refresh_token
        """
        data_auth = {"client_id": "digital-suite-web-app", "grant_type": "password", "username": self._phone_number,
                     "password": sms_code, "password_type": password_type}
        self.session.headers['Content-Type'] = 'application/x-www-form-urlencoded'
        response = self.session.post(self.url_auth, data_auth, verify=False)
        if _get_status_code(response):
            self.access_token = response.json()['access_token']
            self.refresh_token = response.json()['refresh_token']
            return response.json()['access_token'], response.json()['refresh_token']
        return response.json()['error_description']

    def refresh_token(self, refresh_token: str):
        """
        Обновления токена для авторизации
        :param refresh_token: нужно передать в качестве параметра refresh_token, полученный в прошлой сессии
        :return:
        """
        response = self.session.post(self.url_auth, data={
            'client_id': 'digital-suite-web-app',
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
        })
        if _get_status_code(response):
            return response.json()['access_token'], response.json()['refresh_token']
        return response.json()['error_description']

    def get_balance(self):
        """
        Получение данных о балансе
        :return:
        """
        response = self.session.get(self.balance_api)
        if _get_status_code(response):
            return response.json()['data']['value']

    def get_rests(self):
        """
        Получение остатков, доступных для продаж на Маркете
        :return:
        """
        response = self.session.get(self.rests_api)
        response_json = response.json()
        rests = list(response_json['data']['rests'])
        sellable = [a for a in rests if a['type'] == 'tariff' and a['rollover'] == False]
        return {
            'data': int(
                sum(a['remain'] for a in sellable if a['uom'] == 'mb') / 1024),
            'voice': int(
                sum(a['remain'] for a in sellable if a['uom'] == 'min'))
        }

    def create_lot(self, traffic_type, value, amount, emojis='None'):
        """
        Создание нового лота
        :param emojis: если не хотим использовать, оставляем 'None', либо передаем значение 'random',
        либо список необходимых эмоджи: 'cat', 'scream', 'bomb', 'rich', 'zipped', 'tongue', 'cool', 'devil'
        :param traffic_type: тип трафика ('voice' или 'data')
        :param value: число минут или Гб одного лота
        :param amount: стоимость лота
        :return:
        """
        response = self.session.put(self.market_api, json={
            'trafficType': traffic_type,
            'cost': {'amount': amount, 'currency': 'rub'},
            'volume': {'value': value,
                       'uom': 'min' if traffic_type == 'voice' else 'gb'}
        })
        if not _get_status_code(response):
            print(response.json())
            return response.json()['meta']['status']
        id_lot = response.json()["data"]["id"]
        if emojis != 'None':
            if emojis == 'random':
                all_emojis = ['cat', 'scream', 'bomb', 'rich', 'zipped', 'tongue', 'cool', 'devil']
                list_emojis = []
                for _ in range(3):
                    list_emojis.append(random.choice(all_emojis))
            else:
                list_emojis = emojis
            response = self.session.patch(self.market_api + '/{}'.format(id_lot), json={
                "showSellerName": True, "emojis": list_emojis,
                "cost": {"amount": amount, "currency": "rub"}
            })

        return id_lot

    def patch_lot(self, id_lot, amount):
        """
        Изменение цены лота
        :param id_lot: необходимо передать id лота
        :param amount: новая цена лота
        :return:
        """
        response = self.session.patch(self.market_api + '/{}'.format(id_lot), json={
            'cost': {'amount': amount, 'currency': 'rub'}
        })
        if not _get_status_code(response):
            return response.json()['meta']['status']
        return 'OK'

    def bought_lot(self, sms_code, lot):
        response = self.session.put(self.bought_api + '?validationCode={}'.format(sms_code),
                                    json={"volume": {"value": lot['volume']['value'], "uom": lot['volume']['uom']},
                                          "cost": {"amount": lot['cost']['amount'], "currency": "rub"},
                                          "lotId": lot['id'],
                                          "hash": lot['hash'], "trafficType": lot['trafficType']})
        if not _get_status_code(response):
            return response.json()['meta']['status']
        return 'OK'

    def delete_lot(self, id_lot):
        """
        :param id_lot: необходимо передать id лота
        :return: Снимаем с продажи лот
        """
        response = self.session.delete(self.market_api + '/{}'.format(id_lot))
        if not _get_status_code(response):
            return response.json()['meta']['status']
        return 'OK'

    def get_active_lots(self):
        """
        :return: Получаем список активных лотов
        """
        response = self.session.get(self.market_api)
        # print(response.json())
        if _get_status_code(response):
            response_json = response.json()
            lots = list(response_json['data'])
            active_lots = [a for a in lots if a['status'] == 'active']
            return active_lots

    def mixx_update_subscribe(self, action="enable"):
        json_data = {
            'operationType': 'change_service',
            'changedServices': [
                {
                    'billingServiceId': '31299',
                    'action': action,
                },
            ],
        }
        response = self.session.post(f'{self.service_api}/notifications/check',
                                 json=json_data)
        if action == "enable":
            response = self.session.put(f'{self.service_api}/31299')
        elif action == "disable":
            response = self.session.delete(f'{self.service_api}/31299')
        if not _get_status_code(response):
            return response.json()
        return 'OK'


