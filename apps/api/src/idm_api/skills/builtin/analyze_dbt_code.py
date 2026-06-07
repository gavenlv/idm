"""analyze_dbt_code: 读 dbt repo 的 models/*.sql, 解析 ref()/source() 调用, 强化 KG.

M1 S1.7 — GitHub MCP + dbt 代码静态分析.

Inputs:
    owner, repo     GitHub 路径 (二选一: 或 local_path)
    ref             分支 / SHA (默认 HEAD)
    base_path       dbt 根目录 (默认 "models")
    project_name    service 名 (默认 <repo>)
    project_label   用于 FQN 拼装 (默认 'shop')
    local_path      本地目录 (用于无 token / 测试)

Outputs (SkillOutput.items):
    [{table_id, fqn, sql_path, refs, sources, status}, ...]

写入:
    TableAsset.description   <-  -- @description: ... 头部
    TableAsset.extra.code_refs    +=  [{path, sha, ref, source}]
    TableAsset.extra.dbt_refs     +=  ref() 调用
    TableAsset.extra.dbt_sources  +=  source() 调用
    TableAsset.extra.dbt_tags     +=  config(tags=[...])
    ColumnAsset.description       <-  -- 注释

不依赖 dbt-core.  stdlib + re + httpx.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

from idm_api.skills.mcp import get_github_mcp
from idm_api.skills.registry import SkillContext, SkillResult, SkillOutput, skill
from idm_kg.models.column_asset import ColumnAsset
from idm_kg.models.table_asset import TableAsset

logger = logging.getLogger(__name__)

# {{ ref('xxx') }}, {{ ref("xxx") }}, {{ ref("pkg", "xxx") }}
_REF_RE = re.compile(r"""\{\{\s*ref\(\s*['"]([^'"]+)['"](?:\s*,\s*['"]([^'"]+)['"])?\s*\)\s*\}\}""")
# {{ source('src_name', 'tbl_name') }}
_SOURCE_RE = re.compile(r"""\{\{\s*source\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)\s*\}\}""")
# -- @description: xxx (header comment)
_DESC_RE = re.compile(r"^--\s*@description\s*:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
# {{ config(materialized='table', tags=['pii']) }}
_CONFIG_RE = re.compile(r"""\{\{\s*config\(([^)]+)\)\s*\}\}""")

MAX_FILE_BYTES = 256 * 1024
HEAD_BYTES = 16 * 1024
MAX_FILES_PER_RUN = 200


def _parse_jinja(sql: str) -> dict[str, Any]:
    """从 dbt SQL 提取 jinja 引用 + 头部描述 + config tags."""
    refs: list[dict[str, str]] = []
    seen_refs: set[tuple[str, str]] = set()
    for m in _REF_RE.finditer(sql):
        a, b = m.group(1), m.group(2)
        key = (a, b) if b else ("", a)
        if key not in seen_refs:
            seen_refs.add(key)
            refs.append({"package": a, "name": b or a})

    sources: list[dict[str, str]] = []
    seen_src: set[tuple[str, str]] = set()
    for m in _SOURCE_RE.finditer(sql):
        key = (m.group(1), m.group(2))
        if key not in seen_src:
            seen_src.add(key)
            sources.append({"source_name": m.group(1), "name": m.group(2)})

    desc = None
    md = _DESC_RE.search(sql)
    if md:
        desc = md.group(1).strip()[:1024]

    tags: list[str] = []
    mc = _CONFIG_RE.search(sql)
    if mc:
        tags_match = re.search(r"tags\s*=\s*\[([^\]]+)\]", mc.group(1))
        if tags_match:
            tags = [t.strip().strip("'\"") for t in tags_match.group(1).split(",") if t.strip()]

    return {
        "refs": refs,
        "sources": sources,
        "description": desc,
        "tags": tags,
    }


def _extract_column_descriptions(sql: str) -> dict[str, str]:
    """从 SQL 中找带注释的列 alias."""
    out: dict[str, str] = {}
    pat = re.compile(
        r"""AS\s+["']?([a-zA-Z_][\w]*)["']?\s*,?\s*--\s*([^\n]+)""",
        re.IGNORECASE,
    )
    for m in pat.finditer(sql):
        col, desc = m.group(1), m.group(2).strip()
        if col not in out and desc:
            out[col] = desc[:512]
    return out


def _fqn_for(path: str, project_name: str, project_label: str, base_path: str = "models") -> str:
    """文件路径 -> FQN (与 parse_dbt_manifest 一致: dbt-<project>.<db>.<schema>.<name>)."""
    if path.startswith(base_path + "/"):
        rel = path[len(base_path) + 1 :]
    else:
        rel = path
    parts = rel.split("/")
    name = parts[-1].replace(".sql", "")
    schema = parts[0] if len(parts) > 1 else "main"
    return f"dbt-{project_name}.{project_label or 'shop'}.{schema}.{name}"


