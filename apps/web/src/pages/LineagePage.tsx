/**
 * LineagePage — DataHub-style lineage graph.
 *
 * Layout (参考 DataHub Lineage):
 * - Left: asset search + selection list (max 240px)
 * - Right: large react-flow canvas with BFS layout
 *   - Center table in blue (highlighted)
 *   - Upstream nodes (left), Downstream nodes (right)
 *   - Sharp nodes (no border-radius), tier-colored borders
 *   - Solid lines = known lineage, dashed = LLM-inferred
 *   - MiniMap bottom-left, Controls bottom-right
 * - Bottom: legend + transform-type breakdown
 */
import { useQuery } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import ReactFlow, {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  type Edge,
  type Node,
  type NodeMouseHandler,
} from "reactflow";
import "reactflow/dist/style.css";
import { useTranslation } from "react-i18next";
import {
  AssetsApi,
  type ColumnAsset,
  type ColumnLineageEdge,
  type TableAsset,
} from "../lib/api";
import { Card, EmptyState, Input, Select, Stat, Stats, Tag } from "../ui";

interface LineageEdge {
  id: string;
  upstream_id: string;
  downstream_id: string;
  transform_type: string;
  source: string;
  confidence: number;
  upstream_fqn?: string | null;
  downstream_fqn?: string | null;
}

interface LineageGraph {
  center_fqn: string;
  center_id: string;
  upstream: LineageEdge[];
  downstream: LineageEdge[];
  nodes: Array<{ id: string; fqn: string; asset_type: string; tier: string; name: string }>;
  edges: LineageEdge[];
}

const TIER_COLOR: Record<string, string> = {
  critical: "#cf1124",
  important: "#d97706",
  normal: "#2e66f0",
};

const TIER_BG: Record<string, string> = {
  critical: "#fde8e8",
  important: "#fff3e0",
  normal: "#e8f0fe",
};

const SOURCE_LABEL: Record<string, string> = {
  dbt: "dbt",
  superset: "Superset",
  airflow: "Airflow",
  git_blame: "Git",
  llm_inferred: "LLM",
  manual: "Manual",
};

/**
 * 自动布局: 中心表放中央, 上游放左侧, 下游放右侧, 按 BFS 层级拉远.
 * 锐利节点 (border-radius: 0), tier 颜色边框.
 */
