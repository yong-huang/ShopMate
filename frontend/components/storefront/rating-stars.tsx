/** Small star rating display for catalog cards and detail pages. */

export function RatingStars({
  rating,
  className = "",
}: {
  rating: number;
  className?: string;
}) {
  const filled = Math.round(rating);
  return (
    <span
      className={`inline-flex items-center gap-0.5 text-sm ${className}`}
      aria-label={`Rated ${rating} out of 5`}
    >
      {[1, 2, 3, 4, 5].map((star) => (
        <span
          key={star}
          className={star <= filled ? "text-amber-500" : "text-muted-foreground/30"}
          aria-hidden="true"
        >
          ★
        </span>
      ))}
      <span className="ml-1 font-medium tabular-nums">{rating.toFixed(1)}</span>
    </span>
  );
}
