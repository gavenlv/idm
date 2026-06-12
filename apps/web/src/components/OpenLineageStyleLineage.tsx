/**
 * OpenLineageStyleLineage — M2.5+ OpenLineage/Marquez-style column lineage graph.
 *
 * 设计参考:
 *  - OpenLineage/Marquez UI: tables as boxes with column list, edges connect specific columns
 *  - DataHub: column-level view with health/PII indicators
 *
 * 区别于 (简单) 单列图: 这里一次显示**多张表**的列与列之间的连线, 像 Marquez 那样.
 *
 * 用法:
 *   <OpenLineageStyleLineage
 *     centerTableId="..."
 *     centerColumnName="..."
 *     upstream={ColumnLineageEdge[]}
 *     downstream={ColumnLineageEdge[]}
 *     tables={TableAsset[]}
 *     columns={ColumnAsset[]}
 *   />
 */
import { useMemo } from "react";
import ReactFlow, {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  type Edge,
  type Node,
} from "reactflow";
import type { ColumnAsset, ColumnLineageEdge, TableAsset } from "../lib/api";

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

interface Props {
  centerTableId: string;
  centerColumnName?: string;
  upstream: ColumnLineageEdge[];
  downstream: ColumnLineageEdge[];
  tables: TableAsset[];
  columns: ColumnAsset[];
}

export function OpenLineageStyleLineage({
  centerTableId,
  centerColumnName,
  upstream,
  downstream,
  tables,
  columns,
}: Props) {
  const { nodes, edges } = useMemo(() => {
    return buildOpenLineageGraph({
      centerTableId,
      centerColumnName,
      upstream,
      downstream,
      tables,
      columns,
    });
  }, [centerTableId, centerColumnName, upstream, downstream, tables, columns]);

  if (nodes.length === 0) {
    return (
      <div
        style={{
          padding: 24,
          textAlign: "center",
          color: "var(--idm-text-muted)",
          fontSize: 12,
        }}
      >
        No column-level edges to display.
      </div>
    );
  }

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={NODE_TYPES}
      fitView
      fitViewOptions={{ padding: 0.15 }}
      proOptions={{ hideAttribution: true }}
      minZoom={0.1}
      maxZoom={2.5}
      defaultEdgeOptions={{ type: "smoothstep" }}
    >
      <Background gap={20} color="#dde1e6" />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}

// === 自定义 table-box 节点 (含每列的 left/right handle) ===
interface TableBoxData {
  fqn: string;
  tier: string;
  isCenter: boolean;
  borderColor: string;
  bg: string;
  columns: { id: string; name: string; data_type: string; pii_class?: string }[];
  isUpstream: boolean;
  isDownstream: boolean;
  highlightColumnId?: string;
}

