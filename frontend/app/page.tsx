import Link from "next/link";

import { HealthCard } from "@/components/health-card";
import { PreferencesButton } from "@/components/preferences/preferences-button";
import { ProductCard } from "@/components/storefront/product-card";
import { Badge } from "@/components/ui/badge";
import {
  getProducts,
  summarizeCategories,
  type ProductSummary,
} from "@/lib/api-client";

/**
 * Storefront home: categories, keyword search, and the curated featured
 * grid - all rendered from the live catalog API (ticket T02).
 */
export default async function Home({
  searchParams,
}: {
  searchParams: Promise<{ category?: string; q?: string }>;
}) {
  const { category, q } = await searchParams;
  const filtering = Boolean(category || q);
  let fullCatalog: ProductSummary[] | null = null;
  let products: ProductSummary[] | null = null;
  let error: string | null = null;
  try {
    const [all, filtered] = await Promise.all([
      getProducts({ sort: "featured" }),
      filtering
        ? getProducts({ category, q, sort: "featured" })
        : Promise.resolve(null),
    ]);
    fullCatalog = all.items;
    products = filtered ? filtered.items : all.items;
  } catch (cause) {
    error =
      cause instanceof Error ? cause.message : "Failed to load the catalog";
  }

  const categories = fullCatalog ? summarizeCategories(fullCatalog) : [];
  const featured = (fullCatalog ?? []).filter((product) => product.featured);

  return (
    <div className="relative mx-auto flex min-h-screen max-w-6xl flex-col gap-10 px-6 py-10">
      <div className="absolute right-6 top-10 print:hidden">
        <PreferencesButton />
      </div>
      <header className="flex flex-col items-center gap-2 text-center">
        <h1 className="text-4xl font-bold tracking-tight">ShopMate</h1>
        <p className="max-w-prose text-muted-foreground">
          A conversational shopping assistant — Maya guides shoppers from
          fuzzy intent to purchase, grounded strictly in the product catalog.
        </p>
      </header>

      {error && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
          <p className="font-medium">Catalog unavailable</p>
          <p className="text-muted-foreground">
            {error} — start the backend with <code>make backend</code>.
          </p>
        </div>
      )}

      {fullCatalog && products && (
        <>
          <section aria-label="Categories" className="flex flex-wrap justify-center gap-2">
            <Badge
              asChild
              variant={!filtering ? "default" : "outline"}
              className="px-3 py-1 text-sm"
            >
              <Link href="/">All ({fullCatalog.length})</Link>
            </Badge>
            {categories.map((categorySummary) => (
              <Badge
                key={categorySummary.name}
                asChild
                variant={
                  category === categorySummary.name ? "default" : "outline"
                }
                className="px-3 py-1 text-sm"
              >
                <Link href={`/?category=${encodeURIComponent(categorySummary.name)}`}>
                  {categorySummary.name} ({categorySummary.count})
                </Link>
              </Badge>
            ))}
          </section>

          <form action="/" method="get" className="mx-auto flex w-full max-w-md gap-2">
            <input
              type="search"
              name="q"
              defaultValue={q ?? ""}
              placeholder="Search the catalog… e.g. 保温杯, yoga, aurora"
              className="h-9 w-full rounded-md border bg-transparent px-3 text-sm outline-none focus-visible:border-foreground/30"
              aria-label="Keyword search"
            />
            {category && <input type="hidden" name="category" value={category} />}
            <button
              type="submit"
              className="h-9 shrink-0 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            >
              Search
            </button>
          </form>

          <section className="flex flex-col gap-4" aria-label="Catalog">
            <h2 className="text-xl font-semibold">
              {q
                ? `Results for “${q}”`
                : category
                  ? category
                  : "Featured products"}
              <span className="ml-2 text-sm font-normal text-muted-foreground">
                {products.length} item{products.length === 1 ? "" : "s"}
                {filtering ? "" : " · curated picks"}
              </span>
            </h2>
            {products.length === 0 ? (
              <p className="text-muted-foreground">
                No products match. Try another keyword or category.
              </p>
            ) : (
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
                {(filtering ? products : featured).map((product) => (
                  <ProductCard key={product.id} product={product} />
                ))}
              </div>
            )}
          </section>
        </>
      )}

      <HealthCard />
    </div>
  );
}
