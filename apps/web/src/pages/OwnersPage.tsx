/**
 * OwnersPage — DataHub-style owner governance view.
 *
 * Layout:
 * - KPIs: total / verified / unverified / unverified critical assets
 * - Toolbar: team filter + role filter + verified filter + search
 * - Table (ag-grid): owner rows
 *   - Click row → Drawer: detail + Verify / Remove actions
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AgGridReact } from "ag-grid-react";
import type { ColDef } from "ag-grid-community";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Button,
  Card,
  Drawer,
  Input,
  Select,
  Stat,
  Stats,
  Status,
  Tag,
} from "../ui";
import { type AssetOwner, OwnersApi } from "../lib/api";

const ROLE_COLOR: Record<string, string> = {
  owner: "#2e66f0",
  steward: "#7159f3",
  consumer: "#0b7ea4",
};

const SOURCE_COLOR: Record<string, string> = {
  llm: "#7159f3",
  dbt_meta: "#2e66f0",
  airflow: "#d97706",
  git_blame: "#0b7ea4",
  manual: "#697077",
};

export function OwnersPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [team, setTeam] = useState("");
  const [role, setRole] = useState("");
  const [verified, setVerified] = useState<string>("");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<AssetOwner | null>(null);

  const ownersQ = useQuery({
    queryKey: ["owners", team, role, verified],
    queryFn: () =>
      OwnersApi.list({
        team: team || undefined,
        role: role || undefined,
        verified:
          verified === "true" ? true : verified === "false" ? false : undefined,
        limit: 200,
      }),
  });

  const verifyM = useMutation({
    mutationFn: (id: string) => OwnersApi.verify(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["owners"] });
      setSelected(null);
    },
  });

  const removeM = useMutation({
    mutationFn: (id: string) => OwnersApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["owners"] });
      setSelected(null);
    },
  });

  // KPIs
  const stats = useMemo(() => {
    const items = ownersQ.data?.items ?? [];
    const total = items.length;
    const verifiedCount = items.filter((o) => o.is_verified).length;
    const unverifiedCount = total - verifiedCount;
    const byTeam: Record<string, number> = {};
    for (const o of items) {
      const k = o.team ?? "—";
      byTeam[k] = (byTeam[k] ?? 0) + 1;
    }
    return { total, verifiedCount, unverifiedCount, byTeam };
  }, [ownersQ.data]);

  const columnDefs = useMemo<ColDef<AssetOwner>[]>(
    () => [
      {
        field: "user_email",
        headerName: "User",
        flex: 1.6,
        sortable: true,
        filter: true,
        cellRenderer: (p: { data: AssetOwner; value: string }) => (
          <div className="idm-flex idm-items-center idm-gap-2">
            <span
              className="idm-avatar"
              style={{
                width: 22,
                height: 22,
                background: "#dde1e6",
                color: "#21272a",
                fontSize: 10,
              }}
            >
              {(p.data.user_name ?? p.value ?? "?")
                .split(/\s+|\./)
                .filter(Boolean)
                .slice(0, 2)
                .map((s: string) => s[0]?.toUpperCase() ?? "")
                .join("")}
            </span>
            <div className="idm-flex-col" style={{ lineHeight: 1.2 }}>
              <span style={{ fontWeight: 500 }}>{p.data.user_name ?? p.value}</span>
              <span style={{ color: "var(--idm-text-muted)", fontSize: 11 }}>{p.value}</span>
            </div>
          </div>
        ),
      },
      {
        field: "team",
        headerName: "Team",
        flex: 1,
        sortable: true,
        filter: true,
        cellRenderer: (p: { value: string | null }) =>
          p.value ? <Tag color="#697077">{p.value}</Tag> : <span className="idm-text-muted">—</span>,
      },
      {
        field: "role",
        headerName: "Role",
        width: 110,
        cellRenderer: (p: { value: string }) => (
          <Tag solid color={ROLE_COLOR[p.value] ?? "#697077"}>
            {p.value}
          </Tag>
        ),
      },
      {
        field: "table_fqn",
        headerName: "Asset",
        flex: 2.4,
        sortable: true,
        filter: true,
        tooltipField: "table_fqn",
        cellRenderer: (p: { value: string | null }) =>
          p.value ? (
            <span style={{ fontFamily: "var(--idm-mono-font)" }}>{p.value}</span>
          ) : (
            <span className="idm-text-muted">—</span>
          ),
      },
      {
        field: "source",
        headerName: "Source",
        width: 110,
        cellRenderer: (p: { value: string }) => (
          <Tag color={SOURCE_COLOR[p.value] ?? "#697077"}>{p.value}</Tag>
        ),
      },
      {
        field: "confidence",
        headerName: "Conf",
        width: 90,
        cellRenderer: (p: { value: number }) => {
          const pct = (p.value * 100).toFixed(0);
          const color =
            p.value >= 0.85 ? "#2e8540" : p.value >= 0.6 ? "#d97706" : "#cf1124";
          return (
            <Tag solid color={color}>
              {pct}%
            </Tag>
          );
        },
      },
      {
        field: "is_verified",
        headerName: "Status",
        width: 120,
        cellRenderer: (p: { value: boolean }) => (
          <Status kind={p.value ? "ok" : "warn"}>
            {p.value ? "verified" : "unverified"}
          </Status>
        ),
      },
    ],
    [],
  );

  // client-side filter for search
  const filteredRows = useMemo(() => {
    const items = ownersQ.data?.items ?? [];
    if (!q.trim()) return items;
    const needle = q.toLowerCase();
    return items.filter(
      (o) =>
        o.user_email.toLowerCase().includes(needle) ||
        (o.user_name ?? "").toLowerCase().includes(needle) ||
        (o.team ?? "").toLowerCase().includes(needle) ||
        (o.table_fqn ?? "").toLowerCase().includes(needle),
    );
  }, [ownersQ.data, q]);

  return (
    <>
      <Stats>
        <Stat label="Total Owners" value={stats.total} hint="Across all assets" />
        <Stat
          label="Verified"
          value={stats.verifiedCount}
          hint="Confirmed by human"
        />
        <Stat
          label="Unverified"
          value={stats.unverifiedCount}
          hint="Awaiting confirmation"
        />
        <Stat
          label="Teams"
          value={Object.keys(stats.byTeam).length}
          hint="Distinct teams"
        />
      </Stats>

      <Card
        title={`${t("owners.title")} (${ownersQ.data?.total ?? 0})`}
        extra={
          <div className="idm-flex idm-gap-2 idm-items-center">
            <Input
              size="sm"
              placeholder={t("owners.searchPlaceholder")}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              style={{ width: 220 }}
            />
            <Select size="sm" value={team} onChange={(e) => setTeam(e.target.value)}>
              <option value="">{t("owners.allTeams")}</option>
              <option value="data-platform">data-platform</option>
              <option value="analytics">analytics</option>
              <option value="growth">growth</option>
              <option value="finance">finance</option>
            </Select>
            <Select size="sm" value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="">{t("owners.allRoles")}</option>
              <option value="owner">owner</option>
              <option value="steward">steward</option>
              <option value="consumer">consumer</option>
            </Select>
            <Select
              size="sm"
              value={verified}
              onChange={(e) => setVerified(e.target.value)}
            >
              <option value="">{t("owners.allStatus")}</option>
              <option value="true">{t("owners.verified")}</option>
              <option value="false">{t("owners.unverified")}</option>
            </Select>
          </div>
        }
      >
        <div className="ag-theme-quartz" style={{ height: 560, width: "100%" }}>
          <AgGridReact
            rowData={filteredRows}
            columnDefs={columnDefs}
            loading={ownersQ.isLoading}
            pagination
            paginationPageSize={50}
            onRowClicked={(e) => e.data && setSelected(e.data)}
            getRowId={(p) => p.data.id}
            rowHeight={40}
          />
        </div>
      </Card>

      <Drawer
        open={!!selected}
        onClose={() => setSelected(null)}
        title={
          selected ? (
            <div className="idm-flex idm-items-center idm-gap-2">
              <Tag solid color={ROLE_COLOR[selected.role] ?? "#697077"}>
                {selected.role}
              </Tag>
              <span style={{ fontWeight: 600 }}>{selected.user_name ?? selected.user_email}</span>
              <span className="idm-text-muted" style={{ fontSize: 12 }}>
                {selected.user_email}
              </span>
            </div>
          ) : (
            ""
          )
        }
        width={520}
      >
        {selected && (
          <div className="idm-flex-col idm-gap-3">
            <Card title={t("owners.drawer.detail")}>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "120px 1fr",
                  gap: "8px 16px",
                  fontSize: 13,
                }}
              >
                <div className="idm-text-muted">{t("owners.team")}</div>
                <div>
                  {selected.team ? <Tag>{selected.team}</Tag> : <span className="idm-text-muted">—</span>}
                </div>
                <div className="idm-text-muted">{t("owners.role")}</div>
                <div>
                  <Tag solid color={ROLE_COLOR[selected.role] ?? "#697077"}>
                    {selected.role}
                  </Tag>
                </div>
                <div className="idm-text-muted">{t("owners.asset")}</div>
                <div style={{ fontFamily: "var(--idm-mono-font)" }}>
                  {selected.table_fqn ?? "—"}
                </div>
                <div className="idm-text-muted">{t("owners.source")}</div>
                <div>
                  <Tag color={SOURCE_COLOR[selected.source] ?? "#697077"}>
                    {selected.source}
                  </Tag>
                </div>
                <div className="idm-text-muted">{t("common.confidence")}</div>
                <div>
                  <Tag
                    solid
                    color={
                      selected.confidence >= 0.85
                        ? "#2e8540"
                        : selected.confidence >= 0.6
                          ? "#d97706"
                          : "#cf1124"
                    }
                  >
                    {(selected.confidence * 100).toFixed(0)}%
                  </Tag>
                </div>
                <div className="idm-text-muted">{t("common.status")}</div>
                <div>
                  <Status kind={selected.is_verified ? "ok" : "warn"}>
                    {selected.is_verified ? "verified" : "unverified"}
                  </Status>
                </div>
                <div className="idm-text-muted">{t("common.createdAt")}</div>
                <div>{new Date(selected.created_at).toLocaleString()}</div>
                <div className="idm-text-muted">Updated</div>
                <div>{new Date(selected.updated_at).toLocaleString()}</div>
              </div>
            </Card>

            <div
              className="idm-flex idm-gap-2 idm-justify-between"
              style={{ paddingTop: 12, borderTop: "1px solid var(--idm-border)" }}
            >
              <Button
                variant="danger"
                onClick={() => selected && removeM.mutate(selected.id)}
                disabled={removeM.isPending}
              >
                {t("owners.remove")}
              </Button>
              <div className="idm-flex idm-gap-2">
                <Button variant="ghost" onClick={() => setSelected(null)}>
                  {t("common.close")}
                </Button>
                {!selected.is_verified && (
                  <Button
                    variant="primary"
                    onClick={() => verifyM.mutate(selected.id)}
                    disabled={verifyM.isPending}
                  >
                    {t("owners.verify")}
                  </Button>
                )}
              </div>
            </div>
          </div>
        )}
      </Drawer>
    </>
  );
}
