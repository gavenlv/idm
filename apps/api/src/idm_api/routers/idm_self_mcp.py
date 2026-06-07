"""/api/v1/mcp/idm-self: HTTP 入口暴露 idm-self MCP tools (SSE/JSON-RPC 风格).

M5: 外部 Agent (Claude/Cursor) 通过 HTTP 调用 IDM 能力.
注意:
- 仅暴露只读 + 受 audit 的能力
- approve_suggestion 写 audit_log
- 后续可加 OAuth/API key 鉴权

Endpoint:
  GET  /api/v1/mcp/idm-self/tools       列工具
  POST /api/v1/mcp/idm-self/call        调工具 (body: {name, arguments})
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from idm_api.skills.idm_self_mcp import TOOLS, call_tool

router = APIRouter()


class CallRequest(BaseModel):
    name: str = Field(..., min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


@router.get("/tools", summary="List idm-self MCP tools")
async def list_tools() -> dict[str, Any]:
    return {"name": "idm-self", "tools": TOOLS, "count": len(TOOLS)}


@router.post("/call", summary="Call an idm-self MCP tool")
async def call(req: CallRequest) -> dict[str, Any]:
    res = await call_tool(req.name, req.arguments)
    if not res.get("ok") and "error" in res:
        # 业务错误 → 200, 由调用方处理
        return res
    return res
