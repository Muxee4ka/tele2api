# tele2api
Python client for Tele2 market API.

The library allows you to authorise either using a permanent password or via a one time SMS code.
Basic operations for creating and managing lots are supported.

## Usage

```python
from tele2api import Tele2Api

client = Tele2Api("79001234567")
client.get_sms_code()
token, refresh = client.authorization("123456")

balance = client.get_balance()
print(balance)

# or use a context manager
with Tele2Api("79001234567", access_token=token) as api:
    print(api.get_active_lots())
```
