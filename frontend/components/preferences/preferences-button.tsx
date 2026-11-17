"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, ShieldCheck, Trash2, UserRound } from "lucide-react";

import {
  deleteProfile,
  getProfile,
  type ProfileResponse,
} from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/**
 * Preferences panel entry point (ticket T07, PRD FR-6): a storefront-chrome
 * button that opens a small card showing the shopper's learned profile and a
 * one-click clear. The anonymous `shopmate_sid` cookie is managed by the
 * backend; every fetch sends `credentials: "include"`.
 */

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <span>{value}</span>
    </div>
  );
}

function profileToRows(profile: ProfileResponse): { label: string; value: string }[] {
  const rows: { label: string; value: string }[] = [];
  if (profile.interests.length > 0) {
    rows.push({ label: "Interests", value: profile.interests.join(", ") });
  }
  if (profile.price_band) {
    rows.push({ label: "Price band", value: profile.price_band });
  }
  const sizes = Object.entries(profile.sizes);
  if (sizes.length > 0) {
    rows.push({
      label: "Sizes",
      value: sizes.map(([category, size]) => `${category}: ${size}`).join(", "),
    });
  }
  if (profile.likes.length > 0) {
    rows.push({ label: "Likes", value: profile.likes.join(", ") });
  }
  if (profile.dislikes.length > 0) {
    rows.push({ label: "Dislikes", value: profile.dislikes.join(", ") });
  }
  if (profile.notes.length > 0) {
    rows.push({ label: "Notes", value: profile.notes.join(" · ") });
  }
  const recent = profile.interaction_log.slice(-5);
  if (recent.length > 0) {
    const verbs: Record<string, string> = {
      view: "viewed",
      add_to_cart: "added to cart",
      purchase: "purchased",
    };
    rows.push({
      label: "Recent activity",
      value: recent
        .map((event) => `${verbs[event.type] ?? event.type} ${event.product_id ?? ""}`.trim())
        .join(", "),
    });
  }
  return rows;
}

export function PreferencesButton() {
  const [open, setOpen] = useState(false);
  const [profile, setProfile] = useState<ProfileResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setProfile(await getProfile());
    } catch {
      setError("Could not load your profile. Is the backend running?");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) void refresh();
  }, [open, refresh]);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (panelRef.current && !panelRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  async function clearProfile() {
    setClearing(true);
    setError(null);
    try {
      await deleteProfile();
      setProfile(await getProfile()); // refresh back to the empty state
    } catch {
      setError("Could not clear your profile. Please try again.");
    } finally {
      setClearing(false);
    }
  }

  const hasContent = profile !== null && profileToRows(profile).length > 0;

  return (
    <div className="relative">
      <Button
        variant="outline"
        size="sm"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((current) => !current)}
      >
        <UserRound data-icon="inline-start" />
        Preferences
      </Button>

      {open && (
        <div
          ref={panelRef}
          role="dialog"
          aria-label="Shopping preferences"
          className="absolute right-0 top-10 z-50 w-80"
        >
          <Card size="sm" className="shadow-lg">
            <CardHeader>
              <CardTitle className="text-base">Your preferences</CardTitle>
              <CardDescription>
                Learned from your sessions — anonymous, cookie-linked, never
                personal data. Maya uses this to tailor suggestions.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              {loading && (
                <p className="flex items-center gap-2 text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" /> Loading…
                </p>
              )}
              {error && (
                <p className="text-sm text-destructive" role="alert">
                  {error}
                </p>
              )}
              {!loading && !error && profile !== null && !hasContent && (
                <p className="text-sm text-muted-foreground">
                  Nothing learned yet. Chat with Maya or browse products and
                  your preferences will appear here after your session ends.
                </p>
              )}
              {!loading &&
                profileToRows(profile ?? {
                  interests: [],
                  price_band: null,
                  sizes: {},
                  likes: [],
                  dislikes: [],
                  notes: [],
                  interaction_log: [],
                  updated_at: null,
                }).map((row) => <Field key={row.label} label={row.label} value={row.value} />)}
              {hasContent && profile?.updated_at && (
                <p className="flex items-center gap-1 text-xs text-muted-foreground">
                  <ShieldCheck className="size-3" /> Updated{" "}
                  {new Date(profile.updated_at).toLocaleString()}
                </p>
              )}
            </CardContent>
            <CardFooter className="justify-between border-t py-3">
              <Button
                variant="destructive"
                size="xs"
                onClick={() => void clearProfile()}
                disabled={clearing || !hasContent}
              >
                {clearing ? (
                  <Loader2 className="size-3 animate-spin" />
                ) : (
                  <Trash2 data-icon="inline-start" />
                )}
                Clear profile
              </Button>
              <span className="text-xs text-muted-foreground">
                Clears everything Maya remembers
              </span>
            </CardFooter>
          </Card>
        </div>
      )}
    </div>
  );
}
