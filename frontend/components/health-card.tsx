"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { getHealth, type HealthResponse } from "@/lib/api-client";

type HealthState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; health: HealthResponse };

/**
 * Home page health panel: shows whether the backend is reachable and how
 * its model provider is configured (never dials the provider itself).
 */
export function HealthCard() {
  const [state, setState] = useState<HealthState>({ kind: "loading" });

  const refresh = useCallback(async (signal?: AbortSignal) => {
    setState({ kind: "loading" });
    try {
      const health = await getHealth(signal);
      setState({ kind: "ready", health });
    } catch (error) {
      if (signal?.aborted) return;
      setState({
        kind: "error",
        message: error instanceof Error ? error.message : "Unknown error",
      });
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  return (
    <Card className="w-full max-w-md">
      <CardHeader>
        <CardTitle>Backend health</CardTitle>
        <CardDescription>
          Liveness and model provider status from{" "}
          <code className="font-mono text-xs">GET /api/health</code>
        </CardDescription>
        <CardAction>
          <Button variant="outline" size="sm" onClick={() => void refresh()}>
            Refresh
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-3">
        {state.kind === "loading" && (
          <p className="text-sm text-muted-foreground">Checking backend...</p>
        )}
        {state.kind === "error" && (
          <div className="space-y-1">
            <Badge variant="destructive">unreachable</Badge>
            <p className="text-sm text-muted-foreground">{state.message}</p>
          </div>
        )}
        {state.kind === "ready" && (
          <>
            <div className="flex items-center gap-2">
              <Badge variant="default">{state.health.status}</Badge>
              <Badge
                variant={
                  state.health.provider.status === "configured"
                    ? "secondary"
                    : "outline"
                }
              >
                provider {state.health.provider.status}
              </Badge>
            </div>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
              <dt className="text-muted-foreground">Provider</dt>
              <dd className="font-medium">{state.health.provider.name}</dd>
              <dt className="text-muted-foreground">Model</dt>
              <dd className="font-mono text-xs">
                {state.health.provider.model}
              </dd>
              <dt className="text-muted-foreground">API key</dt>
              <dd className="font-medium">
                {state.health.provider.api_key_configured
                  ? "configured"
                  : "not configured"}
              </dd>
              <dt className="text-muted-foreground">API version</dt>
              <dd className="font-mono text-xs">{state.health.version}</dd>
            </dl>
          </>
        )}
      </CardContent>
    </Card>
  );
}
