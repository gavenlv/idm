/**
 * IDM App Shell — DataHub-inspired 侧边栏 + 顶栏布局.
 *
 * 设计原则:
 * - 侧边栏: 始终可见, 群组化导航, 当前页用 2px 蓝条 + 灰底高亮; 支持折叠
 * - 顶栏: 显示当前页标题 + subtitle + 全局 actions (搜索 / 语言切换 / 健康指示)
 * - 内容区: 自适应, 不抢焦点
 */
import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { LanguageSwitcher, SearchPalette, Status, useSearchPalette } from "./ui";
import { DashboardPage } from "./pages/DashboardPage";
import { AssetsPage } from "./pages/AssetsPage";
import { HealthPage } from "./pages/HealthPage";
import { LineagePage } from "./pages/LineagePage";
import { SkillsPage } from "./pages/SkillsPage";
import { SuggestionsPage } from "./pages/SuggestionsPage";
import { OwnersPage } from "./pages/OwnersPage";
import { FeedbackPage } from "./pages/FeedbackPage";
import { TagsPage } from "./pages/TagsPage";
import { GlossaryPage } from "./pages/GlossaryPage";
import { UseCasesPage } from "./pages/UseCasesPage";

const SIDEBAR_COLLAPSED_KEY = "idm_sidebar_collapsed";

interface NavGroup {
  title: string;
  items: Array<{ to: string; key: string; label: string; icon: string }>;
}

const NAV: NavGroup[] = [
  {
    title: "Overview",
    items: [
      { to: "/dashboard", key: "dashboard", label: "Home", icon: "◐" },
    ],
  },
  {
    title: "Govern",
    items: [
      { to: "/", key: "assets", label: "Assets", icon: "▣" },
      { to: "/lineage", key: "lineage", label: "Lineage", icon: "⇄" },
      { to: "/owners", key: "owners", label: "Owners", icon: "◉" },
      { to: "/tags", key: "tags", label: "Tags", icon: "#" },
      { to: "/glossary", key: "glossary", label: "Glossary", icon: "§" },
    ],
  },
  {
    title: "AI",
    items: [
      { to: "/use-cases", key: "use_cases", label: "Use Cases", icon: "✦" },
      { to: "/skills", key: "skills", label: "Skills", icon: "⚙" },
      { to: "/suggestions", key: "suggestions", label: "Suggestions", icon: "◇" },
      { to: "/feedback", key: "feedback", label: "Feedback", icon: "✓" },
    ],
  },
  {
    title: "System",
    items: [
      { to: "/health", key: "health", label: "Health", icon: "♥" },
    ],
  },
];

const PAGE_META: Record<string, { title: string; subtitle: string }> = {
  "/dashboard": { title: "Home", subtitle: "Overview of assets, AI suggestions, owners and system health" },
  "/": { title: "Assets", subtitle: "All data assets across services" },
  "/lineage": { title: "Lineage", subtitle: "Upstream and downstream dependencies" },
  "/owners": { title: "Owners", subtitle: "Suggested owners per asset" },
  "/tags": { title: "Tags", subtitle: "Business tag dictionary + asset binding" },
  "/glossary": { title: "Glossary", subtitle: "Business term dictionary" },
  "/use-cases": { title: "Use Cases", subtitle: "Declarative governance contracts (YAML, GitOps)" },
  "/skills": { title: "Skills", subtitle: "AI-driven governance skills" },
  "/suggestions": { title: "Suggestions", subtitle: "Review and approve AI suggestions" },
  "/feedback": { title: "User Feedback", subtitle: "Improve Few-Shot quality from accept/reject" },
  "/health": { title: "Health", subtitle: "Service info and readiness" },
};

function NotFoundPlaceholder({ path }: { path: string }) {
  const { t } = useTranslation();
  return (
    <div className="idm-card">
      <div className="idm-card__header">
        <div className="idm-card__title">{t("common.noData")}</div>
      </div>
      <div className="idm-card__body">
        <p className="idm-text-muted">
          Page <code>{path}</code> is not implemented yet.
        </p>
      </div>
    </div>
  );
}

function useSidebarCollapsed() {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1";
  });
  useEffect(() => {
    if (typeof window === "undefined") return;
    localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? "1" : "0");
  }, [collapsed]);
  return [collapsed, setCollapsed] as const;
}

