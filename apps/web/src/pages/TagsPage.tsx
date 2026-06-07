/**
 * TagsPage — DataHub-style tag library + asset binding.
 *
 * Layout:
 *  - Toolbar: search + category filter
 *  - Color swatch grid of tags (DataHub-style, no rounded corners)
 *  - Drawer: tag detail (color / description / assets count) + bind/unbind to assets
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AgGridReact } from "ag-grid-react";
import type { ColDef } from "ag-grid-community";
import {
  Button,
  Card,
  Drawer,
  Input,
  Select,
  Stat,
  Stats,
  Tag as TagUi,
} from "../ui";
import { AssetsApi, TagsApi, type Tag, type TagCreate, type TagCategory, type TableAsset } from "../lib/api";

const CATEGORY_COLORS: Record<string, string> = {
  pii: "#cf1124",
  tier: "#1d44ad",
  domain: "#0b7ea4",
  status: "#d97706",
  custom: "#697077",
};

export function TagsPage() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [cat, setCat] = useState<string>("");
  const [selected, setSelected] = useState<Tag | null>(null);

  // create form
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<TagCreate>({
    name: "",
    category: "custom",
    color: "#697077",
    description: "",
  });

  // binding drawer state
  const [bindSearch, setBindSearch] = useState("");
  const [bindMode, setBindMode] = useState<"view" | "add">("view");
  const [pending, setPending] = useState<Tag | null>(null); // tag in binding flow

  const tagsQ = useQuery({
    queryKey: ["tags", cat, q],
    queryFn: () =>
      TagsApi.list({
        category: cat || undefined,
        q: q || undefined,
        limit: 200,
      }),
  });

  const createM = useMutation({
    mutationFn: (p: TagCreate) => TagsApi.create(p),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tags"] });
      setForm({ name: "", category: "custom", color: "#697077", description: "" });
      setCreating(false);
    },
  });

  const removeM = useMutation({
    mutationFn: (id: string) => TagsApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tags"] });
      setSelected(null);
    },
  });

  // tag binding
  const assetQ = useQuery({
    queryKey: ["assets-for-tag", bindSearch],
    queryFn: () => AssetsApi.list({ q: bindSearch || undefined, limit: 50 }),
    enabled: bindMode === "add",
  });

  const bindM = useMutation({
    mutationFn: ({ tid, aid }: { tid: string; aid: string }) => TagsApi.bind(aid, tid, "manual"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tags"] });
    },
  });

  const stats = useMemo(() => {
    const items = tagsQ.data?.items ?? [];
    const byCat: Record<string, number> = {};
    for (const t of items) byCat[t.category] = (byCat[t.category] ?? 0) + 1;
    return {
      total: items.length,
      bindings: items.reduce((s, x) => s + x.asset_count, 0),
      byCat,
    };
  }, [tagsQ.data]);

  // Color swatch grid view (DataHub-style)
  const grouped = useMemo(() => {
    const items = tagsQ.data?.items ?? [];
    const out: Record<string, Tag[]> = {};
    for (const it of items) {
      out[it.category] = out[it.category] ?? [];
      out[it.category].push(it);
    }
    return out;
  }, [tagsQ.data]);

  // Drawer table: tags
  const drawerColumnDefs = useMemo<ColDef<Tag>[]>(
    () => [
      {
        field: "name",
        headerName: "Name",
        flex: 1.5,
        cellRenderer: (p: { data: Tag }) => (
          <span
            className="idm-avatar"
            style={{
              background: p.data.color,
              color: "#fff",
              width: 16,
              height: 16,
              fontSize: 10,
            }}
          />
        ),
        valueGetter: (p) => p.data?.name,
      },
      {
        field: "category",
        headerName: "Category",
        width: 110,
      },
      { field: "asset_count", headerName: "Used", width: 80 },
    ],
    [],
  );

  return (
    <>
      <Stats>
        <Stat label="Total Tags" value={stats.total} hint="In the dictionary" />
        <Stat
          label="Total Bindings"
          value={stats.bindings}
          hint="Tag-asset relations"
        />
        <Stat label="PII Tags" value={stats.byCat.pii ?? 0} hint="Sensitive-data flags" />
        <Stat label="Custom" value={stats.byCat.custom ?? 0} hint="User-defined" />
      </Stats>

      <Card
        title="Tag Library"
        extra={
          <div className="idm-flex idm-gap-2 idm-items-center">
            <Input
              size="sm"
              placeholder="Search by name…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              style={{ width: 200 }}
            />
            <Select size="sm" value={cat} onChange={(e) => setCat(e.target.value)}>
              <option value="">All categories</option>
              <option value="pii">pii</option>
              <option value="tier">tier</option>
              <option value="domain">domain</option>
              <option value="status">status</option>
              <option value="custom">custom</option>
            </Select>
            <Button variant="primary" onClick={() => setCreating(true)}>
              + New Tag
            </Button>
          </div>
        }
      >
        {Object.keys(grouped).length === 0 ? (
          <p className="idm-text-muted" style={{ padding: 16 }}>
            No tags yet. Create one to start organizing assets.
          </p>
        ) : (
          <div className="idm-flex-col idm-gap-4">
            {Object.entries(grouped).map(([category, list]) => (
              <div key={category}>
                <div className="idm-flex idm-items-center idm-gap-2 idm-mb-2">
                  <TagUi
                    solid
                    color={CATEGORY_COLORS[category] ?? "#697077"}
                  >
                    {category}
                  </TagUi>
                  <span className="idm-text-muted" style={{ fontSize: 12 }}>
                    {list.length} tags
                  </span>
                </div>
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
                    gap: 8,
                  }}
                >
                  {list.map((tg) => (
                    <button
                      key={tg.id}
                      onClick={() => setSelected(tg)}
                      className="idm-tag-card"
                      style={{
                        background: "var(--idm-bg-elevated)",
                        border: "1px solid var(--idm-border)",
                        padding: 10,
                        cursor: "pointer",
                        textAlign: "left",
                        display: "flex",
                        gap: 8,
                        alignItems: "center",
                        borderLeft: `4px solid ${tg.color}`,
                      }}
                    >
                      <span
                        className="idm-avatar"
                        style={{
                          background: tg.color,
                          color: "#fff",
                          width: 24,
                          height: 24,
                          fontSize: 11,
                          fontWeight: 600,
                        }}
                      >
                        {tg.name.slice(0, 2).toUpperCase()}
                      </span>
                      <div className="idm-flex-col" style={{ minWidth: 0, flex: 1 }}>
                        <span
                          style={{
                            fontWeight: 500,
                            fontSize: 13,
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                          }}
                        >
                          {tg.name}
                        </span>
                        <span
                          className="idm-text-muted"
                          style={{ fontSize: 11 }}
                        >
                          {tg.asset_count} bound
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

      {/* Tag detail drawer */}
      <Drawer
        open={!!selected}
        onClose={() => setSelected(null)}
        title={
          selected ? (
            <div className="idm-flex idm-items-center idm-gap-2">
              <span
                style={{
                  display: "inline-block",
                  width: 14,
                  height: 14,
                  background: selected.color,
                }}
              />
              <span style={{ fontWeight: 600 }}>{selected.name}</span>
              <TagUi>{selected.category}</TagUi>
            </div>
          ) : (
            ""
          )
        }
        width={620}
      >
        {selected && (
          <div className="idm-flex-col idm-gap-3">
            <Card title="Detail">
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "100px 1fr",
                  gap: "8px 16px",
                  fontSize: 13,
                }}
              >
                <div className="idm-text-muted">Name</div>
                <div style={{ fontFamily: "var(--idm-mono-font)" }}>{selected.name}</div>
                <div className="idm-text-muted">Category</div>
                <div>{selected.category}</div>
                <div className="idm-text-muted">Color</div>
                <div className="idm-flex idm-items-center idm-gap-2">
                  <span
                    style={{
                      display: "inline-block",
                      width: 16,
                      height: 16,
                      background: selected.color,
                    }}
                  />
                  <code style={{ fontFamily: "var(--idm-mono-font)" }}>{selected.color}</code>
                </div>
                <div className="idm-text-muted">Description</div>
                <div>{selected.description || <span className="idm-text-muted">—</span>}</div>
                <div className="idm-text-muted">Bound to</div>
                <div>{selected.asset_count} assets</div>
              </div>
            </Card>

            <div className="idm-flex idm-gap-2 idm-justify-between">
              <Button
                variant="danger"
                onClick={() => {
                  if (confirm(`Delete tag "${selected.name}"?`)) removeM.mutate(selected.id);
                }}
                disabled={removeM.isPending}
              >
                Delete tag
              </Button>
              <div className="idm-flex idm-gap-2">
                <Button variant="ghost" onClick={() => setSelected(null)}>
                  Close
                </Button>
                <Button
                  variant="primary"
                  onClick={() => {
                    setPending(selected);
                    setBindMode("add");
                  }}
                >
                  Bind to asset…
                </Button>
              </div>
            </div>

            <Card title="All tags" flush>
              <div className="ag-theme-quartz" style={{ height: 280, width: "100%" }}>
                <AgGridReact
                  rowData={tagsQ.data?.items ?? []}
                  columnDefs={drawerColumnDefs}
                  rowHeight={32}
                  getRowId={(p) => p.data.id}
                />
              </div>
            </Card>
          </div>
        )}
      </Drawer>

      {/* Create drawer */}
      <Drawer
        open={creating}
        onClose={() => setCreating(false)}
        title="Create a new tag"
        width={420}
      >
        <div className="idm-flex-col idm-gap-3">
          <div>
            <label className="idm-label">Name (kebab-case)</label>
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="e.g. tier-1"
              style={{ width: "100%" }}
            />
          </div>
          <div>
            <label className="idm-label">Category</label>
            <Select
              value={form.category}
              onChange={(e) => {
                const cat = e.target.value as TagCategory;
                setForm({
                  ...form,
                  category: cat,
                  color: CATEGORY_COLORS[cat] ?? form.color,
                });
              }}
              style={{ width: "100%" }}
            >
              <option value="custom">custom</option>
              <option value="pii">pii</option>
              <option value="tier">tier</option>
              <option value="domain">domain</option>
              <option value="status">status</option>
            </Select>
          </div>
          <div>
            <label className="idm-label">Color</label>
            <div className="idm-flex idm-gap-2 idm-items-center">
              <input
                type="color"
                value={form.color}
                onChange={(e) => setForm({ ...form, color: e.target.value })}
                style={{ width: 48, height: 32, border: "1px solid var(--idm-border)" }}
              />
              <Input
                value={form.color}
                onChange={(e) => setForm({ ...form, color: e.target.value })}
                style={{ width: 100, fontFamily: "var(--idm-mono-font)" }}
              />
            </div>
          </div>
          <div>
            <label className="idm-label">Description</label>
            <Input
              value={form.description ?? ""}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="What this tag is for"
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
              onClick={() => createM.mutate(form)}
              disabled={createM.isPending || !form.name.trim()}
            >
              {createM.isPending ? "Creating…" : "Create"}
            </Button>
          </div>
        </div>
      </Drawer>

      {/* Bind to asset */}
      <Drawer
        open={bindMode === "add" && !!pending}
        onClose={() => setBindMode("view")}
        title={pending ? `Bind "${pending.name}" to asset` : ""}
        width={520}
      >
        {pending && (
          <div className="idm-flex-col idm-gap-3">
            <Input
              placeholder="Search assets by fqn / name…"
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
                    onClick={() => {
                      bindM.mutate({ tid: pending.id, aid: a.id });
                    }}
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
