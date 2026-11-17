"""Storefront catalog endpoints (System Design 3.7).

- ``GET /api/products`` - browsing with category / keyword filters and
  sort; the default order is featured-first (the curated 30 lead).
- ``GET /api/products/{id}`` - full detail for the product page.

Browsing goes through the ``CatalogAdapter`` (ADR-008); the hybrid
retrieval module is reserved for the agent's chat tools. Generated SVG
images are served from ``/static/catalog/...``.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.catalog.adapter import CatalogAdapter, SORT_ORDERS
from app.catalog.schemas import ProductDetail, ProductSummary

router = APIRouter(tags=["products"])

STATIC_CATALOG_PREFIX = "/static/catalog"


class ProductSummaryResponse(ProductSummary):
    """Card fields plus the backend-served image URL."""

    image_url: str


class ProductDetailResponse(ProductDetail):
    """Detail fields plus the backend-served image URL."""

    image_url: str


class ProductListResponse(BaseModel):
    items: list[ProductSummaryResponse]
    total: int


def _image_url(image: str) -> str:
    return f"{STATIC_CATALOG_PREFIX}/{image}"


def _summary(product: ProductSummary) -> ProductSummaryResponse:
    payload = product.model_dump()
    payload["image_url"] = _image_url(product.image)
    return ProductSummaryResponse.model_validate(payload)


def _detail(product: ProductDetail) -> ProductDetailResponse:
    payload = product.model_dump()
    payload["image_url"] = _image_url(product.image)
    return ProductDetailResponse.model_validate(payload)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "message": message})


@router.get("/products")
async def list_products(
    request: Request,
    category: str | None = None,
    q: str | None = None,
    sort: str = "featured",
    limit: int | None = None,
) -> ProductListResponse:
    """Catalog browsing: filter by category/keyword, sort, optional limit.

    Error paths return a ``JSONResponse`` directly (FastAPI passes
    Response instances through without applying the response model).
    """
    catalog: CatalogAdapter = request.app.state.catalog
    try:
        products = catalog.list_products(
            category=category, q=q, sort=sort, limit=limit
        )
    except ValueError:
        return _error(
            422,
            "invalid_sort",
            f"sort must be one of {', '.join(SORT_ORDERS)}",
        )
    return ProductListResponse(
        items=[_summary(product) for product in products],
        total=len(products),
    )


@router.get("/products/{product_id}")
async def get_product(
    product_id: str, request: Request
) -> ProductDetailResponse:
    """One product's full detail; 404 when the id is unknown."""
    catalog: CatalogAdapter = request.app.state.catalog
    product = catalog.get_product(product_id)
    if product is None:
        return _error(
            404,
            "product_not_found",
            f"No product with id {product_id!r} in the catalog",
        )
    return _detail(product)
