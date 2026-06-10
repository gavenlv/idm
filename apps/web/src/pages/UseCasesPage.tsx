/**
 * UseCasesPage — Use Case YAML editor (GitOps-friendly) + 业务级/系统级 Trigger & Re-scan.
 *
 * Layout:
 *  - Left: list of use cases (with badge counts)
 *  - Right: YAML editor + parsed spec preview + analysis flow diagram (text)
 *  - Toolbar: New / Save / Delete / Reload + Trigger / Re-scan (业务级) + 系统级入口
 *  - Result panel: 最近一次 trigger / rescan 的结果 (ok / fail / items / stage_results)
 *
 * NOTE: Editor is a simple monospace <textarea> with line numbers gutter.
 * M3+ will swap to Monaco/CodeMirror for syntax highlighting.
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Button,
  Card,
  Drawer,
  Input,
  Stat,
  Stats,
  Status,
  Tag,
} from "../ui";
import {
  UseCasesApi,
  UseCaseTriggerApi,
  type UseCaseSummary,
  type UseCaseTriggerResponse,
  type RescanAssetResponse,
} from "../lib/api";

const TEMPLATE_BASIC = `# Use Case — 最小可用模板
# 参考: docs/design/use-case-spec.md
id: my-first-use-case
version: 1
description: 一句话说明这个 use case 是干什么的
owners:
  - alice@example.com

sources:
  - id: ch-prod
    type: clickhouse
    mcp: clickhouse
    config:
      host: ch.example.com
      database: shop

analysis:
  - task: discover_assets
    agent: schema
    uses: [ch-prod]
  - task: generate_docs
    agent: doc
    depends_on: [discover_assets]
  - task: detect_anomalies
    agent: quality
    schedule: "0 9 * * *"
    uses: [ch-prod]
    depends_on: [discover_assets]
`;

const AGENT_COLORS: Record<string, string> = {
  schema: "#0b7ea4",
  lineage: "#2e66f0",
  doc: "#2e8540",
  owner: "#7159f3",
  pii: "#cf1124",
  quality: "#d97706",
  glossary: "#1d44ad",
  custom: "#697077",
};

const TASK_LABELS: Record<string, string> = {
  discover_assets: "Discover",
  extract_lineage: "Lineage",
  generate_docs: "Docs",
  suggest_owners: "Owners",
  classify_pii: "PII",
  detect_anomalies: "Quality",
  enrich_glossary: "Glossary",
  custom: "Custom",
};

function LineNumberedEditor(props: {
  value: string;
  onChange: (v: string) => void;
  rows?: number;
  readOnly?: boolean;
}) {
  const { value, onChange, rows = 26, readOnly } = props;
  const lineCount = useMemo(() => value.split("\n").length, [value]);
  return (
    <div
      className="idm-yaml-editor"
      style={{
        display: "grid",
        gridTemplateColumns: "44px 1fr",
        border: "1px solid var(--idm-border)",
        background: "var(--idm-bg-elevated)",
        fontFamily: "var(--idm-mono-font)",
        fontSize: 12,
        lineHeight: 1.55,
        maxHeight: 540,
        overflow: "auto",
      }}
    >
      <div
        style={{
          padding: "8px 6px",
          background: "var(--idm-gray-100)",
          color: "var(--idm-text-muted)",
          textAlign: "right",
          userSelect: "none",
        }}
      >
        {Array.from({ length: lineCount }, (_, i) => (
          <div key={i}>{i + 1}</div>
        ))}
      </div>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={rows}
        readOnly={readOnly}
        spellCheck={false}
        style={{
          border: 0,
          outline: "none",
          padding: "8px 10px",
          background: "transparent",
          color: "var(--idm-text)",
          fontFamily: "var(--idm-mono-font)",
          fontSize: 12,
          lineHeight: 1.55,
          resize: "none",
          width: "100%",
          minHeight: lineCount * 21 + 16,
        }}
      />
    </div>
  );
}

// === Trigger / Re-scan 结果展示 ===

function TriggerResultView({
  resp,
  showOutput,
  t,
}: {
  resp: UseCaseTriggerResponse;
  showOutput: boolean;
  t: (k: string) => string;
}) {
  const items = resp.output?.items ?? [];
  const summary = (resp.output?.summary ?? {}) as Record<string, unknown>;
  const stageResults = (summary.stage_results ?? {}) as Record<
    string,
    { ok: boolean; error?: string; stage?: number }
  >;
  const stagesExecuted = (summary.stages_executed ?? []) as string[];
  const stageCoverage = (summary.stage_coverage ?? {}) as Record<string, string[]>;
  const coveredStages = (summary.covered_stages ?? []) as number[];

  return (
    <div className="idm-flex-col idm-gap-3">
      {/* Header: status + duration + items count */}
      <div
        className="idm-flex idm-gap-3 idm-items-center idm-flex-wrap"
        style={{ fontSize: 12 }}
      >
        <Status kind={resp.ok ? "ok" : "fail"}>
          {resp.ok ? t("use_cases.trigger.okBadge") : t("use_cases.trigger.failBadge")}
        </Status>
        <span className="idm-text-muted">
          use_case: <code style={{ fontFamily: "var(--idm-mono-font)" }}>{resp.use_case_id}</code>
        </span>
        {resp.stage != null && (
          <span className="idm-text-muted">
            stage: <strong>{resp.stage}</strong>
          </span>
        )}
        <span className="idm-text-muted">
          {t("use_cases.trigger.itemsCount")}: <strong>{items.length}</strong>
        </span>
        <span className="idm-text-muted">
          {t("use_cases.trigger.duration")}: <strong>{resp.duration_ms} ms</strong>
        </span>
        {Array.isArray(summary.coverage_pct) === false && typeof summary.coverage_pct === "number" && (
          <span className="idm-text-muted">
            coverage: <strong>{summary.coverage_pct}%</strong> ({coveredStages.length}/6)
          </span>
        )}
        {resp.error && (
          <span style={{ color: "var(--idm-red-500, #cf1124)" }}>
            error: {resp.error}
          </span>
        )}
      </div>

      {/* Stages executed list */}
      {stagesExecuted.length > 0 && (
        <div>
          <div
            className="idm-text-muted"
            style={{ fontSize: 11, marginBottom: 4 }}
          >
            {t("use_cases.trigger.stagesExecuted")} ({stagesExecuted.length})
          </div>
          <div className="idm-flex idm-gap-1 idm-flex-wrap">
            {stagesExecuted.map((s) => {
              const sr = stageResults[s];
              return (
                <Tag
                  key={s}
                  color={sr?.ok ? "#2e8540" : "#cf1124"}
                  solid={!sr?.ok}
                >
                  {s} {sr?.ok ? "✓" : "✗"}
                </Tag>
              );
            })}
          </div>
        </div>
      )}

      {/* Stage coverage (1..6) */}
      {Object.keys(stageCoverage).length > 0 && (
        <div className="idm-flex idm-gap-1" style={{ fontSize: 11 }}>
          {[1, 2, 3, 4, 5, 6].map((n) => {
            const skills = stageCoverage[n] ?? [];
            return (
              <span
                key={n}
                style={{
                  padding: "2px 6px",
                  border: "1px solid var(--idm-border)",
                  background: skills.length
                    ? "var(--idm-gray-100)"
                    : "transparent",
                  color: skills.length
                    ? "var(--idm-text)"
                    : "var(--idm-text-muted)",
                  fontFamily: "var(--idm-mono-font)",
                }}
              >
                S{n}: {skills.length}
              </span>
            );
          })}
        </div>
      )}

      {/* Optional full output */}
      {showOutput && (
        <pre
          style={{
            background: "var(--idm-gray-100)",
            color: "var(--idm-text)",
            padding: 12,
            fontSize: 11,
            fontFamily: "var(--idm-mono-font)",
            maxHeight: 320,
            overflow: "auto",
            margin: 0,
          }}
        >
          {JSON.stringify(resp.output, null, 2)}
        </pre>
      )}
    </div>
  );
}

