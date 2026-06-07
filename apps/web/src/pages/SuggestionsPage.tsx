import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Card, Tag, Button, Drawer } from "../ui";
import { SuggestionsApi, type Suggestion } from "../lib/api";
import { AgGridReact } from "ag-grid-react";
import { useMemo, useState } from "react";

type Tab = "pending" | "approved" | "rejected";

const TYPE_LABEL: Record<string, string> = {
  description: "表描述",
  pii_class: "PII 分类",
  owner: "Owner",
  lineage: "Lineage",
  glossary: "术语",
  quality_rule: "质量规则",
  insight: "洞察",
};

export function SuggestionsPage() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("pending");
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [selected, setSelected] = useState<Suggestion | null>(null);
  const [note, setNote] = useState<string>("");

  const { data, isLoading } = useQuery({
    queryKey: ["suggestions", tab, typeFilter],
    queryFn: () =>
      SuggestionsApi.list({
        status: tab,
        suggestion_type: typeFilter || undefined,
        limit: 200,
      }),
  });

  // 顶部统计
  const stats = useQuery({
    queryKey: ["suggestions-stats"],
    queryFn: async () => {
      const [p, a, r] = await Promise.all([
        SuggestionsApi.list({ status: "pending", limit: 1 }),
        SuggestionsApi.list({ status: "approved", limit: 1 }),
        SuggestionsApi.list({ status: "rejected", limit: 1 }),
      ]);
      return { pending: p.total, approved: a.total, rejected: r.total };
    },
    refetchInterval: 10_000,
  });

  const columnDefs = useMemo(
    () => [
      {
        field: "suggestion_type",
        headerName: "类型",
        width: 130,
        cellRenderer: (p: { value: string }) => <Tag color="#1f6feb">{TYPE_LABEL[p.value] ?? p.value}</Tag>,
      },
      { field: "skill", headerName: "Skill", width: 220, tooltipField: "skill" },
      { field: "model", headerName: "Model", width: 110 },
      {
        field: "confidence",
        headerName: "Conf",
        width: 90,
        cellRenderer: (p: { value: number }) => `${(p.value * 100).toFixed(0)}%`,
      },
      {
        field: "target_type",
        headerName: "Target",
        width: 90,
        cellRenderer: (p: { value: string }) => <Tag>{p.value}</Tag>,
      },
      {
        field: "payload",
        headerName: "摘要",
        flex: 1,
        valueGetter: (p: { data: Suggestion }) => payloadSummary(p.data.payload),
        tooltipValueGetter: (p: { data: Suggestion }) => JSON.stringify(p.data.payload ?? {}, null, 2),
      },
      {
        field: "created_at",
        headerName: "Created",
        width: 180,
        valueFormatter: (p: { value: string }) => (p.value ? new Date(p.value).toLocaleString() : ""),
      },
      ...(tab === "pending"
        ? [
            {
              headerName: "Action",
              width: 220,
              cellRenderer: (p: { data: Suggestion }) => (
                <div style={{ display: "flex", gap: 4 }}>
                  <Button size="sm" variant="primary" onClick={() => openDrawer(p.data)}>
                    审核
                  </Button>
                </div>
              ),
            },
          ]
        : []),
      ...(tab !== "pending"
        ? [
            {
              field: "reviewed_at",
              headerName: "Reviewed",
              width: 180,
              valueFormatter: (p: { value: string | null }) => (p.value ? new Date(p.value).toLocaleString() : ""),
            },
          ]
        : []),
    ],
    [tab],
  );

  function openDrawer(s: Suggestion) {
    setSelected(s);
    setNote("");
  }

  async function handleApprove() {
    if (!selected) return;
    await SuggestionsApi.approve(selected.id, note || undefined);
    setSelected(null);
    qc.invalidateQueries({ queryKey: ["suggestions"] });
    qc.invalidateQueries({ queryKey: ["suggestions-stats"] });
  }

  async function handleReject() {
    if (!selected) return;
    await SuggestionsApi.reject(selected.id, note || undefined);
    setSelected(null);
    qc.invalidateQueries({ queryKey: ["suggestions"] });
    qc.invalidateQueries({ queryKey: ["suggestions-stats"] });
  }

  return (
    <>
      {/* 顶部: tabs + 统计 + 类型过滤 */}
      <Card
        title="LLM 建议审核"
        extra={
          <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
            <Tabs value={tab} onChange={setTab} stats={stats.data} />
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              style={{ padding: "4px 8px", border: "1px solid #d9d9d9", borderRadius: 4 }}
            >
              <option value="">所有类型</option>
              <option value="description">表描述</option>
              <option value="pii_class">PII 分类</option>
              <option value="owner">Owner</option>
              <option value="lineage">Lineage</option>
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
            onRowClicked={(e) => tab === "pending" && e.data && openDrawer(e.data)}
          />
        </div>
      </Card>

      {/* 审核 Drawer */}
      <Drawer
        open={!!selected}
        onClose={() => setSelected(null)}
        title={selected ? `${TYPE_LABEL[selected.suggestion_type] ?? selected.suggestion_type} · ${selected.model}` : ""}
        width={620}
      >
        {selected && (
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, fontSize: 13 }}>
              <div>
                <b>Conf:</b> {(selected.confidence * 100).toFixed(0)}%
              </div>
              <div>
                <b>Skill:</b> {selected.skill}
              </div>
              <div>
                <b>Target:</b> {selected.target_type} · {selected.target_id.slice(0, 8)}…
              </div>
              <div>
                <b>Use Case:</b> {selected.use_case_id ?? "—"}
              </div>
            </div>

            {selected.rationale && (
              <div>
                <b>LLM 理由:</b>
                <p style={{ background: "#f7f8fa", padding: 10, borderRadius: 6, marginTop: 4 }}>{selected.rationale}</p>
              </div>
            )}

            <div>
              <b>建议内容 (payload):</b>
              <pre
                style={{
                  background: "#1f2937",
                  color: "#e5e7eb",
                  padding: 12,
                  borderRadius: 6,
                  marginTop: 4,
                  fontSize: 12,
                  overflow: "auto",
                  maxHeight: 220,
                }}
              >
                {JSON.stringify(selected.payload, null, 2)}
              </pre>
            </div>

            {tab === "pending" && (
              <>
                <div>
                  <label style={{ display: "block", fontWeight: 600, marginBottom: 4 }}>审核备注 (可选)</label>
                  <textarea
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    rows={2}
                    placeholder="例如: 同意 / 改用 'partial' 脱敏 / 已下线表, 不再治理"
                    style={{
                      width: "100%",
                      fontFamily: "inherit",
                      fontSize: 13,
                      padding: 8,
                      border: "1px solid #d9d9d9",
                      borderRadius: 4,
                    }}
                  />
                </div>

                <div style={{ display: "flex", gap: 8 }}>
                  <Button onClick={handleApprove} variant="primary">
                    批准 (写 KG)
                  </Button>
                  <Button onClick={handleReject} variant="danger">
                    拒绝
                  </Button>
                  <Button onClick={() => setSelected(null)} variant="ghost">
                    取消
                  </Button>
                </div>
              </>
            )}

            {tab !== "pending" && (
              <div style={{ background: "#f7f8fa", padding: 10, borderRadius: 6, fontSize: 13 }}>
                <b>审核结果:</b> {selected.status}
                <br />
                <b>审核时间:</b> {selected.reviewed_at ? new Date(selected.reviewed_at).toLocaleString() : "—"}
                <br />
                <b>备注:</b> {selected.review_note ?? "—"}
              </div>
            )}
          </div>
        )}
      </Drawer>
    </>
  );
}

