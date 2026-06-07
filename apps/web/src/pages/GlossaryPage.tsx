/**
 * GlossaryPage — Business term dictionary.
 *
 * Layout:
 *  - Toolbar: search + domain filter + new
 *  - Two-column grid: term cards grouped by first letter (DataHub glossary style)
 *  - Drawer: term detail (synonyms / definition / assets bound) + bind to asset
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  Card,
  Drawer,
  Input,
  Select,
  Stat,
  Stats,
  Tag,
  Textarea,
} from "../ui";
import {
  AssetsApi,
  GlossaryApi,
  type GlossaryTerm,
  type TableAsset,
} from "../lib/api";

const DOMAIN_COLORS: Record<string, string> = {
  sales: "#0b7ea4",
  finance: "#2e8540",
  risk: "#cf1124",
  ops: "#d97706",
  marketing: "#7159f3",
};

export function GlossaryPage() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [domain, setDomain] = useState("");
  const [selected, setSelected] = useState<GlossaryTerm | null>(null);
  const [creating, setCreating] = useState(false);
  const [bindMode, setBindMode] = useState(false);
  const [bindSearch, setBindSearch] = useState("");
  const [form, setForm] = useState({
    name: "",
    definition: "",
    domain: "",
    owner_team: "",
    synonyms: "",
  });

  const termsQ = useQuery({
    queryKey: ["glossary", q, domain],
    queryFn: () =>
      GlossaryApi.list({
        q: q || undefined,
        domain: domain || undefined,
        limit: 200,
      }),
  });

  const createM = useMutation({
    mutationFn: () =>
      GlossaryApi.create({
        name: form.name,
        definition: form.definition,
        domain: form.domain || undefined,
        owner_team: form.owner_team || undefined,
        synonyms: form.synonyms
          .split(/[,，]\s*/)
          .map((s) => s.trim())
          .filter(Boolean),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["glossary"] });
      setForm({ name: "", definition: "", domain: "", owner_team: "", synonyms: "" });
      setCreating(false);
    },
  });

  const removeM = useMutation({
    mutationFn: (id: string) => GlossaryApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["glossary"] });
      setSelected(null);
    },
  });

  const assetQ = useQuery({
    queryKey: ["assets-for-glossary", bindSearch],
    queryFn: () => AssetsApi.list({ q: bindSearch || undefined, limit: 50 }),
    enabled: bindMode,
  });

  const bindM = useMutation({
    mutationFn: ({ tid, aid }: { tid: string; aid: string }) =>
      GlossaryApi.bind(aid, tid, 1, "manual"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["glossary"] });
    },
  });

  const stats = useMemo(() => {
    const items = termsQ.data?.items ?? [];
    const domains = new Set(items.map((i) => i.domain).filter(Boolean));
    return {
      total: items.length,
      domains: domains.size,
      bindings: items.reduce((s, x) => s + x.asset_count, 0),
    };
  }, [termsQ.data]);

  // Group by first letter (A-Z)
  const grouped = useMemo(() => {
    const items = termsQ.data?.items ?? [];
    const out: Record<string, GlossaryTerm[]> = {};
    for (const it of items) {
      const k = (it.name[0] ?? "#").toUpperCase();
      out[k] = out[k] ?? [];
      out[k].push(it);
    }
    for (const k of Object.keys(out)) out[k].sort((a, b) => a.name.localeCompare(b.name));
    return out;
  }, [termsQ.data]);

  return (
    <>
      <Stats>
        <Stat label="Total Terms" value={stats.total} hint="In the dictionary" />
        <Stat label="Domains" value={stats.domains} hint="Distinct domains" />
        <Stat
          label="Total Bindings"
          value={stats.bindings}
          hint="Term-asset relations"
        />
      </Stats>

      <Card
        title="Glossary"
        extra={
          <div className="idm-flex idm-gap-2 idm-items-center">
            <Input
              size="sm"
              placeholder="Search term / definition…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              style={{ width: 220 }}
            />
            <Select size="sm" value={domain} onChange={(e) => setDomain(e.target.value)}>
              <option value="">All domains</option>
              <option value="sales">sales</option>
              <option value="finance">finance</option>
              <option value="risk">risk</option>
              <option value="ops">ops</option>
              <option value="marketing">marketing</option>
            </Select>
            <Button variant="primary" onClick={() => setCreating(true)}>
              + New Term
            </Button>
          </div>
        }
      >
        {Object.keys(grouped).length === 0 ? (
          <p className="idm-text-muted" style={{ padding: 16 }}>
            No glossary terms yet. Add one to start aligning team vocabulary.
          </p>
        ) : (
          <div className="idm-flex-col idm-gap-3">
            {Object.entries(grouped).map(([letter, list]) => (
              <div key={letter}>
                <div
                  className="idm-flex idm-items-center idm-gap-2 idm-mb-2"
                  style={{ borderBottom: "1px solid var(--idm-border)", paddingBottom: 4 }}
                >
                  <span
                    style={{
                      fontFamily: "var(--idm-mono-font)",
                      fontSize: 14,
                      fontWeight: 700,
                      color: "var(--idm-text-muted)",
                    }}
                  >
                    {letter}
                  </span>
                  <span className="idm-text-muted" style={{ fontSize: 11 }}>
                    {list.length}
                  </span>
                </div>
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
                    gap: 8,
                  }}
                >
                  {list.map((term) => (
                    <button
                      key={term.id}
                      onClick={() => setSelected(term)}
                      className="idm-glossary-card"
                      style={{
                        background: "var(--idm-bg-elevated)",
                        border: "1px solid var(--idm-border)",
                        padding: 10,
                        cursor: "pointer",
                        textAlign: "left",
                        borderLeft: `4px solid ${
                          DOMAIN_COLORS[term.domain ?? ""] ?? "#697077"
                        }`,
                      }}
                    >
                      <div className="idm-flex idm-items-center idm-gap-2 idm-mb-1">
                        <span style={{ fontWeight: 600, fontSize: 14 }}>{term.name}</span>
                        {term.domain && (
                          <Tag color={DOMAIN_COLORS[term.domain] ?? "#697077"}>
                            {term.domain}
                          </Tag>
                        )}
                      </div>
                      <p
                        className="idm-text-muted"
                        style={{
                          fontSize: 12,
                          lineHeight: 1.4,
                          margin: 0,
                          display: "-webkit-box",
                          WebkitLineClamp: 2,
                          WebkitBoxOrient: "vertical",
                          overflow: "hidden",
                        }}
                      >
                        {term.definition}
                      </p>
                      <div
                        className="idm-flex idm-items-center idm-gap-2"
                        style={{ marginTop: 6, fontSize: 11 }}
                      >
                        {term.synonyms.slice(0, 3).map((s) => (
                          <span
                            key={s}
                            style={{
                              padding: "0 6px",
                              background: "var(--idm-gray-100)",
                              color: "var(--idm-text-muted)",
                            }}
                          >
                            {s}
                          </span>
                        ))}
                        {term.synonyms.length > 3 && (
                          <span className="idm-text-muted">
                            +{term.synonyms.length - 3}
                          </span>
                        )}
                        <span
                          className="idm-text-muted"
                          style={{ marginLeft: "auto" }}
                        >
                          {term.asset_count} bound
                        </span>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Detail drawer */}
      <Drawer
        open={!!selected}
        onClose={() => setSelected(null)}
        title={
          selected ? (
            <div className="idm-flex idm-items-center idm-gap-2">
              <span style={{ fontWeight: 600 }}>{selected.name}</span>
              {selected.domain && (
                <Tag color={DOMAIN_COLORS[selected.domain] ?? "#697077"}>
                  {selected.domain}
                </Tag>
              )}
            </div>
          ) : (
            ""
          )
        }
        width={520}
      >
        {selected && (
          <div className="idm-flex-col idm-gap-3">
            <Card title="Definition">
              <p style={{ margin: 0, lineHeight: 1.5 }}>{selected.definition}</p>
            </Card>

            {selected.synonyms.length > 0 && (
              <Card title="Synonyms">
                <div className="idm-flex idm-gap-2 idm-flex-wrap">
                  {selected.synonyms.map((s) => (
                    <Tag key={s}>{s}</Tag>
                  ))}
                </div>
              </Card>
            )}

            <Card title="Ownership">
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "100px 1fr",
                  gap: "8px 16px",
                  fontSize: 13,
                }}
              >
                <div className="idm-text-muted">Owner team</div>
                <div>{selected.owner_team || <span className="idm-text-muted">—</span>}</div>
                <div className="idm-text-muted">Bound to</div>
                <div>{selected.asset_count} assets</div>
              </div>
            </Card>

            <div
              className="idm-flex idm-gap-2 idm-justify-between"
              style={{ paddingTop: 12, borderTop: "1px solid var(--idm-border)" }}
            >
              <Button
                variant="danger"
                onClick={() => {
                  if (confirm(`Delete term "${selected.name}"?`)) removeM.mutate(selected.id);
                }}
                disabled={removeM.isPending}
              >
                Delete term
              </Button>
              <div className="idm-flex idm-gap-2">
                <Button variant="ghost" onClick={() => setSelected(null)}>
                  Close
                </Button>
                <Button variant="primary" onClick={() => setBindMode(true)}>
                  Bind to asset…
                </Button>
              </div>
            </div>
          </div>
        )}
      </Drawer>

      {/* Create drawer */}
      <Drawer
        open={creating}
        onClose={() => setCreating(false)}
        title="Create a new glossary term"
        width={520}
      >
        <div className="idm-flex-col idm-gap-3">
          <div>
            <label className="idm-label">Term</label>
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="GMV"
              style={{ width: "100%" }}
            />
          </div>
          <div>
            <label className="idm-label">Definition</label>
            <Textarea
              value={form.definition}
              onChange={(e) => setForm({ ...form, definition: e.target.value })}
              rows={3}
              placeholder="成交总额, 含退款前金额"
              style={{ width: "100%" }}
            />
          </div>
          <div className="idm-flex idm-gap-2">
            <div style={{ flex: 1 }}>
              <label className="idm-label">Domain</label>
              <Select
                value={form.domain}
                onChange={(e) => setForm({ ...form, domain: e.target.value })}
                style={{ width: "100%" }}
              >
                <option value="">—</option>
                <option value="sales">sales</option>
                <option value="finance">finance</option>
                <option value="risk">risk</option>
                <option value="ops">ops</option>
                <option value="marketing">marketing</option>
              </Select>
            </div>
            <div style={{ flex: 1 }}>
              <label className="idm-label">Owner team</label>
              <Input
                value={form.owner_team}
                onChange={(e) => setForm({ ...form, owner_team: e.target.value })}
                placeholder="data-platform"
                style={{ width: "100%" }}
              />
            </div>
          </div>
          <div>
            <label className="idm-label">Synonyms (comma-separated)</label>
            <Input
              value={form.synonyms}
              onChange={(e) => setForm({ ...form, synonyms: e.target.value })}
              placeholder="Gross Merchandise Volume, 总成交额"
              style={{ width: "100%" }}
            />
          </div>
          <div
            className="idm-flex idm-gap-2 idm-justify-between"
            style={{ paddingTop: 12, borderTop: "1px solid var(--idm-border)" }}
          >
            <Button variant="ghost" onClick={() => setCreating(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => createM.mutate()}
              disabled={createM.isPending || !form.name.trim() || !form.definition.trim()}
            >
              {createM.isPending ? "Creating…" : "Create"}
            </Button>
          </div>
        </div>
      </Drawer>

      {/* Bind drawer */}
      <Drawer
        open={bindMode && !!selected}
        onClose={() => setBindMode(false)}
        title={selected ? `Bind "${selected.name}" to asset` : ""}
        width={520}
      >
        {selected && (
          <div className="idm-flex-col idm-gap-3">
            <Input
              placeholder="Search assets…"
              value={bindSearch}
              onChange={(e) => setBindSearch(e.target.value)}
              autoFocus
            />
            <div className="idm-flex-col" style={{ maxHeight: 380, overflow: "auto" }}>
              {(assetQ.data?.items ?? []).map((a: TableAsset) => (
                <div
                  key={a.id}
                  className="idm-flex idm-items-center idm-justify-between"
                  style={{
                    padding: "8px 4px",
                    borderBottom: "1px solid var(--idm-border)",
                  }}
                >
                  <div className="idm-flex-col" style={{ minWidth: 0, flex: 1 }}>
                    <span style={{ fontFamily: "var(--idm-mono-font)", fontSize: 12 }}>
                      {a.fqn}
                    </span>
                    <span className="idm-text-muted" style={{ fontSize: 11 }}>
                      {a.asset_type} · tier={a.tier}
                    </span>
                  </div>
                  <Button
                    size="sm"
                    variant="primary"
                    onClick={() => bindM.mutate({ tid: selected.id, aid: a.id })}
                    disabled={bindM.isPending}
                  >
                    Bind
                  </Button>
                </div>
              ))}
              {assetQ.isLoading && (
                <p className="idm-text-muted" style={{ padding: 16 }}>
                  Loading…
                </p>
              )}
              {!assetQ.isLoading && (assetQ.data?.items ?? []).length === 0 && (
                <p className="idm-text-muted" style={{ padding: 16 }}>
                  No assets match.
                </p>
              )}
            </div>
          </div>
        )}
      </Drawer>
    </>
  );
}