function RescanResultView({
  resp,
  showOutput,
  t,
}: {
  resp: RescanAssetResponse;
  showOutput: boolean;
  t: (k: string) => string;
}) {
  const blocks =
    (resp.output?.blocks as Array<Record<string, unknown>> | undefined) ?? [];

  return (
    <div className="idm-flex-col idm-gap-3">
      <div
        className="idm-flex idm-gap-3 idm-items-center idm-flex-wrap"
        style={{ fontSize: 12 }}
      >
        <Status kind={resp.ok ? "ok" : "fail"}>
          {resp.ok ? t("use_cases.trigger.okBadge") : t("use_cases.trigger.failBadge")}
        </Status>
        <span className="idm-text-muted">
          source_type: <code style={{ fontFamily: "var(--idm-mono-font)" }}>{resp.source_type}</code>
        </span>
        <span className="idm-text-muted">
          {t("use_cases.trigger.itemsCount")}: <strong>{resp.items_count}</strong>
        </span>
        <span className="idm-text-muted">
          {t("use_cases.trigger.duration")}: <strong>{resp.duration_ms} ms</strong>
        </span>
        {Object.keys(resp.by_subtype ?? {}).length > 0 && (
          <span className="idm-text-muted">
            {t("use_cases.trigger.bySubtype")}:{" "}
            {Object.entries(resp.by_subtype)
              .map(([k, v]) => `${k}=${v}`)
              .join(", ")}
          </span>
        )}
        {resp.error && (
          <span style={{ color: "var(--idm-red-500, #cf1124)" }}>
            error: {resp.error}
          </span>
        )}
      </div>

      {blocks.length > 0 && (
        <div
          className="idm-flex idm-gap-1 idm-flex-wrap"
          style={{ fontSize: 11 }}
        >
          {blocks.map((b, i) => {
            const ok = b.ok as boolean | undefined;
            const label =
              (b.stage ? `S${b.stage} ` : "") +
              (b.bucket ? `gcs://${b.bucket}` :
                b.database ? `ch://${b.database}` :
                b.service_name ? `superset://${b.service_name}` :
                `#${i}`);
            return (
              <Tag key={i} color={ok ? "#2e8540" : "#cf1124"} solid={!ok}>
                {label} {ok ? "✓" : "✗"}
              </Tag>
            );
          })}
        </div>
      )}

      {showOutput && (
        <pre
          style={{
            background: "var(--idm-gray-100)",
            color: "var(--idm-text)",
            padding: 12,
            fontSize: 11,
            fontFamily: "var(--idm-mono-font)",
            maxHeight: 320,
            overflow: "auto",
            margin: 0,
          }}
        >
          {JSON.stringify(resp.output, null, 2)}
        </pre>
      )}
    </div>
  );
}

