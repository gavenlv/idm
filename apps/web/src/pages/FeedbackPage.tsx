/**
 * FeedbackPage — User feedback on Skill outputs (drives Few-Shot quality).
 *
 * Layout:
 * - KPIs: total / accepted / rejected / acceptance rate
 * - Per-skill breakdown
 * - Toolbar: skill filter + accept/reject filter
 * - Table (ag-grid): feedback rows
 * - Drawer: raw pred payload + few-shot preview
 */
import { useMemo, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef } from "ag-grid-community";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
  Textarea,
} from "../ui";
import { type FeedbackIn, type FeedbackRecord, FeedbackApi } from "../lib/api";

const SKILL_COLOR: Record<string, string> = {
  infer_table_description: "#0b7ea4",
  classify_pii_columns: "#cf1124",
  infer_table_owners: "#7159f3",
  parse_dbt_manifest: "#2e66f0",
  detect_anomalies: "#d97706",
  discover_clickhouse_assets: "#2e8540",
  nl2sql: "#1d44ad",
};

export function FeedbackPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [skillFilter, setSkillFilter] = useState("");
  const [acceptFilter, setAcceptFilter] = useState<string>("");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<FeedbackRecord | null>(null);
  const [form, setForm] = useState<FeedbackIn>({
    skill: "",
    case_key: "",
    accepted: true,
  });
  const [buildMsg, setBuildMsg] = useState<string | null>(null);

  const fbQ = useQuery({
    queryKey: ["feedback", skillFilter, acceptFilter],
    queryFn: () =>
      FeedbackApi.list({
        skill: skillFilter || undefined,
        accepted:
          acceptFilter === "true" ? true : acceptFilter === "false" ? false : undefined,
        limit: 200,
      }),
  });

  const submitM = useMutation({
    mutationFn: (payload: FeedbackIn) => FeedbackApi.submit(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["feedback"] });
      setForm({ skill: "", case_key: "", accepted: true });
    },
  });

  const buildM = useMutation({
    mutationFn: (skill: string) => FeedbackApi.buildFewShots(skill, 5),
    onSuccess: (resp) => {
      setBuildMsg(`Wrote ${resp.count} examples → ${resp.out_path}`);
      setTimeout(() => setBuildMsg(null), 4000);
    },
  });

  // KPIs
  const stats = useMemo(() => {
    const items = fbQ.data ?? [];
    const total = items.length;
    const accepted = items.filter((r) => r.accepted).length;
    const rejected = total - accepted;
    const rate = total === 0 ? 0 : Math.round((accepted / total) * 100);
    // by skill
    const bySkill: Record<string, { total: number; accepted: number }> = {};
    for (const r of items) {
      const s = r.skill || "unknown";
      bySkill[s] = bySkill[s] ?? { total: 0, accepted: 0 };
      bySkill[s].total += 1;
      if (r.accepted) bySkill[s].accepted += 1;
    }
    return { total, accepted, rejected, rate, bySkill };
  }, [fbQ.data]);

  // distinct skills for the filter dropdown
  const skills = useMemo(() => {
    const items = fbQ.data ?? [];
    return Array.from(new Set(items.map((r) => r.skill).filter(Boolean)));
  }, [fbQ.data]);

  const columnDefs = useMemo<ColDef<FeedbackRecord>[]>(
    () => [
      {
        field: "skill",
        headerName: "Skill",
        flex: 1.4,
        sortable: true,
        filter: true,
        cellRenderer: (p: { value: string }) => (
          <Tag solid color={SKILL_COLOR[p.value] ?? "#697077"}>
            {p.value}
          </Tag>
        ),
      },
      {
        field: "case_key",
        headerName: "Case Key",
        flex: 2.4,
        sortable: true,
        filter: true,
        tooltipField: "case_key",
        cellRenderer: (p: { value: string }) => (
          <span style={{ fontFamily: "var(--idm-mono-font)", fontSize: 11 }}>{p.value}</span>
        ),
      },
      {
        field: "accepted",
        headerName: "Verdict",
        width: 110,
        cellRenderer: (p: { value: boolean }) => (
          <Status kind={p.value ? "ok" : "fail"}>
            {p.value ? "accepted" : "rejected"}
          </Status>
        ),
      },
      {
        field: "created_at",
        headerName: t("common.createdAt"),
        flex: 1.2,
        valueFormatter: (p: { value: string }) => (p.value ? new Date(p.value).toLocaleString() : ""),
      },
    ],
    [t],
  );

  const filtered = useMemo(() => {
    const items = fbQ.data ?? [];
    if (!q.trim()) return items;
    const needle = q.toLowerCase();
    return items.filter(
      (r) =>
        r.skill.toLowerCase().includes(needle) ||
        r.case_key.toLowerCase().includes(needle),
    );
  }, [fbQ.data, q]);

  return (
    <>
      <Stats>
        <Stat label="Total Feedback" value={stats.total} hint="All skill outputs reviewed" />
        <Stat
          label="Accepted"
          value={stats.accepted}
          hint="Helps build few-shot examples"
        />
        <Stat label="Rejected" value={stats.rejected} hint="Improves prompts" />
        <Stat
          label="Acceptance Rate"
          value={`${stats.rate}%`}
          hint="Last 200 reviews"
        />
      </Stats>

      {/* Per-skill breakdown */}
      <Card title="Per-skill breakdown">
        {Object.keys(stats.bySkill).length === 0 ? (
          <p className="idm-text-muted">No feedback yet. Submit one below.</p>
        ) : (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
              gap: 12,
            }}
          >
            {Object.entries(stats.bySkill)
              .sort((a, b) => b[1].total - a[1].total)
              .map(([skill, v]) => {
                const rate = v.total === 0 ? 0 : Math.round((v.accepted / v.total) * 100);
                return (
                  <div
                    key={skill}
                    style={{
                      border: "1px solid var(--idm-border)",
                      padding: 12,
                      background: "var(--idm-bg-elevated)",
                    }}
                  >
                    <div className="idm-flex idm-items-center idm-gap-2 idm-mb-2">
                      <Tag solid color={SKILL_COLOR[skill] ?? "#697077"}>
                        {skill}
                      </Tag>
                      <span className="idm-text-muted" style={{ fontSize: 11 }}>
                        {v.total} reviews
                      </span>
                    </div>
                    <div
                      className="idm-flex idm-items-center idm-gap-2"
                      style={{ fontSize: 12 }}
                    >
                      <div
                        style={{
                          flex: 1,
                          height: 6,
                          background: "#e6f6ec",
                          position: "relative",
                        }}
                      >
                        <div
                          style={{
                            position: "absolute",
                            left: 0,
                            top: 0,
                            bottom: 0,
                            width: `${rate}%`,
                            background:
                              rate >= 80 ? "#2e8540" : rate >= 50 ? "#d97706" : "#cf1124",
                          }}
                        />
                      </div>
                      <span className="idm-fw-600">{rate}%</span>
                    </div>
                  </div>
                );
              })}
          </div>
        )}
      </Card>

      <Card
        title={`Feedback (${fbQ.data?.length ?? 0})`}
        extra={
          <div className="idm-flex idm-gap-2 idm-items-center">
            <Input
              size="sm"
              placeholder="Filter case_key…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              style={{ width: 200 }}
            />
            <Select
              size="sm"
              value={skillFilter}
              onChange={(e) => setSkillFilter(e.target.value)}
            >
              <option value="">All skills</option>
              {skills.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </Select>
            <Select
              size="sm"
              value={acceptFilter}
              onChange={(e) => setAcceptFilter(e.target.value)}
            >
              <option value="">All verdicts</option>
              <option value="true">Accepted</option>
              <option value="false">Rejected</option>
            </Select>
          </div>
        }
      >
        <div className="ag-theme-quartz" style={{ height: 360, width: "100%" }}>
          <AgGridReact
            rowData={filtered}
            columnDefs={columnDefs}
            loading={fbQ.isLoading}
            pagination
            paginationPageSize={50}
            onRowClicked={(e) => e.data && setSelected(e.data)}
            getRowId={(p) => p.data.id}
            rowHeight={36}
          />
        </div>
      </Card>

      {/* Submit new feedback */}
      <Card title="Submit feedback">
        <div
          className="idm-flex-col idm-gap-2"
          style={{ maxWidth: 720 }}
        >
          <div className="idm-flex idm-gap-2">
            <div style={{ flex: 1 }}>
              <label className="idm-label">Skill</label>
              <Input
                value={form.skill}
                onChange={(e) => setForm({ ...form, skill: e.target.value })}
                placeholder="e.g. infer_table_description"
                style={{ width: "100%" }}
              />
            </div>
            <div style={{ flex: 1.4 }}>
              <label className="idm-label">Case key (FQN or suggestion_id)</label>
              <Input
                value={form.case_key}
                onChange={(e) => setForm({ ...form, case_key: e.target.value })}
                placeholder="shop.orders_daily"
                style={{ width: "100%" }}
              />
            </div>
          </div>
          <div>
            <label className="idm-label">Reason (optional)</label>
            <Textarea
              value={form.reason ?? ""}
              onChange={(e) => setForm({ ...form, reason: e.target.value })}
              rows={2}
              style={{ width: "100%" }}
            />
          </div>
          <div className="idm-flex idm-gap-2 idm-justify-between idm-items-center">
            <div className="idm-flex idm-gap-2">
              <Button
                variant={form.accepted ? "primary" : "ghost"}
                onClick={() => setForm({ ...form, accepted: true })}
              >
                👍 Accept
              </Button>
              <Button
                variant={!form.accepted ? "danger" : "ghost"}
                onClick={() => setForm({ ...form, accepted: false })}
              >
                👎 Reject
              </Button>
            </div>
            <Button
              variant="primary"
              onClick={() => submitM.mutate(form)}
              disabled={
                submitM.isPending || !form.skill.trim() || !form.case_key.trim()
              }
            >
              {submitM.isPending ? t("common.running") : "Submit"}
            </Button>
          </div>
        </div>
      </Card>

      <Drawer
        open={!!selected}
        onClose={() => setSelected(null)}
        title={
          selected ? (
            <div className="idm-flex idm-items-center idm-gap-2">
              <Tag solid color={SKILL_COLOR[selected.skill] ?? "#697077"}>
                {selected.skill}
              </Tag>
              <span style={{ fontFamily: "var(--idm-mono-font)", fontSize: 12 }}>
                {selected.case_key}
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
            <div className="idm-flex idm-items-center idm-gap-2">
              <span className="idm-text-muted">Verdict:</span>
              <Status kind={selected.accepted ? "ok" : "fail"}>
                {selected.accepted ? "accepted" : "rejected"}
              </Status>
              <span className="idm-text-muted" style={{ fontSize: 12 }}>
                {new Date(selected.created_at).toLocaleString()}
              </span>
            </div>
            <div className="idm-flex idm-gap-2">
              <Button
                variant="primary"
                onClick={() => buildM.mutate(selected.skill)}
                disabled={buildM.isPending}
              >
                {buildM.isPending ? t("common.running") : "Build Few-Shot file"}
              </Button>
              <Button variant="ghost" onClick={() => setSelected(null)}>
                {t("common.close")}
              </Button>
            </div>
            {buildMsg && (
              <div
                className="idm-code"
                style={{ background: "var(--idm-green-50)", color: "var(--idm-green-600)" }}
              >
                {buildMsg}
              </div>
            )}
          </div>
        )}
      </Drawer>
    </>
  );
}
