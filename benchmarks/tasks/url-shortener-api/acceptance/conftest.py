from __future__ import annotations

import os
import time
from collections.abc import Iterator

import httpx
import pytest

BASE_URL = os.environ.get("BASE_URL", "http://app:8000")


@pytest.fixture(scope="session")
def client() -> Iterator[httpx.Client]:
    deadline = time.monotonic() + float(os.environ.get("READY_TIMEOUT", "180"))
    while True:
        try:
            httpx.get(BASE_URL + "/links", timeout=3)
            break
        except httpx.HTTPError:
            if time.monotonic() >= deadline:
                pytest.fail(f"app did not answer at {BASE_URL}")
            time.sleep(1)
    with httpx.Client(base_url=BASE_URL, timeout=15, follow_redirects=False) as http:
        yield http
