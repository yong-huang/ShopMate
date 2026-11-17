"""Tests for the storefront products REST API (ticket T02, 3.7).

Seams: ``GET /api/products`` (category/keyword/sort browsing) and
``GET /api/products/{id}`` (detail page data), plus the static mount that
serves the generated SVG images. Errors use the ``{code, message}``
shape from System Design 3.7.
"""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_client() -> TestClient:
    return TestClient(create_app(Settings(_env_file=None)))


def test_list_returns_all_products_with_bilingual_fields() -> None:
    client = make_client()

    body = client.get("/api/products").json()

    assert body["total"] == 150
    assert len(body["items"]) == 150
    first = body["items"][0]
    assert first["featured"] is True
    # Default order: the 30 curated products lead, id-ordered; APP-001 is
    # the first curated id.
    assert first["id"] == "APP-001"
    assert first["name_en"] == "Trailblazer Waterproof Rain Jacket"
    assert first["name_zh"]
    assert first["price"] > 0
    assert first["currency"] == "USD"
    assert 0 < first["rating"] <= 5
    assert first["image_url"].startswith("/static/catalog/images/")
    assert first["image_url"].endswith(".svg")


def test_category_filter_narrows_the_list() -> None:
    client = make_client()

    body = client.get("/api/products", params={"category": "Electronics"}).json()

    assert body["total"] == 30
    assert {item["category"] for item in body["items"]} == {"Electronics"}


def test_keyword_filter_matches_names_tags_and_brands() -> None:
    client = make_client()

    by_chinese = client.get("/api/products", params={"q": "保温杯"}).json()
    assert any(item["id"] == "HOM-006" for item in by_chinese["items"])

    by_brand = client.get("/api/products", params={"q": "AURORA"}).json()
    assert [item["id"] for item in by_brand["items"]] == ["ELE-001"]


def test_sort_orders_are_price_and_rating() -> None:
    client = make_client()

    price_asc = [
        item["price"]
        for item in client.get(
            "/api/products", params={"sort": "price_asc"}
        ).json()["items"]
    ]
    assert price_asc == sorted(price_asc)

    ratings = [
        item["rating"]
        for item in client.get(
            "/api/products", params={"sort": "rating"}
        ).json()["items"]
    ]
    assert ratings == sorted(ratings, reverse=True)


def test_unknown_sort_is_rejected_with_code_and_message() -> None:
    client = make_client()

    response = client.get("/api/products", params={"sort": "cheapest"})

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"code", "message"}
    assert body["code"] == "invalid_sort"


def test_get_product_returns_full_detail_with_reviews() -> None:
    client = make_client()

    response = client.get("/api/products/ELE-001")

    assert response.status_code == 200
    product = response.json()
    assert product["id"] == "ELE-001"
    assert product["description_en"]
    assert product["description_zh"]
    assert len(product["reviews"]) >= 3
    assert len(product["specs"]) >= 3
    assert product["attributes"]["brand"] == "Aurora"
    assert product["sizing"] is None, "electronics has no sizing block"
    assert product["image_url"] == "/static/catalog/images/ELE-001.svg"


def test_unknown_product_returns_404_with_code_and_message() -> None:
    client = make_client()

    response = client.get("/api/products/NOPE-999")

    assert response.status_code == 404
    body = response.json()
    assert set(body) == {"code", "message"}
    assert body["code"] == "product_not_found"


def test_generated_svg_images_are_served_statically() -> None:
    client = make_client()

    response = client.get("/static/catalog/images/ELE-001.svg")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.text.lstrip().startswith("<svg")
