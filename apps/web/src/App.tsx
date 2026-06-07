import { Routes, Route, Link } from "react-router-dom";
import { AssetsPage } from "./pages/AssetsPage";
import { HealthPage } from "./pages/HealthPage";
import { SkillsPage } from "./pages/SkillsPage";
import { SuggestionsPage } from "./pages/SuggestionsPage";

export default function App() {
  return (
    <div className="idm-app">
      <header className="idm-header">
        <h1 className="idm-title">IDM</h1>
        <nav className="idm-nav">
          <Link to="/">资产</Link>
          <Link to="/skills">Skills</Link>
          <Link to="/suggestions">建议审核</Link>
          <Link to="/health">健康</Link>
        </nav>
      </header>
      <main className="idm-main">
        <Routes>
          <Route path="/" element={<AssetsPage />} />
          <Route path="/skills" element={<SkillsPage />} />
          <Route path="/suggestions" element={<SuggestionsPage />} />
          <Route path="/health" element={<HealthPage />} />
        </Routes>
      </main>
    </div>
  );
}