function layoutGraph(graph: LineageGraph, centerId: string): { nodes: Node[]; edges: Edge[] } {
  if (!graph) return { nodes: [], edges: [] };

  const COL_WIDTH = 280;
  const ROW_HEIGHT = 100;
  const nodesById = new Map<
    string,
    { fqn: string; tier: string; col: number; row: number; asset_type: string }
  >();
  const queue: Array<{ id: string; col: number }> = [{ id: centerId, col: 0 }];
  const seen = new Set<string>([centerId]);
  const centerNode = graph.nodes.find((x) => x.id === centerId);
  nodesById.set(centerId, {
    fqn: centerNode?.fqn ?? graph.center_fqn,
    tier: centerNode?.tier ?? "critical",
    col: 0,
    row: 0,
    asset_type: centerNode?.asset_type ?? "table",
  });

  // BFS
  while (queue.length) {
    const { id, col } = queue.shift()!;
    const ups = graph.upstream.filter((e) => e.downstream_id === id);
    ups.forEach((e) => {
      if (!seen.has(e.upstream_id)) {
        seen.add(e.upstream_id);
        const n = graph.nodes.find((x) => x.id === e.upstream_id);
        const fqn = e.upstream_fqn ?? n?.fqn ?? "";
        nodesById.set(e.upstream_id, {
          fqn,
          tier: n?.tier ?? "normal",
          col: col - 1,
          row: 0,
          asset_type: n?.asset_type ?? "table",
        });
        queue.push({ id: e.upstream_id, col: col - 1 });
      }
    });
    const downs = graph.downstream.filter((e) => e.upstream_id === id);
    downs.forEach((e) => {
      if (!seen.has(e.downstream_id)) {
        seen.add(e.downstream_id);
        const n = graph.nodes.find((x) => x.id === e.downstream_id);
        const fqn = e.downstream_fqn ?? n?.fqn ?? "";
        nodesById.set(e.downstream_id, {
          fqn,
          tier: n?.tier ?? "normal",
          col: col + 1,
          row: 0,
          asset_type: n?.asset_type ?? "table",
        });
        queue.push({ id: e.downstream_id, col: col + 1 });
      }
    });
  }

  // 按 col 分组算 row
  const colGroups: Record<number, string[]> = {};
  for (const [id, info] of nodesById) (colGroups[info.col] ||= []).push(id);
  for (const col of Object.keys(colGroups)) {
    colGroups[+col].forEach((id, i) => {
      const info = nodesById.get(id)!;
      info.row = i;
    });
  }

  // 中心化行
  const maxRow = Math.max(
    ...Array.from(nodesById.values()).map((n) => n.row),
    0,
  );
  for (const info of nodesById.values()) {
    info.row = info.row - Math.floor(maxRow / 2);
  }

  const rfNodes: Node[] = [];
  for (const [id, info] of nodesById) {
    const isCenter = id === centerId;
    const borderColor = isCenter ? "#2e66f0" : TIER_COLOR[info.tier] ?? "#697077";
    const bg = isCenter ? "#e8f0fe" : TIER_BG[info.tier] ?? "#ffffff";
    rfNodes.push({
      id,
      type: "default",
      data: {
        label: (
          <div style={{ minWidth: 220, maxWidth: 260 }}>
            <div
              style={{
                fontSize: 10,
                color: borderColor,
                fontWeight: 700,
                marginBottom: 2,
                letterSpacing: 0.5,
                textTransform: "uppercase",
                display: "flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              <span
                style={{
                  width: 8,
                  height: 8,
                  background: borderColor,
                  display: "inline-block",
                }}
              />
              {info.tier.toUpperCase()} · {info.asset_type}
              {isCenter && (
                <span
                  style={{
                    marginLeft: "auto",
                    background: "#2e66f0",
                    color: "#fff",
                    padding: "0 4px",
                    fontSize: 9,
                  }}
                >
                  CENTER
                </span>
              )}
            </div>
            <div
              style={{
                fontSize: 12,
                fontWeight: isCenter ? 700 : 500,
                color: "#121619",
                wordBreak: "break-all",
                lineHeight: 1.3,
                fontFamily: "var(--idm-mono-font)",
              }}
            >
              {info.fqn}
            </div>
          </div>
        ),
      },
      position: { x: info.col * COL_WIDTH, y: info.row * ROW_HEIGHT },
      style: {
        background: bg,
        border: `${isCenter ? 2.5 : 1.5}px solid ${borderColor}`,
        borderRadius: 0,
        padding: 10,
        minWidth: 220,
        maxWidth: 280,
        fontSize: 12,
        width: 240,
      },
    });
  }

  const rfEdges: Edge[] = graph.edges
    .filter((e) => seen.has(e.upstream_id) && seen.has(e.downstream_id))
    .map((e, i) => {
      const isUp = e.upstream_id !== centerId && nodesById.get(e.downstream_id)?.col === 0;
      const c = e.confidence ?? 0.5;
      const stroke = isUp ? "#2e8540" : "#cf1124";
      return {
        id: `e${i}`,
        source: e.upstream_id,
        target: e.downstream_id,
        label: `${e.transform_type} · ${SOURCE_LABEL[e.source] ?? e.source} · ${(c * 100).toFixed(0)}%`,
        labelStyle: { fontSize: 10, fill: "#4d5358" },
        labelBgStyle: { fill: "#ffffff", stroke: "#dde1e6" },
        labelBgPadding: [4, 4] as [number, number],
        labelBgBorderRadius: 0,
        animated: e.source === "llm_inferred" || c < 0.7,
        style: {
          stroke,
          strokeWidth: 1 + c * 1.5,
          strokeDasharray: e.source === "llm_inferred" ? "5 3" : undefined,
        },
        markerEnd: { type: MarkerType.ArrowClosed, color: stroke },
      };
    });

  return { nodes: rfNodes, edges: rfEdges };
}

export function LineagePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [tier, setTier] = useState<string>("");
  const [selectedAsset, setSelectedAsset] = useState<TableAsset | null>(null);
  const [depth, setDepth] = useState(2);
  // === M2.x: view mode toggle (table | column), default to column for prominence ===
  const [viewMode, setViewMode] = useState<"table" | "column">("column");
  // === M2.x: column-level lineage drilldown ===
  const [selectedColumnName, setSelectedColumnName] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["assets", q, tier],
    queryFn: () => AssetsApi.list({ q: q || undefined, tier: tier || undefined, limit: 200 }),
  });

  const lineageQ = useQuery({
    queryKey: ["asset-lineage", selectedAsset?.id, depth],
    queryFn: () => AssetsApi.lineage(selectedAsset!.id, depth),
    enabled: !!selectedAsset,
  });

  // === M2.x: column-level lineage queries ===
  const columnsQ = useQuery({
    queryKey: ["asset-columns", selectedAsset?.id],
    queryFn: () => AssetsApi.listColumns(selectedAsset!.id),
    enabled: !!selectedAsset,
  });

  const colLineageQ = useQuery({
    queryKey: ["asset-col-lineage", selectedAsset?.id, selectedColumnName],
    queryFn: () => AssetsApi.columnLineage(selectedAsset!.id, selectedColumnName ?? undefined),
    // Always fetch when an asset is selected so column summary can render in both modes
    enabled: !!selectedAsset,
  });

  const { nodes, edges } = useMemo(() => {
    if (!lineageQ.data || !selectedAsset) return { nodes: [], edges: [] };
    return layoutGraph(lineageQ.data as LineageGraph, selectedAsset.id);
  }, [lineageQ.data, selectedAsset]);

  const onNodeClick: NodeMouseHandler = useCallback(
    (_, node) => {
      const a = data?.items.find((x) => x.id === node.id);
      if (a) setSelectedAsset(a);
    },
    [data],
  );

  const sourceBreakdown = useMemo(() => {
    if (!lineageQ.data) return {} as Record<string, number>;
    const all = [
      ...(lineageQ.data as LineageGraph).upstream,
      ...(lineageQ.data as LineageGraph).downstream,
    ];
    return all.reduce<Record<string, number>>((acc, e) => {
      acc[e.source] = (acc[e.source] ?? 0) + 1;
      return acc;
    }, {});
  }, [lineageQ.data]);

  return (
    <>
      <Card
        flush
        bodyClass=""
        title={
          <div style={{ display: "flex", alignItems: "center", gap: 8, width: "100%" }}>
            <span style={{ minWidth: 80 }}>{t("lineage.title")}</span>
            <span className="idm-text-muted" style={{ fontSize: 12, fontWeight: 400 }}>
              {t("lineage.subtitle")}
              <span
                style={{
                  marginLeft: 8,
                  fontSize: 10,
                  color: "#9b51e0",
                  fontWeight: 700,
                  letterSpacing: 0.5,
                }}
              >
                · COLUMN-LEVEL LINEAGE ENABLED
              </span>
            </span>
            <span style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
              <Input
                size="sm"
                placeholder={t("lineage.searchPlaceholder")}
                value={q}
                onChange={(e) => setQ(e.target.value)}
                style={{ width: 200 }}
              />
              <Select size="sm" value={tier} onChange={(e) => setTier(e.target.value)}>
                <option value="">{t("common.allTiers")}</option>
                <option value="critical">{t("common.tierCritical")}</option>
                <option value="important">{t("common.tierImportant")}</option>
                <option value="normal">{t("common.tierNormal")}</option>
              </Select>
              {/* === M2.x: view mode toggle (table | column) === */}
              <div
                style={{
                  display: "flex",
                  border: "1px solid var(--idm-border)",
                  marginLeft: 4,
                }}
              >
                <button
                  type="button"
                  onClick={() => setViewMode("table")}
                  style={{
                    padding: "2px 10px",
                    fontSize: 11,
                    fontWeight: 600,
                    border: "none",
                    background: viewMode === "table" ? "var(--idm-primary)" : "transparent",
                    color: viewMode === "table" ? "#fff" : "var(--idm-text-muted)",
                    cursor: "pointer",
                  }}
                >
                  TABLE
                </button>
                <button
                  type="button"
                  onClick={() => setViewMode("column")}
                  style={{
                    padding: "2px 10px",
                    fontSize: 11,
                    fontWeight: 600,
                    border: "none",
                    borderLeft: "1px solid var(--idm-border)",
                    background: viewMode === "column" ? "var(--idm-primary)" : "transparent",
                    color: viewMode === "column" ? "#fff" : "var(--idm-text-muted)",
                    cursor: "pointer",
                  }}
                >
                  COLUMN
                </button>
              </div>
            </span>
          </div>
        }
      >
        <div style={{ display: "grid", gridTemplateColumns: "260px 1fr", height: 560 }}>
          {/* === Left: asset picker === */}
          <div
            style={{
              borderRight: "1px solid var(--idm-border)",
              overflowY: "auto",
              background: "var(--idm-bg-elevated)",
            }}
          >
            <div
              style={{
                padding: "8px 12px",
                borderBottom: "1px solid var(--idm-border)",
                background: "var(--idm-gray-50)",
                fontSize: 12,
                color: "var(--idm-text-muted)",
                textTransform: "uppercase",
                letterSpacing: 0.5,
                fontWeight: 600,
              }}
            >
              Assets ({data?.items.length ?? 0})
            </div>
            {isLoading && (
              <div style={{ padding: 12, color: "var(--idm-text-muted)" }}>Loading…</div>
            )}
            {data?.items.map((a) => {
              const active = selectedAsset?.id === a.id;
              return (
                <button
                  type="button"
                  key={a.id}
                  onClick={() => setSelectedAsset(a)}
                  style={{
                    display: "block",
                    width: "100%",
                    textAlign: "left",
                    padding: "8px 12px",
                    cursor: "pointer",
                    background: active ? "var(--idm-bg-active)" : "transparent",
                    border: "none",
                    borderLeft: `2px solid ${active ? "var(--idm-primary)" : "transparent"}`,
                    borderBottom: "1px solid var(--idm-border)",
                    fontSize: 12,
                    color: "var(--idm-text)",
                    fontFamily: "inherit",
                  }}
                >
                  <div
                    style={{
                      fontFamily: "var(--idm-mono-font)",
                      color: "var(--idm-text)",
                      wordBreak: "break-all",
                    }}
                  >
                    {a.fqn}
                  </div>
                  <div
                    style={{
                      display: "flex",
                      gap: 4,
                      alignItems: "center",
                      marginTop: 2,
                      color: "var(--idm-text-muted)",
                      fontSize: 10,
                    }}
                  >
                    <span style={{ textTransform: "uppercase", fontWeight: 600 }}>
                      {a.tier}
                    </span>
                    <span>·</span>
                    <span>{a.column_count} cols</span>
                  </div>
                </button>
              );
            })}
          </div>

          {/* === Right: graph canvas (table view) OR column view === */}
          <div style={{ position: "relative", background: "var(--idm-gray-50)" }}>
            {selectedAsset ? (
              viewMode === "table" ? (
                <>
                  <div
                    style={{
                      position: "absolute",
                      top: 8,
                      left: 8,
                      zIndex: 10,
                      display: "flex",
                      gap: 8,
                      alignItems: "center",
                      background: "var(--idm-bg-elevated)",
                      border: "1px solid var(--idm-border)",
                      padding: "4px 8px",
                      fontSize: 12,
                    }}
                  >
                    <span className="idm-text-muted">{t("lineage.center")}:</span>
                    <code style={{ fontFamily: "var(--idm-mono-font)" }}>{selectedAsset.fqn}</code>
                    <span style={{ borderLeft: "1px solid var(--idm-border)", height: 16 }} />
                    <span className="idm-text-muted">{t("lineage.depth")}:</span>
                    <Input
                      size="sm"
                      type="number"
                      value={depth}
                      onChange={(e) => setDepth(Math.max(1, Math.min(5, +e.target.value || 2)))}
                      min={1}
                      max={5}
                      style={{ width: 50 }}
                    />
                    <span style={{ borderLeft: "1px solid var(--idm-border)", height: 16 }} />
                    <Tag solid color="#2e66f0">
                      {nodes.length} nodes
                    </Tag>
                    <Tag>{edges.length} edges</Tag>
                  </div>

                  <ReactFlow
                    nodes={nodes}
                    edges={edges}
                    onNodeClick={onNodeClick}
                    fitView
                    fitViewOptions={{ padding: 0.2 }}
                    proOptions={{ hideAttribution: true }}
                    minZoom={0.1}
                    maxZoom={2}
                    defaultEdgeOptions={{ type: "smoothstep" }}
                  >
                    <Background gap={20} color="#dde1e6" />
                    <Controls showInteractive={false} />
                    <MiniMap
                      nodeStrokeWidth={3}
                      nodeColor={(n) => {
                        if (n.id === selectedAsset.id) return "#2e66f0";
                        const tier = (n.data as any)?.tier ?? "normal";
                        return TIER_COLOR[tier] ?? "#697077";
                      }}
                      style={{
                        background: "#ffffff",
                        border: "1px solid var(--idm-border)",
                        borderRadius: 0,
                      }}
                      maskColor="rgba(241, 243, 246, 0.7)"
                    />
                  </ReactFlow>
                </>
              ) : (
                <ColumnLineageView
                  table={selectedAsset}
                  columns={columnsQ.data?.items ?? []}
                  edges={colLineageQ.data}
                  isLoading={colLineageQ.isLoading}
                  selectedColumn={selectedColumnName}
                  onSelectColumn={setSelectedColumnName}
                  onOpenColumnPage={() => {
                    if (selectedAsset) {
                      navigate(
                        selectedColumnName
                          ? `/lineage/column/${selectedAsset.id}/${encodeURIComponent(selectedColumnName)}`
                          : `/lineage/column/${selectedAsset.id}`,
                      );
                    }
                  }}
                />
              )
            ) : (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  height: "100%",
                }}
              >
                <EmptyState
                  title={t("lineage.selectAsset")}
                  description="Select an asset from the left panel to view its upstream & downstream graph."
                />
              </div>
            )}
          </div>
        </div>
      </Card>

      {/* === Legend + Stats === */}
      {selectedAsset && lineageQ.data && (
        <>
          <Stats>
            <Stat
              label="Upstream"
              value={(lineageQ.data as LineageGraph).upstream.length}
              hint="Sources that feed into this table"
            />
            <Stat
              label="Downstream"
              value={(lineageQ.data as LineageGraph).downstream.length}
              hint="Consumers of this table"
            />
            <Stat
              label="Related Nodes"
              value={(lineageQ.data as LineageGraph).nodes.length}
              hint="Distinct assets in the graph"
            />
            <Stat label="Edges" value={edges.length} hint="Total lineage edges" />
          </Stats>

          <Card title={t("lineage.legend")}>
            <div className="idm-flex idm-gap-4" style={{ flexWrap: "wrap", fontSize: 12 }}>
              <div className="idm-flex idm-items-center idm-gap-2">
                <span
                  style={{
                    width: 14,
                    height: 14,
                    background: "#e8f0fe",
                    border: "2.5px solid #2e66f0",
                    display: "inline-block",
                  }}
                />
                <span>{t("lineage.legendCenter")}</span>
              </div>
              <div className="idm-flex idm-items-center idm-gap-2">
                <span
                  style={{
                    width: 14,
                    height: 14,
                    background: "#ffffff",
                    border: "1.5px solid #cf1124",
                    display: "inline-block",
                  }}
                />
                <span>{t("lineage.legendCritical")}</span>
              </div>
              <div className="idm-flex idm-items-center idm-gap-2">
                <span
                  style={{
                    width: 14,
                    height: 14,
                    background: "#ffffff",
                    border: "1.5px solid #d97706",
                    display: "inline-block",
                  }}
                />
                <span>{t("lineage.legendImportant")}</span>
              </div>
              <div className="idm-flex idm-items-center idm-gap-2">
                <span
                  style={{
                    width: 14,
                    height: 14,
                    background: "#ffffff",
                    border: "1.5px solid #2e66f0",
                    display: "inline-block",
                  }}
                />
                <span>{t("lineage.legendNormal")}</span>
              </div>
              <div className="idm-flex idm-items-center idm-gap-2">
                <span
                  style={{
                    width: 24,
                    height: 2,
                    background: "#2e8540",
                    display: "inline-block",
                  }}
                />
                <span>{t("lineage.legendUpstream")}</span>
              </div>
              <div className="idm-flex idm-items-center idm-gap-2">
                <span
                  style={{
                    width: 24,
                    height: 2,
                    background: "#cf1124",
                    display: "inline-block",
                  }}
                />
                <span>{t("lineage.legendDownstream")}</span>
              </div>
              <div className="idm-flex idm-items-center idm-gap-2">
                <span
                  style={{
                    width: 24,
                    height: 2,
                    background:
                      "repeating-linear-gradient(90deg, #697077 0 5px, transparent 5px 8px)",
                    display: "inline-block",
                  }}
                />
                <span>{t("lineage.legendInferred")}</span>
              </div>
            </div>

            {/* Source breakdown */}
            {Object.keys(sourceBreakdown).length > 0 && (
              <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--idm-border)" }}>
                <div className="idm-text-muted" style={{ fontSize: 11, marginBottom: 6, textTransform: "uppercase", letterSpacing: 0.5, fontWeight: 600 }}>
                  Edges by source
                </div>
                <div className="idm-flex idm-gap-2" style={{ flexWrap: "wrap" }}>
                  {Object.entries(sourceBreakdown).map(([src, n]) => (
                    <Tag key={src} solid color="#2e66f0">
                      {SOURCE_LABEL[src] ?? src} · {n}
                    </Tag>
                  ))}
                </div>
              </div>
            )}
          </Card>
        </>
      )}

      {/* === M2.x: Column-level lineage detail panel (shown in BOTH views) === */}
      {selectedAsset && colLineageQ.data && (
        <ColumnLineageDetail
          table={selectedAsset}
          edges={colLineageQ.data}
          columns={columnsQ.data?.items ?? []}
          selectedColumn={selectedColumnName}
          onOpenColumn={(name) => {
            setSelectedColumnName(name || null);
            setViewMode("column");
          }}
        />
      )}
    </>
  );
}