async def _enrich_one(
    ctx: SkillContext,
    fqn: str,
    path: str,
    content: str,
    sha: str,
    ref: str,
    items: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    """解析 + 增强 1 个文件对应的 TableAsset (若存在)."""
    parsed = _parse_jinja(content)
    col_descs = _extract_column_descriptions(content)
    if not parsed["refs"] and not parsed["sources"] and not parsed["description"]:
        summary["files_no_jinja"] += 1
        return

    summary["refs_found"] += len(parsed["refs"])
    summary["sources_found"] += len(parsed["sources"])
    if parsed["description"]:
        summary["descriptions_extracted"] += 1
    if parsed["tags"]:
        summary["tags_extracted"] += 1

    if ctx.db is None or ctx.dry_run:
        items.append(
            {
                "fqn": fqn,
                "sql_path": path,
                "refs": parsed["refs"],
                "sources": parsed["sources"],
                "description": parsed["description"],
                "tags": parsed["tags"],
                "dry_run": True,
            }
        )
        return

    from sqlalchemy import select

    result = await ctx.db.execute(select(TableAsset).where(TableAsset.fqn == fqn))
    ta = result.scalar_one_or_none()
    if ta is None:
        items.append(
            {
                "fqn": fqn,
                "sql_path": path,
                "refs": parsed["refs"],
                "sources": parsed["sources"],
                "status": "skipped_no_table_asset",
            }
        )
        return

    # 强化 description
    if parsed["description"] and not ta.description:
        ta.description = parsed["description"]
    # 强化 extra
    extra = dict(ta.extra or {})
    code_refs = list(extra.get("code_refs", []))
    code_refs.append({"path": path, "sha": sha, "ref": ref, "source": "github_mcp" if sha else "local"})
    existing_refs = {tuple(sorted(r.items())) for r in extra.get("dbt_refs", [])}
    for r in parsed["refs"]:
        if tuple(sorted(r.items())) not in existing_refs:
            extra.setdefault("dbt_refs", []).append(r)
    existing_src = {tuple(sorted(s.items())) for s in extra.get("dbt_sources", [])}
    for s in parsed["sources"]:
        if tuple(sorted(s.items())) not in existing_src:
            extra.setdefault("dbt_sources", []).append(s)
    if parsed["tags"]:
        extra["dbt_tags"] = list({*extra.get("dbt_tags", []), *parsed["tags"]})
    extra["code_refs"] = code_refs
    ta.extra = extra

    # 强化列描述
    if col_descs:
        cres = await ctx.db.execute(select(ColumnAsset).where(ColumnAsset.table_id == ta.id))
        for c in cres.scalars().all():
            if c.name in col_descs and not c.description:
                c.description = col_descs[c.name]
                summary["columns_described"] += 1

    await ctx.db.flush()
    summary["tables_enriched"] += 1
    items.append(
        {
            "table_id": str(ta.id),
            "fqn": fqn,
            "sql_path": path,
            "refs": parsed["refs"],
            "sources": parsed["sources"],
            "description": parsed["description"],
            "tags": parsed["tags"],
            "status": "enriched",
        }
    )
    ctx.log("dbt_code.enrich", fqn=fqn, refs=len(parsed["refs"]), sources=len(parsed["sources"]))


@skill(name="analyze_dbt_code", version=1, agent="lineage")
async def run(ctx: SkillContext, **inputs: Any) -> SkillResult:
    """扫描 dbt repo 的 models/ 全部 .sql, 把 ref/source 写入 KG.

    required: owner+repo  或  local_path
    optional: ref, base_path, project_name, project_label
    """
    owner = (inputs.get("owner") or "").strip()
    repo = (inputs.get("repo") or "").strip()
    ref = (inputs.get("ref") or "HEAD").strip() or "HEAD"
    base_path = (inputs.get("base_path") or "models").strip().strip("/")
    project_name = (inputs.get("project_name") or repo or "local").strip()
    project_label = (inputs.get("project_label") or "shop").strip()
    local_path = (inputs.get("local_path") or "").strip()

    items: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "owner": owner,
        "repo": repo,
        "ref": ref,
        "base_path": base_path,
        "project": project_name,
        "sql_files_total": 0,
        "files_scanned": 0,
        "files_skipped_binary": 0,
        "files_no_jinja": 0,
        "refs_found": 0,
        "sources_found": 0,
        "descriptions_extracted": 0,
        "tags_extracted": 0,
        "tables_enriched": 0,
        "columns_described": 0,
        "errors": [],
    }

    if local_path:
        return await _run_local(
            ctx, local_path, project_name, project_label, base_path, items, summary
        )

    if not owner or not repo:
        return SkillResult(
            ok=False,
            output=SkillOutput(
                items=items,
                summary={**summary, "errors": ["owner/repo 或 local_path 二选一必填"]},
            ),
            error="missing owner/repo and local_path",
        )

    gh = get_github_mcp()
    if not gh.has_token:
        return SkillResult(
            ok=False,
            output=SkillOutput(
                items=items,
                summary={
                    **summary,
                    "errors": ["MCP_GITHUB_TOKEN 未配置, 无法访问 GitHub API (或使用 local_path)"],
                },
            ),
            error="no github token",
        )

    # 1) list tree
    try:
        tree = await gh.list_tree(owner, repo, ref=ref, recursive=True)
    except Exception as e:  # noqa: BLE001
        return SkillResult(
            ok=False,
            output=SkillOutput(
                items=items, summary={**summary, "errors": [f"list_tree: {e}"]}
            ),
            error=f"list_tree failed: {e}",
        )

    # 2) 过滤 .sql 文件且在 base_path 下
    sql_nodes = [
        t
        for t in tree
        if t.get("type") == "blob"
        and t.get("path", "").lower().endswith(".sql")
        and t.get("path", "").startswith(base_path + "/")
    ]
    summary["sql_files_total"] = len(sql_nodes)

    # 3) 对每个文件: get_file + parse
    for node in sql_nodes[:MAX_FILES_PER_RUN]:
        path = node["path"]
        try:
            content = await gh.get_file(owner, repo, ref, path)
        except Exception as e:  # noqa: BLE001
            summary["errors"].append(f"get_file {path}: {str(e)[:120]}")
            continue
        if len(content) > MAX_FILE_BYTES:
            summary["files_skipped_binary"] += 1
            content = content[:HEAD_BYTES]
        summary["files_scanned"] += 1

        fqn = _fqn_for(path, project_name, project_label, base_path)
        await _enrich_one(
            ctx,
            fqn=fqn,
            path=path,
            content=content,
            sha=node.get("sha", "")[:12],
            ref=ref,
            items=items,
            summary=summary,
        )

    if ctx.db is not None and not ctx.dry_run:
        try:
            await ctx.db.commit()
        except Exception as e:  # noqa: BLE001
            await ctx.db.rollback()
            return SkillResult(
                ok=False,
                output=SkillOutput(items=items, summary={**summary, "errors": [f"commit: {e}"]}),
                error=f"commit failed: {e}",
            )

    summary["ok"] = True
    summary["mode"] = "github"
    return SkillResult(ok=True, output=SkillOutput(items=items, summary=summary))


