"use client";

import { motion } from "framer-motion";
import { RefreshCw, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { IncidentRow } from "@/components/IncidentRow";
import { Button, Card, Eyebrow, Skeleton, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { duration } from "@/lib/format";
import type { Incident } from "@/lib/types";

const FILTERS = ["ALL", "OPEN", "TRIAGED", "RESOLVED"] as const;
type Filter = (typeof FILTERS)[number];

export default function IncidentsPage() {
  const [items, setItems] = useState<Incident[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("ALL");
  const [query, setQuery] = useState("");

  const load = useCallback(async () => {
    try {
      setItems(await api.listIncidents());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load incidents");
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(() => {
      if (!document.hidden) load();
    }, 15000);
    return () => clearInterval(timer);
  }, [load]);

  const stats = useMemo(() => {
    const all = items ?? [];
    const resolved = all.filter((i) => i.status === "RESOLVED" && i.resolved_at);
    const mttr = resolved.length
      ? resolved.reduce((sum, i) => sum + (new Date(i.resolved_at!).getTime() - new Date(i.started_at).getTime()), 0) / resolved.length
      : 0;
    const analysed = all.filter((i) => i.ai_status !== "PENDING");
    const aiOk = analysed.filter((i) => i.ai_status === "OK").length;
    return {
      open: all.filter((i) => i.status === "OPEN").length,
      triaged: all.filter((i) => i.status === "TRIAGED").length,
      resolved: resolved.length,
      mttr: resolved.length ? duration(mttr) : "—",
      aiRate: analysed.length ? `${Math.round((aiOk / analysed.length) * 100)}%` : "—",
    };
  }, [items]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (items ?? []).filter(
      (i) =>
        (filter === "ALL" || i.status === filter) &&
        (!q || i.title.toLowerCase().includes(q) || (i.service ?? "").toLowerCase().includes(q)),
    );
  }, [items, filter, query]);

  return (
    <div>
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <Eyebrow>Overview</Eyebrow>
          <h1 className="mt-2 font-display text-5xl tracking-tight">Incidents</h1>
        </div>
        <Button variant="ghost" onClick={load} aria-label="Refresh incidents">
          <RefreshCw className="size-4" aria-hidden />
          Refresh
        </Button>
      </header>

      <section aria-label="Summary" className="mt-8 grid gap-3 md:grid-cols-4">
        <Card className="p-6 md:col-span-2">
          <Eyebrow>Open now</Eyebrow>
          {items ? (
            <p className="mt-3 font-display text-7xl leading-none tracking-tight text-clay">{stats.open}</p>
          ) : (
            <Skeleton className="mt-3 h-16 w-24" />
          )}
          <p className="mt-3 text-sm text-mute">Alerts firing without a resolution.</p>
        </Card>
        <StatCard label="Triaged" value={items ? String(stats.triaged) : null} tone="text-bronze" />
        <StatCard label="Resolved" value={items ? String(stats.resolved) : null} tone="text-sage" />
        <StatCard label="Mean time to resolve" value={items ? stats.mttr : null} wide />
        <StatCard label="AI analysis success" value={items ? stats.aiRate : null} wide />
      </section>

      <div className="mt-10 flex flex-wrap items-center justify-between gap-4">
        <div role="tablist" aria-label="Filter by status" className="flex gap-1 rounded-xl border border-white/10 bg-ink-850/60 p-1">
          {FILTERS.map((f) => (
            <button
              key={f}
              role="tab"
              aria-selected={filter === f}
              onClick={() => setFilter(f)}
              className={cx(
                "relative rounded-lg px-3.5 py-1.5 font-mono text-xs uppercase tracking-wider transition-colors duration-300",
                filter === f ? "text-bone" : "text-faint hover:text-mute",
              )}
            >
              {filter === f && (
                <motion.span
                  layoutId="filter-pill"
                  className="absolute inset-0 rounded-lg bg-white/[0.08]"
                  transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
                />
              )}
              <span className="relative">{f}</span>
            </button>
          ))}
        </div>
        <div className="relative w-full sm:w-72">
          <Search className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-faint" aria-hidden />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search incidents"
            placeholder="Search title or service"
            className="w-full rounded-xl border border-white/10 bg-ink-850 py-2.5 pl-10 pr-3 text-sm transition-colors duration-300 placeholder:text-faint hover:border-white/20 focus:border-bronze/60 focus:outline-none"
          />
        </div>
      </div>

      <div className="mt-5">
        {error && (
          <Card className="flex items-center justify-between gap-4 border-clay/30 p-5">
            <p className="text-sm text-clay">{error}</p>
            <Button variant="danger" onClick={load}>
              Retry
            </Button>
          </Card>
        )}

        {!items && !error && (
          <div className="space-y-3">
            {[0, 1, 2].map((n) => (
              <Skeleton key={n} className="h-28 w-full rounded-2xl" />
            ))}
          </div>
        )}

        {items && visible.length === 0 && !error && (
          <Card className="p-12 text-center">
            <p className="font-display text-2xl">Nothing here</p>
            <p className="mt-2 text-sm text-mute">
              {items.length === 0 ? "No incidents yet. Alerts from Alertmanager will appear here." : "No incidents match this filter."}
            </p>
          </Card>
        )}

        {visible.length > 0 && (
          <ul className="space-y-3">
            {visible.map((incident, index) => (
              <IncidentRow key={incident.id} incident={incident} index={index} />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function StatCard({ label, value, tone, wide }: { label: string; value: string | null; tone?: string; wide?: boolean }) {
  return (
    <Card className={cx("p-6", wide && "md:col-span-2")}>
      <Eyebrow>{label}</Eyebrow>
      {value === null ? (
        <Skeleton className="mt-3 h-10 w-20" />
      ) : (
        <p className={cx("mt-3 font-display text-4xl tracking-tight", tone)}>{value}</p>
      )}
    </Card>
  );
}