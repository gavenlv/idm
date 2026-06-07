"""MCP client: 外部数据源 Sidecar (clickhouse/github/gcs/...).

M1 S1.2 简化:
- ClickHouse 用 clickhouse-connect 直连 (语义等价 MCP clickhouse server)。
- GitHub 留 placeholder, M1 S1.3 用 httpx + token 实现。
- 后续切换到 stdio MCP server 时, 只需替换 Client, 接口不变。

设计原则 (AGENT_INSTRUCTIONS §5):
- Sidecar 模式: 长连接 + 健康检查 + 失败重连。
- 工具命名空间化: mcp:clickhouse:list_tables, mcp:github:list_files。
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import clickhouse_connect
from clickhouse_connect.driver.client import Client as CHClient

from idm_api.config import Settings, get_settings

logger = logging.getLogger(__name__)


class ClickHouseMCP:
    """ClickHouse 数据源客户端 (MCP 等价实现)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: CHClient | None = None

    def connect(self) -> None:
        if self._client is not None:
            return
        logger.info(
            "CH MCP connect: %s:%s/%s",
            self._settings.clickhouse_host,
            self._settings.clickhouse_port,
            self._settings.clickhouse_database,
        )
        self._client = clickhouse_connect.get_client(
            host=self._settings.clickhouse_host,
            port=self._settings.clickhouse_port,
            database=self._settings.clickhouse_database,
            username=self._settings.clickhouse_user,
            password=self._settings.clickhouse_password,
            secure=self._settings.clickhouse_secure,
            verify=self._settings.clickhouse_verify,
            connect_timeout=10,
            send_receive_timeout=30,
        )
        self._client.command("SELECT 1")

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                logger.warning("CH MCP close error", exc_info=True)
            self._client = None

    @property
    def client(self) -> CHClient:
        if self._client is None:
            raise RuntimeError("CH MCP not connected; call .connect() first")
        return self._client

    # === Tools ===
    def list_databases(self) -> list[str]:
        rows = self.client.command("SHOW DATABASES")
        if isinstance(rows, str):
            return [r for r in rows.splitlines() if r.strip()]
        return [str(r) for r in rows]

    def list_tables(self, database: str | None = None) -> list[str]:
        db = database or self._settings.clickhouse_database
        rows = self.client.command(f"SHOW TABLES FROM `{db}`")
        if isinstance(rows, str):
            return [r for r in rows.splitlines() if r.strip()]
        return [str(r) for r in rows]

    def describe_table(self, database: str, table: str) -> list[dict[str, Any]]:
        db = database or self._settings.clickhouse_database
        sql = f"DESCRIBE TABLE `{db}`.`{table}`"
        result = self.client.query(sql)
        cols = result.column_names
        return [{c: v for c, v in zip(cols, row, strict=False)} for row in result.result_rows]

    def sample_rows(self, database: str, table: str, limit: int = 5) -> list[dict[str, Any]]:
        db = database or self._settings.clickhouse_database
        sql = f"SELECT * FROM `{db}`.`{table}` LIMIT {int(limit)}"
        result = self.client.query(sql)
        cols = result.column_names
        return [{c: v for c, v in zip(cols, row, strict=False)} for row in result.result_rows]

    def get_table_stats(self, database: str, table: str) -> dict[str, Any]:
        """轻量统计: parts / row_count / size_bytes / last_modified."""
        db = database or self._settings.clickhouse_database
        sql = """
            SELECT
                count() AS parts,
                sum(rows) AS total_rows,
                sum(bytes_on_disk) AS total_bytes,
                max(modification_time) AS last_modified
            FROM system.parts
            WHERE database = %(db)s AND table = %(tbl)s AND active
        """
        result = self.client.query(sql, parameters={"db": db, "tbl": table})
        row = result.result_rows[0] if result.result_rows else (0, 0, 0, None)
        return {
            "parts": int(row[0] or 0),
            "row_count": int(row[1] or 0),
            "size_bytes": int(row[2] or 0),
            "last_modified": row[3],
        }

    def run_query(self, sql: str) -> list[dict[str, Any]]:
        result = self.client.query(sql)
        cols = result.column_names
        return [{c: v for c, v in zip(cols, row, strict=False)} for row in result.result_rows]

    def health(self) -> dict[str, str]:
        try:
            assert self._client is not None
            self._client.command("SELECT 1")
            return {"status": "ok"}
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "error": str(e)[:200]}


# === GitHub MCP (REST API; S1.7) ===
import asyncio
import base64
import httpx


