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

export interface ColumnAsset {
  id: string;
  table_id: string;
  name: string;
  ordinal: number;
  data_type: string;
  nullable: boolean;
  is_primary_key: boolean;
  is_partition_key: boolean;
  description: string | null;
  pii_class: string;
  pii_confidence: number;
  pii_source: string | null;
  sample_values: unknown[];
  null_ratio: number;
  distinct_count: number | null;
  created_at: string;
  updated_at: string;
}

export interface AssetPiiSummary {
  table_id: string;
  pii_columns: number;
  high_risk_columns: number;
  by_class: Record<string, number>;
  samples: Array<{ column_name: string; pii_class: string; confidence: number }>;
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
  listColumns: (id: string, params?: { pii_only?: boolean }) =>
    api
      .get<{ items: ColumnAsset[]; total: number }>(`/v1/assets/${id}/columns`, { params })
      .then((r) => r.data),
  piiSummary: (id: string) =>
    api.get<AssetPiiSummary>(`/v1/assets/${id}/pii-summary`).then((r) => r.data),
  lineage: (id: string, depth = 3) =>
    api
      .get<{
        center_fqn: string;
        center_id: string;
        upstream: Array<any>;
        downstream: Array<any>;
        nodes: Array<{ id: string; fqn: string; asset_type: string; tier: string; name: string }>;
        edges: Array<any>;
      }>(`/v1/assets/${id}/lineage`, { params: { depth } })
      .then((r) => r.data),
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

export interface SkillMeta {
  name: string;
  version: number;
  agent: string;
}

export interface SkillRunOutput {
  items: Array<Record<string, unknown>>;
  summary: Record<string, unknown>;
  artifacts: string[];
}

export interface SkillRunResp {
  ok: boolean;
  skill: string;
  output: SkillRunOutput;
  error: string | null;
  duration_ms: number;
  trace: Array<Record<string, unknown>>;
}

export interface McpHealth {
  checks: Record<string, { status: string; [k: string]: unknown }>;
  all_ok: boolean;
}

export const SkillsApi = {
  list: () => api.get<{ items: SkillMeta[] }>("/v1/skills").then((r) => r.data),
  run: (name: string, inputs: Record<string, unknown> = {}, opts?: { use_case_id?: string; dry_run?: boolean }) =>
    api
      .post<SkillRunResp>("/v1/skills/run", {
        name,
        inputs,
        use_case_id: opts?.use_case_id,
        dry_run: opts?.dry_run ?? false,
      })
      .then((r) => r.data),
  mcpHealth: () => api.get<McpHealth>("/v1/skills/mcp/health").then((r) => r.data),
};

// === Skill 输入模板 (前端引导, 非强校验) ===
// 后端 Skill 会自己解析 inputs; 这里只给常用参数默认值, 减少填错.
export const SKILL_INPUT_HINTS: Record<string, { description: string; example: Record<string, unknown> }> = {
  discover_clickhouse_assets: {
    description: "扫描 ClickHouse 库/表/列, 写入知识图谱 (table_assets + column_assets).",
    example: { database: "shop" },
  },
  infer_table_description: {
    description: "对每张表用 LLM 推断描述, 结果入 ai_suggestion 待人工审核.",
    example: { sample_rows: 3 },
  },
  classify_pii_columns: {
    description: "对每列用 LLM 推断 PII 分类 (email/phone/id_card/...), 写入 ai_suggestion.",
    example: { min_confidence: 0.6 },
  },
  parse_dbt_manifest: {
    description: "解析 dbt manifest.json, 把 model/seed/snapshot/source 写入 KG (asset_type=dbt_model).",
    example: { manifest_path: "/abs/path/to/manifest.json" },
  },
};
