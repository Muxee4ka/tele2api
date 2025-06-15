# tele2api
Python client for Tele2 market API.

The library allows you to authorise either using a permanent password or via a one time SMS code.
Basic operations for creating and managing lots are supported.

## Usage

```python
from tele2api import Tele2Api

phone = "79001234567"
client = Tele2Api(phone)
client.get_sms_code()
token, refresh = client.authorization(input("Enter SMS code: "))

balance = client.get_balance()
print(balance)

# or use a context manager
with Tele2Api(phone, access_token=token) as api:
    print(api.get_active_lots())
```
