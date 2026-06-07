/**
 * AssetsPage — DataHub-style asset browser.
 *
 * Layout:
 * - Top: KPI stats (total / by tier / with PII)
 * - Toolbar: search + tier filter + status filter
 * - Table: ag-grid, click row to open detail Drawer
 * - Drawer: metadata, description, PII risk, columns grid, lineage summary
 */
import { useQuery } from "@tanstack/react-query";
import { AgGridReact } from "ag-grid-react";
import type { ColDef } from "ag-grid-community";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AssetsApi,
  type AssetPiiSummary,
  type ColumnAsset,
  type TableAsset,
} from "../lib/api";
import { piiColor, piiRiskLabel } from "../lib/pii";
import {
  Button,
  Card,
  Drawer,
  EmptyState,
  Input,
  Select,
  Stat,
  Stats,
  Status,
  Tag,
} from "../ui";

const TIER_COLOR: Record<string, string> = {
  critical: "#cf1124",
  important: "#d97706",
  normal: "#2e66f0",
};

export function AssetsPage() {
  const { t } = useTranslation();
  const [q, setQ] = useState("");
  const [tier, setTier] = useState<string>("");
  const [status, setStatus] = useState<string>("");
  const [selected, setSelected] = useState<TableAsset | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["assets", q, tier, status],
    queryFn: () =>
      AssetsApi.list({
        q: q || undefined,
        tier: tier || undefined,
        status: status || undefined,
        limit: 200,
      }),
  });

  // KPIs from current list
  const stats = useMemo(() => {
    const items = data?.items ?? [];
    const total = items.length;
    const byTier: Record<string, number> = { critical: 0, important: 0, normal: 0 };
    for (const a of items) byTier[a.tier] = (byTier[a.tier] ?? 0) + 1;
    return { total, byTier };
  }, [data]);

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
  const lineageQ = useQuery({
    queryKey: ["asset-lineage", selected?.id, 2],
    queryFn: () => AssetsApi.lineage(selected!.id, 2),
    enabled: !!selected,
  });

  const columnDefs = useMemo<ColDef<TableAsset>[]>(
    () => [
      {
        field: "fqn",
        headerName: t("assets.columns.fqn"),
        flex: 2.5,
        sortable: true,
        filter: true,
        cellRenderer: (p: { value: string }) => (
          <span style={{ fontFamily: "var(--idm-mono-font)", color: "var(--idm-text)" }}>
            {p.value}
          </span>
        ),
      },
      {
        field: "asset_type",
        headerName: t("assets.columns.type"),
        width: 110,
        cellRenderer: (p: { value: string }) => (
          <Tag dot color="#697077">
            {p.value}
          </Tag>
        ),
      },
      {
        field: "tier",
        headerName: t("assets.columns.tier"),
        width: 110,
        cellRenderer: (p: { value: string }) => (
          <Tag solid color={TIER_COLOR[p.value] ?? "#697077"}>
            {p.value}
          </Tag>
        ),
      },
      { field: "column_count", headerName: t("assets.columns.columnCount"), width: 90 },
      {
        field: "row_count",
        headerName: t("assets.columns.rows"),
        width: 120,
        valueFormatter: (p: { value: number | null | undefined }) =>
          p.value == null ? "—" : p.value.toLocaleString(),
      },
      {
        field: "description",
        headerName: t("assets.columns.description"),
        flex: 3,
        tooltipField: "description",
        cellRenderer: (p: { value: string | null | undefined }) =>
          p.value ? (
            <span style={{ color: "var(--idm-text)" }}>{p.value}</span>
          ) : (
            <span style={{ color: "var(--idm-text-subtle)", fontStyle: "italic" }}>—</span>
          ),
      },
      {
        field: "status",
        headerName: t("common.status"),
        width: 100,
        cellRenderer: (p: { value: string }) => (
          <Status kind={p.value === "active" ? "ok" : "idle"}>{p.value}</Status>
        ),
      },
    ],
    [t],
  );

  const colColumnDefs = useMemo<ColDef<ColumnAsset>[]>(
    () => [
      {
        field: "ordinal",
        headerName: "#",
        width: 60,
        sortable: true,
        cellRenderer: (p: { value: number }) => (
          <span style={{ color: "var(--idm-text-subtle)" }}>{p.value}</span>
        ),
      },
      {
        field: "name",
        headerName: "Name",
        flex: 1.4,
        sortable: true,
        filter: true,
        cellRenderer: (p: { value: string }) => (
          <span style={{ fontFamily: "var(--idm-mono-font)" }}>{p.value}</span>
        ),
      },
      {
        field: "data_type",
        headerName: "Type",
        width: 140,
        cellRenderer: (p: { value: string }) => (
          <Tag color="#697077">{p.value}</Tag>
        ),
      },
      {
        field: "is_primary_key",
        headerName: "PK",
        width: 60,
        cellRenderer: (p: { value: boolean }) =>
          p.value ? <Tag solid color="#2e66f0">PK</Tag> : "",
      },
      {
        field: "pii_class",
        headerName: "PII",
        width: 170,
        cellRenderer: (p: { data: ColumnAsset; value: string }) =>
          p.value && p.value !== "none" ? (
            <Tag solid color={piiColor(p.value)}>
              {p.value} · {piiRiskLabel(p.value)}
            </Tag>
          ) : (
            <span style={{ color: "var(--idm-text-subtle)" }}>—</span>
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
        headerName: "Sample",
        flex: 2.5,
        tooltipValueGetter: (p) =>
          p.data ? JSON.stringify(p.data.sample_values ?? []) : "",
        valueFormatter: (p) =>
          Array.isArray(p.value)
            ? (p.value as unknown[])
                .slice(0, 3)
                .map((v) => (v == null ? "∅" : String(v).slice(0, 20)))
                .join(", ")
            : "",
      },
    ],
    [],
  );

  return (
    <>
      <Stats>
        <Stat label="Total Assets" value={stats.total} />
        <Stat
          label="Critical"
          value={stats.byTier.critical ?? 0}
          hint="Highest governance priority"
        />
        <Stat
          label="Important"
          value={stats.byTier.important ?? 0}
          hint="Business-critical"
        />
        <Stat
          label="Normal"
          value={stats.byTier.normal ?? 0}
          hint="Standard governance"
        />
      </Stats>

      <Card
        title={`Assets (${data?.total ?? 0})`}
        extra={
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <Input
              size="sm"
              placeholder={t("assets.searchPlaceholder")}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              style={{ width: 220 }}
            />
            <Select size="sm" value={tier} onChange={(e) => setTier(e.target.value)}>
              <option value="">{t("common.allTiers")}</option>
              <option value="critical">{t("common.tierCritical")}</option>
              <option value="important">{t("common.tierImportant")}</option>
              <option value="normal">{t("common.tierNormal")}</option>
            </Select>
            <Select size="sm" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">All statuses</option>
              <option value="active">Active</option>
              <option value="deprecated">Deprecated</option>
              <option value="archived">Archived</option>
            </Select>
          </div>
        }
        bodyClass=""
      >
        <div className="ag-theme-quartz" style={{ height: 560, width: "100%" }}>
          <AgGridReact
            rowData={data?.items ?? []}
            columnDefs={columnDefs}
            loading={isLoading}
            pagination
            paginationPageSize={50}
            onRowClicked={(e) => e.data && setSelected(e.data)}
            getRowId={(p) => p.data.id}
            rowHeight={36}
          />
        </div>
      </Card>

      <Drawer
        open={!!selected}
        onClose={() => setSelected(null)}
        title={selected ? <span style={{ fontFamily: "var(--idm-mono-font)" }}>{selected.fqn}</span> : ""}
        width={760}
      >
        {selected && (
          <div className="idm-flex-col idm-gap-3">
            <Card title={t("assets.drawer.metadata")} bodyClass="idm-flex-col idm-gap-2">
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: 8,
                  fontSize: 13,
                }}
              >
                <div>
                  <span className="idm-text-muted">{t("common.tier")}:</span>{" "}
                  <Tag solid color={TIER_COLOR[selected.tier] ?? "#697077"}>
                    {selected.tier}
                  </Tag>
                </div>
                <div>
                  <span className="idm-text-muted">{t("common.type")}:</span>{" "}
                  <Tag>{selected.asset_type}</Tag>
                </div>
                <div>
                  <span className="idm-text-muted">Columns:</span> {selected.column_count}
                </div>
                <div>
                  <span className="idm-text-muted">Rows:</span>{" "}
                  {selected.row_count == null ? "—" : selected.row_count.toLocaleString()}
                </div>
                <div>
                  <span className="idm-text-muted">{t("common.status")}:</span>{" "}
                  <Status kind={selected.status === "active" ? "ok" : "idle"}>
                    {selected.status}
                  </Status>
                </div>
                <div>
                  <span className="idm-text-muted">Created:</span>{" "}
                  {new Date(selected.created_at).toLocaleString()}
                </div>
              </div>
            </Card>

            <Card title={t("assets.drawer.description")}>
              {selected.description ? (
                <div style={{ lineHeight: 1.6 }}>{selected.description}</div>
              ) : (
                <em className="idm-text-subtle">{t("assets.drawer.noDescription")}</em>
              )}
            </Card>

            {piiQ.data && <PiiCard summary={piiQ.data} />}

            {lineageQ.data && <LineageSummary graph={lineageQ.data} />}

            <Card
              title={`${t("assets.drawer.columns")} (${colsQ.data?.total ?? 0})`}
              bodyClass="idm-flex-col idm-gap-2"
            >
              {colsQ.isLoading && (
                <span className="idm-text-muted idm-skeleton" style={{ width: 120 }} />
              )}
              <div
                className="ag-theme-quartz"
                style={{ height: 360, width: "100%", border: "1px solid var(--idm-border)" }}
              >
                <AgGridReact
                  rowData={colsQ.data?.items ?? []}
                  columnDefs={colColumnDefs}
                  pagination
                  paginationPageSize={20}
                  getRowId={(p) => p.data.id}
                  getRowStyle={(p) =>
                    p.data?.pii_class && HIGH_PII.has(p.data.pii_class)
                      ? { background: "var(--idm-red-50)" }
                      : undefined
                  }
                />
              </div>
            </Card>

            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <Button variant="ghost" onClick={() => setSelected(null)}>
                {t("common.close")}
              </Button>
            </div>
          </div>
        )}
      </Drawer>
    </>
  );
}

