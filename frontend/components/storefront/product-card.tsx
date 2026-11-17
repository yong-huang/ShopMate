import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
} from "@/components/ui/card";
import { imageUrl, type ProductSummary } from "@/lib/api-client";
import { RatingStars } from "@/components/storefront/rating-stars";

/**
 * Storefront product card: generated SVG image + bilingual display name
 * (English primary, Chinese secondary, per PRD FR-7) and catalog price.
 */
export function ProductCard({ product }: { product: ProductSummary }) {
  return (
    <Link
      href={`/product/${product.id}`}
      className="group focus-visible:outline-2 focus-visible:outline-offset-2"
    >
      <Card className="relative h-full gap-3 overflow-hidden pt-0 transition-shadow group-hover:shadow-md">
        <CardHeader className="p-0">
          {/* Generated SVG placeholder (category emoji + stable palette). */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={imageUrl(product.image_url)}
            alt={`${product.name_en} / ${product.name_zh}`}
            className="aspect-square w-full object-cover"
            loading="lazy"
          />
        </CardHeader>
        <CardContent className="space-y-1 px-4">
          <p className="line-clamp-1 font-medium leading-snug">
            {product.name_en}
          </p>
          <p className="line-clamp-1 text-sm text-muted-foreground" lang="zh">
            {product.name_zh}
          </p>
        </CardContent>
        <CardFooter className="justify-between gap-2 px-4 pb-4">
          <span className="font-semibold tabular-nums">
            ${product.price.toFixed(2)}
          </span>
          <RatingStars rating={product.rating} />
        </CardFooter>
        {product.featured && (
          <Badge className="absolute right-2 top-2" variant="secondary">
            Featured
          </Badge>
        )}
      </Card>
    </Link>
  );
}
