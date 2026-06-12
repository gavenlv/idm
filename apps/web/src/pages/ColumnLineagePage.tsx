/**
 * ColumnLineagePage — M2.x 完整的列级血缘页面.
 *
 * 路由:
 *   /lineage/column                       -> 列出所有表, 选表后跳到该表所有列血缘
 *   /lineage/column/coverage              -> 覆盖矩阵视图 (OpenLineage/Marquez-style)
 *   /lineage/column/:tableId              -> 该表所有列的上/下游血缘
 *   /lineage/column/:tableId/:columnName  -> 某列的完整血缘 + 转换表达式
 *
 * 数据源:
 *   - GET  /v1/lineage/column/table/{id}
 *   - GET  /v1/lineage/column/table/{id}/{columnName}
 *   - GET  /v1/lineage/column/stats
 *   - GET  /v1/lineage/column/coverage
 *   - POST /v1/lineage/column/infer-all
 *   - GET  /v1/assets/{id}
 *   - GET  /v1/assets/{id}/columns
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import ReactFlow, {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  type Edge,
  type Node,
} from "reactflow";
import "reactflow/dist/style.css";
import { OpenLineageStyleLineage } from "../components/OpenLineageStyleLineage";
import {
  AssetsApi,
  type ColumnAsset,
  type ColumnLineageEdge,
  type TableAsset,
} from "../lib/api";
import {
  Button,
  Card,
  EmptyState,
  Input,
  Stat,
  Stats,
  Tag,
} from "../ui";

// === 常量 ===
const TIER_COLOR: Record<string, string> = {
  critical: "#cf1124",
  important: "#d97706",
  normal: "#2e66f0",
};

const TRANSFORM_COLOR: Record<string, string> = {
  direct: "#2e8540",
  rename: "#2e66f0",
  cast: "#d97706",
  aggregation: "#cf1124",
  expression: "#697077",
  derivation: "#9b51e0",
  passthrough: "#2e66f0",
};

const COMPONENT_COLOR: Record<string, string> = {
  airflow_task: "#1d6f30",
  flink_job: "#cf1124",
  dbt_model: "#2e66f0",
  mex_model: "#9b51e0",
  superset_chart: "#d97706",
  sql: "#697077",
  ai_inferred: "#9b51e0",
};

// === 主页面 ===
export function ColumnLineagePage() {
  const { tableId, columnName } = useParams<{
    tableId?: string;
    columnName?: string;
  }>();
  const navigate = useNavigate();
  const [searchQ, setSearchQ] = useState("");

  // 覆盖矩阵视图 (OpenLineage/Marquez-style)
  if (tableId === "coverage") {
    return <ColumnCoverageView onPickTable={(id) => navigate(`/lineage/column/${id}`)} />;
  }

  // 列表模式 (没有 tableId)
  if (!tableId) {
    return <ColumnLineagePicker q={searchQ} onQ={setSearchQ} />;
  }

  // 表 / 列级详情
  if (columnName) {
    return (
      <ColumnLineageDetail
        tableId={tableId}
        columnName={decodeURIComponent(columnName)}
        onBack={() => navigate(`/lineage/column/${tableId}`)}
        onPickColumn={(c) => navigate(`/lineage/column/${tableId}/${encodeURIComponent(c)}`)}
      />
    );
  }

  return (
    <TableColumnLineageView
      tableId={tableId}
      onPickColumn={(c) => navigate(`/lineage/column/${tableId}/${encodeURIComponent(c)}`)}
      onBack={() => navigate("/lineage/column")}
    />
  );
}

// === 1. 列表模式: 让用户选表 ===
function ColumnLineagePicker({ q, onQ }: { q: string; onQ: (v: string) => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["assets", q],
    queryFn: () => AssetsApi.list({ q: q || undefined, limit: 200 }),
  });
  const statsQ = useQuery({
    queryKey: ["col-lineage-stats"],
    queryFn: () => AssetsApi.columnLineageStats(),
  });
  const coverageQ = useQuery({
    queryKey: ["col-lineage-coverage"],
    queryFn: () => AssetsApi.columnLineageCoverage(),
    refetchInterval: 30_000,
  });

  // === Backfill all mutation ===
  const backfillM = useMutation({
    mutationFn: () =>
      AssetsApi.bulkInferColumnLineage({
        include_table_lineage_inference: true,
        include_column_lineage_inference: true,
        include_lineage_to_column: true,
        min_confidence: 0.5,
        dry_run: false,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["col-lineage-stats"] });
      queryClient.invalidateQueries({ queryKey: ["col-lineage-coverage"] });
      queryClient.invalidateQueries({ queryKey: ["assets"] });
    },
  });

  // 构建 table_id -> coverage% 索引
  const coverageByTable = useMemo(() => {
    const m = new Map<string, number>();
    if (coverageQ.data) {
      for (const t of coverageQ.data.tables) {
        m.set(t.table_id, t.coverage_pct);
      }
    }
    return m;
  }, [coverageQ.data]);

  return (
    <>
      <Stats>
        <Stat
          label="Column edges"
          value={statsQ.data?.n_edges ?? 0}
          hint="Total column-level lineage edges in KG"
        />
        <Stat
          label="Tables w/ col lineage"
          value={statsQ.data?.coverage?.tables_with_col_lineage ?? 0}
          hint={`of ${statsQ.data?.coverage?.tables_total ?? 0} total tables`}
        />
        <Stat
          label="Column coverage"
          value={`${coverageQ.data?.overall_coverage_pct ?? 0}%`}
          hint={`${coverageQ.data?.total_columns_with_lineage ?? 0} / ${coverageQ.data?.total_columns ?? 0} columns`}
        />
        <Stat
          label="Transform types"
          value={Object.keys(statsQ.data?.n_transform_types ?? {}).length}
          hint="Unique transform kinds"
        />
      </Stats>

      <Card
        title={
          <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 11, color: "#9b51e0", fontWeight: 700, letterSpacing: 0.5 }}>
              COLUMN-LEVEL LINEAGE
            </span>
            <span>Pick a table to inspect its column-level upstream &amp; downstream</span>
          </span>
        }
        extra={
          <div style={{ display: "flex", gap: 6 }}>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => navigate("/lineage/column/coverage")}
              title="OpenLineage/Marquez-style coverage matrix"
            >
              📊 Coverage Matrix
            </Button>
            <Button
              size="sm"
              variant="primary"
              onClick={() => {
                if (confirm(
                  `Backfill column lineage for ALL tables?\n\nThis will run:\n  1. lineage_reasoner\n  2. infer_column_lineage\n  3. lineage_to_column\n\nFor ${data?.total ?? "?"} tables. May take several minutes.`,
                )) {
                  backfillM.mutate();
                }
              }}
              disabled={backfillM.isPending}
              title="Run lineage_reasoner + infer_column_lineage + lineage_to_column for all tables"
            >
              {backfillM.isPending ? "⏳ Backfilling…" : "🔄 Backfill All"}
            </Button>
          </div>
        }
      >
        {/* Backfill result banner */}
        {backfillM.data && (
          <div
            style={{
              padding: "8px 12px",
              marginBottom: 8,
              background: backfillM.data.ok ? "#e6f4ea" : "#fde8e8",
              border: `1px solid ${backfillM.data.ok ? "#2e8540" : "#cf1124"}`,
              fontSize: 11,
              borderRadius: 0,
            }}
          >
            <strong>Backfill done in {backfillM.data.duration_ms}ms:</strong>{" "}
            {backfillM.data.tables_processed} processed, {backfillM.data.tables_skipped}{" "}
            skipped · +{backfillM.data.column_lineage_edges_created} column edges ·{" "}
            +{backfillM.data.table_lineage_edges_created} table edges
            {backfillM.data.errors.length > 0 && (
              <div style={{ marginTop: 4, color: "#cf1124" }}>
                ⚠ {backfillM.data.errors.length} errors
              </div>
            )}
          </div>
        )}

        <div style={{ marginBottom: 10 }}>
          <Input
            size="sm"
            placeholder="Search table by name/fqn…"
            value={q}
            onChange={(e) => onQ(e.target.value)}
            style={{ width: 260 }}
          />
        </div>
        {isLoading && <div className="idm-text-muted" style={{ padding: 12 }}>Loading…</div>}
        {!isLoading && (data?.items.length ?? 0) === 0 && (
          <EmptyState
            title="No tables found"
            description="Run discover_clickhouse_assets / parse_dbt_manifest / infer_column_lineage to populate."
          />
        )}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
            gap: 6,
          }}
        >
          {data?.items.map((a) => {
            const cov = coverageByTable.get(a.id) ?? 0;
            return (
              <button
                key={a.id}
                type="button"
                onClick={() => navigate(`/lineage/column/${a.id}`)}
                style={{
                  textAlign: "left",
                  padding: "8px 10px",
                  background: "var(--idm-bg-elevated)",
                  border: "1px solid var(--idm-border)",
                  borderLeft: `3px solid ${TIER_COLOR[a.tier] ?? "#697077"}`,
                  cursor: "pointer",
                  fontSize: 12,
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    marginBottom: 4,
                  }}
                >
                  <Tag dot color={TIER_COLOR[a.tier] ?? "#697077"}>
                    {a.tier}
                  </Tag>
                  <span style={{ fontSize: 10, color: "var(--idm-text-muted)" }}>
                    {a.column_count} cols
                  </span>
                  <span
                    style={{
                      marginLeft: "auto",
                      fontSize: 9,
                      color:
                        cov >= 80
                          ? "#2e8540"
                          : cov >= 50
                            ? "#d97706"
                            : cov > 0
                              ? "#697077"
                              : "#cf1124",
                      fontWeight: 700,
                    }}
                    title={`Column lineage coverage: ${cov.toFixed(1)}%`}
                  >
                    {cov >= 80 ? "●" : cov >= 50 ? "◐" : cov > 0 ? "○" : "✕"} {cov.toFixed(0)}%
                  </span>
                </div>
                <code
                  style={{
                    fontFamily: "var(--idm-mono-font)",
                    fontSize: 12,
                    color: "var(--idm-text)",
                    display: "block",
                    wordBreak: "break-all",
                  }}
                >
                  {a.fqn}
                </code>
              </button>
            );
          })}
        </div>
      </Card>

      {/* === 全局统计 === */}
      {statsQ.data && (
        <Card title="Global column-lineage stats">
          <div style={{ display: "flex", gap: 24, flexWrap: "wrap", fontSize: 12 }}>
            <div>
              <div
                style={{
                  fontSize: 10,
                  color: "var(--idm-text-muted)",
                  textTransform: "uppercase",
                  letterSpacing: 0.5,
                  fontWeight: 600,
                  marginBottom: 4,
                }}
              >
                By transform type
              </div>
              <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                {Object.entries(statsQ.data.n_transform_types).map(([tt, n]) => (
                  <Tag key={tt} solid color={TRANSFORM_COLOR[tt] ?? "#697077"}>
                    {tt} · {n}
                  </Tag>
                ))}
                {Object.keys(statsQ.data.n_transform_types).length === 0 && (
                  <span className="idm-text-muted">none</span>
                )}
              </div>
            </div>
            <div>
              <div
                style={{
                  fontSize: 10,
                  color: "var(--idm-text-muted)",
                  textTransform: "uppercase",
                  letterSpacing: 0.5,
                  fontWeight: 600,
                  marginBottom: 4,
                }}
              >
                By component
              </div>
              <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                {Object.entries(statsQ.data.n_components).map(([c, n]) => (
                  <Tag key={c} solid color={COMPONENT_COLOR[c] ?? "#697077"}>
                    {c} · {n}
                  </Tag>
                ))}
                {Object.keys(statsQ.data.n_components).length === 0 && (
                  <span className="idm-text-muted">none</span>
                )}
              </div>
            </div>
          </div>
        </Card>
      )}
    </>
  );
}

