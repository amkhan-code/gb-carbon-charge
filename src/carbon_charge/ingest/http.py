"""Shared HTTP GET with retries."""

import time

import httpx

_client = httpx.Client(timeout=60, headers={"Accept": "application/json"}, follow_redirects=True)


def get(url: str, params: dict | None = None, retries: int = 5) -> httpx.Response:
    for attempt in range(retries):
        try:
            resp = _client.get(url, params=params)
            if resp.status_code < 400:
                return resp
            if resp.status_code not in (429, 500, 502, 503, 504):
                resp.raise_for_status()
        except httpx.TransportError:
            if attempt == retries - 1:
                raise
        time.sleep(2**attempt)
    resp.raise_for_status()
    return resp