function TableBoxNode({ data }: { data: TableBoxData }) {
  return (
    <div
      style={{
        background: data.bg,
        border: `${data.isCenter ? 2.5 : 1.5}px solid ${data.borderColor}`,
        borderRadius: 0,
        padding: 8,
        minWidth: 200,
        maxWidth: 200,
        fontSize: 11,
      }}
    >
      <div
        style={{
          fontSize: 9,
          color: data.borderColor,
          fontWeight: 700,
          marginBottom: 4,
          textTransform: "uppercase",
          letterSpacing: 0.5,
          display: "flex",
          alignItems: "center",
          gap: 4,
          borderBottom: `1px solid ${data.borderColor}`,
          paddingBottom: 4,
        }}
      >
        {data.isCenter ? "★ CENTER · TABLE" : data.isUpstream ? "↑ UPSTREAM" : data.isDownstream ? "↓ DOWNSTREAM" : "TABLE"}
        <span
          style={{
            marginLeft: "auto",
            background: TIER_COLOR[data.tier] ?? "#697077",
            color: "#fff",
            padding: "0 4px",
            fontSize: 8,
            fontWeight: 700,
          }}
        >
          {data.tier}
        </span>
      </div>
      <div
        style={{
          fontSize: 10,
          color: "#121619",
          fontWeight: 600,
          fontFamily: "var(--idm-mono-font)",
          wordBreak: "break-all",
          lineHeight: 1.3,
          marginBottom: 6,
        }}
      >
        {data.fqn.split(".").slice(-2).join(".")}
      </div>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 1,
          fontFamily: "var(--idm-mono-font)",
          fontSize: 9,
        }}
      >
        {data.columns.map((c, i) => {
          const isCenterCol = data.highlightColumnId === c.id;
          const isPII = c.pii_class && c.pii_class !== "none";
          return (
            <div
              key={c.id}
              style={{
                position: "relative",
                display: "flex",
                alignItems: "center",
                gap: 4,
                padding: "1px 3px",
                background: isCenterCol
                  ? "#fef3c7"
                  : isPII
                    ? "#fee2e2"
                    : "transparent",
                borderLeft: isCenterCol
                  ? "2px solid #9b51e0"
                  : isPII
                    ? "2px solid #cf1124"
                    : "2px solid transparent",
              }}
            >
              {/* 每列的 left/right handle (隐藏, 实际接点) */}
              <Handle
                id={`col-${i}`}
                type="target"
                position={Position.Left}
                style={{ background: "transparent", border: "none", width: 1, height: 1, left: 0, top: "50%" }}
              />
              <Handle
                id={`col-${i}`}
                type="source"
                position={Position.Right}
                style={{ background: "transparent", border: "none", width: 1, height: 1, right: 0, top: "50%" }}
              />
              <span style={{ flex: 1, color: "#121619" }}>{c.name}</span>
              {isPII && (
                <span
                  style={{
                    fontSize: 7,
                    color: "#cf1124",
                    fontWeight: 700,
                    textTransform: "uppercase",
                  }}
                >
                  PII
                </span>
              )}
              <span
                style={{
                  fontSize: 7,
                  color: "var(--idm-text-muted)",
                  fontWeight: 400,
                }}
              >
                {c.data_type.length > 12 ? c.data_type.slice(0, 12) + "…" : c.data_type}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

const NODE_TYPES = { tableBox: TableBoxNode };

// === 构图算法 ===
// 1) 中心表 (centerTableId) 在中间
// 2) 上游表在左 (按 BFS 距离分层)
// 3) 下游表在右
// 4) 每个表是 1 个 "table box" 节点, 里面列出 column names (PII 高亮)
function buildOpenLineageGraph(args: Props): {
  nodes: Node[];
  edges: Edge[];
} {
  const { centerTableId, centerColumnName, upstream, downstream, tables, columns } = args;

  // 1) 找出所有涉及的 table
  const tableIds = new Set<string>([centerTableId]);
  for (const e of [...upstream, ...downstream]) {
    tableIds.add(e.upstream_table_id);
    tableIds.add(e.downstream_table_id);
  }
  const tableById = new Map<string, TableAsset>();
  for (const t of tables) {
    if (tableIds.has(t.id)) tableById.set(t.id, t);
  }
  const involvedTables = Array.from(tableById.values());

  // 2) 找出所有涉及的 column
  const colById = new Map<string, ColumnAsset>();
  const colByTable = new Map<string, ColumnAsset[]>();
  for (const c of columns) {
    colById.set(c.id, c);
    if (tableIds.has(c.table_id)) {
      const arr = colByTable.get(c.table_id) ?? [];
      arr.push(c);
      colByTable.set(c.table_id, arr);
    }
  }

  // 3) 给每张表算 BFS 距离 (相对于 center)
  //    distance = 0 (center), 1 (直接上下游), 2 (2 跳)
  const distance: Record<string, number> = { [centerTableId]: 0 };
  // build adjacency
  const adj: Record<string, Set<string>> = {};
  for (const tid of tableIds) adj[tid] = new Set();
  for (const e of [...upstream, ...downstream]) {
    adj[e.upstream_table_id]?.add(e.downstream_table_id);
    adj[e.downstream_table_id]?.add(e.upstream_table_id);
  }
  // BFS
  const queue: string[] = [centerTableId];
  while (queue.length > 0) {
    const cur = queue.shift()!;
    const d = distance[cur];
    for (const n of adj[cur] ?? []) {
      if (distance[n] === undefined || distance[n] > d + 1) {
        distance[n] = d + 1;
        queue.push(n);
      }
    }
  }

  // 4) 按 distance 分列, 中心 (0) 在 col=0, 上游 (-1, -2) 在左, 下游 (+1, +2) 在右
  const TABLE_WIDTH = 200;
  const COL_GAP = 80;

  // 按 distance 排序
  const sorted = involvedTables.sort((a, b) => {
    return (distance[a.id] ?? 99) - (distance[b.id] ?? 99);
  });

  // 把 tables 按 distance 分组
  const byDist: Record<number, TableAsset[]> = {};
  for (const t of sorted) {
    const d = distance[t.id] ?? 0;
    byDist[d] = byDist[d] ?? [];
    byDist[d].push(t);
  }
  const distances = Object.keys(byDist)
    .map(Number)
    .sort((a, b) => a - b);
  // 简化: 直接 left-justify, center = max(upstream) + 1
  // 实际上重新分配 col index
  const tableCol: Record<string, number> = {};
  let col = 0;
  for (const d of distances) {
    if (byDist[d].some((t) => t.id === centerTableId)) {
      // center col
      for (const t of byDist[d]) tableCol[t.id] = col;
      col++;
    }
  }
  // 中心 col 找到后, 上游 dist 放左边, 下游 dist 放右边
  const centerCol = tableCol[centerTableId] ?? 0;
  let leftCursor = centerCol - 1;
  let rightCursor = centerCol + 1;
  for (const d of distances) {
    if (d === 0) continue;
    for (const t of byDist[d]) {
      // 判断是上游还是下游
      const isUp = isUpstream(t.id, centerTableId, [...upstream, ...downstream]);
      if (isUp) {
        tableCol[t.id] = leftCursor--;
      } else {
        tableCol[t.id] = rightCursor++;
      }
    }
  }
  // 把 leftCursor / rightCursor 重新 normalize
  const allCols = Object.values(tableCol);
  const minCol = Math.min(...allCols);
  for (const k of Object.keys(tableCol)) {
    tableCol[k] = tableCol[k] - minCol;
  }

  // 5) 给每张表内的 column 算 row
  const tableRow: Record<string, Record<string, number>> = {};
  for (const t of involvedTables) {
    const cs = colByTable.get(t.id) ?? [];
    cs.sort((a, b) => a.ordinal - b.ordinal);
    const rows: Record<string, number> = {};
    cs.forEach((c, i) => {
      rows[c.id] = i;
    });
    tableRow[t.id] = rows;
  }

  // 6) 构 ReactFlow nodes (1 个 node per table, 使用 tableBox 自定义节点)
  const rfNodes: Node[] = involvedTables.map((t) => {
    const cs = colByTable.get(t.id) ?? [];
    cs.sort((a, b) => a.ordinal - b.ordinal);
    const isCenter = t.id === centerTableId;
    const dist = distance[t.id] ?? 0;
    const centerDist = distance[centerTableId] ?? 0;
    const isUp = dist < centerDist;
    const isDown = dist > centerDist;
    const borderColor = isCenter
      ? "#9b51e0"
      : isUp
        ? "#2e8540"
        : isDown
          ? "#cf1124"
          : "#697077";
    const bg = isCenter ? "#f3e8ff" : "#ffffff";
    const highlightColId = isCenter
      ? cs.find((c) => c.name === centerColumnName)?.id
      : undefined;
    return {
      id: `t::${t.id}`,
      type: "tableBox",
      data: {
        fqn: t.fqn,
        tier: t.tier,
        isCenter,
        isUpstream: isUp && !isCenter,
        isDownstream: isDown && !isCenter,
        borderColor,
        bg,
        columns: cs.map((c) => ({
          id: c.id,
          name: c.name,
          data_type: c.data_type,
          pii_class: c.pii_class,
        })),
        highlightColumnId: highlightColId,
      } as TableBoxData,
      position: {
        x: (tableCol[t.id] ?? 0) * (TABLE_WIDTH + COL_GAP),
        y: 0,
      },
    };
  });

  // 7) 构 ReactFlow edges (1 条 per column_lineage)
  //    从 upstream table 的对应 column row → downstream table 的对应 column row
  //    因为 ReactFlow node 是 table-box, 边要接 table-box 的 handle
  //    这里用 leftHandle / rightHandle via sourceHandle/targetHandle
  const rfEdges: Edge[] = [...upstream, ...downstream].map((e, i) => {
    const stroke = TRANSFORM_COLOR[e.transform_type] ?? "#697077";
    const isUp = upstream.includes(e);
    const sourceId = `t::${e.upstream_table_id}`;
    const targetId = `t::${e.downstream_table_id}`;
    const sourceRow = tableRow[e.upstream_table_id]?.[e.upstream_column_id] ?? 0;
    const targetRow = tableRow[e.downstream_table_id]?.[e.downstream_column_id] ?? 0;
    return {
      id: `e${i}`,
      source: sourceId,
      target: targetId,
      sourceHandle: `col-${sourceRow}`,
      targetHandle: `col-${targetRow}`,
      label: `${isUp ? "↑" : "↓"} ${e.transform_type}`,
      labelStyle: { fontSize: 8, fill: "#4d5358" },
      labelBgStyle: { fill: "#ffffff", stroke, strokeWidth: 1 },
      labelBgPadding: [2, 2] as [number, number],
      labelBgBorderRadius: 0,
      animated: e.source === "ai_inferred" || (e.confidence ?? 1) < 0.7,
      style: {
        stroke,
        strokeWidth: 1 + (e.confidence ?? 1) * 1.0,
        strokeDasharray: e.source === "ai_inferred" ? "5 3" : undefined,
      },
      markerEnd: { type: MarkerType.ArrowClosed, color: stroke },
      title: [
        `${e.upstream_column_name} → ${e.downstream_column_name}`,
        e.transform_expression,
        e.description,
        `[${e.component ?? "?"}] ${e.source} · ${((e.confidence ?? 1) * 100).toFixed(0)}%`,
      ]
        .filter(Boolean)
        .join("\n"),
    };
  });

  return { nodes: rfNodes, edges: rfEdges };
}

// 简单判断: t_id 在 center_id 的上游吗?
// (BFS from t_id, 看能否 reach center)
function isUpstream(
  tId: string,
  centerId: string,
  edges: ColumnLineageEdge[],
): boolean {
  if (tId === centerId) return false;
  // BFS
  const visited = new Set<string>([tId]);
  const queue: string[] = [tId];
  while (queue.length > 0) {
    const cur = queue.shift()!;
    if (cur === centerId) return true;
    for (const e of edges) {
      if (e.upstream_table_id === cur && !visited.has(e.downstream_table_id)) {
        visited.add(e.downstream_table_id);
        queue.push(e.downstream_table_id);
      }
      if (e.downstream_table_id === cur && !visited.has(e.upstream_table_id)) {
        visited.add(e.upstream_table_id);
        queue.push(e.upstream_table_id);
      }
    }
  }
  return false;
}