async def _run_local(
    ctx: SkillContext,
    local_path: str,
    project_name: str,
    project_label: str,
    base_path: str,
    items: list[dict[str, Any]],
    summary: dict[str, Any],
) -> SkillResult:
    if not os.path.isdir(local_path):
        return SkillResult(
            ok=False,
            output=SkillOutput(
                items=items, summary={**summary, "errors": [f"local_path not dir: {local_path}"]}
            ),
            error=f"local_path not a dir: {local_path}",
        )

    sql_files: list[str] = []
    for root, _dirs, files in os.walk(local_path):
        for f in files:
            if f.lower().endswith(".sql"):
                sql_files.append(os.path.join(root, f))
    summary["sql_files_total"] = len(sql_files)

    for fp in sql_files[:MAX_FILES_PER_RUN]:
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except Exception as e:  # noqa: BLE001
            summary["errors"].append(f"read {fp}: {e}")
            continue
        if len(content) > MAX_FILE_BYTES:
            summary["files_skipped_binary"] += 1
            content = content[:HEAD_BYTES]
        summary["files_scanned"] += 1

        rel_path = os.path.relpath(fp, local_path).replace("\\", "/")
        full_path = f"{base_path}/{rel_path}"
        fqn = _fqn_for(rel_path, project_name, project_label, base_path=base_path)

        await _enrich_one(
            ctx,
            fqn=fqn,
            path=full_path,
            content=content,
            sha="",
            ref="local",
            items=items,
            summary=summary,
        )

    if ctx.db is not None and not ctx.dry_run:
        try:
            await ctx.db.commit()
        except Exception as e:  # noqa: BLE001
            await ctx.db.rollback()
            return SkillResult(
                ok=False,
                output=SkillOutput(items=items, summary={**summary, "errors": [f"commit: {e}"]}),
                error=f"commit failed: {e}",
            )

    summary["ok"] = True
    summary["mode"] = "local"
    return SkillResult(ok=True, output=SkillOutput(items=items, summary=summary))
