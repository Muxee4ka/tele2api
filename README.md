# tele2api

Python-клиент неофициального API личного кабинета и Маркета **t2** (бывш. Tele2).

Авторизация по SMS-коду или постоянному паролю, работа с балансом и остатками,
полный цикл торговли на Маркете (создание лотов, поднятие «ракетой», авто-демпинг,
покупка), управление статусом SIM-карты и услугами.

## Возможности

- 🔐 Логин из Python **без браузера** — как делает официальное приложение
- 💰 Баланс, остатки, профиль, расходы, фактическая абонплата (со скидкой)
- 🛒 Маркет: лоты, поднятие в топ, позиция в выдаче, авто-демпинг, массовые операции
- 📱 Блокировка / разблокировка SIM, услуги, подписка MIXX
- 🧯 Типизированные исключения вместо «магических строк»
- ⏱ Авто-обновление access-токена перед истечением

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

## Обработка ошибок (v3)

Мутирующие методы (`create_lot`, `patch_lot`, `premium_lot`, `delete_lot`,
`connect_service`, …) возвращают значение/id при успехе и **бросают исключение**
при ошибке — больше не нужно угадывать, значение перед вами или код ошибки:

```python
from tele2api import Tele2Api, Tele2LotError, Tele2AuthError

try:
    lot_id = api.create_lot('data', value=5, amount=100)
except Tele2LotError as e:
    print(e.code)        # код ошибки t2, напр. 'LOT_LIMIT_EXCEEDED'
```

Иерархия: `Tele2ApiError` (база) → `Tele2AuthError`, `Tele2LotError`,
`Tele2ServiceError`. У каждого исключения есть `.code` (код из ответа t2).

## Токены: переиспользование и авто-обновление

`access_token` живёт ~4 часа. Его и `refresh_token` можно сохранить и подставить
в новый клиент. Логику «обновить незадолго до истечения» теперь не нужно писать
самому:

```python
api = Tele2Api('79991234567', access_token=AT, refresh_token=RT)

api.token_expires_at          # datetime истечения (UTC) или None
api.is_token_expired(margin=300)   # True, если истечёт в ближайшие 5 минут
api.ensure_token()            # обновит токен, если пора (иначе ничего не делает)
api.update_token()            # обновить принудительно по refresh_token
```

При неудачном обновлении поднимается `Tele2AuthError`.

## Маркет: помощники

```python
# опустить цену лота ниже ближайшего конкурента (но не ниже min_amount)
api.undercut_lot(lot, step=1, min_amount=1)
# -> {'changed': True, 'old_price': 100, 'new_price': 79, 'reason': 'undercut'}

api.undercut_all_lots(step=1)                 # демпинг по всем активным лотам
api.get_my_lot_position(lot)                  # 1-based позиция лота в выдаче (0 — нет)

# массовые операции -> {'ok': [...], 'failed': [...], 'errors': {...}}
api.premium_all_lots(traffic_type='data')
api.delete_all_lots()
api.create_lots('voice', volumes=[8, 16, 34], amount=40, emojis='random')
```

## Услуги и расходы (семантика t2)

```python
for svc in api.get_services_catalogue():
    svc.is_connected      # status == 'CONNECTED' (а не ненадёжный showDisconnectButton)
    svc.can_disconnect    # disconnectionAvailabilityStatus.canDisconnect
    svc.fee               # абонплата, float

api.connect_service(billing_id)       # по billingServiceId
api.disconnect_service(billing_id)

api.get_charges_flat()    # плоский список {name, amount, category} за месяц
api.get_actual_fee()      # фактическая (со скидкой) абонплата текущего месяца
```

## Методы

| Группа | Методы |
|--------|--------|
| Авторизация | `get_sms_code`, `authorization`, `update_token`, `ensure_token`, `reset_password` |
| Токен | `token_expires_at`, `is_token_expired`, `ensure_token` |
| Данные абонента | `get_balance`, `get_rests`, `get_rests_detailed`, `get_rests_rollover`, `get_profile`, `get_status`, `get_tariff`, `get_charges`, `get_charges_flat`, `get_actual_fee`, `get_slaves` |
| Маркет | `get_active_lots`, `create_lot`, `create_lots`, `patch_lot`, `delete_lot`, `delete_all_lots`, `premium_lot`, `premium_all_lots`, `get_lot_position`, `get_my_lot_position`, `undercut_lot`, `undercut_all_lots`, `bought_lot` |
| Услуги | `get_services`, `get_services_catalogue`, `connect_service`, `disconnect_service`, `set_status` (блокировка SIM), `mixx_update_subscribe` |

Любой метод данных/маркета принимает `subscriber=<номер>` для обращения к
привязанному номеру (`get_slaves()` вернёт список). Подробности — в докстрингах
`tele2api/tele2_api.py`.

## Под капотом

В 2024 Tele2 стал t2, API переехал на `api.t2.ru`, а инфраструктура закрылась
анти-ботом **NGENIX**. Эндпоинты логина (`/auth/*`) и запроса SMS
(`/api/validation/*`) пропускают запрос, только если он неотличим от запроса
мобильного приложения. Для этого нужно одновременно:

1. **TLS-рукопожатие мобильного браузера** — его воспроизводит
   [`curl_cffi`](https://github.com/lexiforest/curl_cffi). Обычный
   `requests`/`urllib` (TLS OpenSSL) получает `403`.
2. Заголовок **`Tele2-User-Agent`** — выставляется клиентом автоматически.

Эндпоинты данных (`/api/subscribers/*`) работают с Bearer-токеном с любым
клиентом, но для единообразия идут через ту же импер­сонированную сессию.

### Профиль impersonate

По умолчанию клиент использует рабочий профиль `firefox133` (профили
периодически блокируются анти-ботом). Если дефолт перестал проходить —
переопределите его:

```python
api = Tele2Api('79991234567', impersonate='chrome131_android')
```

## Дисклеймер

Неофициальный клиент для доступа к **собственному** аккаунту. Используйте на свой
страх и риск; автор не связан с t2.
