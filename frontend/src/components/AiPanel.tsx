"use client";

import { AlertTriangle, Sparkles } from "lucide-react";
import { toLines } from "@/lib/format";
import type { Incident } from "@/lib/types";
import { Card, Eyebrow, Skeleton } from "./ui";

export function AiPanel({ incident }: { incident: Incident }) {
  const steps = toLines(incident.ai_next_steps);

  if (incident.ai_status === "PENDING") {
    return (
      <Card className="p-6" >
        <div className="flex items-center gap-2 text-mute" role="status">
          <Sparkles className="size-4 animate-pulse text-bronze" aria-hidden />
          <span className="text-sm">Gathering metrics, logs and deploys, then asking the model…</span>
        </div>
        <Skeleton className="mt-5 h-4 w-11/12" />
        <Skeleton className="mt-3 h-4 w-9/12" />
        <Skeleton className="mt-3 h-4 w-10/12" />
      </Card>
    );
  }

  if (incident.ai_status === "FAILED" || incident.ai_status === "FALLBACK") {
    return (
      <Card className="flex gap-3 border-bronze/30 p-6">
        <AlertTriangle className="mt-0.5 size-5 shrink-0 text-bronze" aria-hidden />
        <div>
          <p className="font-display text-xl">AI analysis unavailable</p>
          <p className="mt-1 text-sm leading-relaxed text-mute">
            The alert was delivered with raw context only. Investigate manually; nothing was lost.
          </p>
        </div>
      </Card>
    );
  }

  return (
    <Card className="divide-y divide-white/10">
      <section className="p-6">
        <Eyebrow>Summary</Eyebrow>
        <p className="mt-3 text-base leading-relaxed">{incident.ai_summary ?? "No summary was produced."}</p>
      </section>
      <section className="p-6">
        <Eyebrow>Likely root cause</Eyebrow>
        <p className="mt-3 font-display text-2xl leading-snug tracking-tight">{incident.ai_root_cause ?? "Not determined."}</p>
        <p className="mt-3 text-xs text-faint">A hypothesis from the model, not a verified fact. Confirm before acting.</p>
      </section>
      {steps.length > 0 && (
        <section className="p-6">
          <Eyebrow>Suggested next steps</Eyebrow>
          <ol className="mt-4 space-y-3">
            {steps.map((step, i) => (
              <li key={i} className="flex gap-3 text-sm leading-relaxed">
                <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full border border-bronze/40 font-mono text-[10px] text-bronze">
                  {i + 1}
                </span>
                <span>{step}</span>
              </li>
            ))}
          </ol>
        </section>
      )}
    </Card>
  );
}