const HIGH_PII = new Set([
  "id_card",
  "card_full",
  "ssn",
  "passport",
  "phone",
  "email",
  "address",
]);

function PiiCard({ summary }: { summary: AssetPiiSummary }) {
  const { t } = useTranslation();
  if (summary.pii_columns === 0) {
    return (
      <div className="idm-pii-card idm-pii-card--clean">
        <div className="idm-pii-card__title">{t("assets.pii.noColumns")}</div>
      </div>
    );
  }
  const high = summary.high_risk_columns > 0;
  return (
    <div className={`idm-pii-card${high ? " idm-pii-card--high" : ""}`}>
      <div className="idm-pii-card__title">
        {t("assets.pii.risk")}: {summary.pii_columns} {t("assets.pii.columns")}{" "}
        {high && <Tag solid color="#cf1124">{summary.high_risk_columns} {t("assets.pii.highRisk")}</Tag>}
      </div>
      <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
        {Object.entries(summary.by_class).map(([cls, n]) => (
          <Tag key={cls} solid color={piiColor(cls)}>
            {cls} · {n}
          </Tag>
        ))}
      </div>
      <div className="idm-pii-card__samples">
        <b>{t("assets.pii.samples")}: </b>
        {summary.samples
          .filter((s) => HIGH_PII.has(s.pii_class))
          .slice(0, 5)
          .map((s) => `${s.column_name} (${s.pii_class}@${(s.confidence * 100).toFixed(0)}%)`)
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
  edges: Array<{ upstream_id: string; downstream_id: string; transform_type: string; source: string; confidence: number }>;
}

function LineageSummary({ graph }: { graph: LineageGraph }) {
  const { t } = useTranslation();
  return (
    <Card title={`${t("assets.drawer.lineage")} · ${t("lineage.upstreamCount", { count: graph.upstream.length })} / ${t("lineage.downstreamCount", { count: graph.downstream.length })}`}>
      {graph.upstream.length === 0 && graph.downstream.length === 0 ? (
        <EmptyState
          title="No lineage found"
          description="Run parse_dbt_manifest / parse_superset_dashboard / infer_table_owners to populate upstream & downstream."
        />
      ) : (
        <div className="idm-flex idm-gap-3" style={{ alignItems: "stretch" }}>
          {graph.upstream.length > 0 && (
            <div style={{ flex: 1 }}>
              <div className="idm-flex idm-items-center idm-gap-2 idm-mb-2">
                <span style={{ color: "#1d6f30", fontWeight: 600 }}>↑ {t("assets.drawer.upstream")}</span>
                <Tag solid color="#2e8540">
                  {graph.upstream.length}
                </Tag>
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {graph.upstream.slice(0, 8).map((e, i) => (
                  <Tag
                    key={i}
                    color="#2e8540"
                    title={`${e.transform_type} · ${e.source} · ${(e.confidence * 100).toFixed(0)}%`}
                  >
                    ← {e.upstream_fqn}
                  </Tag>
                ))}
                {graph.upstream.length > 8 && <Tag>+{graph.upstream.length - 8} more</Tag>}
              </div>
            </div>
          )}
          {graph.downstream.length > 0 && (
            <div style={{ flex: 1 }}>
              <div className="idm-flex idm-items-center idm-gap-2 idm-mb-2">
                <span style={{ color: "#cf1124", fontWeight: 600 }}>↓ {t("assets.drawer.downstream")}</span>
                <Tag solid color="#cf1124">
                  {graph.downstream.length}
                </Tag>
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {graph.downstream.slice(0, 8).map((e, i) => (
                  <Tag
                    key={i}
                    color="#cf1124"
                    title={`${e.transform_type} · ${e.source} · ${(e.confidence * 100).toFixed(0)}%`}
                  >
                    {e.downstream_fqn} →
                  </Tag>
                ))}
                {graph.downstream.length > 8 && <Tag>+{graph.downstream.length - 8} more</Tag>}
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
