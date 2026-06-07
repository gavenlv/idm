import { useQuery } from "@tanstack/react-query";
import { AgGridReact } from "ag-grid-react";
import { useMemo, useState } from "react";
import { AssetsApi, type TableAsset } from "../lib/api";
import { Card, Drawer, Tag, Button } from "../ui";

export function AssetsPage() {
  const [q, setQ] = useState("");
  const [tier, setTier] = useState<string>("");
  const [selected, setSelected] = useState<TableAsset | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["assets", q, tier],
    queryFn: () => AssetsApi.list({ q: q || undefined, tier: tier || undefined, limit: 200 }),
  });

  const columnDefs = useMemo(
    () => [
      { field: "fqn", headerName: "FQN", flex: 2, sortable: true, filter: true },
      { field: "asset_type", headerName: "Type", width: 130 },
      {
        field: "tier",
        headerName: "Tier",
        width: 110,
        cellRenderer: (p: { value: string }) => <Tag color={p.value === "critical" ? "#d4380d" : p.value === "important" ? "#fa8c16" : "#999"}>{p.value}</Tag>,
      },
      { field: "column_count", headerName: "Cols", width: 90 },
      { field: "row_count", headerName: "Rows", width: 110, valueFormatter: (p: { value: number | null }) => (p.value == null ? "—" : p.value.toLocaleString()) },
      { field: "description", headerName: "Description", flex: 3, tooltipField: "description" },
    ],
    [],
  );

  return (
    <>
      <Card title="资产" extra={
        <div style={{ display: "flex", gap: 8 }}>
          <input placeholder="按 name / fqn 搜索" value={q} onChange={(e) => setQ(e.target.value)} />
          <select value={tier} onChange={(e) => setTier(e.target.value)}>
            <option value="">全部 tier</option>
            <option value="critical">critical</option>
            <option value="important">important</option>
            <option value="normal">normal</option>
          </select>
        </div>
      }>
        <div className="ag-theme-quartz" style={{ height: 600, width: "100%" }}>
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

      <Drawer open={!!selected} onClose={() => setSelected(null)} title={selected?.fqn} width={520}>
        {selected && (
          <div>
            <p><b>Tier:</b> <Tag>{selected.tier}</Tag></p>
            <p><b>Type:</b> {selected.asset_type}</p>
            <p><b>Columns:</b> {selected.column_count}</p>
            <p><b>Description:</b></p>
            <blockquote style={{ background: "#f7f8fa", padding: 12, borderRadius: 6 }}>
              {selected.description ?? <em>暂无描述</em>}
            </blockquote>
            <Button onClick={() => setSelected(null)}>关闭</Button>
          </div>
        )}
      </Drawer>
    </>
  );
}
