"use client";

import { motion } from "framer-motion";
import { ArrowUpRight } from "lucide-react";
import Link from "next/link";
import { timeAgo } from "@/lib/format";
import type { Incident } from "@/lib/types";
import { AiBadge, severityColor, SeverityBadge, StatusBadge, cx } from "./ui";

export function IncidentRow({ incident, index }: { incident: Incident; index: number }) {
  return (
    <motion.li
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1], delay: Math.min(index, 10) * 0.04 }}
    >
      <Link
        href={`/incidents/${incident.id}`}
        className="group relative flex flex-col gap-3 overflow-hidden rounded-2xl border border-white/10 bg-ink-850/80 p-5 pl-6 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] transition-all duration-300 ease-expo hover:scale-[1.01] hover:border-white/25 hover:bg-ink-800/80 sm:flex-row sm:items-center sm:justify-between"
      >
        <span className={cx("absolute inset-y-0 left-0 w-1", severityColor(incident.severity))} aria-hidden />
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={incident.status} />
            <SeverityBadge severity={incident.severity} />
            <AiBadge status={incident.ai_status} />
          </div>
          <h3 className="mt-3 truncate font-display text-xl tracking-tight">{incident.title}</h3>
          <p className="mt-1 font-mono text-xs text-faint">
            {incident.service ?? "unknown service"} · #{incident.id} · {timeAgo(incident.started_at)}
          </p>
          {incident.ai_summary && <p className="mt-3 line-clamp-2 max-w-2xl text-sm leading-relaxed text-mute">{incident.ai_summary}</p>}
        </div>
        <ArrowUpRight
          className="hidden size-5 shrink-0 text-faint transition-all duration-300 ease-expo group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-bronze sm:block"
          aria-hidden
        />
      </Link>
    </motion.li>
  );
}