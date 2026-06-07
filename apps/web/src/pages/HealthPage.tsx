/**
 * HealthPage — Service info and readiness checks.
 */
import { useQuery } from "@tanstack/react-query";
import axios from "axios";
import { useTranslation } from "react-i18next";
import { Card, Stat, Stats, Status, Tag } from "../ui";

interface HealthInfo {
  service: string;
  env: string;
  version: string;
  planner_model: string;
  default_model: string;
}

interface Readiness {
  status: "ok" | "degraded" | "down";
  env: string;
  version: string;
  checks: Record<string, string>;
}

export function HealthPage() {
  const { t } = useTranslation();
  const { data: info } = useQuery<HealthInfo>({
    queryKey: ["info"],
    queryFn: async () => (await axios.get("/api/health/info")).data,
  });
  const { data: ready } = useQuery<Readiness>({
    queryKey: ["ready"],
    queryFn: async () => (await axios.get("/api/health/ready")).data,
    refetchInterval: 10_000,
  });

  return (
    <>
      <Stats>
        <Stat
          label={t("common.status")}
          value={
            ready ? (
              <Status
                kind={
                  ready.status === "ok"
                    ? "ok"
                    : ready.status === "degraded"
                      ? "warn"
                      : "fail"
                }
              >
                {ready.status}
              </Status>
            ) : (
              "…"
            )
          }
        />
        <Stat label={t("health.env")} value={info?.env ?? "…"} />
        <Stat label={t("health.version")} value={info?.version ?? "…"} />
        <Stat
          label={t("health.plannerModel")}
          value={info ? <Tag solid color="#2e66f0">{info.planner_model}</Tag> : "…"}
        />
      </Stats>

      <Card title={t("health.serviceInfo")}>
        {info ? (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "160px 1fr",
              gap: "8px 16px",
              fontSize: 13,
              alignItems: "center",
            }}
          >
            <div className="idm-text-muted">{t("health.service")}</div>
            <div className="idm-fw-600">{info.service}</div>
            <div className="idm-text-muted">{t("health.env")}</div>
            <div>
              <Tag>{info.env}</Tag>
            </div>
            <div className="idm-text-muted">{t("health.version")}</div>
            <div style={{ fontFamily: "var(--idm-mono-font)" }}>{info.version}</div>
            <div className="idm-text-muted">{t("health.plannerModel")}</div>
            <div>
              <Tag solid color="#2e66f0">{info.planner_model}</Tag>
            </div>
            <div className="idm-text-muted">{t("health.defaultModel")}</div>
            <div>
              <Tag>{info.default_model}</Tag>
            </div>
          </div>
        ) : (
          <p className="idm-text-muted">Loading…</p>
        )}
      </Card>

      <Card title={t("health.readiness")}>
        {ready ? (
          <>
            <div className="idm-flex idm-gap-2 idm-items-center idm-mb-3">
              <span className="idm-text-muted">{t("common.status")}:</span>
              <Status
                kind={
                  ready.status === "ok"
                    ? "ok"
                    : ready.status === "degraded"
                      ? "warn"
                      : "fail"
                }
              >
                {ready.status}
              </Status>
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "160px 1fr",
                gap: "8px 16px",
                fontSize: 13,
                alignItems: "center",
              }}
            >
              {Object.entries(ready.checks).map(([k, v]) => (
                <>
                  <div key={`k-${k}`} className="idm-text-muted" style={{ fontFamily: "var(--idm-mono-font)" }}>
                    {k}
                  </div>
                  <div key={`v-${k}`}>
                    <Status kind={v === "ok" ? "ok" : "fail"}>{v}</Status>
                  </div>
                </>
              ))}
            </div>
          </>
        ) : (
          <p className="idm-text-muted">Loading…</p>
        )}
      </Card>
    </>
  );
}
