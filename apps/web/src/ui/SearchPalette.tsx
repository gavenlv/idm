/**
 * SearchPalette — Global command palette (Cmd+K / Ctrl+K).
 *
 * Aggregates hits from 6 entity types via the `/v1/search` endpoint.
 * Inspired by DataHub / Linear command palette.
 *
 * Features:
 *  - Cmd/Ctrl+K to open; ESC to close
 *  - Debounced query (200ms)
 *  - Keyboard navigation (↑/↓/Enter)
 *  - Grouped results by kind
 *  - Click or Enter to navigate
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { SearchApi, type SearchHit, type SearchKind } from "../lib/api";

const KIND_COLORS: Record<SearchKind, string> = {
  asset: "#0b7ea4",
  owner: "#7159f3",
  tag: "#2e8540",
  glossary: "#d97706",
  use_case: "#1d44ad",
  suggestion: "#cf1124",
};

const KIND_ICON: Record<SearchKind, string> = {
  asset: "▣",
  owner: "◉",
  tag: "#",
  glossary: "§",
  use_case: "✦",
  suggestion: "◇",
};

function useDebounced<T>(value: T, delay = 200): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return v;
}

interface Props {
  open: boolean;
  onClose: () => void;
}

export function SearchPalette({ open, onClose }: Props) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const debounced = useDebounced(query, 180);

  // Search query
  const searchQ = useQuery({
    queryKey: ["search", debounced],
    queryFn: () => SearchApi.query(debounced, 30),
    enabled: open && debounced.length > 0,
  });

  // Focus input when opened
  useEffect(() => {
    if (open) {
      setQuery("");
      setActive(0);
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  // Group hits by kind
  const grouped = useMemo(() => {
    const items: SearchHit[] = searchQ.data?.items ?? [];
    const out: Record<SearchKind, SearchHit[]> = {
      asset: [],
      owner: [],
      tag: [],
      glossary: [],
      use_case: [],
      suggestion: [],
    };
    for (const h of items) out[h.kind].push(h);
    return out;
  }, [searchQ.data]);

  const flat = useMemo(() => Object.values(grouped).flat(), [grouped]);

  // Keyboard nav
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive((a) => Math.min(flat.length - 1, a + 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive((a) => Math.max(0, a - 1));
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        const hit = flat[active];
        if (hit) {
          navigate(hit.url);
          onClose();
        }
        return;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, flat, active, navigate, onClose]);

  // Auto-scroll active into view
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${active}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [active]);

  if (!open) return null;

  return (
    <div
      className="idm-palette-backdrop"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(33, 39, 42, 0.45)",
        zIndex: 1000,
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "center",
        paddingTop: "12vh",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--idm-bg)",
          border: "1px solid var(--idm-border)",
          width: 640,
          maxWidth: "92vw",
          maxHeight: "70vh",
          display: "flex",
          flexDirection: "column",
          boxShadow: "0 12px 48px rgba(0,0,0,0.18)",
        }}
      >
        {/* Search input */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "12px 14px",
            borderBottom: "1px solid var(--idm-border)",
          }}
        >
          <span style={{ fontSize: 16, color: "var(--idm-text-muted)" }}>⌘K</span>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActive(0);
            }}
            placeholder={t("palette.placeholder")}
            style={{
              flex: 1,
              border: 0,
              outline: "none",
              background: "transparent",
              fontSize: 15,
              color: "var(--idm-text)",
            }}
          />
          <span
            className="idm-text-muted"
            style={{
              fontSize: 11,
              padding: "2px 6px",
              border: "1px solid var(--idm-border)",
            }}
          >
            ESC
          </span>
        </div>

        {/* Results */}
        <div
          ref={listRef}
          style={{ flex: 1, overflow: "auto", padding: 4 }}
        >
          {debounced.length === 0 ? (
            <div style={{ padding: 24, color: "var(--idm-text-muted)" }}>
              <p style={{ margin: 0, fontSize: 13 }}>
                {t("palette.intro")}
              </p>
              <ul
                style={{
                  marginTop: 8,
                  paddingLeft: 16,
                  fontSize: 12,
                  lineHeight: 1.8,
                }}
              >
                <li><code style={{ fontFamily: "var(--idm-mono-font)" }}>orders_daily</code> — search assets</li>
                <li><code style={{ fontFamily: "var(--idm-mono-font)" }}>alice</code> — search owners</li>
                <li><code style={{ fontFamily: "var(--idm-mono-font)" }}>pii</code> — search tags</li>
                <li><code style={{ fontFamily: "var(--idm-mono-font)" }}>GMV</code> — search glossary</li>
              </ul>
            </div>
          ) : searchQ.isLoading ? (
            <p className="idm-text-muted" style={{ padding: 16 }}>Searching…</p>
          ) : flat.length === 0 ? (
            <p className="idm-text-muted" style={{ padding: 16 }}>
              No results for &ldquo;{debounced}&rdquo;.
            </p>
          ) : (
            Object.entries(grouped).map(([kind, hits]) =>
              hits.length === 0 ? null : (
                <div key={kind} style={{ marginBottom: 4 }}>
                  <div
                    style={{
                      padding: "6px 12px 4px",
                      fontSize: 10,
                      fontWeight: 600,
                      letterSpacing: 0.5,
                      color: "var(--idm-text-muted)",
                      textTransform: "uppercase",
                    }}
                  >
                    {kind.replace("_", " ")} · {hits.length}
                  </div>
                  {hits.map((h) => {
                    const idx = flat.indexOf(h);
                    const isActive = idx === active;
                    return (
                      <div
                        key={`${h.kind}-${h.id}`}
                        data-idx={idx}
                        onMouseEnter={() => setActive(idx)}
                        onClick={() => {
                          navigate(h.url);
                          onClose();
                        }}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 10,
                          padding: "8px 12px",
                          cursor: "pointer",
                          background: isActive ? "var(--idm-gray-100)" : "transparent",
                          borderLeft: isActive
                            ? `3px solid ${KIND_COLORS[h.kind]}`
                            : "3px solid transparent",
                        }}
                      >
                        <span
                          style={{
                            width: 22,
                            height: 22,
                            background: KIND_COLORS[h.kind],
                            color: "#fff",
                            display: "inline-flex",
                            alignItems: "center",
                            justifyContent: "center",
                            fontSize: 12,
                            fontWeight: 600,
                          }}
                        >
                          {KIND_ICON[h.kind]}
                        </span>
                        <div style={{ minWidth: 0, flex: 1 }}>
                          <div
                            style={{
                              fontSize: 13,
                              fontWeight: 500,
                              whiteSpace: "nowrap",
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                            }}
                          >
                            {h.title}
                          </div>
                          {h.subtitle && (
                            <div
                              className="idm-text-muted"
                              style={{
                                fontSize: 11,
                                whiteSpace: "nowrap",
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                              }}
                            >
                              {h.subtitle}
                            </div>
                          )}
                        </div>
                        <span
                          className="idm-text-muted"
                          style={{ fontSize: 11, fontFamily: "var(--idm-mono-font)" }}
                        >
                          ↵
                        </span>
                      </div>
                    );
                  })}
                </div>
              ),
            )
          )}
        </div>

        {/* Footer */}
        <div
          style={{
            display: "flex",
            gap: 12,
            padding: "8px 14px",
            borderTop: "1px solid var(--idm-border)",
            fontSize: 11,
            color: "var(--idm-text-muted)",
          }}
        >
          <span>↑↓ navigate</span>
          <span>↵ open</span>
          <span>esc close</span>
          <span style={{ marginLeft: "auto" }}>
            {searchQ.data ? `${searchQ.data.total} results` : ""}
          </span>
        </div>
      </div>
    </div>
  );
}

/**
 * Hook helper: register Cmd/Ctrl+K to open palette.
 */
export function useSearchPalette() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return { open, setOpen };
}
