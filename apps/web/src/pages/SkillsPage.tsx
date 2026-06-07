/**
 * SkillsPage — AI-driven governance skills browser.
 *
 * Layout:
 * - MCP health strip at top
 * - Skills table (ag-grid)
 * - Drawer: skill description, JSON inputs, Run button, result panel
 */
import { useMemo, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef } from "ag-grid-community";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Button, Card, Drawer, Status, Tag, Textarea } from "../ui";
import { SKILL_INPUT_HINTS, SkillsApi, type SkillMeta, type SkillRunResp } from "../lib/api";

const AGENT_COLOR: Record<string, string> = {
  schema: "#2e66f0",
  lineage: "#7159f3",
  doc: "#0b7ea4",
  pii: "#cf1124",
  core: "#2e8540",
  governance: "#d97706",
};

export function SkillsPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [selected, setSelected] = useState<SkillMeta | null>(null);
  const [inputsText, setInputsText] = useState<string>("");
  const [lastResult, setLastResult] = useState<SkillRunResp | null>(null);
  const [parseErr, setParseErr] = useState<string | null>(null);

  const skillsQ = useQuery({
    queryKey: ["skills"],
    queryFn: () => SkillsApi.list(),
    refetchInterval: 30_000,
  });

  const mcpQ = useQuery({
    queryKey: ["mcp-health"],
    queryFn: () => SkillsApi.mcpHealth(),
    refetchInterval: 15_000,
  });

  const runM = useMutation({
    mutationFn: (vars: { name: string; inputs: Record<string, unknown> }) =>
      SkillsApi.run(vars.name, vars.inputs),
    onSuccess: (resp, vars) => {
      setLastResult(resp);
      if (vars.name === "discover_clickhouse_assets") {
        qc.invalidateQueries({ queryKey: ["assets"] });
      }
      if (vars.name === "infer_table_description" || vars.name.includes("pii")) {
        qc.invalidateQueries({ queryKey: ["suggestions"] });
      }
    },
  });

  const columnDefs = useMemo<ColDef<SkillMeta>[]>(
    () => [
      {
        field: "name",
        headerName: "Skill",
        flex: 2,
        sortable: true,
        filter: true,
        cellRenderer: (p: { value: string }) => (
          <span style={{ fontFamily: "var(--idm-mono-font)" }}>{p.value}</span>
        ),
      },
      {
        field: "agent",
        headerName: "Agent",
        width: 120,
        cellRenderer: (p: { value: string }) => (
          <Tag solid color={AGENT_COLOR[p.value] ?? "#697077"}>
            {p.value}
          </Tag>
        ),
      },
      {
        field: "version",
        headerName: "v",
        width: 70,
        cellRenderer: (p: { value: number }) => (
          <span className="idm-text-muted">v{p.value}</span>
        ),
      },
      {
        headerName: "Actions",
        width: 120,
        cellRenderer: (p: { data: SkillMeta }) => (
          <Button size="sm" variant="primary" onClick={() => openDrawer(p.data)}>
            {t("skills.open")}
          </Button>
        ),
      },
    ],
    [t],
  );

  function openDrawer(skill: SkillMeta) {
    setSelected(skill);
    setLastResult(null);
    setParseErr(null);
    const hint = SKILL_INPUT_HINTS[skill.name];
    setInputsText(JSON.stringify(hint?.example ?? {}, null, 2));
  }

  function handleRun() {
    if (!selected) return;
    let parsed: Record<string, unknown> = {};
    const txt = inputsText.trim();
    if (txt.length > 0) {
      try {
        parsed = JSON.parse(txt);
      } catch (e) {
        setParseErr(t("skills.jsonParseError", { message: (e as Error).message }));
        return;
      }
    }
    setParseErr(null);
    runM.mutate({ name: selected.name, inputs: parsed });
  }

  return (
    <>
      {/* MCP Health Strip */}
      <Card
        title={
          <span className="idm-flex idm-items-center idm-gap-2">
            {t("skills.mcpHealth")}
            {mcpQ.data && (
              <Status kind={mcpQ.data.all_ok ? "ok" : "warn"}>
                {mcpQ.data.all_ok ? "OK" : "DEGRADED"}
              </Status>
            )}
          </span>
        }
        extra={
          <Button size="sm" variant="ghost" onClick={() => mcpQ.refetch()}>
            {t("skills.refreshMcp")}
          </Button>
        }
      >
        <div className="idm-flex idm-gap-3 idm-items-center" style={{ flexWrap: "wrap" }}>
          {mcpQ.isLoading ? (
            <span className="idm-text-muted">{t("skills.checking")}</span>
          ) : mcpQ.data ? (
            <>
              {Object.entries(mcpQ.data.checks).map(([name, c]) => (
                <div key={name} className="idm-flex idm-items-center idm-gap-2">
                  <span className="idm-text-muted" style={{ fontFamily: "var(--idm-mono-font)" }}>
                    {name}
                  </span>
                  <Status kind={c.status === "ok" ? "ok" : "fail"}>{c.status}</Status>
                  {c.status !== "ok" && (c as { error?: string }).error && (
                    <span className="idm-text-muted" style={{ fontSize: 11 }}>
                      {String((c as { error?: string }).error).slice(0, 80)}
                    </span>
                  )}
                </div>
              ))}
            </>
          ) : (
            <span style={{ color: "var(--idm-red-500)" }}>{t("skills.pullFailed")}</span>
          )}
        </div>
      </Card>

      {/* Skills Table */}
      <Card title={t("skills.registeredCount", { count: skillsQ.data?.items.length ?? 0 })}>
        <div className="ag-theme-quartz" style={{ height: 360, width: "100%" }}>
          <AgGridReact
            rowData={skillsQ.data?.items ?? []}
            columnDefs={columnDefs}
            loading={skillsQ.isLoading}
            pagination
            paginationPageSize={20}
            onRowClicked={(e) => e.data && openDrawer(e.data)}
            getRowId={(p) => p.data.name}
            rowHeight={36}
          />
        </div>
      </Card>

      {/* Skill Detail Drawer */}
      <Drawer
        open={!!selected}
        onClose={() => setSelected(null)}
        title={
          selected ? (
            <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontFamily: "var(--idm-mono-font)" }}>{selected.name}</span>
              <span className="idm-text-muted" style={{ fontWeight: 400, fontSize: 12 }}>
                v{selected.version} · {selected.agent}
              </span>
            </span>
          ) : (
            ""
          )
        }
        width={600}
      >
        {selected && (
          <div className="idm-flex-col idm-gap-3">
            <p className="idm-text-muted" style={{ lineHeight: 1.6, margin: 0 }}>
              {SKILL_INPUT_HINTS[selected.name]?.description ??
                "AI-driven governance skill. See backend handler for details."}
            </p>

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
                {t("skills.inputs")}
              </label>
              <Textarea
                value={inputsText}
                onChange={(e) => setInputsText(e.target.value)}
                rows={10}
                style={{ width: "100%" }}
              />
              {parseErr && (
                <p style={{ color: "var(--idm-red-500)", fontSize: 12, marginTop: 4 }}>{parseErr}</p>
              )}
            </div>

            <div className="idm-flex idm-gap-2">
              <Button onClick={handleRun} disabled={runM.isPending} variant="primary">
                {runM.isPending ? t("common.running") : t("skills.run")}
              </Button>
              <Button variant="ghost" onClick={() => setSelected(null)}>
                {t("common.close")}
              </Button>
            </div>

            {runM.isError && (
              <pre className="idm-code idm-code--dark">
                {String((runM.error as Error)?.message ?? runM.error)}
              </pre>
            )}

            {lastResult && (
              <div>
                <div
                  className="idm-flex idm-items-center idm-gap-2 idm-mb-2"
                  style={{ paddingTop: 12, borderTop: "1px solid var(--idm-border)" }}
                >
                  <span className="idm-fw-600">{t("skills.result")}:</span>
                  <Status kind={lastResult.ok ? "ok" : "fail"}>
                    {lastResult.ok ? t("common.ok") : t("common.fail")}
                  </Status>
                  <span className="idm-text-muted" style={{ fontSize: 12 }}>
                    {lastResult.duration_ms} ms
                  </span>
                </div>

                {lastResult.error && (
                  <pre className="idm-code idm-code--dark">{lastResult.error}</pre>
                )}

                {Object.keys(lastResult.output.summary ?? {}).length > 0 && (
                  <details open style={{ marginTop: 8 }}>
                    <summary
                      style={{
                        cursor: "pointer",
                        fontWeight: 600,
                        fontSize: 12,
                        color: "var(--idm-text-muted)",
                        textTransform: "uppercase",
                        letterSpacing: 0.5,
                      }}
                    >
                      Summary
                    </summary>
                    <pre className="idm-code">
                      {JSON.stringify(lastResult.output.summary, null, 2)}
                    </pre>
                  </details>
                )}

                {lastResult.output.items.length > 0 && (
                  <details style={{ marginTop: 8 }}>
                    <summary
                      style={{
                        cursor: "pointer",
                        fontWeight: 600,
                        fontSize: 12,
                        color: "var(--idm-text-muted)",
                        textTransform: "uppercase",
                        letterSpacing: 0.5,
                      }}
                    >
                      {t("skills.items", { count: Math.min(20, lastResult.output.items.length) })}
                    </summary>
                    <pre className="idm-code" style={{ maxHeight: 260 }}>
                      {JSON.stringify(lastResult.output.items.slice(0, 20), null, 2)}
                    </pre>
                  </details>
                )}

                {lastResult.output.artifacts.length > 0 && (
                  <p className="idm-text-muted" style={{ fontSize: 12, marginTop: 8 }}>
                    {t("skills.kgWrites", { count: lastResult.output.artifacts.length })} (
                    <Link to="/suggestions" onClick={() => setSelected(null)}>
                      {t("skills.goSuggestions")}
                    </Link>
                    {selected.name === "discover_clickhouse_assets" && (
                      <>
                        {" / "}
                        <Link to="/" onClick={() => setSelected(null)}>
                          {t("skills.goAssets")}
                        </Link>
                      </>
                    )}
                    )
                  </p>
                )}
              </div>
            )}
          </div>
        )}
      </Drawer>
    </>
  );
}
