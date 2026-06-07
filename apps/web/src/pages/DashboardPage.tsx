/**
 * DashboardPage — IDM Home / Overview.
 *
 * Inspired by DataHub "Home" + IDM roadmap §6.3.
 * Aggregates KPIs from assets / suggestions / health / owners.
 *
 * Layout (CSS grid):
 *  ┌────────────────────────────────────────────┐
 *  │ 4 KPI cards (total assets, pending sug,    │
 *  │  unverified owners, system health)         │
 *  ├──────────────────────┬─────────────────────┤
 *  │ Asset tier breakdown │ Top pending sug.    │
 *  │ (bar)                │ (list)              │
 *  ├──────────────────────┴─────────────────────┤
 *  │ System readiness (checks table)            │
 *  └────────────────────────────────────────────┘
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Card, EmptyState, Stat, Stats, Status, Tag } from "../ui";
import { AssetsApi, OwnersApi, SuggestionsApi } from "../lib/api";
import axios from "axios";

interface Readiness {
  status: "ok" | "degraded" | "down";
  env: string;
  version: string;
  checks: Record<string, string>;
}

const TIER_COLOR: Record<string, string> = {
  critical: "#cf1124",
  important: "#d97706",
  normal: "#2e66f0",
};

const SUG_TYPE_COLOR: Record<string, string> = {
  description: "#0b7ea4",
  pii_class: "#cf1124",
  owner: "#7159f3",
  lineage: "#2e66f0",
  glossary: "#2e8540",
  quality_rule: "#d97706",
  insight: "#1d44ad",
};

export function DashboardPage() {
  const assetsQ = useQuery({
    queryKey: ["dashboard-assets"],
    queryFn: () => AssetsApi.list({ limit: 200 }),
  });
  const pendingQ = useQuery({
    queryKey: ["dashboard-pending"],
    queryFn: () => SuggestionsApi.list({ status: "pending", limit: 10 }),
  });
  const ownersQ = useQuery({
    queryKey: ["dashboard-owners"],
    queryFn: () => OwnersApi.list({ limit: 200 }),
  });
  const readyQ = useQuery<Readiness>({
    queryKey: ["dashboard-ready"],
    queryFn: async () => (await axios.get("/api/health/ready")).data,
    refetchInterval: 15_000,
  });

  // KPIs
  const assetItems = assetsQ.data?.items ?? [];
  const byTier: Record<string, number> = { critical: 0, important: 0, normal: 0 };
  for (const a of assetItems) byTier[a.tier] = (byTier[a.tier] ?? 0) + 1;
  const totalAssets = assetsQ.data?.total ?? 0;
  const pendingCount = pendingQ.data?.total ?? 0;
  const ownersItems = ownersQ.data?.items ?? [];
  const ownersUnverified = ownersItems.filter((o) => !o.is_verified).length;
  const healthStatus = readyQ.data?.status ?? "—";

  // tier bar (max width = 100%)
  const tierMax = Math.max(1, ...Object.values(byTier));

  return (
    <>
      <Stats>
        <Stat
          label="Total Assets"
          value={totalAssets}
          hint="All data assets across services"
        />
        <Stat
          label="Pending Suggestions"
          value={pendingCount}
          hint="Awaiting human review"
        />
        <Stat
          label="Unverified Owners"
          value={ownersUnverified}
          hint="Need manual confirmation"
        />
        <Stat
          label="System Health"
          value={
            <Status
              kind={
                healthStatus === "ok"
                  ? "ok"
                  : healthStatus === "degraded"
                    ? "warn"
                    : "fail"
              }
            >
              {healthStatus}
            </Status>
          }
          hint="All checks"
        />
      </Stats>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 16,
        }}
      >
        {/* Asset tier breakdown */}
        <Card title="Asset Tier Breakdown" extra={
          <Link to="/" style={{ fontSize: 12, color: "var(--idm-text-muted)" }}>
            View all →
          </Link>
        }>
          {assetItems.length === 0 ? (
            <EmptyState
              title="No assets yet"
              description="Run discover_clickhouse_assets to ingest tables."
            />
          ) : (
            <div className="idm-flex-col idm-gap-3">
              {(["critical", "important", "normal"] as const).map((tier) => {
                const n = byTier[tier] ?? 0;
                const pct = (n / tierMax) * 100;
                return (
                  <div key={tier}>
                    <div className="idm-flex idm-items-center idm-gap-2 idm-mb-1">
                      <Tag solid color={TIER_COLOR[tier]}>
                        {tier}
                      </Tag>
                      <span style={{ fontWeight: 600, fontSize: 14 }}>{n}</span>
                      <span className="idm-text-muted" style={{ fontSize: 11 }}>
                        {pct.toFixed(0)}%
                      </span>
                    </div>
                    <div
                      style={{
                        height: 8,
                        background: "var(--idm-gray-100)",
                        position: "relative",
                      }}
                    >
                      <div
                        style={{
                          position: "absolute",
                          left: 0,
                          top: 0,
                          bottom: 0,
                          width: `${pct}%`,
                          background: TIER_COLOR[tier],
                          transition: "width 240ms ease",
                        }}
                      />
                    </div>
                  </div>
                );
              })}
              <div
                className="idm-flex idm-justify-between"
                style={{
                  paddingTop: 8,
                  marginTop: 4,
                  borderTop: "1px solid var(--idm-border)",
                  fontSize: 12,
                }}
              >
                <span className="idm-text-muted">Total</span>
                <span className="idm-fw-600">{assetItems.length}</span>
              </div>
            </div>
          )}
        </Card>

        {/* Top pending suggestions */}
        <Card title="Top Pending Suggestions" extra={
          <Link to="/suggestions" style={{ fontSize: 12, color: "var(--idm-text-muted)" }}>
            Review queue →
          </Link>
        }>
          {(pendingQ.data?.items ?? []).length === 0 ? (
            <EmptyState
              title="Inbox zero"
              description="All AI suggestions are reviewed. Nice work."
            />
          ) : (
            <div className="idm-flex-col idm-gap-2">
              {(pendingQ.data?.items ?? []).map((s) => (
                <div
                  key={s.id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "8px 0",
                    borderBottom: "1px solid var(--idm-border)",
                  }}
                >
                  <Tag solid color={SUG_TYPE_COLOR[s.suggestion_type] ?? "#697077"}>
                    {s.suggestion_type}
                  </Tag>
                  <div
                    className="idm-flex-col"
                    style={{ flex: 1, minWidth: 0, lineHeight: 1.3 }}
                  >
                    <span
                      style={{
                        fontFamily: "var(--idm-mono-font)",
                        fontSize: 12,
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                      title={s.target_id}
                    >
                      {s.target_id.slice(0, 8)}…
                    </span>
                    <span
                      className="idm-text-muted"
                      style={{
                        fontSize: 11,
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {s.skill} · {new Date(s.created_at).toLocaleString()}
                    </span>
                  </div>
                  <Tag
                    solid
                    color={
                      s.confidence >= 0.85
                        ? "#2e8540"
                        : s.confidence >= 0.6
                          ? "#d97706"
                          : "#cf1124"
                    }
                  >
                    {(s.confidence * 100).toFixed(0)}%
                  </Tag>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      {/* System readiness */}
      <Card title="System Readiness" extra={
        <Link to="/health" style={{ fontSize: 12, color: "var(--idm-text-muted)" }}>
          Health detail →
        </Link>
      }>
        {readyQ.data ? (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
              gap: 8,
            }}
          >
            {Object.entries(readyQ.data.checks).map(([k, v]) => (
              <div
                key={k}
                className="idm-flex idm-items-center idm-justify-between"
                style={{
                  padding: "8px 12px",
                  border: "1px solid var(--idm-border)",
                  background: "var(--idm-bg-elevated)",
                }}
              >
                <span
                  className="idm-text-muted"
                  style={{ fontFamily: "var(--idm-mono-font)", fontSize: 12 }}
                >
                  {k}
                </span>
                <Status kind={v === "ok" ? "ok" : "fail"}>{v}</Status>
              </div>
            ))}
          </div>
        ) : (
          <p className="idm-text-muted">Loading…</p>
        )}
      </Card>

      {/* Quick links */}
      <Card title="Quick Actions">
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
            gap: 8,
          }}
        >
          {[
            { to: "/skills", label: "Run a Skill", desc: "Trigger governance skills" },
            { to: "/suggestions", label: "Review Queue", desc: "Approve AI suggestions" },
            { to: "/lineage", label: "Inspect Lineage", desc: "Trace data dependencies" },
            { to: "/owners", label: "Verify Owners", desc: "Confirm suggested owners" },
            { to: "/feedback", label: "Improve Few-Shot", desc: "Submit accept/reject" },
            { to: "/health", label: "System Health", desc: "Check service readiness" },
          ].map((q) => (
            <Link
              key={q.to}
              to={q.to}
              style={{
                border: "1px solid var(--idm-border)",
                padding: 12,
                background: "var(--idm-bg-elevated)",
                display: "block",
                textDecoration: "none",
                color: "var(--idm-text)",
              }}
            >
              <div className="idm-fw-600" style={{ fontSize: 13 }}>
                {q.label} →
              </div>
              <div className="idm-text-muted" style={{ fontSize: 11, marginTop: 2 }}>
                {q.desc}
              </div>
            </Link>
          ))}
        </div>
      </Card>
    </>
  );
}
