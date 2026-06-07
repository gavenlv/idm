"""Skills package: AGENT_INSTRUCTIONS §6.

包结构:
- mcp.py          外部数据源 MCP 客户端 (clickhouse/github/gcs/...)
- llm.py          LiteLLM 路由 (gpt-5 / deepseek / qwen)
- registry.py     Skill 注册表 (装饰器 + 名称查找)
- runner.py       Skill 执行引擎 (trace + 重试 + 错误处理)
- builtin/        内置 Skills (discover_assets, infer_description, ...)
"""
from idm_api.skills.registry import Skill, SkillContext, SkillResult, skill

__all__ = ["Skill", "SkillContext", "SkillResult", "skill"]
