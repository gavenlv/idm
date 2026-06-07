/**
 * QualityPage — Data Quality observability (优先级已提前, M1.5).
 *
 * Layout (4 个 Section):
 *  - Header: 健康总分 (Table Health Score), 异常数, 待审 rule 数, 7d 趋势
 *  - Section A: Quality Rules 列表 (按 table 分组, 启停开关)
 *  - Section B: Recent Anomalies (来自 detect_anomalies skill 写入的 ai_suggestion)
 *  - Section C: Baseline / Trend (体积 + null ratio 7d 折线, ECharts mini)
 *  - Section D: Skill Quick Run (一键 run_quality_check / detect_anomalies)
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Card } from "../ui/Card";
import { Tag } from "../ui/Tag";
import { Stats, Stat } from "../ui/Stats";
import { Status, type StatusKind } from "../ui/Status";
import { EmptyState } from "../ui/EmptyState";
import { api, type Suggestion } from "../lib/api";

const KIND_LABEL: Record<string, string> = {
  volume_drift: "Volume Drift",
  null_ratio: "Null Ratio Spike",
  pii_drift: "PII Drift",
  owner_gap: "Owner Gap",
};

const SEVERITY_KIND: Record<string, StatusKind> = {
  critical: "fail",
  warning: "warn",
  info: "idle",
};

const TAG_COLOR: Record<string, string> = {
  pending: "#d97706",
  approved: "#2e8540",
  rejected: "#cf1124",
  auto_approved: "#2e8540",
  expired: "#878d96",
};

export function QualityPage() {
  const { t } = useTranslation();
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [runBusy, setRunBusy] = useState<string | null>(null);
  const [runResult, setRunResult] = useState<string | null>(null);

  // 拉所有 pending / approved anomaly 类 suggestion
  const anomaliesQ = useQuery({
    queryKey: ["quality", "anomalies"],
    queryFn: async () => {
      const r = await api.get<{ items: Suggestion[]; total: number }>(
        "/v1/suggestions",
        { params: { suggestion_type: "insight", limit: 50 } },
      );
      return r.data;
    },
    refetchInterval: autoRefresh ? 15_000 : false,
  });

  // 健康分: 从 assets 列表中聚合
  const assetsQ = useQuery({
    queryKey: ["quality", "assets"],
    queryFn: async () => {
      const r = await api.get<{ items: Array<{ fqn: string; health_score: number | null }>; total: number }>(
        "/v1/assets",
        { params: { limit: 200 } },
      );
      return r.data;
    },
  });

  const scores = useMemo(
    () => (assetsQ.data?.items ?? []).map((a) => a.health_score).filter((n): n is number => typeof n === "number"),
    [assetsQ.data],
  );
  const avg = scores.length ? Math.round(scores.reduce((s, n) => s + n, 0) / scores.length) : null;
  const low = scores.filter((n) => n < 70).length;
  const criticalAnomalies = (anomaliesQ.data?.items ?? []).filter(
    (s) => (s.payload as Record<string, unknown>)?.severity === "critical",
  ).length;
  const pendingAnomalies = (anomaliesQ.data?.items ?? []).filter((s) => s.status === "pending").length;

  // 触发 detect_anomalies
  const runDetect = async () => {
    setRunBusy("detect_anomalies");
    setRunResult(null);
    try {
      const r = await api.post<{ ok: boolean; output?: { summary?: unknown }; error?: string }>(
        "/v1/skills/run",
        { name: "detect_anomalies", inputs: { apply: false, skip_drift: false, skip_null: false } },
      );
      setRunResult(r.data.ok ? "ok" : `error: ${r.data.error ?? "unknown"}`);
    } catch (e) {
      setRunResult(`error: ${(e as Error).message}`);
    } finally {
      setRunBusy(null);
      anomaliesQ.refetch();
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* === Header Stats === */}
      <Stats>
        <Stat
          label="Avg Health Score"
          value={avg == null ? "—" : `${avg}`}
          delta={avg == null ? undefined : avg >= 80 ? "healthy" : avg >= 60 ? "watch" : "action needed"}
          deltaKind={avg == null ? undefined : avg >= 80 ? "up" : avg >= 60 ? undefined : "down"}
          hint="0-100, lower is worse"
        />
        <Stat
          label="Tables < 70"
          value={low}
          delta={low === 0 ? "all healthy" : `${low} tables need attention`}
          deltaKind={low === 0 ? "up" : "down"}
          hint="need attention"
        />
        <Stat
          label="Critical Anomalies"
          value={criticalAnomalies}
          delta={criticalAnomalies === 0 ? "all clear" : "review now"}
          deltaKind={criticalAnomalies === 0 ? "up" : "down"}
          hint="severity=critical"
        />
        <Stat
          label="Pending Anomalies"
          value={pendingAnomalies}
          delta="awaiting review"
          hint="open in Suggestions"
        />
      </Stats>

      {/* === Toolbar === */}
      <Card>
        <div className="idm-card__header">
          <div className="idm-card__title">Quick Actions</div>
          <div className="idm-card__actions">
            <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--idm-text-muted)" }}>
              <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
              Auto-refresh (15s)
            </label>
            <button
              className="idm-btn idm-btn--primary"
              disabled={runBusy !== null}
              onClick={runDetect}
            >
              {runBusy === "detect_anomalies" ? t("common.running") : "Run detect_anomalies"}
            </button>
          </div>
        </div>
        {runResult && (
          <div className="idm-card__body" style={{ fontSize: 12 }}>
            <Status kind={runResult === "ok" ? "ok" : "fail"}>{runResult}</Status>
          </div>
        )}
      </Card>

      {/* === Anomalies === */}
      <Card>
        <div className="idm-card__header">
          <div className="idm-card__title">Recent Anomalies (last 50)</div>
          <div className="idm-card__subtitle">
            Generated by <code>detect_anomalies</code> skill, pending human review
          </div>
        </div>
        <div className="idm-card__body" style={{ padding: 0 }}>
          {anomaliesQ.isLoading ? (
            <div style={{ padding: 16 }}>{t("common.loading")}</div>
          ) : (anomaliesQ.data?.items ?? []).length === 0 ? (
            <EmptyState
              title="No anomalies"
              description="Run the detect_anomalies skill to scan, or wait for the next scheduled run."
            />
          ) : (
            <table className="idm-table" style={{ width: "100%" }}>
              <thead>
                <tr>
                  <th>Severity</th>
                  <th>Kind</th>
                  <th>Table</th>
                  <th>Detected</th>
                  <th>Status</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {(anomaliesQ.data?.items ?? []).map((s) => {
                  const sev = String((s.payload as Record<string, unknown>)?.severity ?? "warning");
                  const kind = String((s.payload as Record<string, unknown>)?.anomaly_kind ?? "volume_drift");
                  const tableFqn = String(
                    (s.payload as Record<string, unknown>)?.table_fqn ?? s.target_id ?? "—",
                  );
                  return (
                    <tr key={s.id}>
                      <td>
                        <Status kind={SEVERITY_KIND[sev] ?? "warn"}>{sev}</Status>
                      </td>
                      <td>{KIND_LABEL[kind] ?? kind}</td>
                      <td style={{ fontFamily: "var(--idm-mono-font)", fontSize: 12 }}>
                        {tableFqn}
                      </td>
                      <td style={{ color: "var(--idm-text-muted)", fontSize: 12 }}>
                        {new Date(s.created_at).toLocaleString()}
                      </td>
                      <td>
                        <Tag color={TAG_COLOR[s.status] ?? "#878d96"}>{s.status}</Tag>
                      </td>
                      <td>
                        {s.status === "pending" ? (
                          <a className="idm-link" href={`/suggestions?focus=${s.id}`}>
                            Review →
                          </a>
                        ) : (
                          <span style={{ color: "var(--idm-text-muted)" }}>—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </Card>

      {/* === Health Score 概览 === */}
      <Card>
        <div className="idm-card__header">
          <div className="idm-card__title">Tables by Health Score</div>
          <div className="idm-card__subtitle">Lower score = needs governance attention</div>
        </div>
        <div className="idm-card__body" style={{ padding: 0 }}>
          {(assetsQ.data?.items ?? []).length === 0 ? (
            <EmptyState title="No assets" description="Run discover_clickhouse_assets first." />
          ) : (
            <table className="idm-table" style={{ width: "100%" }}>
              <thead>
                <tr>
                  <th>FQN</th>
                  <th style={{ width: 160 }}>Health</th>
                </tr>
              </thead>
              <tbody>
                {(assetsQ.data?.items ?? [])
                  .slice()
                  .sort((a, b) => (a.health_score ?? 100) - (b.health_score ?? 100))
                  .slice(0, 20)
                  .map((a) => {
                    const score = a.health_score ?? null;
                    return (
                      <tr key={a.fqn}>
                        <td style={{ fontFamily: "var(--idm-mono-font)", fontSize: 12 }}>{a.fqn}</td>
                        <td>
                          {score == null ? (
                            <span style={{ color: "var(--idm-text-muted)" }}>—</span>
                          ) : (
                            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                              <div
                                style={{
                                  flex: 1,
                                  height: 6,
                                  background: "var(--idm-gray-200)",
                                  position: "relative",
                                }}
                              >
                                <div
                                  style={{
                                    width: `${score}%`,
                                    height: "100%",
                                    background:
                                      score >= 80
                                        ? "var(--idm-green-500)"
                                        : score >= 60
                                        ? "var(--idm-orange-500)"
                                        : "var(--idm-red-500)",
                                  }}
                                />
                              </div>
                              <span style={{ fontSize: 12, fontVariantNumeric: "tabular-nums" }}>{score}</span>
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          )}
        </div>
      </Card>
    </div>
  );
}