// === M2.x: Column-level lineage view (replaces graph canvas in COLUMN mode) ===
interface ColumnLineageViewProps {
  table: TableAsset;
  columns: ColumnAsset[];
  edges:
    | {
        center_table_id: string;
        center_column_id: string | null;
        upstream: ColumnLineageEdge[];
        downstream: ColumnLineageEdge[];
        total: number;
      }
    | undefined;
  isLoading: boolean;
  selectedColumn: string | null;
  onSelectColumn: (name: string | null) => void;
  onOpenColumnPage?: () => void;
}

const TRANSFORM_COLOR: Record<string, string> = {
  direct: "#2e8540",
  rename: "#2e66f0",
  cast: "#d97706",
  aggregation: "#cf1124",
  expression: "#697077",
  derivation: "#9b51e0",
  passthrough: "#2e66f0",
};

function ColumnLineageView({
  table,
  columns,
  edges,
  isLoading,
  selectedColumn,
  onSelectColumn,
  onOpenColumnPage,
}: ColumnLineageViewProps) {
  const upstreamEdges = edges?.upstream ?? [];
  const downstreamEdges = edges?.downstream ?? [];
  const total = edges?.total ?? 0;

  // 1) Build column-level summary
  // upstream: edges where the center table is the downstream (i.e., the table is being fed)
  // downstream: edges where the center table is the upstream (i.e., the table is feeding)
  const upstreamByCol = new Map<string, ColumnLineageEdge[]>();
  for (const e of upstreamEdges) {
    const k = e.downstream_column_name ?? "?";
    if (!upstreamByCol.has(k)) upstreamByCol.set(k, []);
    upstreamByCol.get(k)!.push(e);
  }
  const downstreamByCol = new Map<string, ColumnLineageEdge[]>();
  for (const e of downstreamEdges) {
    const k = e.upstream_column_name ?? "?";
    if (!downstreamByCol.has(k)) downstreamByCol.set(k, []);
    downstreamByCol.get(k)!.push(e);
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Header bar */}
      <div
        style={{
          padding: "6px 10px",
          borderBottom: "1px solid var(--idm-border)",
          background: "var(--idm-bg-elevated)",
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontSize: 12,
          flexShrink: 0,
        }}
      >
        <span className="idm-text-muted">Column lineage of:</span>
        <code style={{ fontFamily: "var(--idm-mono-font)", fontWeight: 600 }}>
          {table.fqn}
        </code>
        {selectedColumn && (
          <>
            <span className="idm-text-muted">·</span>
            <span
              style={{
                fontFamily: "var(--idm-mono-font)",
                color: "var(--idm-primary)",
                fontWeight: 600,
              }}
            >
              {selectedColumn}
            </span>
            <button
              type="button"
              onClick={() => onSelectColumn(null)}
              style={{
                marginLeft: 4,
                padding: "1px 6px",
                fontSize: 10,
                border: "1px solid var(--idm-border)",
                background: "transparent",
                cursor: "pointer",
              }}
            >
              CLEAR
            </button>
          </>
        )}
        <span style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}>
          <Tag solid color="#2e8540">
            ↑ {upstreamEdges.length} upstream
          </Tag>
          <Tag solid color="#cf1124">
            ↓ {downstreamEdges.length} downstream
          </Tag>
          <Tag>{total} total</Tag>
          {onOpenColumnPage && (
            <button
              type="button"
              onClick={onOpenColumnPage}
              style={{
                marginLeft: 6,
                padding: "2px 8px",
                fontSize: 10,
                fontWeight: 600,
                border: "1px solid #9b51e0",
                background: "transparent",
                color: "#9b51e0",
                cursor: "pointer",
                textTransform: "uppercase",
                letterSpacing: 0.5,
              }}
              title="Open dedicated column lineage page"
            >
              ↗ Column Page
            </button>
          )}
        </span>
      </div>

      {isLoading && (
        <div style={{ padding: 12, color: "var(--idm-text-muted)" }}>Loading…</div>
      )}

      <div style={{ flex: 1, overflowY: "auto", padding: 8 }}>
        {/* === Column-level graph (mini lineage map) === */}
        {edges && (edges.upstream.length > 0 || edges.downstream.length > 0) && (
          <div
            style={{
              marginBottom: 12,
              border: "1px solid var(--idm-border)",
              background: "var(--idm-gray-50)",
              height: 280,
            }}
          >
            <ColumnGraph
              table={table}
              columns={columns}
              upstream={edges.upstream}
              downstream={edges.downstream}
              selectedColumn={selectedColumn}
            />
          </div>
        )}

        {/* === Columns list (clickable) === */}
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
          Columns ({columns.length})
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
            gap: 6,
            marginBottom: 12,
          }}
        >
          {columns.map((c) => {
            const upN = upstreamByCol.get(c.name)?.length ?? 0;
            const downN = downstreamByCol.get(c.name)?.length ?? 0;
            const active = selectedColumn === c.name;
            return (
              <button
                type="button"
                key={c.id}
                onClick={() => onSelectColumn(active ? null : c.name)}
                style={{
                  textAlign: "left",
                  padding: "6px 8px",
                  background: active ? "var(--idm-bg-active)" : "var(--idm-bg-elevated)",
                  border: `1px solid ${active ? "var(--idm-primary)" : "var(--idm-border)"}`,
                  borderLeft: `3px solid ${active ? "var(--idm-primary)" : "transparent"}`,
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
                    color: "var(--idm-text)",
                    wordBreak: "break-all",
                  }}
                >
                  <span style={{ flex: 1 }}>{c.name}</span>
                  <span
                    style={{
                      fontSize: 9,
                      color: "var(--idm-text-muted)",
                      fontWeight: 400,
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
                  {upN > 0 && (
                    <span style={{ color: "#2e8540", fontWeight: 600 }}>↑ {upN}</span>
                  )}
                  {downN > 0 && (
                    <span style={{ color: "#cf1124", fontWeight: 600 }}>↓ {downN}</span>
                  )}
                  {upN === 0 && downN === 0 && <span>· no lineage</span>}
                </div>
              </button>
            );
          })}
        </div>

        {/* === Edges list === */}
        {upstreamEdges.length > 0 && (
          <EdgeList
            title="Upstream column-level edges"
            color="#2e8540"
            edges={upstreamEdges}
            mode="upstream"
            selectedColumn={selectedColumn}
          />
        )}
        {downstreamEdges.length > 0 && (
          <EdgeList
            title="Downstream column-level edges"
            color="#cf1124"
            edges={downstreamEdges}
            mode="downstream"
            selectedColumn={selectedColumn}
          />
        )}
        {upstreamEdges.length === 0 && downstreamEdges.length === 0 && !isLoading && (
          <div
            style={{
              padding: 24,
              textAlign: "center",
              color: "var(--idm-text-muted)",
              fontSize: 12,
            }}
          >
            No column-level lineage yet. Run the{" "}
            <code>lineage_to_column</code> or <code>infer_column_lineage</code> skill to populate.
          </div>
        )}
      </div>
    </div>
  );
}

