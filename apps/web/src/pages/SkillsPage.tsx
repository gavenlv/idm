/**
 * SkillsPage — M1 S1.3
 *
 * 1. 顶部: MCP 健康条 (点击刷新)
 * 2. 中部: ag-grid 列出已注册 Skill (name / version / agent / action)
 * 3. 选中一行: 右侧 Drawer 显示 Skill 描述 / 默认 inputs / Run 按钮 / 最近一次执行结果
 *
 * 设计原则:
 * - LLM / MCP 调用全部走后端 Skill API, 前端不直接调 (符合 AGENT_INSTRUCTIONS §1)
 * - 跑 discover 后, "跳到资产页" 按钮自动出现
 */
import { useMemo, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Card, Drawer, Tag, Button } from "../ui";
import { SKILL_INPUT_HINTS, SkillsApi, type SkillMeta, type SkillRunResp } from "../lib/api";

export function SkillsPage() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<SkillMeta | null>(null);
  const [inputsText, setInputsText] = useState<string>("");
  const [lastResult, setLastResult] = useState<SkillRunResp | null>(null);
  const [parseErr, setParseErr] = useState<string | null>(null);

  // 1) 拉 Skill 名单
  const skillsQ = useQuery({
    queryKey: ["skills"],
    queryFn: () => SkillsApi.list(),
    refetchInterval: 30_000,
  });

  // 2) MCP 健康
  const mcpQ = useQuery({
    queryKey: ["mcp-health"],
    queryFn: () => SkillsApi.mcpHealth(),
    refetchInterval: 15_000,
  });

  // 3) 跑 Skill
  const runM = useMutation({
    mutationFn: (vars: { name: string; inputs: Record<string, unknown> }) =>
      SkillsApi.run(vars.name, vars.inputs),
    onSuccess: (resp, vars) => {
      setLastResult(resp);
      // 跑完 discover 失效资产缓存, 让"跳到资产页"看到新数据
      if (vars.name === "discover_clickhouse_assets") {
        qc.invalidateQueries({ queryKey: ["assets"] });
      }
      if (vars.name === "infer_table_description") {
        qc.invalidateQueries({ queryKey: ["suggestions"] });
      }
    },
  });

  const columnDefs = useMemo(
    () => [
      { field: "name", headerName: "Skill", flex: 2, sortable: true, filter: true },
      { field: "agent", headerName: "Agent", width: 110, cellRenderer: (p: { value: string }) => <Tag color="#1f6feb">{p.value}</Tag> },
      { field: "version", headerName: "v", width: 70 },
      {
        headerName: "Action",
        width: 130,
        cellRenderer: (p: { data: SkillMeta }) => (
          <Button size="sm" variant="primary" onClick={() => openDrawer(p.data)}>
            打开
          </Button>
        ),
      },
    ],
    [],
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
        setParseErr(`JSON 解析失败: ${(e as Error).message}`);
        return;
      }
    }
    setParseErr(null);
    runM.mutate({ name: selected.name, inputs: parsed });
  }

  return (
    <>
      {/* MCP 健康条 */}
      <Card
        title="MCP Sidecar 健康"
        extra={
          <Button size="sm" variant="ghost" onClick={() => mcpQ.refetch()}>
            刷新
          </Button>
        }
      >
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          {mcpQ.isLoading ? (
            <span>检查中…</span>
          ) : mcpQ.data ? (
            <>
              {Object.entries(mcpQ.data.checks).map(([name, c]) => (
                <span key={name}>
                  {name}:{" "}
                  <Tag color={c.status === "ok" ? "#52c41a" : "#d4380d"}>
                    {c.status}
                  </Tag>
                  {c.status !== "ok" && c.error ? (
                    <span style={{ color: "#d4380d", marginLeft: 6, fontSize: 12 }}>
                      {String(c.error).slice(0, 80)}
                    </span>
                  ) : null}
                </span>
              ))}
              <span style={{ color: "#999", fontSize: 12, marginLeft: 8 }}>
                all_ok: {String(mcpQ.data.all_ok)}
              </span>
            </>
          ) : (
            <span style={{ color: "#d4380d" }}>拉取失败</span>
          )}
        </div>
      </Card>

      {/* Skill 名单 */}
      <Card title={`已注册 Skill (${skillsQ.data?.items.length ?? 0})`}>
        <div className="ag-theme-quartz" style={{ height: 360, width: "100%" }}>
          <AgGridReact
            rowData={skillsQ.data?.items ?? []}
            columnDefs={columnDefs}
            loading={skillsQ.isLoading}
            pagination
            paginationPageSize={20}
            onRowClicked={(e) => e.data && openDrawer(e.data)}
          />
        </div>
      </Card>

      {/* 详情 / Run Drawer */}
      <Drawer
        open={!!selected}
        onClose={() => setSelected(null)}
        title={selected ? `${selected.name} (v${selected.version} · ${selected.agent})` : ""}
        width={560}
      >
        {selected && (
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <p style={{ color: "#666" }}>{SKILL_INPUT_HINTS[selected.name]?.description ?? "AI 驱动的治理 Skill, 详见后端 handler."}</p>

            <div>
              <label style={{ display: "block", fontWeight: 600, marginBottom: 4 }}>Inputs (JSON)</label>
              <textarea
                value={inputsText}
                onChange={(e) => setInputsText(e.target.value)}
                rows={8}
                style={{ width: "100%", fontFamily: "monospace", fontSize: 12, padding: 8, border: "1px solid #d9d9d9", borderRadius: 4 }}
              />
              {parseErr && <p style={{ color: "#d4380d", fontSize: 12, marginTop: 4 }}>{parseErr}</p>}
            </div>

            <div style={{ display: "flex", gap: 8 }}>
              <Button onClick={handleRun} disabled={runM.isPending}>
                {runM.isPending ? "执行中…" : "Run"}
              </Button>
              <Button variant="ghost" onClick={() => setSelected(null)}>
                关闭
              </Button>
            </div>

            {runM.isError && (
              <pre style={{ background: "#fff1f0", color: "#d4380d", padding: 10, borderRadius: 6, fontSize: 12, overflow: "auto" }}>
                {String((runM.error as Error)?.message ?? runM.error)}
              </pre>
            )}

            {lastResult && (
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                  <span>结果:</span>
                  <Tag color={lastResult.ok ? "#52c41a" : "#d4380d"}>
                    {lastResult.ok ? "OK" : "FAIL"}
                  </Tag>
                  <span style={{ color: "#999", fontSize: 12 }}>{lastResult.duration_ms} ms</span>
                </div>

                {lastResult.error && (
                  <pre style={{ background: "#fff1f0", color: "#d4380d", padding: 10, borderRadius: 6, fontSize: 12, overflow: "auto" }}>
                    {lastResult.error}
                  </pre>
                )}

                {Object.keys(lastResult.output.summary ?? {}).length > 0 && (
                  <details open>
                    <summary style={{ cursor: "pointer", fontWeight: 600 }}>summary</summary>
                    <pre style={{ background: "#f7f8fa", padding: 10, borderRadius: 6, fontSize: 12, overflow: "auto" }}>
                      {JSON.stringify(lastResult.output.summary, null, 2)}
                    </pre>
                  </details>
                )}

                {lastResult.output.items.length > 0 && (
                  <details>
                    <summary style={{ cursor: "pointer", fontWeight: 600 }}>
                      items (前 {Math.min(20, lastResult.output.items.length)} 条)
                    </summary>
                    <pre style={{ background: "#f7f8fa", padding: 10, borderRadius: 6, fontSize: 12, overflow: "auto", maxHeight: 260 }}>
                      {JSON.stringify(lastResult.output.items.slice(0, 20), null, 2)}
                    </pre>
                  </details>
                )}

                {lastResult.output.artifacts.length > 0 && (
                  <p style={{ fontSize: 12, color: "#666" }}>
                    写入 KG: {lastResult.output.artifacts.length} 个 entity (跳{" "}
                    <Link to="/suggestions" onClick={() => setSelected(null)}>建议页</Link>
                    {selected.name === "discover_clickhouse_assets" && (
                      <>
                        {" / "}
                        <Link to="/" onClick={() => setSelected(null)}>资产页</Link>
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
