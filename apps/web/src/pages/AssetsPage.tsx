import { useQuery } from "@tanstack/react-query";
import { AgGridReact } from "ag-grid-react";
import { useMemo, useState } from "react";
import { AssetsApi, type TableAsset, type ColumnAsset, type AssetPiiSummary } from "../lib/api";
import { piiColor, piiRiskLabel } from "../lib/pii";
import { Card, Drawer, Tag, Button } from "../ui";

export function AssetsPage() {
  const [q, setQ] = useState("");
  const [tier, setTier] = useState<string>("");
  const [selected, setSelected] = useState<TableAsset | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["assets", q, tier],
    queryFn: () => AssetsApi.list({ q: q || undefined, tier: tier || undefined, limit: 200 }),
  });

  // 详情加载列 + PII 摘要
  const colsQ = useQuery({
    queryKey: ["asset-cols", selected?.id],
    queryFn: () => AssetsApi.listColumns(selected!.id),
    enabled: !!selected,
  });
  const piiQ = useQuery({
    queryKey: ["asset-pii", selected?.id],
    queryFn: () => AssetsApi.piiSummary(selected!.id),
    enabled: !!selected,
  });

  const columnDefs = useMemo(
    () => [
      { field: "fqn", headerName: "FQN", flex: 2, sortable: true, filter: true },
      { field: "asset_type", headerName: "Type", width: 130 },
      {
        field: "tier",
        headerName: "Tier",
        width: 110,
        cellRenderer: (p: { value: string }) => (
          <Tag color={p.value === "critical" ? "#d4380d" : p.value === "important" ? "#fa8c16" : "#999"}>
            {p.value}
          </Tag>
        ),
      },
      { field: "column_count", headerName: "Cols", width: 90 },
      {
        field: "row_count",
        headerName: "Rows",
        width: 110,
        valueFormatter: (p: { value: number | null }) => (p.value == null ? "—" : p.value.toLocaleString()),
      },
      { field: "description", headerName: "Description", flex: 3, tooltipField: "description" },
    ],
    [],
  );

  const colColumnDefs = useMemo(
    () => [
      { field: "ordinal", headerName: "#", width: 60, sortable: true },
      { field: "name", headerName: "列名", flex: 1.4, sortable: true, filter: true },
      { field: "data_type", headerName: "类型", width: 130 },
      {
        field: "is_primary_key",
        headerName: "PK",
        width: 60,
        cellRenderer: (p: { value: boolean }) => (p.value ? <Tag color="#1f6feb">PK</Tag> : ""),
      },
      {
        field: "pii_class",
        headerName: "PII",
        width: 150,
        cellRenderer: (p: { data: ColumnAsset; value: string }) =>
          p.value && p.value !== "none" ? (
            <Tag color={piiColor(p.value)}>
              {p.value} · {piiRiskLabel(p.value)}
            </Tag>
          ) : (
            <span style={{ color: "#bbb" }}>—</span>
          ),
        sortable: true,
      },
      {
        field: "pii_confidence",
        headerName: "Conf",
        width: 90,
        cellRenderer: (p: { data: ColumnAsset; value: number }) =>
          p.data.pii_class !== "none" ? `${(p.value * 100).toFixed(0)}%` : "",
      },
      {
        field: "sample_values",
        headerName: "样本",
        flex: 2,
        tooltipValueGetter: (p: { data: ColumnAsset }) => JSON.stringify(p.data.sample_values ?? []),
        valueFormatter: (p: { value: unknown[] }) =>
          Array.isArray(p.value) ? p.value.slice(0, 3).map((v) => (v == null ? "∅" : String(v).slice(0, 20))).join(", ") : "",
      },
    ],
    [],
  );

  return (
    <>
      <Card
        title={`资产 (${data?.total ?? 0})`}
        extra={
          <div style={{ display: "flex", gap: 8 }}>
            <input
              placeholder="按 name / fqn 搜索"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              style={{ padding: "4px 8px", border: "1px solid #d9d9d9", borderRadius: 4 }}
            />
            <select
              value={tier}
              onChange={(e) => setTier(e.target.value)}
              style={{ padding: "4px 8px", border: "1px solid #d9d9d9", borderRadius: 4 }}
            >
              <option value="">全部 tier</option>
              <option value="critical">critical</option>
              <option value="important">important</option>
              <option value="normal">normal</option>
            </select>
          </div>
        }
      >
        <div className="ag-theme-quartz" style={{ height: 560, width: "100%" }}>
          <AgGridReact
            rowData={data?.items ?? []}
            columnDefs={columnDefs}
            loading={isLoading}
            pagination
            paginationPageSize={50}
            onRowClicked={(e) => setSelected(e.data ?? null)}
          />
        </div>
      </Card>

      <Drawer
        open={!!selected}
        onClose={() => setSelected(null)}
        title={selected?.fqn ?? ""}
        width={720}
      >
        {selected && (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {/* 基础元数据 */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, fontSize: 13 }}>
              <div>
                <b>Tier:</b> <Tag>{selected.tier}</Tag>
              </div>
              <div>
                <b>Type:</b> {selected.asset_type}
              </div>
              <div>
                <b>Columns:</b> {selected.column_count}
              </div>
              <div>
                <b>Status:</b> {selected.status}
              </div>
            </div>

            <div>
              <b>Description:</b>
              <blockquote style={{ background: "#f7f8fa", padding: 10, borderRadius: 6, margin: "6px 0" }}>
                {selected.description ?? <em style={{ color: "#bbb" }}>暂无描述</em>}
              </blockquote>
            </div>

            {/* PII 合规摘要 */}
            {piiQ.data && <PiiCard summary={piiQ.data} />}

            {/* Lineage 上下游 */}
            {lineageQ.data && <LineageView graph={lineageQ.data} />}

            {/* 列清单 */}
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                <b>列清单 ({colsQ.data?.total ?? 0})</b>
                {colsQ.isLoading && <span style={{ fontSize: 12, color: "#999" }}>加载中…</span>}
              </div>
              <div className="ag-theme-quartz" style={{ height: 320, width: "100%", border: "1px solid #eee" }}>
                <AgGridReact
                  rowData={colsQ.data?.items ?? []}
                  columnDefs={colColumnDefs}
                  pagination
                  paginationPageSize={20}
                  getRowStyle={(p) =>
                    p.data?.pii_class && HIGH_PII.has(p.data.pii_class)
                      ? { background: "#fff1f0" }
                      : undefined
                  }
                />
              </div>
            </div>

            <div>
              <Button onClick={() => setSelected(null)}>关闭</Button>
            </div>
          </div>
        )}
      </Drawer>
    </>
  );
}

