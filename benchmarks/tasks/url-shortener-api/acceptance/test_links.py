from __future__ import annotations

import uuid

import httpx
import pytest


def code() -> str:
    return "c" + uuid.uuid4().hex[:15]


def create(client: httpx.Client, *, url: str = "https://example.org/path?q=1") -> dict:
    response = client.post("/links", json={"url": url, "code": code()})
    assert response.status_code == 201, response.text
    return response.json()


def test_create_and_fetch(client: httpx.Client) -> None:
    url = "https://example.org/path?q=1"
    item = create(client, url=url)
    assert set(item) == {"code", "url", "clicks"}
    assert item["url"] == url and item["clicks"] == 0
    response = client.get(f"/links/{item['code']}")
    assert response.status_code == 200
    assert response.json() == item


def test_generated_code(client: httpx.Client) -> None:
    response = client.post("/links", json={"url": "http://example.org"})
    assert response.status_code == 201, response.text
    item = response.json()
    assert isinstance(item["code"], str) and 4 <= len(item["code"]) <= 20
    assert all(c.isascii() and (c.isalnum() or c in "-_") for c in item["code"])
    assert item["url"] == "http://example.org" and item["clicks"] == 0


def test_redirect_and_click_count(client: httpx.Client) -> None:
    item = create(client)
    for clicks in (1, 2):
        response = client.get(f"/r/{item['code']}")
        assert response.status_code == 302
        assert response.headers["location"] == item["url"]
        detail = client.get(f"/links/{item['code']}")
        assert detail.status_code == 200
        assert detail.json()["clicks"] == clicks


def test_listing_order_and_total(client: httpx.Client) -> None:
    first, second = create(client), create(client)
    response = client.get("/links")
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"items", "total"}
    assert data["total"] == len(data["items"])
    indexes = {item["code"]: i for i, item in enumerate(data["items"])}
    assert indexes[first["code"]] < indexes[second["code"]]
    assert first in data["items"] and second in data["items"]


def test_duplicate_code(client: httpx.Client) -> None:
    item = create(client)
    response = client.post("/links", json={"url": "https://other.example", "code": item["code"]})
    assert response.status_code == 409 and "error" in response.json()
    assert client.get(f"/links/{item['code']}").json()["url"] == item["url"]


@pytest.mark.parametrize("payload", [
    {}, {"url": ""}, {"url": "ftp://example.org"}, {"url": "https://"},
    {"url": 42}, {"url": "https://example.org", "code": "abc"},
    {"url": "https://example.org", "code": "with space"},
    {"url": "https://example.org", "code": 12},
])
def test_invalid_input(client: httpx.Client, payload: dict) -> None:
    response = client.post("/links", json=payload)
    assert response.status_code == 422, response.text
    assert "error" in response.json()


def test_malformed_json(client: httpx.Client) -> None:
    response = client.post("/links", content="{", headers={"Content-Type": "application/json"})
    assert response.status_code == 422 and "error" in response.json()


@pytest.mark.parametrize("path", ["/links/missing-code-12345", "/r/missing-code-12345"])
def test_unknown_code(client: httpx.Client, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 404 and "error" in response.json()
