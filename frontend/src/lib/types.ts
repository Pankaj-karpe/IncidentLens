export type Role = "viewer" | "developer" | "devops" | "sre";
export type IncidentStatus = "OPEN" | "TRIAGED" | "RESOLVED";
export type AiStatus = "PENDING" | "OK" | "FAILED" | "FALLBACK";
export type PostmortemStatus = "DRAFT" | "REVIEWED";

export interface User {
  id: number;
  username: string;
  role: Role;
  is_active: boolean;
}

export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
}

export interface Incident {
  id: number;
  fingerprint: string;
  title: string;
  service: string | null;
  severity: string | null;
  status: IncidentStatus;
  ai_status: AiStatus;
  ai_summary: string | null;
  ai_root_cause: string | null;
  ai_next_steps: unknown;
  started_at: string;
  created_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
}

export interface Postmortem {
  summary?: string | null;
  timeline?: unknown;
  root_cause?: string | null;
  impact?: string | null;
  action_items?: unknown;
  status: PostmortemStatus;
  model_used?: string | null;
  reviewed_by?: string | null;
}

export interface PostmortemUpdate {
  summary?: string;
  root_cause?: string;
  impact?: string;
  action_items?: string[];
}