export default function App() {
  const { t } = useTranslation();
  const location = useLocation();
  const palette = useSearchPalette();
  const [collapsed, setCollapsed] = useSidebarCollapsed();
  const meta = PAGE_META[location.pathname] ?? {
    title: t("common.noData"),
    subtitle: "",
  };

  // 滚到顶部当路由切换
  useEffect(() => {
    document.querySelector(".idm-main")?.scrollTo?.(0, 0);
  }, [location.pathname]);

  return (
    <div className={"idm-app" + (collapsed ? " idm-app--collapsed" : "")}>
      {/* === Sidebar === */}
      <aside className="idm-sidebar">
        <div className="idm-sidebar__brand">
          <span className="idm-sidebar__brand-mark">IDM</span>
          <span className="idm-sidebar__brand-text">{t("app.name")}</span>
          <span className="idm-sidebar__brand-sub">{t("app.subtitle")}</span>
        </div>

        {NAV.map((group) => (
          <div key={group.title}>
            <div className="idm-sidebar__section">{group.title}</div>
            <nav className="idm-sidebar__nav">
              {group.items.map((it) => (
                <NavLink
                  key={it.to}
                  to={it.to}
                  end={it.to === "/"}
                  title={collapsed ? t(`nav.${it.key}`, it.label) : undefined}
                  className={({ isActive }) =>
                    "idm-sidebar__link" + (isActive ? " idm-sidebar__link--active" : "")
                  }
                >
                  <span className="idm-sidebar__link-icon" aria-hidden>
                    {it.icon}
                  </span>
                  <span className="idm-sidebar__link-text">
                    {t(`nav.${it.key}`, it.label)}
                  </span>
                </NavLink>
              ))}
            </nav>
          </div>
        ))}

        <div className="idm-sidebar__footer">
          <span className="idm-sidebar__footer-text">
            IDM v0.1.0 · M1
            <br />
            © {new Date().getFullYear()} IDM
          </span>
          <button
            className="idm-sidebar__toggle"
            onClick={() => setCollapsed((c) => !c)}
            title={collapsed ? t("sidebar.expand") : t("sidebar.collapse")}
            aria-label={collapsed ? t("sidebar.expand") : t("sidebar.collapse")}
            aria-expanded={!collapsed}
          >
            {collapsed ? "›" : "‹"}
          </button>
        </div>
      </aside>

      {/* === Topbar === */}
      <header className="idm-topbar">
        <div>
          <div className="idm-topbar__title">{meta.title}</div>
          <div className="idm-topbar__subtitle">{meta.subtitle}</div>
        </div>
        <div className="idm-topbar__spacer" />
        <div className="idm-topbar__actions">
          <button
            className="idm-search-trigger"
            onClick={() => palette.setOpen(true)}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 8,
              padding: "6px 10px",
              border: "1px solid var(--idm-border)",
              background: "var(--idm-bg-elevated)",
              color: "var(--idm-text-muted)",
              fontSize: 12,
              cursor: "pointer",
              minWidth: 200,
            }}
          >
            <span>⌕</span>
            <span style={{ flex: 1, textAlign: "left" }}>
              {t("palette.trigger")}
            </span>
            <span
              style={{
                fontSize: 10,
                padding: "1px 4px",
                border: "1px solid var(--idm-border)",
              }}
            >
              ⌘K
            </span>
          </button>
          <Status kind="ok">LIVE</Status>
          <LanguageSwitcher />
        </div>
      </header>

      {/* === Main === */}
      <main className="idm-main">
        <Routes>
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/" element={<AssetsPage />} />
          <Route path="/lineage" element={<LineagePage />} />
          <Route path="/owners" element={<OwnersPage />} />
          <Route path="/tags" element={<TagsPage />} />
          <Route path="/glossary" element={<GlossaryPage />} />
          <Route path="/use-cases" element={<UseCasesPage />} />
          <Route path="/skills" element={<SkillsPage />} />
          <Route path="/suggestions" element={<SuggestionsPage />} />
          <Route path="/feedback" element={<FeedbackPage />} />
          <Route path="/health" element={<HealthPage />} />
          <Route
            path="*"
            element={<NotFoundPlaceholder path={location.pathname} />}
          />
        </Routes>
      </main>

      {/* === Global search palette === */}
      <SearchPalette open={palette.open} onClose={() => palette.setOpen(false)} />
    </div>
  );
}
