export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
}

export function timeAgo(iso: string): string {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export function duration(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return "—";
  const minutes = Math.round(ms / 60000);
  if (minutes < 1) return "<1m";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

function describe(item: unknown): string {
  if (typeof item === "string") return item;
  if (item && typeof item === "object") {
    const o = item as Record<string, unknown>;
    const text = o.step ?? o.action ?? o.description ?? o.event ?? o.title;
    if (typeof text === "string") return text;
    return JSON.stringify(item);
  }
  return String(item);
}

export function toLines(value: unknown): string[] {
  if (!value) return [];
  if (Array.isArray(value)) return value.map(describe);
  if (typeof value === "object") return Object.entries(value as Record<string, unknown>).map(([k, v]) => `${k}: ${describe(v)}`);
  return [String(value)];
}

export function isStringList(value: unknown): value is string[] {
  return value == null || (Array.isArray(value) && value.every((v) => typeof v === "string"));
}

export function toTimeline(value: unknown): { time?: string; event: string }[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => {
    if (item && typeof item === "object") {
      const o = item as Record<string, unknown>;
      return { time: typeof o.time === "string" ? o.time : undefined, event: describe(o.event ?? item) };
    }
    return { event: describe(item) };
  });
}