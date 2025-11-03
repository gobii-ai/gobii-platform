import requests


class TemporaryError(Exception):
    ...


class PermanentError(Exception):
    ...


RETRYABLE_4XX = {408, 425, 429}


def post_json(url, json=None, params=None, headers=None, timeout=6):
    response = requests.post(url, json=json, params=params, headers=headers, timeout=timeout)
    status = response.status_code
    if status >= 500 or status in RETRYABLE_4XX:
        raise TemporaryError(f"{status}: {response.text}")
    if status >= 400:
        raise PermanentError(f"{status}: {response.text}")
    return response.json() if response.content else {}
