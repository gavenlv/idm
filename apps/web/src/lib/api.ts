/**
 * IDM API Client (axios + react-query).
 */
import axios from "axios";

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE ?? "/api",
  timeout: 30_000,
  headers: { "Content-Type": "application/json" },
});

// === 类型定义 ===
export interface Service {
  id: string;
  name: string;
  type: string;
  description: string | null;
  tier: "critical" | "important" | "normal";
  status: "active" | "deprecated" | "archived";
  config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface TableAsset {
  id: string;
  schema_id: string;
  name: string;
  fqn: string;
  asset_type: string;
  tier: string;
  status: string;
  description: string | null;
  column_count: number;
  row_count: number | null;
  size_bytes: number | null;
  extra: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface AssetListResponse {
  items: TableAsset[];
  total: number;
  limit: number;
  offset: number;
}

export interface Suggestion {
  id: string;
  suggestion_type: string;
  target_type: string;
  target_id: string;
  payload: Record<string, unknown>;
  rationale: string | null;
  confidence: number;
  model: string;
  skill: string;
  use_case_id: string | null;
  status: "pending" | "approved" | "rejected" | "auto_approved" | "expired";
  created_at: string;
  reviewed_at: string | null;
  review_note: string | null;
}

// === 调用 ===
export const AssetsApi = {
  list: (params?: { q?: string; tier?: string; service?: string; limit?: number; offset?: number }) =>
    api.get<AssetListResponse>("/v1/assets", { params }).then((r) => r.data),
  get: (id: string) => api.get<TableAsset>(`/v1/assets/${id}`).then((r) => r.data),
  create: (payload: Partial<TableAsset>) => api.post<TableAsset>("/v1/assets", payload).then((r) => r.data),
};

export const ServicesApi = {
  list: () => api.get<Service[]>("/v1/services").then((r) => r.data),
  create: (payload: Partial<Service>) => api.post<Service>("/v1/services", payload).then((r) => r.data),
};

export const SuggestionsApi = {
  list: (params?: { status?: string; suggestion_type?: string; limit?: number; offset?: number }) =>
    api
      .get<{ items: Suggestion[]; total: number }>("/v1/suggestions", { params })
      .then((r) => r.data),
  approve: (id: string, review_note?: string) =>
    api.post<Suggestion>(`/v1/suggestions/${id}/approve`, { review_note }).then((r) => r.data),
  reject: (id: string, review_note?: string) =>
    api.post<Suggestion>(`/v1/suggestions/${id}/reject`, { review_note }).then((r) => r.data),
};
