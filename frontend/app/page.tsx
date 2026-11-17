import { HealthCard } from "@/components/health-card";

export default function Home() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-8 px-6">
      <main className="flex flex-col items-center gap-2 text-center">
        <h1 className="text-4xl font-bold tracking-tight">ShopMate</h1>
        <p className="max-w-prose text-muted-foreground">
          A conversational shopping assistant — Maya guides shoppers from
          fuzzy intent to purchase, grounded strictly in the product catalog.
        </p>
      </main>
      <HealthCard />
    </div>
  );
}
