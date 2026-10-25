"use client";

import { AnimatePresence, motion } from "framer-motion";
import { Loader2 } from "lucide-react";
import { useEffect, useRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import type { AiStatus, IncidentStatus, PostmortemStatus, Role } from "@/lib/types";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div
      className={cx(
        "rounded-2xl border border-white/10 bg-ink-850/80 shadow-[inset_0_1px_0_rgba(255,255,255,0.04),0_1px_2px_rgba(0,0,0,0.4)]",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function Eyebrow({ children }: { children: ReactNode }) {
  return <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-faint">{children}</p>;
}

type Variant = "primary" | "ghost" | "danger";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-bronze text-ink-950 hover:bg-[#d6b06a] shadow-[inset_0_1px_0_rgba(255,255,255,0.35)]",
  ghost: "border border-white/10 bg-white/[0.03] text-bone hover:border-white/25 hover:bg-white/[0.07]",
  danger: "border border-clay/40 bg-clay/10 text-clay hover:bg-clay/20",
};

export function Button({
  variant = "primary",
  loading = false,
  className,
  children,
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; loading?: boolean }) {
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium",
        "transition-all duration-300 ease-expo hover:scale-[1.02] active:scale-[0.98]",
        "disabled:pointer-events-none disabled:opacity-50",
        VARIANTS[variant],
        className,
      )}
    >
      {loading && <Loader2 className="size-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={cx(
        "animate-shimmer rounded-lg bg-ink-800 bg-[linear-gradient(90deg,transparent,rgba(255,255,255,0.06),transparent)] bg-[length:200%_100%]",
        className,
      )}
    />
  );
}

function Pill({ tone, children, pulse }: { tone: string; children: ReactNode; pulse?: boolean }) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-mono text-[11px] uppercase tracking-wider",
        tone,
      )}
    >
      <span className={cx("size-1.5 rounded-full bg-current", pulse && "animate-pulse")} aria-hidden />
      {children}
    </span>
  );
}

const SEVERITY_TONE: Record<string, string> = {
  critical: "border-clay/40 bg-clay/10 text-clay",
  warning: "border-bronze/40 bg-bronze/10 text-bronze",
  info: "border-sage/40 bg-sage/10 text-sage",
};

export function severityColor(severity: string | null): string {
  const key = (severity ?? "").toLowerCase();
  return key === "critical" ? "bg-clay" : key === "warning" ? "bg-bronze" : key === "info" ? "bg-sage" : "bg-faint";
}

export function SeverityBadge({ severity }: { severity: string | null }) {
  const key = (severity ?? "").toLowerCase();
  return <Pill tone={SEVERITY_TONE[key] ?? "border-white/10 bg-white/5 text-mute"}>{severity ?? "unknown"}</Pill>;
}

const STATUS_TONE: Record<IncidentStatus, string> = {
  OPEN: "border-clay/40 bg-clay/10 text-clay",
  TRIAGED: "border-bronze/40 bg-bronze/10 text-bronze",
  RESOLVED: "border-sage/40 bg-sage/10 text-sage",
};

export function StatusBadge({ status }: { status: IncidentStatus }) {
  return (
    <Pill tone={STATUS_TONE[status]} pulse={status === "OPEN"}>
      {status}
    </Pill>
  );
}

const AI_LABEL: Record<AiStatus, string> = {
  PENDING: "AI analysing",
  OK: "AI analysed",
  FAILED: "AI failed",
  FALLBACK: "Raw context",
};

const AI_TONE: Record<AiStatus, string> = {
  PENDING: "border-white/15 bg-white/5 text-mute",
  OK: "border-sage/40 bg-sage/10 text-sage",
  FAILED: "border-clay/40 bg-clay/10 text-clay",
  FALLBACK: "border-bronze/40 bg-bronze/10 text-bronze",
};

export function AiBadge({ status }: { status: AiStatus }) {
  return (
    <Pill tone={AI_TONE[status]} pulse={status === "PENDING"}>
      {AI_LABEL[status]}
    </Pill>
  );
}

export function PostmortemBadge({ status }: { status: PostmortemStatus }) {
  return (
    <Pill tone={status === "REVIEWED" ? "border-sage/40 bg-sage/10 text-sage" : "border-bronze/40 bg-bronze/10 text-bronze"}>
      {status}
    </Pill>
  );
}

export function RolePill({ role }: { role: Role }) {
  return (
    <span className="rounded-md border border-white/10 bg-white/5 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-mute">
      {role}
    </span>
  );
}

export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  loading,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body: string;
  confirmLabel: string;
  loading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    confirmRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
        >
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onCancel} aria-hidden />
          <motion.div
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="dialog-title"
            className="relative w-full max-w-md rounded-2xl border border-white/10 bg-ink-850/95 p-6 backdrop-blur-md"
            initial={{ opacity: 0, y: 16, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.98 }}
            transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
          >
            <h2 id="dialog-title" className="font-display text-2xl">
              {title}
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-mute">{body}</p>
            <div className="mt-6 flex justify-end gap-2">
              <Button variant="ghost" onClick={onCancel}>
                Cancel
              </Button>
              <button
                ref={confirmRef}
                onClick={onConfirm}
                disabled={loading}
                className="inline-flex items-center gap-2 rounded-xl bg-bronze px-4 py-2.5 text-sm font-medium text-ink-950 transition-all duration-300 ease-expo hover:scale-[1.02] active:scale-[0.98] disabled:opacity-50"
              >
                {loading && <Loader2 className="size-4 animate-spin" aria-hidden />}
                {confirmLabel}
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}