const HIGH_PII = new Set(["id_card", "card_full", "ssn", "passport", "phone", "email", "address"]);

function PiiCard({ summary }: { summary: AssetPiiSummary }) {
  if (summary.pii_columns === 0) {
    return (
      <div
        style={{
          background: "#f6ffed",
          border: "1px solid #b7eb8f",
          padding: 10,
          borderRadius: 6,
          color: "#389e0d",
          fontSize: 13,
        }}
      >
        未发现 PII 列
      </div>
    );
  }
  return (
    <div
      style={{
        background: summary.high_risk_columns > 0 ? "#fff1f0" : "#fff7e6",
        border: `1px solid ${summary.high_risk_columns > 0 ? "#ffa39e" : "#ffd591"}`,
        padding: 10,
        borderRadius: 6,
        fontSize: 13,
      }}
    >
      <div style={{ marginBottom: 6 }}>
        <b style={{ color: summary.high_risk_columns > 0 ? "#d4380d" : "#fa8c16" }}>
          PII 风险: {summary.pii_columns} 列 ({summary.high_risk_columns} 高风险)
        </b>
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 6 }}>
        {Object.entries(summary.by_class).map(([cls, n]) => (
          <Tag key={cls} color={piiColor(cls)}>
            {cls} · {n}
          </Tag>
        ))}
      </div>
      <div style={{ fontSize: 12, color: "#666" }}>
        高风险样例:{" "}
        {summary.samples
          .filter((s) => HIGH_PII.has(s.pii_class))
          .slice(0, 5)
          .map((s) => `${s.column_name}(${s.pii_class}@${(s.confidence * 100).toFixed(0)}%)`)
          .join(", ") || "—"}
      </div>
    </div>
  );
}

interface LineageGraph {
  center_fqn: string;
  center_id: string;
  upstream: Array<{ upstream_fqn: string; transform_type: string; source: string; confidence: number }>;
  downstream: Array<{ downstream_fqn: string; transform_type: string; source: string; confidence: number }>;
  nodes: Array<{ id: string; fqn: string; asset_type: string; tier: string; name: string }>;
  edges: Array<any>;
}

function LineageView({ graph }: { graph: LineageGraph }) {
  const total = graph.upstream.length + graph.downstream.length;
  return (
    <div
      style={{
        background: "#f0f5ff",
        border: "1px solid #adc6ff",
        padding: 10,
        borderRadius: 6,
        fontSize: 13,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
        <b style={{ color: "#1f6feb" }}>
          Lineage (depth=2) · {graph.upstream.length} 上游 / {graph.downstream.length} 下游 · {graph.nodes.length} 节点
        </b>
        {total === 0 && <span style={{ color: "#999", fontSize: 12 }}>暂无血缘</span>}
      </div>

      {graph.upstream.length > 0 && (
        <div style={{ marginBottom: 6 }}>
          <span style={{ color: "#389e0d", fontWeight: 600 }}>⬆ 上游 ({graph.upstream.length})</span>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
            {graph.upstream.map((e, i) => (
              <Tag key={i} color="#389e0d" title={`${e.transform_type} · ${e.source} · ${(e.confidence * 100).toFixed(0)}%`}>
                ← {e.upstream_fqn}
              </Tag>
            ))}
          </div>
        </div>
      )}

      {graph.downstream.length > 0 && (
        <div>
          <span style={{ color: "#d4380d", fontWeight: 600 }}>⬇ 下游 ({graph.downstream.length})</span>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
            {graph.downstream.map((e, i) => (
              <Tag key={i} color="#d4380d" title={`${e.transform_type} · ${e.source} · ${(e.confidence * 100).toFixed(0)}%`}>
                {e.downstream_fqn} →
              </Tag>
            ))}
          </div>
        </div>
      )}

      {/* 所有节点的小图 (text-based) */}
      {graph.nodes.length > 0 && (
        <details style={{ marginTop: 8, fontSize: 12 }}>
          <summary style={{ cursor: "pointer", color: "#666" }}>
            所有相关节点 ({graph.nodes.length})
          </summary>
          <ul style={{ margin: "4px 0 0 16px", padding: 0, color: "#555" }}>
            {graph.nodes.map((n) => (
              <li key={n.id}>
                <code>{n.fqn}</code> <span style={{ color: "#999" }}>({n.asset_type}/{n.tier})</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
