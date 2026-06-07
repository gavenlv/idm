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
import { AssetsApi, type TableAsset } from "../lib/api";
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
  const [q, setQ] = useState("");
  const [tier, setTier] = useState<string>("");
  const [selectedAsset, setSelectedAsset] = useState<TableAsset | null>(null);
  const [depth, setDepth] = useState(2);

  const { data, isLoading } = useQuery({
    queryKey: ["assets", q, tier],
    queryFn: () => AssetsApi.list({ q: q || undefined, tier: tier || undefined, limit: 200 }),
  });

  const lineageQ = useQuery({
    queryKey: ["asset-lineage", selectedAsset?.id, depth],
    queryFn: () => AssetsApi.lineage(selectedAsset!.id, depth),
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

          {/* === Right: graph canvas === */}
          <div style={{ position: "relative", background: "var(--idm-gray-50)" }}>
            {selectedAsset ? (
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
    </>
  );
}
