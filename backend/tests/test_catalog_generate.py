"""Tests for the mock catalog generator (ticket T02, System Design 3.8).

The seam under test is ``app.catalog.generate``: generating the catalog into
a directory (products.json + SVG images) and the build check that validates
a catalog directory. The committed backend/data/catalog is produced by the
same code via ``make catalog``.
"""

import json
from pathlib import Path

from app.catalog.generate import CATEGORY_SPLITS, check_catalog, generate_catalog

EXPECTED_TOTAL = 150
EXPECTED_CURATED = 30


def generate_into(tmp_path: Path) -> Path:
    catalog_dir = tmp_path / "catalog"
    generate_catalog(catalog_dir)
    return catalog_dir


def load_products(catalog_dir: Path) -> list[dict]:
    return json.loads((catalog_dir / "products.json").read_text(encoding="utf-8"))


def test_generated_catalog_has_150_products_with_required_split(tmp_path: Path) -> None:
    catalog_dir = generate_into(tmp_path)

    products = load_products(catalog_dir)
    assert len(products) == EXPECTED_TOTAL
    counts: dict[str, int] = {}
    for product in products:
        counts[product["category"]] = counts.get(product["category"], 0) + 1
    assert counts == CATEGORY_SPLITS
    assert sum(CATEGORY_SPLITS.values()) == EXPECTED_TOTAL


def test_every_product_carries_bilingual_display_fields(tmp_path: Path) -> None:
    catalog_dir = generate_into(tmp_path)

    for product in load_products(catalog_dir):
        for field in ("name_en", "name_zh", "description_en", "description_zh"):
            assert isinstance(product[field], str) and product[field].strip(), (
                f"{product['id']}: {field} must be a non-empty string"
            )


def test_product_identifiers_are_stable_sku_style_and_unique(tmp_path: Path) -> None:
    catalog_dir = generate_into(tmp_path)

    products = load_products(catalog_dir)
    ids = [product["id"] for product in products]
    slugs = [product["slug"] for product in products]
    assert len(set(ids)) == EXPECTED_TOTAL
    assert len(set(slugs)) == EXPECTED_TOTAL
    for product in products:
        prefix = product["id"].split("-")[0]
        assert prefix.isalpha() and prefix.isupper()
        assert product["id"].split("-")[1].isdigit()
        assert product["slug"].replace("-", "").isalnum()


def test_curated_products_are_featured_rich_records(tmp_path: Path) -> None:
    catalog_dir = generate_into(tmp_path)

    products = load_products(catalog_dir)
    curated = [p for p in products if p.get("featured")]
    assert len(curated) == EXPECTED_CURATED
    curated_categories = {p["category"] for p in curated}
    assert curated_categories == set(CATEGORY_SPLITS)
    for product in curated:
        assert 3 <= len(product["reviews"]) <= 5, product["id"]
        assert len(product["specs"]) >= 3, product["id"]
        assert product["description_en"] and product["description_zh"]
        assert 0.0 < product["rating"] <= 5.0


def test_templated_products_meet_the_quality_floor(tmp_path: Path) -> None:
    catalog_dir = generate_into(tmp_path)

    products = load_products(catalog_dir)
    templated = [p for p in products if not p.get("featured")]
    assert len(templated) == EXPECTED_TOTAL - EXPECTED_CURATED
    for product in templated:
        assert product["price"] > 0, product["id"]
        assert product["currency"] == "USD"
        assert len(product["reviews"]) >= 1, product["id"]
        assert product["tags"], product["id"]
        assert product["stock"] >= 0, product["id"]
        assert product["rating"] > 0, product["id"]
        assert product["specs"], product["id"]


def test_apparel_products_carry_apparel_heavy_fields(tmp_path: Path) -> None:
    catalog_dir = generate_into(tmp_path)

    apparel = [
        p for p in load_products(catalog_dir)
        if p["category"] == "Apparel & Accessories"
    ]
    assert len(apparel) == CATEGORY_SPLITS["Apparel & Accessories"]
    for product in apparel:
        assert product.get("sizing"), product["id"]
        assert product.get("care"), product["id"]
        assert product.get("materials"), product["id"]


def test_generation_is_deterministic(tmp_path: Path) -> None:
    first = generate_into(tmp_path / "first")
    second = generate_into(tmp_path / "second")

    assert (first / "products.json").read_bytes() == (
        second / "products.json"
    ).read_bytes()
    for image in sorted((first / "images").iterdir()):
        assert image.name.endswith(".svg")
        assert image.read_bytes() == (second / "images" / image.name).read_bytes()


def test_images_are_deterministic_svgs_per_product(tmp_path: Path) -> None:
    catalog_dir = generate_into(tmp_path)

    products = load_products(catalog_dir)
    images_dir = catalog_dir / "images"
    assert len(list(images_dir.iterdir())) == EXPECTED_TOTAL
    for product in products:
        image_path = catalog_dir / product["image"]
        assert image_path.is_file(), product["id"]
        content = image_path.read_text(encoding="utf-8")
        assert content.lstrip().startswith("<svg"), product["id"]
        assert "</svg>" in content, product["id"]


def test_check_passes_on_a_fresh_catalog(tmp_path: Path) -> None:
    catalog_dir = generate_into(tmp_path)

    problems = check_catalog(catalog_dir)

    assert problems == []


def test_check_flags_a_corrupted_catalog(tmp_path: Path) -> None:
    catalog_dir = generate_into(tmp_path)
    products = load_products(catalog_dir)
    del products[0]
    (catalog_dir / "products.json").write_text(
        json.dumps(products), encoding="utf-8"
    )

    problems = check_catalog(catalog_dir)

    assert problems, "a catalog missing a product must be flagged"
    assert any("149" in problem for problem in problems)
    assert any("split" in problem.lower() for problem in problems)
