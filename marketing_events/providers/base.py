import requests


class TemporaryError(Exception):
    ...


class PermanentError(Exception):
    ...


def post_json(url, json=None, params=None, headers=None, timeout=6):
    response = requests.post(url, json=json, params=params, headers=headers, timeout=timeout)
    if response.status_code >= 500:
        raise TemporaryError(f"{response.status_code}: {response.text}")
    if response.status_code >= 400:
        raise PermanentError(f"{response.status_code}: {response.text}")
    return response.json() if response.content else {}