// === M2.x: Column-level lineage graph (mini view) ===
function ColumnGraph({
  table,
  columns,
  upstream,
  downstream,
  selectedColumn,
}: {
  table: TableAsset;
  columns: ColumnAsset[];
  upstream: ColumnLineageEdge[];
  downstream: ColumnLineageEdge[];
  selectedColumn: string | null;
}) {
  const { nodes, edges } = useMemo(() => {
    return layoutColumnGraph(table, columns, upstream, downstream, selectedColumn);
  }, [table, columns, upstream, downstream, selectedColumn]);

  if (nodes.length === 0) {
    return null;
  }

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      fitView
      fitViewOptions={{ padding: 0.15 }}
      proOptions={{ hideAttribution: true }}
      minZoom={0.3}
      maxZoom={1.5}
      defaultEdgeOptions={{ type: "smoothstep" }}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={true}
    >
      <Background gap={16} color="#dde1e6" />
      <MiniMap
        nodeStrokeWidth={2}
        nodeColor={(n) => {
          const data = n.data as any;
          if (data?.kind === "center") return "#9b51e0";
          if (data?.kind === "upstream") return "#2e8540";
          return "#cf1124";
        }}
        style={{ background: "#fff", border: "1px solid var(--idm-border)", borderRadius: 0 }}
        maskColor="rgba(241, 243, 246, 0.7)"
      />
    </ReactFlow>
  );
}