// === 1.5 覆盖矩阵视图 (OpenLineage/Marquez-style) ===
function ColumnCoverageView({ onPickTable }: { onPickTable: (id: string) => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const coverageQ = useQuery({
    queryKey: ["col-lineage-coverage"],
    queryFn: () => AssetsApi.columnLineageCoverage(),
    refetchInterval: 30_000,
  });

  const backfillM = useMutation({
    mutationFn: () =>
      AssetsApi.bulkInferColumnLineage({
        include_table_lineage_inference: true,
        include_column_lineage_inference: true,
        include_lineage_to_column: true,
        min_confidence: 0.5,
        dry_run: false,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["col-lineage-coverage"] });
    },
  });

  if (coverageQ.isLoading) {
    return <div className="idm-text-muted">Loading coverage matrix…</div>;
  }
  if (!coverageQ.data) {
    return <EmptyState title="No data" />;
  }

  const cov = coverageQ.data;
  const sortedTables = [...cov.tables].sort((a, b) => a.coverage_pct - b.coverage_pct);

  return (
    <>
      <Card
        title={
          <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Button variant="ghost" size="sm" onClick={() => navigate("/lineage/column")}>
              ← Picker
            </Button>
            <span style={{ fontSize: 11, color: "#9b51e0", fontWeight: 700, letterSpacing: 0.5 }}>
              COVERAGE MATRIX
            </span>
            <span>OpenLineage/Marquez-style: which tables/columns lack column lineage?</span>
          </span>
        }
        extra={
          <Button
            size="sm"
            variant="primary"
            onClick={() => {
              if (confirm("Backfill column lineage for all tables?")) {
                backfillM.mutate();
              }
            }}
            disabled={backfillM.isPending}
          >
            {backfillM.isPending ? "⏳ …" : "🔄 Backfill All"}
          </Button>
        }
      >
        <Stats>
          <Stat label="Total tables" value={cov.total_tables} />
          <Stat label="Total columns" value={cov.total_columns} />
          <Stat
            label="Columns w/ lineage"
            value={cov.total_columns_with_lineage}
            hint={`${cov.overall_coverage_pct}% overall coverage`}
          />
          <Stat
            label="Tables 0% coverage"
            value={cov.tables.filter((t) => t.coverage_pct === 0).length}
            hint="Need attention first"
          />
        </Stats>
      </Card>

      <Card title={`Per-table coverage (sorted low → high) — ${cov.tables.length} tables`}>
        <table
          style={{
            width: "100%",
            borderCollapse: "collapse",
            fontSize: 11,
            fontFamily: "var(--idm-mono-font)",
          }}
        >
          <thead>
            <tr style={{ background: "var(--idm-gray-50)", borderBottom: "2px solid var(--idm-border)" }}>
              <th style={{ padding: "6px 8px", textAlign: "left", fontSize: 10 }}>Table</th>
              <th style={{ padding: "6px 8px", textAlign: "center", fontSize: 10, width: 70 }}>
                Tier
              </th>
              <th style={{ padding: "6px 8px", textAlign: "center", fontSize: 10, width: 60 }}>
                Cols
              </th>
              <th style={{ padding: "6px 8px", textAlign: "center", fontSize: 10, width: 80 }}>
                w/ Lineage
              </th>
              <th style={{ padding: "6px 8px", textAlign: "left", fontSize: 10, width: 200 }}>
                Coverage
              </th>
              <th style={{ padding: "6px 8px", textAlign: "center", fontSize: 10, width: 100 }}>
                Table Lineage
              </th>
              <th style={{ padding: "6px 8px", textAlign: "center", fontSize: 10, width: 80 }}>
                Action
              </th>
            </tr>
          </thead>
          <tbody>
            {sortedTables.map((t) => {
              const covPct = t.coverage_pct;
              const covColor =
                covPct >= 80 ? "#2e8540" : covPct >= 50 ? "#d97706" : covPct > 0 ? "#697077" : "#cf1124";
              return (
                <tr
                  key={t.table_id}
                  style={{
                    borderBottom: "1px solid var(--idm-border)",
                    background:
                      covPct === 0
                        ? "rgba(207, 17, 36, 0.04)"
                        : covPct < 50
                          ? "rgba(217, 119, 6, 0.04)"
                          : undefined,
                  }}
                >
                  <td style={{ padding: "4px 8px" }}>
                    <code style={{ color: "var(--idm-text)" }}>{t.table_fqn}</code>
                  </td>
                  <td style={{ padding: "4px 8px", textAlign: "center" }}>
                    <Tag dot color={TIER_COLOR[t.tier] ?? "#697077"}>
                      {t.tier}
                    </Tag>
                  </td>
                  <td style={{ padding: "4px 8px", textAlign: "center" }}>{t.n_columns}</td>
                  <td style={{ padding: "4px 8px", textAlign: "center" }}>{t.n_columns_with_lineage}</td>
                  <td style={{ padding: "4px 8px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <div
                        style={{
                          flex: 1,
                          height: 8,
                          background: "var(--idm-gray-50)",
                          border: "1px solid var(--idm-border)",
                          position: "relative",
                        }}
                      >
                        <div
                          style={{
                            width: `${covPct}%`,
                            height: "100%",
                            background: covColor,
                            transition: "width 0.3s",
                          }}
                        />
                      </div>
                      <span
                        style={{
                          color: covColor,
                          fontWeight: 700,
                          fontSize: 10,
                          minWidth: 40,
                          textAlign: "right",
                        }}
                      >
                        {covPct.toFixed(1)}%
                      </span>
                    </div>
                  </td>
                  <td style={{ padding: "4px 8px", textAlign: "center" }}>
                    {t.has_table_lineage ? (
                      <span style={{ color: "#2e8540", fontSize: 10 }}>
                        ✓ {t.n_table_lineage_edges}
                      </span>
                    ) : (
                      <span style={{ color: "#cf1124", fontSize: 10 }}>✕ none</span>
                    )}
                  </td>
                  <td style={{ padding: "4px 8px", textAlign: "center" }}>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => onPickTable(t.table_id)}
                    >
                      →
                    </Button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>
    </>
  );
}

// === 2. 表级: 该表所有列的列级血缘汇总 ===
function TableColumnLineageView({
  tableId,
  onPickColumn,
  onBack,
}: {
  tableId: string;
  onPickColumn: (columnName: string) => void;
  onBack: () => void;
}) {
  const tableQ = useQuery({
    queryKey: ["asset", tableId],
    queryFn: () => AssetsApi.get(tableId),
  });
  const colsQ = useQuery({
    queryKey: ["asset-cols", tableId],
    queryFn: () => AssetsApi.listColumns(tableId),
  });
  const colLineageQ = useQuery({
    queryKey: ["asset-col-lineage-all", tableId],
    queryFn: () => AssetsApi.columnLineage(tableId),
  });

  const table = tableQ.data;
  const columns = colsQ.data?.items ?? [];
  const upEdges = colLineageQ.data?.upstream ?? [];
  const downEdges = colLineageQ.data?.downstream ?? [];

  // Build per-column counts
  // upstream edges: current table is the downstream of these edges; key by downstream_column_name
  // downstream edges: current table is the upstream of these edges; key by upstream_column_name
  const upByCol = new Map<string, number>();
  const downByCol = new Map<string, number>();
  for (const e of upEdges) {
    const k = e.downstream_column_name ?? "?";
    upByCol.set(k, (upByCol.get(k) ?? 0) + 1);
  }
  for (const e of downEdges) {
    const k = e.upstream_column_name ?? "?";
    downByCol.set(k, (downByCol.get(k) ?? 0) + 1);
  }

  return (
    <>
      <Card
        title={
          <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Button variant="ghost" size="sm" onClick={onBack}>
              ← All tables
            </Button>
            <span style={{ fontSize: 11, color: "#9b51e0", fontWeight: 700, letterSpacing: 0.5 }}>
              COLUMN-LEVEL LINEAGE
            </span>
            {table && (
              <code style={{ fontFamily: "var(--idm-mono-font)", fontSize: 13 }}>{table.fqn}</code>
            )}
          </span>
        }
      >
        <Stats>
          <Stat label="Upstream edges" value={upEdges.length} hint="Source column edges" />
          <Stat label="Downstream edges" value={downEdges.length} hint="Target column edges" />
          <Stat
            label="Columns w/ lineage"
            value={
              columns.filter((c) => (upByCol.get(c.name) ?? 0) + (downByCol.get(c.name) ?? 0) > 0)
                .length
            }
            hint={`of ${columns.length} total columns`}
          />
          <Stat
            label="Total edges"
            value={colLineageQ.data?.total ?? 0}
            hint="upstream + downstream"
          />
        </Stats>
      </Card>

      <Card title="Columns (click to drill into a column)">
        {colsQ.isLoading && <div className="idm-text-muted" style={{ padding: 12 }}>Loading…</div>}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
            gap: 6,
          }}
        >
          {columns.map((c) => {
            const upN = upByCol.get(c.name) ?? 0;
            const downN = downByCol.get(c.name) ?? 0;
            return (
              <button
                key={c.id}
                type="button"
                onClick={() => onPickColumn(c.name)}
                style={{
                  textAlign: "left",
                  padding: "6px 8px",
                  background: "var(--idm-bg-elevated)",
                  border: "1px solid var(--idm-border)",
                  borderLeft: "3px solid var(--idm-primary)",
                  cursor: "pointer",
                  fontSize: 11,
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 4,
                    fontFamily: "var(--idm-mono-font)",
                    fontWeight: 600,
                  }}
                >
                  <span style={{ flex: 1 }}>{c.name}</span>
                  <span
                    style={{
                      fontSize: 9,
                      color: "var(--idm-text-muted)",
                      textTransform: "lowercase",
                    }}
                  >
                    {c.data_type}
                  </span>
                </div>
                {c.description && (
                  <div
                    style={{
                      fontSize: 10,
                      color: "var(--idm-text-muted)",
                      marginTop: 2,
                      lineHeight: 1.3,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                    title={c.description}
                  >
                    {c.description}
                  </div>
                )}
                <div
                  style={{
                    display: "flex",
                    gap: 6,
                    marginTop: 4,
                    fontSize: 10,
                    color: "var(--idm-text-muted)",
                  }}
                >
                  {upN > 0 && <span style={{ color: "#2e8540", fontWeight: 600 }}>↑ {upN}</span>}
                  {downN > 0 && (
                    <span style={{ color: "#cf1124", fontWeight: 600 }}>↓ {downN}</span>
                  )}
                  {upN === 0 && downN === 0 && <span>· no lineage</span>}
                </div>
              </button>
            );
          })}
        </div>
      </Card>

      <Card title={`Column-level edges (${upEdges.length + downEdges.length})`}>
        <EdgeGroup
          title={`Upstream column edges (${upEdges.length})`}
          color="#2e8540"
          mode="upstream"
          edges={upEdges}
        />
        <EdgeGroup
          title={`Downstream column edges (${downEdges.length})`}
          color="#cf1124"
          mode="downstream"
          edges={downEdges}
        />
        {upEdges.length === 0 && downEdges.length === 0 && !colLineageQ.isLoading && (
          <div className="idm-text-muted" style={{ padding: 12, fontSize: 12 }}>
            No column-level lineage for this table. Run{" "}
            <code>lineage_to_column</code> or <code>infer_column_lineage</code> skill.
          </div>
        )}
      </Card>

      {/* === OpenLineage-style multi-table graph === */}
      <ColumnLineageGraph
        tableId={tableId}
        table={table}
        upEdges={upEdges}
        downEdges={downEdges}
      />
    </>
  );
}

// === 2.5 OpenLineage-style multi-table column graph ===
function ColumnLineageGraph({
  tableId,
  table,
  upEdges,
  downEdges,
}: {
  tableId: string;
  table?: TableAsset;
  upEdges: ColumnLineageEdge[];
  downEdges: ColumnLineageEdge[];
}) {
  // 收集所有涉及的 table id (center + 上下游)
  const involvedTableIds = useMemo(() => {
    const ids = new Set<string>([tableId]);
    for (const e of [...upEdges, ...downEdges]) {
      ids.add(e.upstream_table_id);
      ids.add(e.downstream_table_id);
    }
    return Array.from(ids);
  }, [tableId, upEdges, downEdges]);

  // 拉每张表的 columns
  const colsQueries = useQuery({
    queryKey: ["graph-cols", involvedTableIds],
    queryFn: async () => {
      const results = await Promise.all(
        involvedTableIds.map((id) =>
          AssetsApi.listColumns(id).then((r) => ({ id, cols: r.items })),
        ),
      );
      return results;
    },
    enabled: involvedTableIds.length > 0,
  });

  // 拉每张表的 table meta
  const tablesQueries = useQuery({
    queryKey: ["graph-tables", involvedTableIds],
    queryFn: async () => {
      const results = await Promise.all(
        involvedTableIds.map((id) =>
          AssetsApi.get(id).then((t) => ({ id, table: t })),
        ),
      );
      return results;
    },
    enabled: involvedTableIds.length > 0,
  });

  if (upEdges.length === 0 && downEdges.length === 0) {
    return null; // 没边就别画图
  }
  if (colsQueries.isLoading || tablesQueries.isLoading) {
    return (
      <Card title="Column graph (OpenLineage-style)">
        <div className="idm-text-muted" style={{ padding: 12 }}>
          Loading graph data…
        </div>
      </Card>
    );
  }
  if (!table || !colsQueries.data || !tablesQueries.data) {
    return null;
  }

  const allCols: ColumnAsset[] = colsQueries.data.flatMap((r) => r.cols);
  const allTables: TableAsset[] = tablesQueries.data
    .map((r) => r.table)
    .filter((t): t is TableAsset => !!t);

  return (
    <Card
      title={
        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 11, color: "#9b51e0", fontWeight: 700, letterSpacing: 0.5 }}>
            OPENLINEAGE-STYLE GRAPH
          </span>
          <span style={{ fontSize: 10, color: "var(--idm-text-muted)" }}>
            {involvedTableIds.length} tables · {upEdges.length + downEdges.length} column edges
          </span>
        </span>
      }
    >
      <div style={{ height: 520, border: "1px solid var(--idm-border)" }}>
        <OpenLineageStyleLineage
          centerTableId={tableId}
          upstream={upEdges}
          downstream={downEdges}
          tables={allTables}
          columns={allCols}
        />
      </div>
    </Card>
  );
}

// === 3. 列级详情: 某列 + 上/下游 (含图) ===
function ColumnLineageDetail({
  tableId,
  columnName,
  onBack,
  onPickColumn,
}: {
  tableId: string;
  columnName: string;
  onBack: () => void;
  onPickColumn: (columnName: string) => void;
}) {
  const tableQ = useQuery({
    queryKey: ["asset", tableId],
    queryFn: () => AssetsApi.get(tableId),
  });
  const colLineageQ = useQuery({
    queryKey: ["asset-col-lineage-one", tableId, columnName],
    queryFn: () => AssetsApi.columnLineage(tableId, columnName),
  });
  const colsQ = useQuery({
    queryKey: ["asset-cols", tableId],
    queryFn: () => AssetsApi.listColumns(tableId),
  });

  const table = tableQ.data;
  const colAsset = colsQ.data?.items.find((c) => c.name === columnName);
  const upEdges = colLineageQ.data?.upstream ?? [];
  const downEdges = colLineageQ.data?.downstream ?? [];
  const allEdges = [...upEdges, ...downEdges];

  // === 列级血缘图 (ReactFlow) ===
  const { nodes, edges } = useMemo(() => {
    if (!colAsset || !table) return { nodes: [], edges: [] };
    return layoutColumnGraph(colAsset, table, allEdges);
  }, [colAsset, table, allEdges]);

  return (
    <>
      <Card
        title={
          <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Button variant="ghost" size="sm" onClick={onBack}>
              ← All columns of {table?.fqn ?? "…"}
            </Button>
            <span style={{ fontSize: 11, color: "#9b51e0", fontWeight: 700, letterSpacing: 0.5 }}>
              COLUMN LINEAGE
            </span>
            <code style={{ fontFamily: "var(--idm-mono-font)", fontSize: 13 }}>
              {table?.fqn}.{columnName}
            </code>
          </span>
        }
        bodyClass=""
      >
        <div style={{ display: "grid", gridTemplateColumns: "260px 1fr", height: 480 }}>
          {/* Left: column list of this table */}
          <div
            style={{
              borderRight: "1px solid var(--idm-border)",
              overflowY: "auto",
              background: "var(--idm-bg-elevated)",
            }}
          >
            <div
              style={{
                padding: "6px 10px",
                borderBottom: "1px solid var(--idm-border)",
                background: "var(--idm-gray-50)",
                fontSize: 10,
                color: "var(--idm-text-muted)",
                textTransform: "uppercase",
                letterSpacing: 0.5,
                fontWeight: 600,
              }}
            >
              Columns ({colsQ.data?.items.length ?? 0})
            </div>
            {(colsQ.data?.items ?? []).map((c) => {
              const active = c.name === columnName;
              return (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => onPickColumn(c.name)}
                  style={{
                    display: "block",
                    width: "100%",
                    textAlign: "left",
                    padding: "4px 8px",
                    cursor: "pointer",
                    background: active ? "var(--idm-bg-active)" : "transparent",
                    border: "none",
                    borderLeft: `2px solid ${active ? "var(--idm-primary)" : "transparent"}`,
                    borderBottom: "1px solid var(--idm-border)",
                    fontSize: 11,
                    color: "var(--idm-text)",
                    fontFamily: "var(--idm-mono-font)",
                  }}
                >
                  {c.name}
                  <span
                    style={{
                      marginLeft: 4,
                      fontSize: 9,
                      color: "var(--idm-text-muted)",
                    }}
                  >
                    {c.data_type}
                  </span>
                </button>
              );
            })}
          </div>

          {/* Right: column lineage graph */}
          <div style={{ position: "relative", background: "var(--idm-gray-50)" }}>
            {nodes.length === 0 ? (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  height: "100%",
                }}
              >
                <EmptyState
                  title="No column-level edges"
                  description="This column has no upstream or downstream lineage yet."
                />
              </div>
            ) : (
              <ReactFlow
                nodes={nodes}
                edges={edges}
                fitView
                fitViewOptions={{ padding: 0.2 }}
                proOptions={{ hideAttribution: true }}
                minZoom={0.2}
                maxZoom={2}
                defaultEdgeOptions={{ type: "smoothstep" }}
              >
                <Background gap={20} color="#dde1e6" />
                <Controls showInteractive={false} />
                <MiniMap
                  nodeStrokeWidth={3}
                  nodeColor={(n) => {
                    const data = n.data as any;
                    if (data?.kind === "center") return "#9b51e0";
                    if (data?.kind === "upstream") return "#2e8540";
                    return "#cf1124";
                  }}
                  style={{
                    background: "#ffffff",
                    border: "1px solid var(--idm-border)",
                    borderRadius: 0,
                  }}
                  maskColor="rgba(241, 243, 246, 0.7)"
                />
              </ReactFlow>
            )}
          </div>
        </div>
      </Card>

      <Stats>
        <Stat label="Upstream edges" value={upEdges.length} hint="Sources for this column" />
        <Stat label="Downstream edges" value={downEdges.length} hint="Targets of this column" />
        <Stat
          label="Transforms"
          value={new Set(allEdges.map((e) => e.transform_type)).size}
          hint="Unique transform kinds"
        />
        <Stat
          label="Components"
          value={new Set(allEdges.map((e) => e.component).filter(Boolean)).size}
          hint="Pipeline components"
        />
      </Stats>

      <Card title={`Upstream column edges (${upEdges.length})`}>
        <EdgeGroup mode="upstream" color="#2e8540" edges={upEdges} />
      </Card>
      <Card title={`Downstream column edges (${downEdges.length})`}>
        <EdgeGroup mode="downstream" color="#cf1124" edges={downEdges} />
      </Card>
    </>
  );
}

