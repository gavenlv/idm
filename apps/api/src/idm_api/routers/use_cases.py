"""/api/v1/use-cases: use case YAML 文件读写 (GitOps 友好).

文件位置: <repo>/use_cases/{id}.yml
- 读: 列出 / 获取 / 校验
- 写: 新建 / 覆盖 (M1: 直接覆盖; M3+ 将走 Git PR)
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError

from idm_api.config import Settings, get_settings
from idm_api.schemas import (
    UseCaseListResponse,
    UseCaseRead,
    UseCaseSave,
    UseCaseSummary,
)

router = APIRouter()

# kebab-case id 校验
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _uc_dir(settings: Settings) -> Path:
    """use case 根目录.

    默认: <repo>/use_cases, 可由 env IDM_USE_CASES_DIR 覆盖.
    """
    base = os.environ.get("IDM_USE_CASES_DIR")
    if base:
        return Path(base)
    # 默认 <repo>/use_cases
    return Path(__file__).resolve().parents[5] / "use_cases"


def _parse(raw: str, path: Path) -> UseCaseSummary:
    try:
        spec: dict[str, Any] = yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid YAML in {path.name}: {e}",
        ) from e
    return UseCaseSummary(
        id=str(spec.get("id") or path.stem),
        version=int(spec.get("version") or 1),
        description=str(spec.get("description") or ""),
        owners=list(spec.get("owners") or []),
        sources_count=len(spec.get("sources") or []),
        analysis_count=len(spec.get("analysis") or []),
        path=str(path),
        updated_at=datetime.fromtimestamp(path.stat().st_mtime),
    )


def _validate_id(uc_id: str) -> None:
    if not ID_RE.match(uc_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid use case id '{uc_id}' (must be kebab-case)",
        )


@router.get("", response_model=UseCaseListResponse, summary="List use cases")
async def list_use_cases(
    settings: Settings = Depends(get_settings),
    q: str | None = Query(None, description="模糊查询 id / description / owner"),
) -> UseCaseListResponse:
    base = _uc_dir(settings)
    if not base.exists():
        return UseCaseListResponse(items=[], total=0)
    items: list[UseCaseSummary] = []
    for f in sorted(base.glob("*.yml")):
        try:
            summary = _parse(f.read_text(encoding="utf-8"), f)
        except HTTPException:
            continue
        if q:
            n = q.lower()
            hay = " ".join(
                [summary.id, summary.description, *summary.owners]
            ).lower()
            if n not in hay:
                continue
        items.append(summary)
    return UseCaseListResponse(items=items, total=len(items))


@router.get("/{uc_id}", response_model=UseCaseRead, summary="Get a use case")
async def get_use_case(
    uc_id: str,
    settings: Settings = Depends(get_settings),
) -> UseCaseRead:
    _validate_id(uc_id)
    base = _uc_dir(settings)
    # 优先精确匹配
    path = base / f"{uc_id}.yml"
    if not path.exists():
        # 模糊搜索匹配
        for f in base.glob("*.yml"):
            try:
                s = _parse(f.read_text(encoding="utf-8"), f)
            except HTTPException:
                continue
            if s.id == uc_id:
                path = f
                break
        else:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="use case not found")
    raw = path.read_text(encoding="utf-8")
    summary = _parse(raw, path)
    try:
        spec: dict[str, Any] = yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"invalid YAML: {e}") from e
    return UseCaseRead(**summary.model_dump(), raw=raw, spec=spec)


@router.put(
    "/{uc_id}",
    response_model=UseCaseRead,
    summary="Create or overwrite a use case (raw YAML)",
)
async def save_use_case(
    uc_id: str,
    payload: UseCaseSave,
    settings: Settings = Depends(get_settings),
) -> UseCaseRead:
    _validate_id(uc_id)
    base = _uc_dir(settings)
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{uc_id}.yml"
    raw = payload.raw
    try:
        spec: dict[str, Any] = yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"invalid YAML: {e}") from e

    # 强制 id 匹配
    if str(spec.get("id") or uc_id) != uc_id:
        spec["id"] = uc_id
        # 重写回 raw 中
        raw = yaml.safe_dump(spec, sort_keys=False, allow_unicode=True)

    # 简单必填校验
    for field in ("version", "description", "owners", "sources", "analysis"):
        if field not in spec:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"missing required field: {field}",
            )

    path.write_text(raw, encoding="utf-8")
    summary = _parse(raw, path)
    return UseCaseRead(**summary.model_dump(), raw=raw, spec=spec)


@router.delete(
    "/{uc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a use case file",
)
async def delete_use_case(
    uc_id: str,
    settings: Settings = Depends(get_settings),
) -> None:
    _validate_id(uc_id)
    base = _uc_dir(settings)
    path = base / f"{uc_id}.yml"
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="use case not found")
    path.unlink()