function layoutColumnGraph(
  table: TableAsset,
  columns: ColumnAsset[],
  upstream: ColumnLineageEdge[],
  downstream: ColumnLineageEdge[],
  selectedColumn: string | null,
): { nodes: Node[]; edges: Edge[] } {
  const COL_W = 180;
  const COL_GAP = 50;
  const ROW_H = 50;

  // Center column = selected column or the table itself (group of columns)
  const center = selectedColumn
    ? columns.find((c) => c.name === selectedColumn)
    : null;

  const nodes: Node[] = [];

  // Place the center column
  if (center) {
    nodes.push({
      id: `c::${center.id}`,
      type: "default",
      position: { x: 0, y: 0 },
      data: {
        kind: "center",
        label: (
          <div>
            <div style={{ fontSize: 9, color: "#9b51e0", fontWeight: 700, marginBottom: 2 }}>
              CENTER · COLUMN
            </div>
            <div
              style={{
                fontSize: 11,
                fontWeight: 700,
                color: "#121619",
                fontFamily: "var(--idm-mono-font)",
                wordBreak: "break-all",
              }}
            >
              {table.fqn.split(".").slice(-1)[0]}.{center.name}
            </div>
            <div style={{ fontSize: 9, color: "#697077", marginTop: 2 }}>
              {center.data_type}
            </div>
          </div>
        ),
      },
      style: {
        background: "#f3e8ff",
        border: "2.5px solid #9b51e0",
        borderRadius: 0,
        padding: 6,
        width: 160,
      },
    });
  } else {
    // Show all columns in a vertical stack in the center
    columns.slice(0, 6).forEach((c, i) => {
      nodes.push({
        id: `c::${c.id}`,
        type: "default",
        position: { x: 0, y: i * ROW_H - 50 },
        data: {
          kind: "center",
          label: (
            <div>
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  fontFamily: "var(--idm-mono-font)",
                  color: "#121619",
                }}
              >
                {c.name}
              </div>
              <div style={{ fontSize: 9, color: "#697077" }}>{c.data_type}</div>
            </div>
          ),
        },
        style: {
          background: "#f3e8ff",
          border: "1.5px solid #9b51e0",
          borderRadius: 0,
          padding: 4,
          width: 150,
        },
      });
    });
  }

  // Upstream columns (left side)
  const upCol = new Map<string, number>();
  let upIdx = 0;
  for (const e of upstream) {
    if (e.upstream_column_id === center?.id) continue; // skip self
    const k = `u::${e.upstream_table_id}::${e.upstream_column_id}`;
    if (upCol.has(k)) continue;
    upCol.set(k, upIdx);
    const tableShort = e.upstream_table_fqn?.split(".").slice(-1)[0] ?? "?";
    const colName = e.upstream_column_name ?? "?";
    nodes.push({
      id: k,
      type: "default",
      position: { x: -(COL_W + COL_GAP), y: (upIdx - 2) * ROW_H },
      data: {
        kind: "upstream",
        label: (
          <div>
            <div
              style={{
                fontSize: 9,
                color: "#2e8540",
                fontWeight: 700,
                marginBottom: 1,
              }}
            >
              ↑ UPSTREAM
            </div>
            <div
              style={{
                fontSize: 11,
                fontWeight: 600,
                fontFamily: "var(--idm-mono-font)",
                color: "#121619",
                wordBreak: "break-all",
              }}
            >
              {tableShort}.{colName}
            </div>
            <div style={{ fontSize: 9, color: "#697077", marginTop: 1 }}>
              {e.upstream_column_type ?? ""}
            </div>
          </div>
        ),
      },
      style: {
        background: "#e6f4ea",
        border: "1.5px solid #2e8540",
        borderRadius: 0,
        padding: 4,
        width: 150,
      },
    });
    upIdx++;
  }

  // Downstream columns (right side)
  const downCol = new Map<string, number>();
  let downIdx = 0;
  for (const e of downstream) {
    if (e.downstream_column_id === center?.id) continue; // skip self
    const k = `d::${e.downstream_table_id}::${e.downstream_column_id}`;
    if (downCol.has(k)) continue;
    downCol.set(k, downIdx);
    const tableShort = e.downstream_table_fqn?.split(".").slice(-1)[0] ?? "?";
    const colName = e.downstream_column_name ?? "?";
    nodes.push({
      id: k,
      type: "default",
      position: { x: COL_W + COL_GAP, y: (downIdx - 2) * ROW_H },
      data: {
        kind: "downstream",
        label: (
          <div>
            <div
              style={{
                fontSize: 9,
                color: "#cf1124",
                fontWeight: 700,
                marginBottom: 1,
              }}
            >
              ↓ DOWNSTREAM
            </div>
            <div
              style={{
                fontSize: 11,
                fontWeight: 600,
                fontFamily: "var(--idm-mono-font)",
                color: "#121619",
                wordBreak: "break-all",
              }}
            >
              {tableShort}.{colName}
            </div>
            <div style={{ fontSize: 9, color: "#697077", marginTop: 1 }}>
              {e.downstream_column_type ?? ""}
            </div>
          </div>
        ),
      },
      style: {
        background: "#fde8e8",
        border: "1.5px solid #cf1124",
        borderRadius: 0,
        padding: 4,
        width: 150,
      },
    });
    downIdx++;
  }

  // Edges
  const rfEdges: Edge[] = [];
  let edgeIdx = 0;
  for (const e of upstream) {
    const src = `u::${e.upstream_table_id}::${e.upstream_column_id}`;
    const tgt = center
      ? `c::${center.id}`
      : `c::${e.downstream_column_id}`;
    if (!nodes.find((n) => n.id === src) || !nodes.find((n) => n.id === tgt)) continue;
    rfEdges.push({
      id: `e_${edgeIdx++}`,
      source: src,
      target: tgt,
      label: e.transform_type,
      labelStyle: { fontSize: 9, fill: "#4d5358" },
      labelBgStyle: { fill: "#fff", stroke: "#dde1e6" },
      labelBgPadding: [2, 2] as [number, number],
      labelBgBorderRadius: 0,
      style: { stroke: "#2e8540", strokeWidth: 1.2 },
      markerEnd: { type: MarkerType.ArrowClosed, color: "#2e8540" },
      data: { tooltip: e.transform_expression ?? e.description ?? "" },
    });
  }
  for (const e of downstream) {
    const src = center
      ? `c::${center.id}`
      : `c::${e.upstream_column_id}`;
    const tgt = `d::${e.downstream_table_id}::${e.downstream_column_id}`;
    if (!nodes.find((n) => n.id === src) || !nodes.find((n) => n.id === tgt)) continue;
    rfEdges.push({
      id: `e_${edgeIdx++}`,
      source: src,
      target: tgt,
      label: e.transform_type,
      labelStyle: { fontSize: 9, fill: "#4d5358" },
      labelBgStyle: { fill: "#fff", stroke: "#dde1e6" },
      labelBgPadding: [2, 2] as [number, number],
      labelBgBorderRadius: 0,
      style: { stroke: "#cf1124", strokeWidth: 1.2 },
      markerEnd: { type: MarkerType.ArrowClosed, color: "#cf1124" },
      data: { tooltip: e.transform_expression ?? e.description ?? "" },
    });
  }

  return { nodes, edges: rfEdges };
}

