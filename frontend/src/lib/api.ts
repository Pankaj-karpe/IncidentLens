import type { Incident, Postmortem, PostmortemUpdate, TokenResponse, User } from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8001";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

type Hooks = { getToken: () => string | null; onUnauthorized: () => void };
let hooks: Hooks = { getToken: () => null, onUnauthorized: () => {} };

export function configureApi(next: Hooks) {
  hooks = next;
}

function messageFrom(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) =>
          item && typeof item === "object" && "msg" in item
            ? String((item as { msg: unknown }).msg)
            : "Invalid input",
        )
        .join("; ");
    }
  }
  return fallback;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = hooks.getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && typeof init.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, headers, cache: "no-store" });
  } catch {
    throw new ApiError(0, "Cannot reach the IncidentLens API. Is it running?");
  }

  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      body = null;
    }
    if (res.status === 401 && path !== "/auth/login") hooks.onUnauthorized();
    throw new ApiError(res.status, messageFrom(body, res.statusText || "Request failed"));
  }
  return (await res.json()) as T;
}

export const api = {
  login(username: string, password: string) {
    const body = new URLSearchParams({ username, password });
    return request<TokenResponse>("/auth/login", { method: "POST", body });
  },
  me: () => request<User>("/auth/me"),
  listIncidents: () => request<Incident[]>("/incidents?limit=200"),
  getIncident: (id: number | string) => request<Incident>(`/incidents/${id}`),
  resolve: (id: number | string) => request<Incident>(`/incidents/${id}/resolve`, { method: "POST" }),
  getPostmortem: (id: number | string) => request<Postmortem>(`/incidents/${id}/postmortem`),
  editPostmortem: (id: number | string, patch: PostmortemUpdate) =>
    request<Postmortem>(`/incidents/${id}/postmortem`, { method: "PATCH", body: JSON.stringify(patch) }),
  reviewPostmortem: (id: number | string) =>
    request<Postmortem>(`/incidents/${id}/postmortem/review`, { method: "POST" }),
};