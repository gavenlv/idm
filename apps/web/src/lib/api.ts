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
  list: (params?: { q?: string; tier?: string; service?: string; status?: string; limit?: number; offset?: number }) =>
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

// === Owner ===
export interface AssetOwner {
  id: string;
  table_id: string;
  table_fqn: string | null;
  user_email: string;
  user_name: string | null;
  team: string | null;
  role: "owner" | "steward" | "consumer" | string;
  source: string;
  confidence: number;
  is_verified: boolean;
  created_at: string;
  updated_at: string;
}

export const OwnersApi = {
  list: (params?: { team?: string; service?: string; role?: string; verified?: boolean; limit?: number; offset?: number }) =>
    api
      .get<{ items: AssetOwner[]; total: number }>("/v1/owners", { params })
      .then((r) => r.data),
  verify: (id: string) => api.post<AssetOwner>(`/v1/owners/${id}/verify`).then((r) => r.data),
  remove: (id: string) => api.delete<void>(`/v1/owners/${id}`).then((r) => r.data),
};

// === Feedback ===
export interface FeedbackRecord {
  id: string;
  skill: string;
  case_key: string;
  accepted: boolean;
  created_at: string;
}

export interface FeedbackIn {
  skill: string;
  case_key: string;
  pred?: Record<string, unknown>;
  accepted: boolean;
  reason?: string;
  new_payload?: Record<string, unknown>;
  user_email?: string;
}

export interface FewShotBuildResp {
  skill: string;
  count: number;
  out_path: string;
}

export const FeedbackApi = {
  list: (params?: { skill?: string; accepted?: boolean; limit?: number }) =>
    api.get<FeedbackRecord[]>("/v1/feedback", { params }).then((r) => r.data),
  submit: (payload: FeedbackIn) =>
    api.post<FeedbackRecord>("/v1/feedback", payload).then((r) => r.data),
  buildFewShots: (skill: string, k = 5, out_path?: string) =>
    api
      .post<FewShotBuildResp>(`/v1/feedback/few-shots/${skill}`, null, {
        params: { k, out_path },
      })
      .then((r) => r.data),
  previewFewShots: (skill: string, k = 5) =>
    api
      .get<Array<Record<string, unknown>>>(`/v1/feedback/few-shots/${skill}/preview`, {
        params: { k },
      })
      .then((r) => r.data),
};

// === Health (for dashboard widgets) ===
export interface DashboardSummary {
  assets: {
    total: number;
    by_tier: Record<string, number>;
    with_pii: number;
  };
  suggestions: {
    pending: number;
    approved_24h: number;
    rejected_24h: number;
  };
  health: {
    status: "ok" | "degraded" | "down";
    checks: Record<string, string>;
  };
  owners: {
    total: number;
    verified: number;
    unverified: number;
  };
  top_pending: Array<{ id: string; suggestion_type: string; target_id: string; confidence: number; created_at: string }>;
}

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

// === Tags ===
export type TagCategory = "pii" | "tier" | "domain" | "status" | "custom";

export interface Tag {
  id: string;
  name: string;
  category: TagCategory | string;
  color: string;
  description: string | null;
  asset_count: number;
  created_at: string;
  updated_at: string;
}

export interface TagCreate {
  name: string;
  category: TagCategory;
  color: string;
  description?: string;
}

export const TagsApi = {
  list: (params?: { q?: string; category?: string; limit?: number; offset?: number }) =>
    api.get<{ items: Tag[]; total: number }>("/v1/tags", { params }).then((r) => r.data),
  create: (payload: TagCreate) =>
    api.post<Tag>("/v1/tags", payload).then((r) => r.data),
  update: (id: string, payload: { color?: string; description?: string }) =>
    api.patch<Tag>(`/v1/tags/${id}`, payload).then((r) => r.data),
  remove: (id: string) => api.delete<void>(`/v1/tags/${id}`).then((r) => r.data),
  bind: (tableId: string, tagId: string, source: "manual" | "ai_inferred" | "policy" = "manual") =>
    api.post<{ bound: boolean }>(`/v1/tags/assets/${tableId}/bind`, { tag_id: tagId, source }).then((r) => r.data),
  unbind: (tableId: string, tagId: string) =>
    api.post<{ bound: boolean }>(`/v1/tags/assets/${tableId}/unbind`, { tag_id: tagId }).then((r) => r.data),
  listForAsset: (tableId: string) =>
    api.get<Tag[]>(`/v1/tags/assets/${tableId}`).then((r) => r.data),
};

// === Glossary ===
export interface GlossaryTerm {
  id: string;
  name: string;
  definition: string;
  domain: string | null;
  owner_team: string | null;
  synonyms: string[];
  asset_count: number;
  created_at: string;
  updated_at: string;
}

export const GlossaryApi = {
  list: (params?: { q?: string; domain?: string; limit?: number; offset?: number }) =>
    api.get<{ items: GlossaryTerm[]; total: number }>("/v1/glossary", { params }).then((r) => r.data),
  create: (payload: {
    name: string;
    definition: string;
    domain?: string;
    owner_team?: string;
    synonyms?: string[];
  }) => api.post<GlossaryTerm>("/v1/glossary", payload).then((r) => r.data),
  update: (id: string, payload: Partial<{ definition: string; domain: string; owner_team: string; synonyms: string[] }>) =>
    api.patch<GlossaryTerm>(`/v1/glossary/${id}`, payload).then((r) => r.data),
  remove: (id: string) => api.delete<void>(`/v1/glossary/${id}`).then((r) => r.data),
  bind: (tableId: string, termId: string, confidence = 1, source = "manual") =>
    api.post<{ bound: boolean }>(`/v1/glossary/assets/${tableId}/bind`, { term_id: termId, confidence, source }).then((r) => r.data),
  unbind: (tableId: string, termId: string) =>
    api.post<{ bound: boolean }>(`/v1/glossary/assets/${tableId}/unbind`, { term_id: termId }).then((r) => r.data),
  listForAsset: (tableId: string) =>
    api.get<GlossaryTerm[]>(`/v1/glossary/assets/${tableId}`).then((r) => r.data),
};

// === Use Cases ===
export interface UseCaseSummary {
  id: string;
  version: number;
  description: string;
  owners: string[];
  sources_count: number;
  analysis_count: number;
  path: string;
  updated_at: string | null;
}

export interface UseCaseRead extends UseCaseSummary {
  raw: string;
  spec: Record<string, unknown>;
}

export const UseCasesApi = {
  list: (q?: string) =>
    api.get<{ items: UseCaseSummary[]; total: number }>("/v1/use-cases", { params: q ? { q } : {} }).then((r) => r.data),
  get: (id: string) =>
    api.get<UseCaseRead>(`/v1/use-cases/${id}`).then((r) => r.data),
  save: (id: string, raw: string, message?: string) =>
    api.put<UseCaseRead>(`/v1/use-cases/${id}`, { raw, message }).then((r) => r.data),
  remove: (id: string) => api.delete<void>(`/v1/use-cases/${id}`).then((r) => r.data),
};

// === Search ===
export type SearchKind = "asset" | "owner" | "tag" | "glossary" | "use_case" | "suggestion";

export interface SearchHit {
  kind: SearchKind;
  id: string;
  title: string;
  subtitle: string | null;
  url: string;
  score: number;
  extra: Record<string, unknown>;
}

export const SearchApi = {
  query: (q: string, limit = 20) =>
    api
      .get<{ query: string; total: number; items: SearchHit[] }>("/v1/search", { params: { q, limit } })
      .then((r) => r.data),
};