// === 边列表 (按 transform_type 分组) ===
function EdgeGroup({
  title,
  color,
  mode,
  edges,
}: {
  title?: string;
  color: string;
  mode: "upstream" | "downstream";
  edges: ColumnLineageEdge[];
}) {
  const byType = edges.reduce<Record<string, ColumnLineageEdge[]>>((acc, e) => {
    (acc[e.transform_type] ||= []).push(e);
    return acc;
  }, {});

  if (edges.length === 0) {
    return (
      <div
        className="idm-text-muted"
        style={{ padding: 12, fontSize: 12, fontStyle: "italic" }}
      >
        No {mode} column edges.
      </div>
    );
  }

  return (
    <div>
      {title && (
        <div
          style={{
            fontSize: 10,
            color,
            textTransform: "uppercase",
            letterSpacing: 0.5,
            fontWeight: 700,
            marginBottom: 6,
            borderBottom: `2px solid ${color}`,
            paddingBottom: 2,
          }}
        >
          {title}
        </div>
      )}
      {Object.entries(byType).map(([tt, list]) => (
        <div key={tt} style={{ marginBottom: 10 }}>
          <div
            style={{
              fontSize: 10,
              color: "var(--idm-text-muted)",
              fontWeight: 600,
              marginBottom: 2,
              display: "flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            <span
              style={{
                background: TRANSFORM_COLOR[tt] ?? "#697077",
                color: "#fff",
                padding: "0 4px",
                fontSize: 9,
                fontWeight: 700,
              }}
            >
              {tt.toUpperCase()}
            </span>
            <span>{list.length} edges</span>
          </div>
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: 11,
              fontFamily: "var(--idm-mono-font)",
            }}
          >
            <tbody>
              {list.map((e) => (
                <tr
                  key={e.id}
                  style={{
                    borderBottom: "1px solid var(--idm-border)",
                    verticalAlign: "top",
                  }}
                >
                  <td
                    style={{
                      padding: "4px 6px",
                      color: "var(--idm-text-muted)",
                      fontSize: 10,
                      width: 16,
                    }}
                  >
                    {mode === "upstream" ? "←" : "→"}
                  </td>
                  <td style={{ padding: "4px 6px", width: "30%" }}>
                    <div style={{ color, fontWeight: 600 }}>
                      {e.upstream_table_fqn?.split(".").slice(-1)[0] ?? "?"}.
                      {e.upstream_column_name ?? "?"}
                    </div>
                    <div
                      style={{
                        color: "var(--idm-text-muted)",
                        fontSize: 9,
                      }}
                    >
                      {e.upstream_column_type ?? ""}
                    </div>
                  </td>
                  <td style={{ padding: "4px 6px", width: "30%" }}>
                    <div style={{ color, fontWeight: 600 }}>
                      {e.downstream_table_fqn?.split(".").slice(-1)[0] ?? "?"}.
                      {e.downstream_column_name ?? "?"}
                    </div>
                    <div
                      style={{
                        color: "var(--idm-text-muted)",
                        fontSize: 9,
                      }}
                    >
                      {e.downstream_column_type ?? ""}
                    </div>
                  </td>
                  <td style={{ padding: "4px 6px" }}>
                    {e.transform_expression && (
                      <code
                        style={{
                          fontSize: 10,
                          color: "var(--idm-text-muted)",
                          background: "var(--idm-gray-50)",
                          padding: "1px 4px",
                        }}
                      >
                        {e.transform_expression.length > 80
                          ? e.transform_expression.slice(0, 80) + "…"
                          : e.transform_expression}
                      </code>
                    )}
                    {e.description && (
                      <div
                        style={{
                          fontSize: 11,
                          color: "var(--idm-text)",
                          marginTop: 2,
                          fontFamily: "var(--idm-font)",
                          lineHeight: 1.4,
                        }}
                        title={e.description}
                      >
                        {e.description}
                      </div>
                    )}
                    <div
                      style={{
                        display: "flex",
                        gap: 4,
                        marginTop: 2,
                        fontSize: 9,
                        color: "var(--idm-text-muted)",
                        fontFamily: "var(--idm-font)",
                      }}
                    >
                      {e.component && (
                        <span style={{ color: COMPONENT_COLOR[e.component] ?? undefined }}>
                          [{e.component}]
                        </span>
                      )}
                      {e.source && <span>· {e.source}</span>}
                      {e.confidence != null && (
                        <span>· {(e.confidence * 100).toFixed(0)}%</span>
                      )}
                      {e.job_id && <span>· job:{e.job_id}</span>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

// === 列级血缘图布局 (BFS 风格) ===
function layoutColumnGraph(
  centerCol: ColumnAsset,
  centerTable: TableAsset,
  edges: ColumnLineageEdge[],
): { nodes: Node[]; edges: Edge[] } {
  const COL_WIDTH = 220;
  const ROW_HEIGHT = 80;
  const nodesById = new Map<
    string,
    {
      label: string;
      tier: string;
      col: number;
      row: number;
      kind: "center" | "upstream" | "downstream";
    }
  >();

  // Center node
  nodesById.set(`c::${centerCol.id}`, {
    label: `${centerTable.fqn.split(".").slice(-1)[0]}.${centerCol.name}`,
    tier: centerTable.tier,
    col: 0,
    row: 0,
    kind: "center",
  });

  // Upstream column nodes (left)
  const seenUpstream = new Set<string>();
  edges
    .filter((e) => e.upstream_column_id !== centerCol.id || e.upstream_table_id !== centerTable.id)
    .forEach((e, i) => {
      const k = `${e.upstream_table_id}::${e.upstream_column_id}`;
      if (seenUpstream.has(k)) return;
      seenUpstream.add(k);
      const tableShort = e.upstream_table_fqn?.split(".").slice(-1)[0] ?? "?";
      const colName = e.upstream_column_name ?? "?";
      nodesById.set(k, {
        label: `${tableShort}.${colName}`,
        tier: "normal",
        col: -1 - Math.floor(i / 2),
        row: i % 2,
        kind: "upstream",
      });
    });

  // Downstream column nodes (right)
  const seenDownstream = new Set<string>();
  edges
    .filter(
      (e) =>
        e.downstream_column_id !== centerCol.id || e.downstream_table_id !== centerTable.id,
    )
    .forEach((e, i) => {
      const k = `${e.downstream_table_id}::${e.downstream_column_id}`;
      if (seenDownstream.has(k)) return;
      seenDownstream.add(k);
      const tableShort = e.downstream_table_fqn?.split(".").slice(-1)[0] ?? "?";
      const colName = e.downstream_column_name ?? "?";
      nodesById.set(k, {
        label: `${tableShort}.${colName}`,
        tier: "normal",
        col: 1 + Math.floor(i / 2),
        row: i % 2,
        kind: "downstream",
      });
    });

  // Center rows
  const maxRow = Math.max(...Array.from(nodesById.values()).map((n) => n.row), 0);
  for (const info of nodesById.values()) {
    info.row = info.row - Math.floor(maxRow / 2);
  }

  // Build ReactFlow nodes
  const rfNodes: Node[] = Array.from(nodesById.entries()).map(([id, info]) => {
    const borderColor =
      info.kind === "center"
        ? "#9b51e0"
        : info.kind === "upstream"
          ? "#2e8540"
          : "#cf1124";
    const bg =
      info.kind === "center"
        ? "#f3e8ff"
        : info.kind === "upstream"
          ? "#e6f4ea"
          : "#fde8e8";
    return {
      id,
      type: "default",
      data: {
        label: (
          <div style={{ minWidth: 160, maxWidth: 200 }}>
            <div
              style={{
                fontSize: 9,
                color: borderColor,
                fontWeight: 700,
                marginBottom: 2,
                textTransform: "uppercase",
                letterSpacing: 0.5,
              }}
            >
              {info.kind === "center" ? "CENTER · COLUMN" : info.kind.toUpperCase()}
            </div>
            <div
              style={{
                fontSize: 11,
                fontWeight: info.kind === "center" ? 700 : 500,
                color: "#121619",
                wordBreak: "break-all",
                lineHeight: 1.3,
                fontFamily: "var(--idm-mono-font)",
              }}
            >
              {info.label}
            </div>
          </div>
        ),
      },
      position: { x: info.col * COL_WIDTH, y: info.row * ROW_HEIGHT },
      style: {
        background: bg,
        border: `${info.kind === "center" ? 2.5 : 1.5}px solid ${borderColor}`,
        borderRadius: 0,
        padding: 8,
        minWidth: 180,
        maxWidth: 220,
        fontSize: 11,
        width: 200,
      },
    };
  });

  // Build edges
  const rfEdges: Edge[] = edges.map((e, i) => {
    const isUp = nodesById.get(`${e.upstream_table_id}::${e.upstream_column_id}`)?.kind === "upstream";
    const stroke = isUp ? "#2e8540" : "#cf1124";
    const sourceId = `${e.upstream_table_id}::${e.upstream_column_id}`;
    const targetId = `${e.downstream_table_id}::${e.downstream_column_id}`;
    return {
      id: `e${i}`,
      source: sourceId,
      target: targetId,
      label: e.transform_type,
      labelStyle: { fontSize: 9, fill: "#4d5358" },
      labelBgStyle: { fill: "#ffffff", stroke: "#dde1e6" },
      labelBgPadding: [3, 3] as [number, number],
      labelBgBorderRadius: 0,
      animated: e.source === "ai_inferred" || e.confidence < 0.7,
      style: {
        stroke,
        strokeWidth: 1 + e.confidence * 1.2,
        strokeDasharray: e.source === "ai_inferred" ? "5 3" : undefined,
      },
      markerEnd: { type: MarkerType.ArrowClosed, color: stroke },
      title: [
        e.transform_expression,
        e.description,
        `[${e.component ?? "?"}] ${e.source} · ${(e.confidence * 100).toFixed(0)}%`,
      ]
        .filter(Boolean)
        .join("\n"),
    };
  });

  return { nodes: rfNodes, edges: rfEdges };
}
