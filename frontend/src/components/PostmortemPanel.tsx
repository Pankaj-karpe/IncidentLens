"use client";

import { Check, FileText, Pencil, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { isStringList, toLines, toTimeline } from "@/lib/format";
import type { IncidentStatus, Postmortem, PostmortemUpdate } from "@/lib/types";
import { useToast } from "./Toast";
import { Button, Card, Eyebrow, PostmortemBadge, Skeleton } from "./ui";

type LoadState = "loading" | "missing" | "error" | "ready";

interface Draft {
  summary: string;
  root_cause: string;
  impact: string;
  actions: string;
}

function toDraft(pm: Postmortem): Draft {
  return {
    summary: pm.summary ?? "",
    root_cause: pm.root_cause ?? "",
    impact: pm.impact ?? "",
    actions: toLines(pm.action_items).join("\n"),
  };
}

export function PostmortemPanel({ incidentId, incidentStatus }: { incidentId: number; incidentStatus: IncidentStatus }) {
  const { canModify, canReview } = useAuth();
  const toast = useToast();
  const [pm, setPm] = useState<Postmortem | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const [errorText, setErrorText] = useState("");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [reviewing, setReviewing] = useState(false);

  const load = useCallback(async () => {
    try {
      setPm(await api.getPostmortem(incidentId));
      setState("ready");
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setState("missing");
      } else {
        setErrorText(e instanceof Error ? e.message : "Failed to load postmortem");
        setState("error");
      }
    }
  }, [incidentId]);

  useEffect(() => {
    load();
  }, [load, incidentStatus]);

  useEffect(() => {
    if (state !== "missing" || incidentStatus !== "RESOLVED") return;
    const timer = setInterval(load, 4000);
    return () => clearInterval(timer);
  }, [state, incidentStatus, load]);

  const actionsEditable = pm ? isStringList(pm.action_items) : false;

  function startEdit() {
    if (!pm) return;
    setDraft(toDraft(pm));
    setEditing(true);
  }

  async function save() {
    if (!pm || !draft) return;
    const original = toDraft(pm);
    const patch: PostmortemUpdate = {};
    if (draft.summary !== original.summary) patch.summary = draft.summary;
    if (draft.root_cause !== original.root_cause) patch.root_cause = draft.root_cause;
    if (draft.impact !== original.impact) patch.impact = draft.impact;
    if (actionsEditable && draft.actions !== original.actions) {
      patch.action_items = draft.actions.split("\n").map((l) => l.trim()).filter(Boolean);
    }
    if (Object.keys(patch).length === 0) {
      setEditing(false);
      return;
    }
    setSaving(true);
    try {
      setPm(await api.editPostmortem(incidentId, patch));
      setEditing(false);
      toast.ok(pm.status === "REVIEWED" ? "Saved. Sign-off was reset to draft." : "Postmortem saved.");
    } catch (e) {
      toast.err(e instanceof Error ? e.message : "Could not save");
    } finally {
      setSaving(false);
    }
  }

  async function review() {
    setReviewing(true);
    try {
      setPm(await api.reviewPostmortem(incidentId));
      toast.ok("Postmortem signed off.");
    } catch (e) {
      toast.err(e instanceof Error ? e.message : "Could not sign off");
    } finally {
      setReviewing(false);
    }
  }

  if (state === "loading") {
    return (
      <Card className="p-6">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="mt-4 h-4 w-full" />
        <Skeleton className="mt-3 h-4 w-10/12" />
      </Card>
    );
  }

  if (state === "missing") {
    return (
      <Card className="flex items-center gap-4 p-6">
        <FileText className="size-6 shrink-0 text-faint" aria-hidden />
        <div role="status">
          <p className="font-display text-xl">No postmortem yet</p>
          <p className="mt-1 text-sm text-mute">
            {incidentStatus === "RESOLVED"
              ? "Drafting now. This page checks every few seconds."
              : "A draft is generated automatically once the incident is resolved."}
          </p>
        </div>
      </Card>
    );
  }

  if (state === "error" || !pm) {
    return (
      <Card className="flex items-center justify-between gap-4 border-clay/30 p-6">
        <p className="text-sm text-clay">{errorText}</p>
        <Button variant="danger" onClick={load}>
          Retry
        </Button>
      </Card>
    );
  }

  const timeline = toTimeline(pm.timeline);

  return (
    <Card>
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 p-6">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="font-display text-2xl tracking-tight">Postmortem</h2>
          <PostmortemBadge status={pm.status} />
          {pm.reviewed_by && <span className="font-mono text-xs text-faint">signed off by {pm.reviewed_by}</span>}
          {pm.model_used && <span className="font-mono text-xs text-faint">drafted by {pm.model_used}</span>}
        </div>
        <div className="flex gap-2">
          {editing ? (
            <>
              <Button variant="ghost" onClick={() => setEditing(false)}>
                <X className="size-4" aria-hidden />
                Cancel
              </Button>
              <Button onClick={save} loading={saving}>
                <Check className="size-4" aria-hidden />
                Save
              </Button>
            </>
          ) : (
            <>
              {canModify && (
                <Button variant="ghost" onClick={startEdit}>
                  <Pencil className="size-4" aria-hidden />
                  Edit
                </Button>
              )}
              {canReview && pm.status === "DRAFT" && (
                <Button onClick={review} loading={reviewing}>
                  <Check className="size-4" aria-hidden />
                  Sign off
                </Button>
              )}
            </>
          )}
        </div>
      </header>

      {editing && draft && (
        <p className="border-b border-white/10 bg-bronze/5 px-6 py-3 text-xs text-bronze">
          Editing a signed-off postmortem resets it to draft, because the sign-off applied to the old text.
        </p>
      )}

      <div className="divide-y divide-white/10">
        <TextField label="Summary" value={pm.summary} editing={editing} draft={draft?.summary} onChange={(v) => setDraft((d) => d && { ...d, summary: v })} />
        <TextField label="Root cause" value={pm.root_cause} editing={editing} draft={draft?.root_cause} onChange={(v) => setDraft((d) => d && { ...d, root_cause: v })} />
        <TextField label="Impact" value={pm.impact} editing={editing} draft={draft?.impact} onChange={(v) => setDraft((d) => d && { ...d, impact: v })} />

        {timeline.length > 0 && (
          <section className="p-6">
            <Eyebrow>Timeline</Eyebrow>
            <ol className="mt-4 space-y-4 border-l border-white/10 pl-5">
              {timeline.map((entry, i) => (
                <li key={i} className="relative text-sm leading-relaxed">
                  <span className="absolute -left-[1.55rem] top-1.5 size-2 rounded-full border border-bronze bg-ink-900" aria-hidden />
                  {entry.time && <p className="font-mono text-xs text-faint">{entry.time}</p>}
                  <p>{entry.event}</p>
                </li>
              ))}
            </ol>
          </section>
        )}

        <section className="p-6">
          <Eyebrow>Action items</Eyebrow>
          {editing && draft && actionsEditable ? (
            <>
              <textarea
                aria-label="Action items, one per line"
                rows={5}
                value={draft.actions}
                onChange={(e) => setDraft({ ...draft, actions: e.target.value })}
                className="mt-3 w-full rounded-xl border border-white/10 bg-ink-900 p-3 text-sm leading-relaxed focus:border-bronze/60 focus:outline-none"
              />
              <p className="mt-1 text-xs text-faint">One action per line.</p>
            </>
          ) : (
            <ul className="mt-3 space-y-2">
              {toLines(pm.action_items).map((item, i) => (
                <li key={i} className="flex gap-3 text-sm leading-relaxed">
                  <span className="mt-2 size-1.5 shrink-0 rounded-full bg-bronze" aria-hidden />
                  {item}
                </li>
              ))}
              {toLines(pm.action_items).length === 0 && <li className="text-sm text-faint">None recorded.</li>}
            </ul>
          )}
        </section>
      </div>
    </Card>
  );
}

function TextField({
  label,
  value,
  editing,
  draft,
  onChange,
}: {
  label: string;
  value: string | null | undefined;
  editing: boolean;
  draft: string | undefined;
  onChange: (v: string) => void;
}) {
  return (
    <section className="p-6">
      <Eyebrow>{label}</Eyebrow>
      {editing ? (
        <textarea
          aria-label={label}
          rows={4}
          value={draft ?? ""}
          onChange={(e) => onChange(e.target.value)}
          className="mt-3 w-full rounded-xl border border-white/10 bg-ink-900 p-3 text-sm leading-relaxed focus:border-bronze/60 focus:outline-none"
        />
      ) : (
        <p className="mt-3 whitespace-pre-line text-sm leading-relaxed">{value || <span className="text-faint">Not provided.</span>}</p>
      )}
    </section>
  );
}