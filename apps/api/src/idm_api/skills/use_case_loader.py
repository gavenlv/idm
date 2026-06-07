"""use_case_loader: Use Case YAML GitOps loader (M5).

设计:
- 监听 <use_cases_dir> 下 *.yml 文件的 mtime 变化
- 文件变更 → 解析 → 调 Planner (M1 简化: 直接记录到 ai_suggestion, 待人工触发)
- 默认 disable; 通过 env `IDM_USE_CASE_WATCHER=1` 启用
- 周期 60s (可配)

M5 起步: 不实现完整 Planner, 只做:
  1) 检测到新 / 改文件
  2) 校验 (Pydantic)
  3) 写一条 ai_suggestion(suggestion_type=use_case_diff, target_type=use_case, ...)
  4) UI 上人工点击 "Plan" 才真跑

后续: 接 LangGraph Planner 自动调度.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from idm_api.db import get_session_factory
from idm_kg.models.ai_suggestion import AISuggestion

logger = logging.getLogger(__name__)


@dataclass
class UseCaseFile:
    path: Path
    mtime: float
    spec: dict[str, Any]
    raw: str
    sha: str


def _scan_dir(uc_dir: Path) -> list[UseCaseFile]:
    if not uc_dir.exists():
        return []
    out: list[UseCaseFile] = []
    for f in sorted(uc_dir.glob("*.yml")):
        try:
            raw = f.read_text(encoding="utf-8")
            spec = yaml.safe_load(raw) or {}
            out.append(
                UseCaseFile(
                    path=f,
                    mtime=f.stat().st_mtime,
                    spec=spec,
                    raw=raw,
                    sha=hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16],
                )
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("loader: bad %s: %s", f, e)
    return out


async def _emit_diff_suggestion(uc: UseCaseFile) -> None:
    """写一条 ai_suggestion (use_case_diff) 给 UI."""
    spec = uc.spec
    factory = get_session_factory()
    async with factory() as db:
        # 简单去重: 同 uc_id + sha 不重复
        from sqlalchemy import select
        existing = (
            await db.execute(
                select(AISuggestion).where(
                    AISuggestion.suggestion_type == "use_case_diff",
                    AISuggestion.payload["uc_id"].astext == str(spec.get("id") or uc.path.stem),
                )
            )
        ).scalars().first()
        if existing is not None and (existing.payload or {}).get("sha") == uc.sha:
            return
        # target_id 用 use_case id (字符串, 用一个固定 UUID 占位)
        import uuid as _uuid
        sug = AISuggestion(
            suggestion_type="use_case_diff",
            target_type="use_case",
            target_id=_uuid.UUID("00000000-0000-0000-0000-000000000000"),
            payload={
                "uc_id": spec.get("id") or uc.path.stem,
                "version": spec.get("version"),
                "path": str(uc.path),
                "sha": uc.sha,
                "sources_count": len(spec.get("sources") or []),
                "analysis_count": len(spec.get("analysis") or []),
                "description": spec.get("description", ""),
            },
            rationale=f"Detected change in {uc.path.name} (sha={uc.sha})",
            confidence=1.0,
            model="loader",
            skill="use_case_loader",
            status="pending",
        )
        db.add(sug)
        await db.commit()


async def watch_loop(poll_interval: float = 60.0) -> None:
    """主循环. 持续跑直到 cancelled."""
    uc_dir_env = os.environ.get("IDM_USE_CASES_DIR")
    if uc_dir_env:
        uc_dir = Path(uc_dir_env)
    else:
        uc_dir = Path(__file__).resolve().parents[5] / "use_cases"
    logger.info("use_case_loader watching %s every %.0fs", uc_dir, poll_interval)
    last_seen: dict[str, tuple[float, str]] = {}
    while True:
        try:
            files = _scan_dir(uc_dir)
            for f in files:
                key = f.path.name
                prev = last_seen.get(key)
                if prev is None or prev[0] != f.mtime or prev[1] != f.sha:
                    last_seen[key] = (f.mtime, f.sha)
                    if prev is not None:
                        # changed
                        try:
                            await _emit_diff_suggestion(f)
                            logger.info("loader: emit diff for %s", f.path.name)
                        except Exception as e:  # noqa: BLE001
                            logger.exception("loader: emit failed: %s", e)
                    else:
                        # 启动时已有文件, 不发 diff
                        logger.info("loader: discovered %s (sha=%s)", f.path.name, f.sha)
        except Exception as e:  # noqa: BLE001
            logger.exception("use_case_loader tick failed: %s", e)
        await asyncio.sleep(poll_interval)


def main() -> None:  # pragma: no cover
    """CLI: `python -m idm_api.skills.use_case_loader`."""
    asyncio.run(watch_loop())


if __name__ == "__main__":  # pragma: no cover
    main()
