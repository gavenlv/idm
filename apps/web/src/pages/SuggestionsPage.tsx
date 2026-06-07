/**
 * SuggestionsPage — Review & approve AI-generated governance suggestions.
 *
 * Layout:
 * - Tabs: Pending / Approved / Rejected (with counts)
 * - Type filter
 * - Table: suggestion rows
 * - Drawer: full payload + LLM rationale + Approve / Reject actions
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useMemo, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef } from "ag-grid-community";
import { Button, Card, Drawer, Select, Stat, Stats, Status, Tabs, Tag, Textarea } from "../ui";
import { SuggestionsApi, type Suggestion } from "../lib/api";

type Tab = "pending" | "approved" | "rejected";

const TYPE_I18N_KEY: Record<string, string> = {
  description: "suggestions.typeDescription",
  pii_class: "suggestions.typePii",
  owner: "suggestions.typeOwner",
  lineage: "suggestions.typeLineage",
  glossary: "suggestions.typeGlossary",
  quality_rule: "suggestions.typeQuality",
  insight: "suggestions.typeInsight",
};

const TYPE_COLOR: Record<string, string> = {
  description: "#0b7ea4",
  pii_class: "#cf1124",
  owner: "#7159f3",
  lineage: "#2e66f0",
  glossary: "#2e8540",
  quality_rule: "#d97706",
  insight: "#1d44ad",
};

export function SuggestionsPage() {
  const { t } = useTranslation();
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

  const tabItems = useMemo(
    () => [
      { key: "pending" as Tab, label: t("suggestions.tabPending"), count: stats.data?.pending ?? "…", color: "#d97706" },
      { key: "approved" as Tab, label: t("suggestions.tabApproved"), count: stats.data?.approved ?? "…", color: "#2e8540" },
      { key: "rejected" as Tab, label: t("suggestions.tabRejected"), count: stats.data?.rejected ?? "…", color: "#878d96" },
    ],
    [t, stats.data],
  );

  const columnDefs = useMemo<ColDef<Suggestion>[]>(
    () => [
      {
        field: "suggestion_type",
        headerName: t("common.type"),
        width: 140,
        cellRenderer: (p: { value: string }) => (
          <Tag solid color={TYPE_COLOR[p.value] ?? "#697077"}>
            {TYPE_I18N_KEY[p.value] ? t(TYPE_I18N_KEY[p.value]) : p.value}
          </Tag>
        ),
      },
      {
        field: "skill",
        headerName: t("common.skill"),
        width: 240,
        tooltipField: "skill",
        cellRenderer: (p: { value: string }) => (
          <span style={{ fontFamily: "var(--idm-mono-font)", fontSize: 11 }}>{p.value}</span>
        ),
      },
      {
        field: "model",
        headerName: t("common.model"),
        width: 130,
        cellRenderer: (p: { value: string }) => <Tag>{p.value}</Tag>,
      },
      {
        field: "confidence",
        headerName: t("common.confidence"),
        width: 100,
        cellRenderer: (p: { value: number }) => {
          const pct = (p.value * 100).toFixed(0);
          const color = p.value >= 0.85 ? "#2e8540" : p.value >= 0.6 ? "#d97706" : "#cf1124";
          return <Tag solid color={color}>{pct}%</Tag>;
        },
      },
      {
        field: "target_type",
        headerName: t("common.target"),
        width: 100,
        cellRenderer: (p: { value: string }) => <Tag>{p.value}</Tag>,
      },
      {
        field: "payload",
        headerName: "Summary",
        flex: 1,
        valueGetter: (p) => (p.data ? payloadSummary(p.data.payload) : ""),
        tooltipValueGetter: (p) =>
          p.data ? JSON.stringify(p.data.payload ?? {}, null, 2) : "",
        cellRenderer: (p: { value: string }) => (
          <span style={{ color: "var(--idm-text-muted)" }}>{p.value}</span>
        ),
      },
      {
        field: "created_at",
        headerName: t("common.createdAt"),
        width: 180,
        valueFormatter: (p: { value: string }) =>
          p.value ? new Date(p.value).toLocaleString() : "",
      },
      ...(tab === "pending"
        ? ([
            {
              headerName: t("common.actions"),
              width: 130,
              cellRenderer: (p: { data: Suggestion }) => (
                <Button size="sm" variant="primary" onClick={() => openDrawer(p.data)}>
                  {t("suggestions.review")}
                </Button>
              ),
            },
          ] as ColDef<Suggestion>[])
        : []),
      ...(tab !== "pending"
        ? ([
            {
              field: "reviewed_at",
              headerName: t("common.reviewedAt"),
              width: 180,
              valueFormatter: (p: { value: string | null }) =>
                p.value ? new Date(p.value).toLocaleString() : "",
            },
          ] as ColDef<Suggestion>[])
        : []),
    ],
    [tab, t],
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
      <Stats>
        <Stat
          label={t("suggestions.tabPending")}
          value={stats.data?.pending ?? "…"}
          hint="Awaiting human review"
        />
        <Stat
          label={t("suggestions.tabApproved")}
          value={stats.data?.approved ?? "…"}
          hint="Already written to KG"
        />
        <Stat
          label={t("suggestions.tabRejected")}
          value={stats.data?.rejected ?? "…"}
          hint="Discarded"
        />
        <Stat
          label="Total"
          value={
            (stats.data?.pending ?? 0) +
            (stats.data?.approved ?? 0) +
            (stats.data?.rejected ?? 0)
          }
          hint="All suggestions"
        />
      </Stats>

      <Card
        title={t("suggestions.title")}
        extra={
          <div className="idm-flex idm-gap-3 idm-items-center">
            <Tabs value={tab} onChange={setTab} items={tabItems} />
            <Select size="sm" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
              <option value="">{t("suggestions.allTypes")}</option>
              <option value="description">{t("suggestions.typeDescription")}</option>
              <option value="pii_class">{t("suggestions.typePii")}</option>
              <option value="owner">{t("suggestions.typeOwner")}</option>
              <option value="lineage">{t("suggestions.typeLineage")}</option>
              <option value="glossary">{t("suggestions.typeGlossary")}</option>
              <option value="quality_rule">{t("suggestions.typeQuality")}</option>
              <option value="insight">{t("suggestions.typeInsight")}</option>
            </Select>
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
            getRowId={(p) => p.data.id}
            rowHeight={36}
          />
        </div>
      </Card>

      <Drawer
        open={!!selected}
        onClose={() => setSelected(null)}
        title={
          selected ? (
            <span className="idm-flex idm-items-center idm-gap-2">
              <Tag solid color={TYPE_COLOR[selected.suggestion_type] ?? "#697077"}>
                {TYPE_I18N_KEY[selected.suggestion_type]
                  ? t(TYPE_I18N_KEY[selected.suggestion_type])
                  : selected.suggestion_type}
              </Tag>
              <span style={{ fontFamily: "var(--idm-mono-font)" }}>{selected.model}</span>
            </span>
          ) : (
            ""
          )
        }
        width={680}
      >
        {selected && (
          <div className="idm-flex-col idm-gap-3">
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: 8,
                fontSize: 13,
              }}
            >
              <div>
                <span className="idm-text-muted">{t("common.confidence")}:</span>{" "}
                <Tag solid color={selected.confidence >= 0.85 ? "#2e8540" : selected.confidence >= 0.6 ? "#d97706" : "#cf1124"}>
                  {(selected.confidence * 100).toFixed(0)}%
                </Tag>
              </div>
              <div>
                <span className="idm-text-muted">{t("common.skill")}:</span>{" "}
                <code style={{ fontFamily: "var(--idm-mono-font)", fontSize: 11 }}>
                  {selected.skill}
                </code>
              </div>
              <div>
                <span className="idm-text-muted">{t("common.target")}:</span>{" "}
                <Tag>{selected.target_type}</Tag>{" "}
                <span className="idm-text-muted" style={{ fontSize: 11 }}>
                  {selected.target_id.slice(0, 8)}…
                </span>
              </div>
              <div>
                <span className="idm-text-muted">{t("suggestions.useCase")}:</span>{" "}
                {selected.use_case_id ?? "—"}
              </div>
            </div>

            {selected.rationale && (
              <div>
                <div
                  className="idm-fw-600"
                  style={{
                    fontSize: 12,
                    color: "var(--idm-text-muted)",
                    textTransform: "uppercase",
                    letterSpacing: 0.5,
                    marginBottom: 4,
                  }}
                >
                  {t("suggestions.rationale")}
                </div>
                <div className="idm-code" style={{ maxHeight: 140, whiteSpace: "pre-wrap" }}>
                  {selected.rationale}
                </div>
              </div>
            )}

            <div>
              <div
                className="idm-fw-600"
                style={{
                  fontSize: 12,
                  color: "var(--idm-text-muted)",
                  textTransform: "uppercase",
                  letterSpacing: 0.5,
                  marginBottom: 4,
                }}
              >
                {t("suggestions.payload")}
              </div>
              <pre className="idm-code idm-code--dark" style={{ maxHeight: 240 }}>
                {JSON.stringify(selected.payload, null, 2)}
              </pre>
            </div>

            {tab === "pending" ? (
              <>
                <div>
                  <label
                    style={{
                      display: "block",
                      fontWeight: 600,
                      fontSize: 12,
                      marginBottom: 4,
                      color: "var(--idm-text-muted)",
                      textTransform: "uppercase",
                      letterSpacing: 0.5,
                    }}
                  >
                    {t("suggestions.reviewNote")}
                  </label>
                  <Textarea
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    rows={3}
                    placeholder={t("suggestions.reviewNotePlaceholder")}
                    style={{ width: "100%" }}
                  />
                </div>

                <div className="idm-flex idm-gap-2 idm-justify-between" style={{ paddingTop: 12, borderTop: "1px solid var(--idm-border)" }}>
                  <Button variant="ghost" onClick={() => setSelected(null)}>
                    {t("common.cancel")}
                  </Button>
                  <div className="idm-flex idm-gap-2">
                    <Button onClick={handleReject} variant="danger">
                      {t("suggestions.reject")}
                    </Button>
                    <Button onClick={handleApprove} variant="primary">
                      {t("suggestions.approve")}
                    </Button>
                  </div>
                </div>
              </>
            ) : (
              <div className="idm-code" style={{ background: "var(--idm-gray-50)" }}>
                <div>
                  <b>{t("suggestions.reviewResult")}:</b>{" "}
                  <Status kind={selected.status === "approved" ? "ok" : "fail"}>
                    {selected.status}
                  </Status>
                </div>
                <div>
                  <b>{t("suggestions.reviewTime")}:</b>{" "}
                  {selected.reviewed_at ? new Date(selected.reviewed_at).toLocaleString() : "—"}
                </div>
                <div>
                  <b>{t("suggestions.reviewNote")}:</b> {selected.review_note ?? "—"}
                </div>
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
    return `${payload.column_name ?? ""} → ${payload.pii_class}` +
      (payload.masking_policy ? ` (mask=${payload.masking_policy})` : "");
  if (typeof payload.tier === "string") return `tier=${payload.tier}`;
  const entries = Object.entries(payload).slice(0, 2);
  return entries
    .map(([k, v]) => `${k}=${typeof v === "string" ? v.slice(0, 30) : JSON.stringify(v)}`)
    .join(", ");
}
