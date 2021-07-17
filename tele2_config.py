HEADERS = {
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7,de;q=0.6,fr;q=0.5',
            "Cache-Control": "max-age=0",
            'Tele2-User-Agent': '"mytele2-app/3.17.0"; "unknown"; "Android/9"; "Build/12998710"',
            'X-API-Version': '1',
            'User-Agent': 'okhttp/4.2.0',
            'Accept-Encoding': 'gzip, deflate',
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json',
            'Connection': 'keep-alive'
        }

MAIN_API = 'https://my.tele2.ru/api/subscribers/'
URL_VALIDATION = 'https://my.tele2.ru/api/validation/number/'
URL_AUTH = 'https://my.tele2.ru/auth/realms/tele2-b2c/protocol/openid-connect/token'
URL_RESET_OPTION = 'https://my.tele2.ru/auth/realms/tele2-b2c/credential-management/reset-options?username='
URL_RESET_PASS = 'https://my.tele2.ru/auth/realms/tele2-b2c/credential-management/reset-password?username='