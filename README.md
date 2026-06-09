# tele2api

Python-клиент неофициального API личного кабинета и Маркета **t2** (бывш. Tele2).

Авторизация по SMS-коду или постоянному паролю, работа с балансом и остатками,
полный цикл торговли на Маркете (создание лотов, поднятие «ракетой», покупка),
управление статусом SIM-карты.

## Возможности

- 🔐 Логин из Python **без браузера** — как делает официальное приложение
- 💰 Баланс, остатки, профиль, расходы за месяц
- 🛒 Маркет: создание / изменение / удаление лотов, поднятие в топ, позиция в выдаче, покупка
- 📱 Блокировка / разблокировка SIM, подписка MIXX

## Установка

```bash
pip install curl_cffi   # единственная зависимость
```

> Нужен именно `curl_cffi`, а не `requests` — почему, см. [«Под капотом»](#под-капотом).

## Быстрый старт

```python
from tele2api import Tele2Api

api = Tele2Api('79991234567')

api.get_sms_code()                      # -> 'OK', на номер придёт код
api.authorization('123456')             # код из SMS -> (access_token, refresh_token)

print(api.get_balance())                # 251.4
print(api.get_rests())                  # {'data': 30, 'voice': 600, 'sms': 0}
print(api.get_active_lots())            # [...]

# создать лот и поднять «ракетой»
lot_id = api.create_lot('voice', value=50, amount=40, emojis='random')
api.premium_lot(lot_id)
```

## Переиспользование токенов

`access_token` живёт ~4 часа. Его и `refresh_token` можно сохранить и подставить
в новый клиент, а по истечении — обновить:

```python
api = Tele2Api('79991234567', access_token=AT, refresh_token=RT)

api.update_token()                      # обновит по сохранённому refresh_token
```

## Методы

| Группа | Методы |
|--------|--------|
| Авторизация | `get_sms_code`, `authorization`, `update_token`, `reset_password` |
| Данные абонента | `get_balance`, `get_rests`, `get_profile`, `get_status`, `get_charges`, `get_slaves` |
| Маркет | `get_active_lots`, `create_lot`, `patch_lot`, `delete_lot`, `premium_lot`, `get_lot_position`, `bought_lot` |
| Услуги | `set_status` (блокировка SIM), `mixx_update_subscribe` |

Методы возвращают данные при успехе либо строку с кодом ошибки (`authorization`
при ошибке — текст). Подробности — в докстрингах `tele2api/tele2_api.py`.

## Под капотом

В 2024 Tele2 стал t2, API переехал на `api.t2.ru`, а инфраструктура закрылась
анти-ботом **NGENIX**. Эндпоинты логина (`/auth/*`) и запроса SMS
(`/api/validation/*`) пропускают запрос, только если он неотличим от запроса
мобильного приложения. Для этого нужно одновременно:

1. **TLS-рукопожатие как у okhttp/Android** — его воспроизводит
   [`curl_cffi`](https://github.com/lexiforest/curl_cffi) с профилем
   `chrome131_android`. Обычный `requests`/`urllib` (TLS OpenSSL) получает `403`.
2. Заголовок **`Tele2-User-Agent`** — выставляется клиентом автоматически.

Эндпоинты данных (`/api/subscribers/*`) работают с Bearer-токеном с любым
клиентом, но для единообразия идут через ту же импер­сонированную сессию.

## Дисклеймер

Неофициальный клиент для доступа к **собственному** аккаунту. Используйте на свой
страх и риск; автор не связан с t2.