class GitHubMCP:
    """GitHub MCP 客户端 (REST API 实现, 等价 MCP github server).

    工具集:
    - list_files(owner, repo, ref, path): 列出目录文件
    - get_file(owner, repo, ref, path): 读文件 (base64 -> text)
    - search_code(owner, repo, query): 代码搜索 (REST search)
    - list_tree(owner, repo, ref): 整棵文件树 (递归)
    - get_commit_log(owner, repo, ref, path): 提交历史 (可推断 owner/active)
    - health(): 校验 token + 限流
    """

    API = "https://api.github.com"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._token = settings.mcp_github_token.strip()
        self._headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            self._headers["Authorization"] = f"Bearer {self._token}"
        self._client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        if self._client is not None:
            return
        self._client = httpx.AsyncClient(headers=self._headers, timeout=20.0)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def cli(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("GitHubMCP not connected")
        return self._client

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    async def health(self) -> dict[str, Any]:
        if not self.has_token:
            return {"status": "no_token", "note": "GITHUB_TOKEN unset; 限速 60/h 且只能读 public"}
        try:
            r = await self.cli.get(f"{self.API}/user")
            if r.status_code == 200:
                u = r.json()
                return {
                    "status": "ok",
                    "login": u.get("login"),
                    "rate_remaining": r.headers.get("x-ratelimit-remaining"),
                }
            return {"status": "error", "code": r.status_code, "body": r.text[:200]}
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "error": str(e)[:200]}

    async def list_files(
        self, owner: str, repo: str, ref: str = "HEAD", path: str = ""
    ) -> list[dict[str, Any]]:
        url = f"{self.API}/repos/{owner}/{repo}/contents/{path}"
        r = await self.cli.get(url, params={"ref": ref})
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return [
                {
                    "name": item["name"],
                    "path": item["path"],
                    "type": item["type"],
                    "size": item.get("size", 0),
                    "sha": item.get("sha", "")[:12],
                }
                for item in data
            ]
        return []

    async def get_file(self, owner: str, repo: str, ref: str, path: str) -> str:
        url = f"{self.API}/repos/{owner}/{repo}/contents/{path}"
        r = await self.cli.get(url, params={"ref": ref})
        r.raise_for_status()
        data = r.json()
        if data.get("encoding") == "base64":
            content = data.get("content", "")
            # GitHub returns content with embedded newlines
            content = content.replace("\n", "")
            return base64.b64decode(content).decode("utf-8", errors="replace")
        return data.get("content", "")

    async def get_file_async(self, *args: Any, **kwargs: Any) -> str:
        return await self.get_file(*args, **kwargs)

    async def search_code(
        self, owner: str, repo: str, query: str, limit: int = 30
    ) -> list[dict[str, Any]]:
        """注意: code search 需要 user/repo scope 单独授权, 这里 fallback 到 list+grep."""
        # 简单 fallback: 在 repo 根用 git tree 拿所有文本文件路径, 再 in-memory grep
        try:
            tree = await self.list_tree(owner, repo, "HEAD")
            q = query.lower()
            hits: list[dict[str, Any]] = []
            for node in tree:
                if node.get("type") != "blob":
                    continue
                p = node.get("path", "")
                # 只在路径上做粗匹配, 内容匹配交给调用方
                if q in p.lower():
                    hits.append({"path": p, "sha": node.get("sha", "")[:12]})
                    if len(hits) >= limit:
                        break
            return hits
        except Exception:  # noqa: BLE001
            return []

    async def list_tree(
        self, owner: str, repo: str, ref: str = "HEAD", recursive: bool = True
    ) -> list[dict[str, Any]]:
        url = f"{self.API}/repos/{owner}/{repo}/git/trees/{ref}"
        r = await self.cli.get(url, params={"recursive": "true" if recursive else "false"})
        r.raise_for_status()
        data = r.json()
        return data.get("tree", []) or []

    async def get_commit_log(
        self, owner: str, repo: str, ref: str = "HEAD", path: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
        url = f"{self.API}/repos/{owner}/{repo}/commits"
        params: dict[str, Any] = {"per_page": min(limit, 100)}
        if path:
            params["path"] = path
        if ref and ref != "HEAD":
            params["sha"] = ref
        r = await self.cli.get(url, params=params)
        r.raise_for_status()
        data = r.json()
        out: list[dict[str, Any]] = []
        for c in data:
            out.append(
                {
                    "sha": c.get("sha", "")[:12],
                    "message": (c.get("commit", {}).get("message") or "").splitlines()[0][:200],
                    "author": (c.get("commit", {}).get("author") or {}).get("name"),
                    "date": (c.get("commit", {}).get("author") or {}).get("date"),
                }
            )
        return out


# === Sidecar registry (process-wide singletons) ===
_mcp_clients: dict[str, Any] = {}


def get_clickhouse_mcp() -> ClickHouseMCP:
    if "clickhouse" not in _mcp_clients:
        cli = ClickHouseMCP(get_settings())
        cli.connect()
        _mcp_clients["clickhouse"] = cli
    return _mcp_clients["clickhouse"]


def get_github_mcp() -> GitHubMCP:
    if "github" not in _mcp_clients:
        cli = GitHubMCP(get_settings())
        _mcp_clients["github"] = cli
    return _mcp_clients["github"]


async def init_mcp_async() -> None:
    """在 FastAPI startup 中调用, 把 async 客户端连上。"""
    gh = get_github_mcp()
    await gh.connect()
    # CH 是同步, 也保证连上
    get_clickhouse_mcp()
    # Superset (async), 仅 connect, login 懒加载
    ss = get_superset_mcp()
    await ss.connect()


@asynccontextmanager
async def mcp_lifespan() -> AsyncIterator[None]:
    """FastAPI lifespan: 启动时连接, 关闭时断开。"""
    try:
        await init_mcp_async()
        yield
    finally:
        for cli in _mcp_clients.values():
            try:
                if isinstance(cli, (GitHubMCP, SupersetMCP)):
                    await cli.close()
                else:
                    cli.close()
            except Exception:  # noqa: BLE001
                pass
        _mcp_clients.clear()


# === Superset MCP (REST API; S1.8) ===
class SupersetMCP:
    """Superset MCP 客户端 (REST API 实现, 等价 MCP superset server).

    工具集:
    - login(): 拿 access_token
    - list_dashboards(limit): 列出 dashboards
    - get_dashboard(id): 单个 dashboard 详情 (含 position, json_metadata)
    - list_datasets(limit): 列出 datasets
    - get_dataset(id): dataset 详情 (含 database / schema / table_name)
    - list_charts(limit): 列出 charts
    - get_chart(id): chart 详情 (含 viz_type, params)
    - export_dashboard(id): 导出 zip
    - health(): 探活

    资产映射 (M1 S1.8):
    - dashboard -> dashboard 资产 (asset_type=superset_dashboard)
    - chart     -> chart 资产 (asset_type=superset_chart)
    - dataset   -> 指向 ClickHouse 表 (asset_type=superset_dataset) 并建 lineage
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base = settings.superset_url.rstrip("/")
        self._creds = (settings.superset_username, settings.superset_password)
        self._client: httpx.AsyncClient | None = None
        self._access_token: str | None = None
        self._csrf_token: str | None = None

    async def connect(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(verify=self._settings.superset_verify_ssl, timeout=20.0)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._access_token = None
        self._csrf_token = None

    @property
    def cli(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("SupersetMCP not connected")
        return self._client

    def _auth_headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self._access_token:
            h["Authorization"] = f"Bearer {self._access_token}"
        if self._csrf_token:
            h["X-CSRFToken"] = self._csrf_token
        h["Referer"] = self._base
        return h

    async def login(self) -> bool:
        try:
            # 1) login: 拿 session cookie
            r = await self.cli.post(
                f"{self._base}/api/v1/security/login",
                json={"username": self._creds[0], "password": self._creds[1], "provider": "db"},
            )
            if r.status_code != 200:
                return False
            self._access_token = r.json().get("access_token")
            # 2) CSRF
            r2 = await self.cli.get(f"{self._base}/api/v1/security/csrf_token/", headers=self._auth_headers())
            if r2.status_code == 200:
                self._csrf_token = r2.json().get("result")
            return bool(self._access_token)
        except Exception:  # noqa: BLE001
            return False

    async def health(self) -> dict[str, Any]:
        try:
            ok = await self.login()
            return {"status": "ok" if ok else "auth_failed", "url": self._base}
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "error": str(e)[:200]}

    async def list_dashboards(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self._access_token:
            await self.login()
        r = await self.cli.get(
            f"{self._base}/api/v1/dashboard/",
            params={"page_size": min(limit, 200)},
            headers=self._auth_headers(),
        )
        r.raise_for_status()
        return r.json().get("result", [])

    async def get_dashboard(self, dashboard_id: int) -> dict[str, Any]:
        if not self._access_token:
            await self.login()
        r = await self.cli.get(
            f"{self._base}/api/v1/dashboard/{dashboard_id}",
            headers=self._auth_headers(),
        )
        r.raise_for_status()
        return r.json().get("result", {})

    async def list_charts(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self._access_token:
            await self.login()
        r = await self.cli.get(
            f"{self._base}/api/v1/chart/",
            params={"page_size": min(limit, 200)},
            headers=self._auth_headers(),
        )
        r.raise_for_status()
        return r.json().get("result", [])

    async def get_chart(self, chart_id: int) -> dict[str, Any]:
        if not self._access_token:
            await self.login()
        r = await self.cli.get(
            f"{self._base}/api/v1/chart/{chart_id}",
            headers=self._auth_headers(),
        )
        r.raise_for_status()
        return r.json().get("result", {})

    async def list_datasets(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self._access_token:
            await self.login()
        r = await self.cli.get(
            f"{self._base}/api/v1/dataset/",
            params={"page_size": min(limit, 200)},
            headers=self._auth_headers(),
        )
        r.raise_for_status()
        return r.json().get("result", [])

    async def get_dataset(self, dataset_id: int) -> dict[str, Any]:
        if not self._access_token:
            await self.login()
        r = await self.cli.get(
            f"{self._base}/api/v1/dataset/{dataset_id}",
            headers=self._auth_headers(),
        )
        r.raise_for_status()
        return r.json().get("result", {})


def get_superset_mcp() -> SupersetMCP:
    if "superset" not in _mcp_clients:
        cli = SupersetMCP(get_settings())
        _mcp_clients["superset"] = cli
    return _mcp_clients["superset"]