function payloadSummary(payload: Record<string, unknown> | undefined): string {
  if (!payload) return "";
  if (typeof payload.description === "string") return payload.description.slice(0, 100);
  if (typeof payload.pii_class === "string")
    return `${payload.column_name ?? ""} → ${payload.pii_class}` + (payload.masking_policy ? ` (mask=${payload.masking_policy})` : "");
  if (typeof payload.tier === "string") return `tier=${payload.tier}`;
  const entries = Object.entries(payload).slice(0, 2);
  return entries.map(([k, v]) => `${k}=${typeof v === "string" ? v.slice(0, 30) : JSON.stringify(v)}`).join(", ");
}

function Tabs({ value, onChange, stats }: { value: Tab; onChange: (t: Tab) => void; stats?: { pending: number; approved: number; rejected: number } }) {
  const items: Array<{ k: Tab; label: string; color: string }> = [
    { k: "pending", label: "待审核", color: "#fa8c16" },
    { k: "approved", label: "已批准", color: "#52c41a" },
    { k: "rejected", label: "已拒绝", color: "#999" },
  ];
  return (
    <div style={{ display: "flex", gap: 4 }}>
      {items.map((it) => (
        <button
          key={it.k}
          onClick={() => onChange(it.k)}
          style={{
            padding: "4px 12px",
            border: "1px solid " + (value === it.k ? it.color : "#d9d9d9"),
            background: value === it.k ? it.color : "#fff",
            color: value === it.k ? "#fff" : "#333",
            borderRadius: 4,
            cursor: "pointer",
            fontSize: 13,
          }}
        >
          {it.label} ({stats?.[it.k] ?? "…"})
        </button>
      ))}
    </div>
  );
}
