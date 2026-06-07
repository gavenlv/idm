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


# === Sidecar registry (process-wide singletons) ===
_mcp_clients: dict[str, ClickHouseMCP] = {}


def get_clickhouse_mcp() -> ClickHouseMCP:
    if "clickhouse" not in _mcp_clients:
        cli = ClickHouseMCP(get_settings())
        cli.connect()
        _mcp_clients["clickhouse"] = cli
    return _mcp_clients["clickhouse"]


@asynccontextmanager
async def mcp_lifespan() -> AsyncIterator[None]:
    """FastAPI lifespan: 启动时连接, 关闭时断开。"""
    try:
        get_clickhouse_mcp()
        yield
    finally:
        for cli in _mcp_clients.values():
            try:
                cli.close()
            except Exception:  # noqa: BLE001
                pass
        _mcp_clients.clear()
