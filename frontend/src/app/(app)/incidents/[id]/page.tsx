"use client";

import { motion } from "framer-motion";
import { ArrowLeft, CheckCircle2 } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AiPanel } from "@/components/AiPanel";
import { PostmortemPanel } from "@/components/PostmortemPanel";
import { useToast } from "@/components/Toast";
import { AiBadge, Button, Card, ConfirmDialog, Eyebrow, Skeleton, SeverityBadge, StatusBadge } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { duration, fmtDate } from "@/lib/format";
import type { Incident } from "@/lib/types";

export default function IncidentPage() {
  const { id } = useParams<{ id: string }>();
  const { canModify } = useAuth();
  const toast = useToast();
  const [incident, setIncident] = useState<Incident | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [resolving, setResolving] = useState(false);

  const load = useCallback(async () => {
    try {
      setIncident(await api.getIncident(id));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, "Failed to load incident"));
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (incident?.ai_status !== "PENDING") return;
    const timer = setInterval(load, 4000);
    return () => clearInterval(timer);
  }, [incident?.ai_status, load]);

  async function resolve() {
    setResolving(true);
    try {
      setIncident(await api.resolve(id));
      setConfirming(false);
      toast.ok("Incident resolved. Drafting the postmortem.");
    } catch (e) {
      toast.err(e instanceof Error ? e.message : "Could not resolve");
    } finally {
      setResolving(false);
    }
  }

  if (error) {
    return (
      <div>
        <BackLink />
        <Card className="mt-6 p-10 text-center">
          <p className="font-display text-3xl">{error.status === 404 ? "Incident not found" : "Something went wrong"}</p>
          <p className="mt-2 text-sm text-mute">{error.message}</p>
          <Button variant="ghost" className="mt-6" onClick={load}>
            Try again
          </Button>
        </Card>
      </div>
    );
  }

  if (!incident) {
    return (
      <div>
        <BackLink />
        <Skeleton className="mt-6 h-12 w-2/3" />
        <Skeleton className="mt-4 h-6 w-1/3" />
        <Skeleton className="mt-8 h-64 w-full rounded-2xl" />
      </div>
    );
  }

  const ended = incident.resolved_at ? new Date(incident.resolved_at).getTime() : Date.now();
  const elapsed = duration(ended - new Date(incident.started_at).getTime());

  return (
    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}>
      <BackLink />

      <header className="mt-6 flex flex-wrap items-start justify-between gap-6">
        <div className="min-w-0 max-w-3xl">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={incident.status} />
            <SeverityBadge severity={incident.severity} />
            <AiBadge status={incident.ai_status} />
          </div>
          <h1 className="mt-4 font-display text-5xl leading-[1.05] tracking-tight">{incident.title}</h1>
          <p className="mt-3 font-mono text-xs text-faint">
            {incident.service ?? "unknown service"} · #{incident.id} · fingerprint {incident.fingerprint}
          </p>
        </div>
        {canModify && incident.status !== "RESOLVED" && (
          <Button onClick={() => setConfirming(true)}>
            <CheckCircle2 className="size-4" aria-hidden />
            Resolve incident
          </Button>
        )}
      </header>

      <div className="mt-10 grid gap-4 lg:grid-cols-[1.6fr_1fr]">
        <AiPanel incident={incident} />

        <Card className="h-fit divide-y divide-white/10">
          <Meta label="Started" value={fmtDate(incident.started_at)} />
          <Meta label={incident.resolved_at ? "Time to resolve" : "Open for"} value={elapsed} />
          <Meta label="Resolved" value={fmtDate(incident.resolved_at)} />
          <Meta label="Resolved by" value={incident.resolved_by ?? "—"} />
        </Card>
      </div>

      <div className="mt-4">
        <PostmortemPanel incidentId={incident.id} incidentStatus={incident.status} />
      </div>

      <ConfirmDialog
        open={confirming}
        title="Resolve this incident?"
        body="It will be marked resolved under your name and a postmortem draft will be generated for review."
        confirmLabel="Resolve"
        loading={resolving}
        onConfirm={resolve}
        onCancel={() => setConfirming(false)}
      />
    </motion.div>
  );
}

function BackLink() {
  return (
    <Link
      href="/incidents"
      className="inline-flex items-center gap-2 text-sm text-mute transition-all duration-300 ease-expo hover:-translate-x-0.5 hover:text-bone"
    >
      <ArrowLeft className="size-4" aria-hidden />
      All incidents
    </Link>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="p-5">
      <Eyebrow>{label}</Eyebrow>
      <p className="mt-1.5 font-display text-2xl tracking-tight">{value}</p>
    </div>
  );
}