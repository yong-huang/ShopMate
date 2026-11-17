import Link from "next/link";
import { notFound } from "next/navigation";

import { RatingStars } from "@/components/storefront/rating-stars";
import { ViewPing } from "@/components/storefront/view-ping";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { getProduct, imageUrl, type ProductDetail } from "@/lib/api-client";

/**
 * Product detail page: bilingual name/description, specs, reviews, and
 * the generated SVG image (ticket T02, System Design 3.8 / PRD FR-2).
 */
export default async function ProductPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let product: ProductDetail;
  try {
    product = await getProduct(id);
  } catch (error) {
    if (error instanceof Error && "status" in error && error.status === 404) {
      notFound();
    }
    throw error;
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-5xl flex-col gap-6 px-6 py-10">
      {/* Interaction capture: append this product to the shopper's view log. */}
      <ViewPing productId={product.id} />
      <nav className="text-sm text-muted-foreground" aria-label="Breadcrumb">
        <Link href="/" className="hover:underline">
          ← Back to storefront
        </Link>
      </nav>

      <div className="grid gap-8 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <div className="flex flex-col gap-3">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={imageUrl(product.image_url)}
            alt={`${product.name_en} / ${product.name_zh}`}
            className="aspect-square w-full rounded-xl border object-cover"
          />
          {product.featured && <Badge className="w-fit">Featured</Badge>}
        </div>

        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <span className="text-sm text-muted-foreground">
              {product.category} · {product.id}
            </span>
            <h1 className="text-3xl font-bold leading-tight">
              {product.name_en}
            </h1>
            <p className="text-lg text-muted-foreground" lang="zh">
              {product.name_zh}
            </p>
          </div>

          <div className="flex items-center gap-4">
            <span className="text-2xl font-semibold tabular-nums">
              ${product.price.toFixed(2)}{" "}
              <span className="text-sm font-normal text-muted-foreground">
                {product.currency}
              </span>
            </span>
            <RatingStars rating={product.rating} />
            <span className="text-sm text-muted-foreground">
              {product.stock} in stock
            </span>
          </div>

          <div className="flex flex-wrap gap-1.5">
            {product.tags.map((tag) => (
              <Badge key={tag} variant="outline">
                {tag}
              </Badge>
            ))}
          </div>

          <section className="space-y-2" aria-label="Description">
            <p>{product.description_en}</p>
            <p className="text-muted-foreground" lang="zh">
              {product.description_zh}
            </p>
          </section>

          <SpecsTable product={product} />
        </div>
      </div>

      <ApparelInfo product={product} />
      <ReviewsSection product={product} />
    </div>
  );
}

function SpecsTable({ product }: { product: ProductDetail }) {
  return (
    <section aria-label="Specifications">
      <div className="overflow-hidden rounded-lg border">
        <table className="w-full text-sm">
          <tbody>
            {product.specs.map((spec, index) => (
              <tr
                key={spec.label}
                className={index % 2 === 0 ? "bg-muted/50" : ""}
              >
                <th
                  scope="row"
                  className="w-1/3 px-4 py-2 text-left font-medium text-muted-foreground"
                >
                  {spec.label}
                </th>
                <td className="px-4 py-2">{spec.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ApparelInfo({ product }: { product: ProductDetail }) {
  if (!product.sizing && !product.care && !product.materials) return null;
  return (
    <section className="grid gap-4 md:grid-cols-3" aria-label="Sizing, care and materials">
      {product.sizing && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Sizing</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            <p className="text-muted-foreground">{product.sizing.fit}</p>
            <p className="flex flex-wrap gap-1 pt-1">
              {product.sizing.sizes.map((size) => (
                <Badge key={size} variant="secondary">
                  {size}
                </Badge>
              ))}
            </p>
          </CardContent>
        </Card>
      )}
      {product.care && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Care</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            {product.care}
          </CardContent>
        </Card>
      )}
      {product.materials && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Materials</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm text-muted-foreground">
            {Object.entries(product.materials).map(([key, value]) => (
              <p key={key}>
                <span className="font-medium capitalize text-foreground">
                  {key}:
                </span>{" "}
                {value}
              </p>
            ))}
          </CardContent>
        </Card>
      )}
    </section>
  );
}

function ReviewsSection({ product }: { product: ProductDetail }) {
  return (
    <section className="space-y-3" aria-label="Reviews">
      <h2 className="text-xl font-semibold">
        Reviews ({product.reviews.length})
      </h2>
      <div className="grid gap-3 md:grid-cols-2">
        {product.reviews.map((review, index) => (
          <Card key={`${review.author}-${index}`}>
            <CardHeader className="flex-row items-center justify-between gap-2">
              <CardTitle className="text-sm font-medium">
                {review.author}
              </CardTitle>
              <RatingStars rating={review.rating} />
            </CardHeader>
            <CardContent className="text-sm text-muted-foreground">
              “{review.comment}”
            </CardContent>
          </Card>
        ))}
      </div>
    </section>
  );
}
