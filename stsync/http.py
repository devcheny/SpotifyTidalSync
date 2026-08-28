"""Sesion HTTP con reintentos, backoff y respeto de Retry-After."""
from __future__ import annotations

import random
import time
from typing import Any, Callable

import requests

RETRY_STATUS = {429, 500, 502, 503, 504}


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int = 0, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class HttpClient:
    def __init__(self, log: Callable[[str], None] | None = None, max_retries: int = 5) -> None:
        self.session = requests.Session()
        self.max_retries = max_retries
        self.log = log or (lambda _msg: None)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: Any = None,
        json_body: Any = None,
        timeout: int = 30,
        expected: tuple[int, ...] = (200, 201, 202, 204),
    ) -> Any:
        last_exc: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                resp = self.session.request(
                    method, url, headers=headers, params=params,
                    json=json_body, timeout=timeout,
                )
            except requests.RequestException as exc:
                last_exc = exc
                self._sleep(attempt, None, f"red: {exc}")
                continue

            if resp.status_code in RETRY_STATUS and attempt < self.max_retries - 1:
                self._sleep(attempt, resp.headers.get("Retry-After"),
                            f"HTTP {resp.status_code} en {url}")
                continue

            if resp.status_code in expected:
                if resp.status_code == 204 or not resp.content:
                    return None
                try:
                    return resp.json()
                except ValueError:
                    return resp.text

            raise ApiError(
                f"{method} {url} -> HTTP {resp.status_code}: {resp.text[:400]}",
                status=resp.status_code,
                body=resp.text,
            )

        raise ApiError(f"{method} {url} fallo tras {self.max_retries} intentos: {last_exc}")

    def _sleep(self, attempt: int, retry_after: str | None, reason: str) -> None:
        if retry_after:
            try:
                delay = min(float(retry_after), 120.0)
            except ValueError:
                delay = 2.0 ** attempt
        else:
            delay = min(2.0 ** attempt + random.random(), 60.0)
        self.log(f"    reintento en {delay:.1f}s ({reason})")
        time.sleep(delay)