interface EdgeListProps {
  title: string;
  color: string;
  edges: ColumnLineageEdge[];
  mode: "upstream" | "downstream";
  selectedColumn: string | null;
}

function EdgeList({ title, color, edges, mode, selectedColumn }: EdgeListProps) {
  // Group by transform_type
  const byType = edges.reduce<Record<string, ColumnLineageEdge[]>>((acc, e) => {
    (acc[e.transform_type] ||= []).push(e);
    return acc;
  }, {});

  // Filter to selected column if any
  const filtered = selectedColumn
    ? edges.filter((e) =>
        mode === "upstream"
          ? e.upstream_column_name === selectedColumn
          : e.downstream_column_name === selectedColumn,
      )
    : edges;

  return (
    <div style={{ marginBottom: 12 }}>
      <div
        style={{
          fontSize: 10,
          color,
          textTransform: "uppercase",
          letterSpacing: 0.5,
          fontWeight: 700,
          marginBottom: 4,
          borderBottom: `2px solid ${color}`,
          paddingBottom: 2,
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        {title} ({filtered.length})
        {selectedColumn && (
          <span style={{ marginLeft: 6, color: "var(--idm-text-muted)", fontWeight: 400 }}>
            filtered by {selectedColumn}
          </span>
        )}
      </div>

      {selectedColumn && filtered.length === 0 && (
        <div
          style={{
            padding: 12,
            fontSize: 11,
            color: "var(--idm-text-muted)",
            fontStyle: "italic",
          }}
        >
          No {mode} edges for column <code>{selectedColumn}</code>.
        </div>
      )}

      {Object.entries(byType)
        .filter(([, list]) =>
          selectedColumn
            ? list.some((e) =>
                mode === "upstream"
                  ? e.upstream_column_name === selectedColumn
                  : e.downstream_column_name === selectedColumn,
              )
            : true,
        )
        .map(([tt, list]) => {
          const visible = selectedColumn
            ? list.filter((e) =>
                mode === "upstream"
                  ? e.upstream_column_name === selectedColumn
                  : e.downstream_column_name === selectedColumn,
              )
            : list;
          return (
            <div key={tt} style={{ marginBottom: 6 }}>
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
                <span>{visible.length} edges</span>
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
                  {visible.map((e) => (
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
                            {e.transform_expression.length > 60
                              ? e.transform_expression.slice(0, 60) + "…"
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
                          {e.component && <span>[{e.component}]</span>}
                          {e.source && <span>· {e.source}</span>}
                          {e.confidence != null && (
                            <span>· {(e.confidence * 100).toFixed(0)}%</span>
                          )}
                          {e.description_source && (
                            <span>· desc:{e.description_source}</span>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        })}
    </div>
  );
}

// === M2.x: Column-level lineage summary stats card ===
function ColumnLineageDetail({
  table,
  edges,
  columns,
  selectedColumn,
  onOpenColumn,
}: {
  table: TableAsset;
  edges: {
    upstream: ColumnLineageEdge[];
    downstream: ColumnLineageEdge[];
    total: number;
  };
  columns: ColumnAsset[];
  selectedColumn: string | null;
  onOpenColumn: (columnName: string) => void;
}) {
  // Coverage: how many columns in this table have at least one upstream or downstream edge
  const colsWithLineage = new Set<string>();
  for (const e of edges.upstream) {
    if (e.upstream_column_name) colsWithLineage.add(e.upstream_column_name);
  }
  for (const e of edges.downstream) {
    if (e.downstream_column_name) colsWithLineage.add(e.downstream_column_name);
  }
  const coveragePct = columns.length
    ? ((colsWithLineage.size / columns.length) * 100).toFixed(0)
    : "0";

  const transformTypes: Record<string, number> = {};
  for (const e of [...edges.upstream, ...edges.downstream]) {
    transformTypes[e.transform_type] = (transformTypes[e.transform_type] ?? 0) + 1;
  }
  const components: Record<string, number> = {};
  for (const e of [...edges.upstream, ...edges.downstream]) {
    if (e.component) components[e.component] = (components[e.component] ?? 0) + 1;
  }

  // Build per-column edge counts
  const upByCol = new Map<string, number>();
  const downByCol = new Map<string, number>();
  for (const e of edges.upstream) {
    const k = e.upstream_column_name ?? "?";
    upByCol.set(k, (upByCol.get(k) ?? 0) + 1);
  }
  for (const e of edges.downstream) {
    const k = e.downstream_column_name ?? "?";
    downByCol.set(k, (downByCol.get(k) ?? 0) + 1);
  }

  return (
    <Card
      title={
        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 11, color: "var(--idm-text-muted)" }}>
            COLUMN-LEVEL LINEAGE
          </span>
          <code style={{ fontFamily: "var(--idm-mono-font)", fontSize: 13 }}>{table.fqn}</code>
          {selectedColumn && (
            <Tag solid color="var(--idm-primary)">
              filtered: {selectedColumn}
            </Tag>
          )}
        </span>
      }
    >
      <Stats>
        <Stat
          label="Upstream edges"
          value={edges.upstream.length}
          hint="Source column-level dependencies"
        />
        <Stat
          label="Downstream edges"
          value={edges.downstream.length}
          hint="Target column-level dependencies"
        />
        <Stat
          label="Columns with lineage"
          value={`${colsWithLineage.size} / ${columns.length}`}
          hint={`${coveragePct}% coverage`}
        />
        <Stat label="Total column edges" value={edges.total} hint="upstream + downstream" />
      </Stats>

      <div style={{ display: "flex", gap: 24, flexWrap: "wrap", fontSize: 12, marginTop: 8 }}>
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
            {Object.keys(transformTypes).length === 0 && (
              <span className="idm-text-muted" style={{ fontSize: 11 }}>
                none
              </span>
            )}
            {Object.entries(transformTypes).map(([tt, n]) => (
              <Tag key={tt} solid color={TRANSFORM_COLOR[tt] ?? "#697077"}>
                {tt} · {n}
              </Tag>
            ))}
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
            {Object.keys(components).length === 0 && (
              <span className="idm-text-muted" style={{ fontSize: 11 }}>
                none
              </span>
            )}
            {Object.entries(components).map(([c, n]) => (
              <Tag key={c}>{c} · {n}</Tag>
            ))}
          </div>
        </div>
      </div>

      {/* === Per-column lineage list (clickable, drives selection) === */}
      {columns.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div
            style={{
              fontSize: 10,
              color: "var(--idm-text-muted)",
              textTransform: "uppercase",
              letterSpacing: 0.5,
              fontWeight: 600,
              marginBottom: 6,
            }}
          >
            Per-column lineage · click to drill down
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
              gap: 4,
            }}
          >
            {columns.map((c) => {
              const upN = upByCol.get(c.name) ?? 0;
              const downN = downByCol.get(c.name) ?? 0;
              const active = selectedColumn === c.name;
              return (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => onOpenColumn(active ? "" : c.name)}
                  style={{
                    textAlign: "left",
                    padding: "4px 6px",
                    background: active ? "var(--idm-bg-active)" : "var(--idm-bg-elevated)",
                    border: `1px solid ${active ? "var(--idm-primary)" : "var(--idm-border)"}`,
                    borderLeft: `3px solid ${active ? "var(--idm-primary)" : "transparent"}`,
                    cursor: "pointer",
                    fontSize: 11,
                    fontFamily: "var(--idm-mono-font)",
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                  title={
                    upN > 0 || downN > 0
                      ? `${c.name}: ↑${upN} downstream / ↓${downN} downstream`
                      : `${c.name}: no column-level lineage`
                  }
                >
                  <span style={{ flex: 1, color: "var(--idm-text)" }}>{c.name}</span>
                  <span
                    style={{
                      fontSize: 9,
                      color: "var(--idm-text-muted)",
                      textTransform: "lowercase",
                    }}
                  >
                    {c.data_type}
                  </span>
                  {upN > 0 && (
                    <span style={{ color: "#2e8540", fontWeight: 600 }}>↑{upN}</span>
                  )}
                  {downN > 0 && (
                    <span style={{ color: "#cf1124", fontWeight: 600 }}>↓{downN}</span>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {selectedColumn && (
        <div
          style={{
            marginTop: 8,
            padding: 8,
            background: "var(--idm-gray-50)",
            borderLeft: "3px solid var(--idm-primary)",
            fontSize: 12,
            color: "var(--idm-text-muted)",
          }}
        >
          Click a column card on the right to drill into its lineage. Currently filtered by:{" "}
          <code style={{ color: "var(--idm-primary)" }}>{selectedColumn}</code>
        </div>
      )}
    </Card>
  );
}