export function UseCasesPage() {
  const qc = useQueryClient();
  const { t } = useTranslation();
  const [q, setQ] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<string>("");
  const [dirty, setDirty] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [newId, setNewId] = useState("");
  // 触发 / 重扫 状态
  const [triggerStage, setTriggerStage] = useState<number | null>(null); // null = 全量
  const [triggerDryRun, setTriggerDryRun] = useState(false);
  const [lastTrigger, setLastTrigger] = useState<UseCaseTriggerResponse | null>(null);
  const [lastRescan, setLastRescan] = useState<RescanAssetResponse | null>(null);
  const [showOutput, setShowOutput] = useState(false);

  const listQ = useQuery({
    queryKey: ["use-cases", q],
    queryFn: () => UseCasesApi.list(q || undefined),
  });

  const detailQ = useQuery({
    queryKey: ["use-case", selectedId],
    queryFn: () => UseCasesApi.get(selectedId!),
    enabled: !!selectedId,
  });

  // sync draft with fetched
  useEffect(() => {
    if (detailQ.data) {
      setDraft(detailQ.data.raw);
      setDirty(false);
    }
  }, [detailQ.data]);

  const saveM = useMutation({
    mutationFn: () => UseCasesApi.save(selectedId!, draft),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["use-case", selectedId] });
      qc.invalidateQueries({ queryKey: ["use-cases"] });
      setDirty(false);
    },
  });

  const createM = useMutation({
    mutationFn: () => UseCasesApi.save(newId, TEMPLATE_BASIC, "created via UI"),
    onSuccess: (resp) => {
      qc.invalidateQueries({ queryKey: ["use-cases"] });
      setSelectedId(resp.id);
      setShowNew(false);
      setNewId("");
    },
  });

  const removeM = useMutation({
    mutationFn: (id: string) => UseCasesApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["use-cases"] });
      setSelectedId(null);
    },
  });

  // === 业务级: 触发 use case (全量 / 单阶段) ===
  const triggerM = useMutation({
    mutationFn: (id: string) => {
      const stages = triggerStage ? [triggerStage] : undefined;
      return UseCaseTriggerApi.trigger(id, {
        apply: !triggerDryRun,
        dry_run: triggerDryRun,
        stages,
      });
    },
    onSuccess: (resp) => {
      setLastTrigger(resp);
      setShowOutput(true);
      qc.invalidateQueries({ queryKey: ["use-cases"] });
    },
  });

  // === 业务级: 重扫 (alias of trigger) ===
  const rescanM = useMutation({
    mutationFn: (id: string) => UseCaseTriggerApi.rescan(id, { apply: !triggerDryRun, dry_run: triggerDryRun }),
    onSuccess: (resp) => {
      setLastTrigger(resp);
      setShowOutput(true);
      qc.invalidateQueries({ queryKey: ["use-cases"] });
    },
  });

  // === 系统级: 按 source_type 扫资源 ===
  const sysRescanM = useMutation({
    mutationFn: (req: Parameters<typeof UseCaseTriggerApi.rescanAssets>[0]) =>
      UseCaseTriggerApi.rescanAssets(req),
    onSuccess: (resp) => {
      setLastRescan(resp);
      setShowOutput(true);
    },
  });

  const stats = useMemo(() => {
    const items = listQ.data?.items ?? [];
    return {
      total: items.length,
      sources: items.reduce((s, x) => s + x.sources_count, 0),
      analysis: items.reduce((s, x) => s + x.analysis_count, 0),
    };
  }, [listQ.data]);

  // Try to parse current draft (best-effort YAML preview)
  const parsedSpec = useMemo(() => {
    if (!draft) return null;
    try {
      // Lazy import yaml via dynamic eval-light: a tiny parser is enough
      // for preview. We use JSON-like minimal parser to avoid heavy dep.
      // For accurate YAML, users should rely on backend validation.
      const lines = draft.split("\n");
      // simple key: value extraction
      const obj: Record<string, unknown> = {};
      for (const l of lines) {
        const m = l.match(/^([a-zA-Z_][\w-]*):\s*(.+?)\s*$/);
        if (m) obj[m[1]] = m[2].replace(/^["']|["']$/g, "");
      }
      return obj as Record<string, unknown>;
    } catch {
      return null;
    }
  }, [draft]);

  const analysisList = useMemo(() => {
    if (!detailQ.data) return [] as Array<{ id: string; task: string; agent: string; schedule?: string; depends_on?: string[] }>;
    const a = (detailQ.data.spec as Record<string, unknown>).analysis;
    if (!Array.isArray(a)) return [];
    return a as Array<{ id: string; task: string; agent: string; schedule?: string; depends_on?: string[] }>;
  }, [detailQ.data]);

  return (
    <>
      <Stats>
        <Stat label="Use Cases" value={stats.total} hint="YAML files in repo" />
        <Stat label="Total Sources" value={stats.sources} hint="MCP sources declared" />
        <Stat label="Total Analysis Tasks" value={stats.analysis} hint="Across all use cases" />
      </Stats>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "320px 1fr",
          gap: 16,
          alignItems: "start",
        }}
      >
        {/* === List === */}
        <Card
          title="Use Cases"
          flush
          extra={
            <Button size="sm" variant="primary" onClick={() => setShowNew(true)}>
              + New
            </Button>
          }
        >
          <div style={{ padding: 8, borderBottom: "1px solid var(--idm-border)" }}>
            <Input
              size="sm"
              placeholder="Filter by id / description…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              style={{ width: "100%" }}
            />
          </div>
          <div style={{ maxHeight: 580, overflow: "auto" }}>
            {(listQ.data?.items ?? []).map((uc: UseCaseSummary) => (
              <button
                key={uc.id}
                onClick={() => {
                  if (dirty && !confirm("Discard unsaved changes?")) return;
                  setSelectedId(uc.id);
                  setLastTrigger(null);
                  setLastRescan(null);
                }}
                className="idm-uc-item"
                style={{
                  display: "block",
                  width: "100%",
                  textAlign: "left",
                  padding: "10px 12px",
                  borderBottom: "1px solid var(--idm-border)",
                  background:
                    selectedId === uc.id ? "var(--idm-gray-100)" : "transparent",
                  borderLeft:
                    selectedId === uc.id
                      ? "3px solid var(--idm-blue-500)"
                      : "3px solid transparent",
                  cursor: "pointer",
                }}
              >
                <div className="idm-flex idm-items-center idm-justify-between idm-mb-1">
                  <span
                    style={{
                      fontFamily: "var(--idm-mono-font)",
                      fontSize: 12,
                      fontWeight: 600,
                    }}
                  >
                    {uc.id}
                  </span>
                  <Tag>v{uc.version}</Tag>
                </div>
                <p
                  className="idm-text-muted"
                  style={{
                    margin: 0,
                    fontSize: 12,
                    lineHeight: 1.4,
                    display: "-webkit-box",
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: "vertical",
                    overflow: "hidden",
                  }}
                >
                  {uc.description}
                </p>
                <div
                  className="idm-flex idm-items-center idm-gap-2"
                  style={{ marginTop: 6, fontSize: 11 }}
                >
                  <span className="idm-text-muted">
                    {uc.sources_count} src · {uc.analysis_count} tasks
                  </span>
                </div>
              </button>
            ))}
            {(listQ.data?.items ?? []).length === 0 && (
              <p className="idm-text-muted" style={{ padding: 16 }}>
                No use cases match.
              </p>
            )}
          </div>
        </Card>

        {/* === Right column: Editor + Trigger Controls + Result === */}
        <div className="idm-flex-col idm-gap-3">

        {/* === Editor === */}
        <Card
          title={
            selectedId
              ? `Editor — ${selectedId}${dirty ? " (unsaved)" : ""}`
              : "Select a use case"
          }
          extra={
            <div className="idm-flex idm-gap-2 idm-items-center idm-flex-wrap">
              {selectedId && (
                <>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => detailQ.refetch()}
                  >
                    Reload
                  </Button>
                  <Button
                    size="sm"
                    variant="danger"
                    onClick={() => {
                      if (confirm(`Delete use case "${selectedId}"?`))
                        removeM.mutate(selectedId);
                    }}
                    disabled={removeM.isPending}
                  >
                    Delete
                  </Button>
                  <Button
                    size="sm"
                    variant="primary"
                    onClick={() => saveM.mutate()}
                    disabled={saveM.isPending || !dirty}
                  >
                    {saveM.isPending ? "Saving…" : "Save"}
                  </Button>
                  <span
                    style={{
                      width: 1,
                      height: 20,
                      background: "var(--idm-border)",
                      margin: "0 4px",
                    }}
                  />
                  {/* 业务级: Trigger / Re-scan */}
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => triggerM.mutate(selectedId)}
                    disabled={
                      triggerM.isPending ||
                      rescanM.isPending ||
                      !selectedId
                    }
                    title={t("use_cases.trigger.hint")}
                  >
                    {triggerM.isPending
                      ? t("use_cases.trigger.triggerBusy")
                      : t("use_cases.trigger.button")}
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => rescanM.mutate(selectedId)}
                    disabled={
                      triggerM.isPending ||
                      rescanM.isPending ||
                      !selectedId
                    }
                    title={t("use_cases.trigger.hint")}
                  >
                    {rescanM.isPending
                      ? t("use_cases.trigger.rescanBusy")
                      : t("use_cases.trigger.rescan")}
                  </Button>
                </>
              )}
            </div>
          }
        >
          {!selectedId ? (
            <p className="idm-text-muted" style={{ padding: 16 }}>
              Pick a use case from the left, or click <strong>+ New</strong>.
            </p>
          ) : detailQ.isLoading ? (
            <p className="idm-text-muted" style={{ padding: 16 }}>
              Loading…
            </p>
          ) : (
            <div className="idm-flex-col idm-gap-3">
              {/* Top meta row */}
              <div
                className="idm-flex idm-gap-3 idm-flex-wrap"
                style={{ fontSize: 12 }}
              >
                <Tag>v{detailQ.data?.version}</Tag>
                <span className="idm-text-muted">id: {detailQ.data?.id}</span>
                <span className="idm-text-muted">
                  owners: {detailQ.data?.owners.join(", ")}
                </span>
                <span className="idm-text-muted">
                  path:{" "}
                  <code style={{ fontFamily: "var(--idm-mono-font)" }}>
                    {detailQ.data?.path}
                  </code>
                </span>
              </div>

              {/* Editor */}
              <LineNumberedEditor
                value={draft}
                onChange={(v) => {
                  setDraft(v);
                  setDirty(true);
                }}
              />

              {/* Spec preview */}
              {parsedSpec && (
                <Card title="Spec preview (top-level scalars)">
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "140px 1fr",
                      gap: "4px 16px",
                      fontSize: 12,
                      fontFamily: "var(--idm-mono-font)",
                    }}
                  >
                    {Object.entries(parsedSpec).map(([k, v]) => (
                      <div key={k} style={{ display: "contents" }}>
                        <div style={{ color: "var(--idm-text-muted)" }}>{k}</div>
                        <div style={{ wordBreak: "break-all" }}>
                          {typeof v === "string" ? v : JSON.stringify(v)}
                        </div>
                      </div>
                    ))}
                  </div>
                </Card>
              )}

              {/* Analysis flow */}
              {analysisList.length > 0 && (
                <Card title={`Analysis DAG (${analysisList.length} tasks)`}>
                  <div
                    className="idm-flex idm-gap-2"
                    style={{ flexWrap: "wrap", alignItems: "stretch" }}
                  >
                    {analysisList.map((task, idx) => (
                      <div
                        key={task.id ?? idx}
                        style={{ display: "flex", alignItems: "center", gap: 8 }}
                      >
                        <div
                          style={{
                            border: "1px solid var(--idm-border)",
                            padding: "6px 10px",
                            background: "var(--idm-bg-elevated)",
                            minWidth: 130,
                          }}
                        >
                          <div
                            className="idm-flex idm-items-center idm-gap-2 idm-mb-1"
                          >
                            <Tag solid color={AGENT_COLORS[task.agent] ?? "#697077"}>
                              {task.agent}
                            </Tag>
                            <span style={{ fontWeight: 500, fontSize: 12 }}>
                              {TASK_LABELS[task.task] ?? task.task}
                            </span>
                          </div>
                          {task.schedule && (
                            <div className="idm-text-muted" style={{ fontSize: 11 }}>
                              ⏰ {task.schedule}
                            </div>
                          )}
                          {task.depends_on && task.depends_on.length > 0 && (
                            <div className="idm-text-muted" style={{ fontSize: 11 }}>
                              ← {task.depends_on.join(", ")}
                            </div>
                          )}
                        </div>
                        {idx < analysisList.length - 1 && (
                          <span className="idm-text-muted">→</span>
                        )}
                      </div>
                    ))}
                  </div>
                </Card>
              )}
            </div>
          )}
        </Card>

        {/* === Trigger Controls (业务级) === */}
        {selectedId && (
          <Card title={t("use_cases.trigger.button") + " / " + t("use_cases.trigger.rescan")}>
            <div
              className="idm-flex idm-gap-3 idm-items-center idm-flex-wrap"
              style={{ fontSize: 12 }}
            >
              <div className="idm-flex idm-items-center idm-gap-2">
                <span className="idm-text-muted">
                  {t("use_cases.trigger.stageLabel")}:
                </span>
                <select
                  value={triggerStage ?? ""}
                  onChange={(e) =>
                    setTriggerStage(e.target.value ? Number(e.target.value) : null)
                  }
                  disabled={triggerM.isPending || rescanM.isPending}
                  style={{
                    padding: "4px 8px",
                    border: "1px solid var(--idm-border)",
                    background: "var(--idm-bg-elevated)",
                    fontFamily: "var(--idm-mono-font)",
                    fontSize: 12,
                  }}
                >
                  <option value="">{t("use_cases.trigger.stageAll")} (1-6)</option>
                  {[1, 2, 3, 4, 5, 6].map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </div>
              <label
                className="idm-flex idm-items-center idm-gap-1"
                style={{ cursor: "pointer" }}
              >
                <input
                  type="checkbox"
                  checked={triggerDryRun}
                  onChange={(e) => setTriggerDryRun(e.target.checked)}
                  disabled={triggerM.isPending || rescanM.isPending}
                />
                <span>{t("use_cases.trigger.dryRun")}</span>
              </label>
              <span className="idm-text-muted" style={{ flex: 1 }}>
                {t("use_cases.trigger.hint")}
              </span>
            </div>
          </Card>
        )}

        {/* === System-level Re-scan (不依赖 use case) === */}
        <Card title={t("use_cases.trigger.sysRescan")}>
          <div
            className="idm-flex idm-gap-2 idm-items-center idm-flex-wrap"
            style={{ fontSize: 12 }}
          >
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                sysRescanM.mutate({ source_type: "gcs", bucket: "company-raw" })
              }
              disabled={sysRescanM.isPending}
            >
              {t("use_cases.trigger.sysRescanGcs")}: company-raw
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                sysRescanM.mutate({ source_type: "clickhouse", database: "shop" })
              }
              disabled={sysRescanM.isPending}
            >
              {t("use_cases.trigger.sysRescanCh")}: shop
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => sysRescanM.mutate({ source_type: "all" })}
              disabled={sysRescanM.isPending}
            >
              {t("use_cases.trigger.sysRescanAll")}
            </Button>
            {sysRescanM.isPending && (
              <span className="idm-text-muted">
                {t("use_cases.trigger.running")}
              </span>
            )}
          </div>
        </Card>

        {/* === Result Panel (最近一次 trigger / rescan / sys-rescan) === */}
        {(lastTrigger || lastRescan) && (
          <Card
            title={t("use_cases.trigger.result")}
            extra={
              <Button size="sm" variant="ghost" onClick={() => setShowOutput((v) => !v)}>
                {showOutput
                  ? t("use_cases.trigger.hideOutput")
                  : t("use_cases.trigger.viewOutput")}
              </Button>
            }
          >
            {lastTrigger && (
              <TriggerResultView resp={lastTrigger} showOutput={showOutput} t={t} />
            )}
            {lastRescan && (
              <RescanResultView resp={lastRescan} showOutput={showOutput} t={t} />
            )}
          </Card>
        )}
        </div>
      </div>

      {/* New use case drawer */}
      <Drawer
        open={showNew}
        onClose={() => setShowNew(false)}
        title="Create new use case"
        width={420}
      >
        <div className="idm-flex-col idm-gap-3">
          <div>
            <label className="idm-label">ID (kebab-case)</label>
            <Input
              value={newId}
              onChange={(e) => setNewId(e.target.value)}
              placeholder="my-new-use-case"
              style={{ width: "100%" }}
            />
          </div>
          <p className="idm-text-muted" style={{ fontSize: 12 }}>
            A starter YAML will be generated. You can edit it after creation.
          </p>
          <div
            className="idm-flex idm-gap-2 idm-justify-between"
            style={{ paddingTop: 12, borderTop: "1px solid var(--idm-border)" }}
          >
            <Button variant="ghost" onClick={() => setShowNew(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => createM.mutate()}
              disabled={createM.isPending || !/^[a-z0-9][a-z0-9-]*$/.test(newId)}
            >
              {createM.isPending ? "Creating…" : "Create"}
            </Button>
          </div>
        </div>
      </Drawer>
    </>
  );
}
