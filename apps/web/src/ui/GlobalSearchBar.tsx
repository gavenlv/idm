/**
 * GlobalSearchBar — 顶部内联全局搜索 (DataHub 风格).
 *
 * 与 SearchPalette (Cmd+K 弹窗) 相比:
 *  - 永远显示在 topbar, 不弹层
 *  - 输入防抖 (180ms), 实时拉 /v1/search
 *  - 键盘: ↓/↑ 选中 / Enter 跳转 / Esc 收起 / "/" 聚焦
 *  - 结果按 kind 分组下拉
 *  - 空态显示 6 类 hint (asset / owner / tag / glossary / use_case / suggestion)
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

function useDebounced<T>(value: T, delay = 180): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return v;
}

interface Props {
  className?: string;
}

export function GlobalSearchBar({ className }: Props) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const wrapRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const debounced = useDebounced(query, 180);

  // 拉搜索
  const searchQ = useQuery({
    queryKey: ["search", debounced],
    queryFn: () => SearchApi.query(debounced, 30),
    enabled: debounced.length > 0,
    staleTime: 30_000,
  });

  // 分组
  const grouped = useMemo(() => {
    const items: SearchHit[] = searchQ.data?.items ?? [];
    const out: Record<SearchKind, SearchHit[]> = {
      asset: [], owner: [], tag: [], glossary: [], use_case: [], suggestion: [],
    };
    for (const h of items) out[h.kind].push(h);
    return out;
  }, [searchQ.data]);

  const flat = useMemo(() => Object.values(grouped).flat(), [grouped]);

  // 外面点击收起
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, []);

  // "/" 全局快捷键聚焦
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const inForm =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);
      if (e.key === "/" && !inForm) {
        e.preventDefault();
        inputRef.current?.focus();
        setOpen(true);
        return;
      }
      if (e.key === "Escape" && open) {
        setOpen(false);
        inputRef.current?.blur();
        return;
      }
      if (!open) return;
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
          setOpen(false);
          setQuery("");
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, flat, active, navigate]);

  // 活动项滚到可见
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${active}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const showDropdown = open && (debounced.length > 0);

  return (
    <div ref={wrapRef} className={`idm-search ${className ?? ""}`}>
      <div
        className="idm-search__input-row"
        onClick={() => inputRef.current?.focus()}
      >
        <span className="idm-search__icon" aria-hidden>⌕</span>
        <input
          ref={inputRef}
          className="idm-search__input"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
            setActive(0);
          }}
          onFocus={() => setOpen(true)}
          placeholder={t("palette.trigger")}
        />
        {query ? (
          <button
            className="idm-search__clear"
            onClick={(e) => {
              e.stopPropagation();
              setQuery("");
              inputRef.current?.focus();
            }}
            title={t("common.close")}
            aria-label={t("common.close")}
          >
            ×
          </button>
        ) : (
          <span className="idm-search__kbd" aria-hidden>/</span>
        )}
      </div>

      {showDropdown && (
        <div ref={listRef} className="idm-search__dropdown" role="listbox">
          {searchQ.isLoading ? (
            <div className="idm-search__hint">{t("common.loading")}</div>
          ) : flat.length === 0 ? (
            <div className="idm-search__hint">
              No results for &ldquo;{debounced}&rdquo;.
            </div>
          ) : (
            Object.entries(grouped).map(([kind, hits]) =>
              hits.length === 0 ? null : (
                <div key={kind} className="idm-search__group">
                  <div className="idm-search__group-title">
                    {kind.replace("_", " ")} · {hits.length}
                  </div>
                  {hits.map((h) => {
                    const idx = flat.indexOf(h);
                    const isActive = idx === active;
                    return (
                      <div
                        key={`${h.kind}-${h.id}`}
                        data-idx={idx}
                        role="option"
                        aria-selected={isActive}
                        onMouseEnter={() => setActive(idx)}
                        onClick={() => {
                          navigate(h.url);
                          setOpen(false);
                          setQuery("");
                        }}
                        className={
                          "idm-search__hit" + (isActive ? " idm-search__hit--active" : "")
                        }
                        style={{
                          borderLeftColor: isActive ? KIND_COLORS[h.kind] : "transparent",
                        }}
                      >
                        <span
                          className="idm-search__hit-icon"
                          style={{ background: KIND_COLORS[h.kind] }}
                        >
                          {KIND_ICON[h.kind]}
                        </span>
                        <div className="idm-search__hit-body">
                          <div className="idm-search__hit-title">{h.title}</div>
                          {h.subtitle && (
                            <div className="idm-search__hit-sub">{h.subtitle}</div>
                          )}
                        </div>
                        <span className="idm-search__hit-enter">↵</span>
                      </div>
                    );
                  })}
                </div>
              ),
            )
          )}
        </div>
      )}

      {/* 空态 hint (聚焦但未输入) */}
      {open && debounced.length === 0 && (
        <div className="idm-search__dropdown" role="listbox">
          <div className="idm-search__hint">{t("palette.intro")}</div>
          <ul className="idm-search__hint-list">
            <li><code>orders_daily</code> — search assets</li>
            <li><code>alice</code> — search owners</li>
            <li><code>pii</code> — search tags</li>
            <li><code>GMV</code> — search glossary</li>
            <li><code>shop-orders-daily</code> — search use cases</li>
          </ul>
          <div className="idm-search__hint-foot">
            <span>↑↓ navigate</span>
            <span>↵ open</span>
            <span>esc close</span>
            <span style={{ marginLeft: "auto" }}>/ focus</span>
          </div>
        </div>
      )}
    </div>
  );
}
