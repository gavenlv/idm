"""LiteLLM 路由: gpt-5 (主力) / deepseek (廉价) / qwen2.5:32b (本地兜底).

策略 (AGENT_INSTRUCTIONS §6):
- planner (任务拆解, 复杂推理)        -> gpt-5
- 默认 (description / lineage)       -> gpt-5
- 廉价 (PII 分类 / 重复模板)          -> deepseek-chat
- 本地兜底 (敏感数据 / 离线)          -> ollama qwen2.5:32b

API key 从 .env (OPENAI_API_KEY / DEEPSEEK_API_KEY) 读取; key 缺失的 tier 会被自动跳过。
所有 tier 全失败时, 抛 LLMUnavailableError (不再静默返回 mock).
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import litellm

from idm_api.config import Settings, get_settings

logger = logging.getLogger(__name__)

# 关闭 litellm 的 telemetry / debug
litellm.telemetry = False
litellm.suppress_debug_info = True


class LLMUnavailableError(RuntimeError):
    """所有 tier 都失败, 无法完成 LLM 调用."""


class LLMRouter:
    """按策略路由 LLM 调用, 失败时降级到下一档."""

    FALLBACK_ORDER = ["primary", "cheap", "local"]

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._configure()

    def _configure(self) -> None:
        s = self._settings
        # 把 key 塞到 litellm (也支持 env, 显式 set 更稳)
        if s.openai_api_key:
            litellm.openai_key = s.openai_api_key
        if s.openai_base_url:
            litellm.api_base = s.openai_base_url
        if s.deepseek_api_key:
            # litellm 同时认 DEEPSEEK_API_KEY env 与此属性
            litellm.deepseek_key = s.deepseek_api_key

    def _routes(self, profile: str) -> list[tuple[str, str]]:
        """返回 (tier, model) 列表, 按优先级. key 缺失的 tier 自动跳过."""
        s = self._settings
        primary = s.idm_llm_default_model
        cheap = s.idm_llm_cheap_model
        local = s.idm_llm_local_model
        pii = s.idm_llm_pii_model

        # 哪些 tier 有 key 支持 (没 key 的不调, 直接降级)
        def has_key_for(model: str) -> bool:
            m = model.lower()
            if m.startswith("gpt-") or "openai" in m:
                return bool(s.openai_api_key and s.openai_api_key != "sk-replace-me")
            if "deepseek" in m:
                return bool(s.deepseek_api_key)
            # ollama / qwen 本地不需要 key
            if m.startswith("ollama/") or "qwen" in m or "llama" in m:
                return True
            # 未知 provider 视为无 key
            return False

        if profile == "planner":
            tiers = [(primary, "primary"), (cheap, "cheap"), (local, "local")]
        elif profile == "pii":
            tiers = [(pii, "primary"), (cheap, "cheap"), (local, "local")]
        else:  # default
            tiers = [(primary, "primary"), (cheap, "cheap"), (local, "local")]

        # 只保留有 key 的 tier; 让 cheap/local 自然接手
        out: list[tuple[str, str]] = []
        for model, tier in tiers:
            if has_key_for(model):
                out.append((tier, model))
            else:
                logger.info("Skip tier=%s model=%s (no API key)", tier, model)
        return out

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        profile: str = "default",
        temperature: float = 0.2,
        max_tokens: int = 1024,
        response_format: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """调用 LLM, 返回 {content, model, tier, usage, prompt_hash}.

        失败时按 tier 列表降级. 全失败抛 LLMUnavailableError.
        """
        s = self._settings
        prompt_hash = self._hash_messages(messages)
        routes = self._routes(profile)
        if not routes:
            raise LLMUnavailableError(
                f"no LLM tier has API key configured (profile={profile}); "
                f"check OPENAI_API_KEY / DEEPSEEK_API_KEY in .env"
            )
        last_err: Exception | None = None

        for tier, model in routes:
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                if response_format:
                    kwargs["response_format"] = response_format
                # 保险: 调之前再 set 一次 (litellm 的 key 可能在并发下被改)
                self._set_provider_key(model)
                logger.info("LLM call: profile=%s tier=%s model=%s", profile, tier, model)
                resp = litellm.completion(**kwargs)
                content = (resp["choices"][0]["message"]["content"] or "").strip()
                usage = resp.get("usage", {}) or {}
                logger.info(
                    "LLM ok: model=%s tokens=%s/%s",
                    model,
                    usage.get("prompt_tokens", "?"),
                    usage.get("completion_tokens", "?"),
                )
                return {
                    "content": content,
                    "model": model,
                    "tier": tier,
                    "usage": dict(usage),
                    "prompt_hash": prompt_hash,
                }
            except Exception as e:  # noqa: BLE001
                logger.warning("LLM tier=%s model=%s failed: %s", tier, model, e)
                last_err = e
                continue

        raise LLMUnavailableError(
            f"all LLM tiers failed for profile={profile} routes={routes}; "
            f"last_err={last_err!r}"
        ) from last_err

    def _set_provider_key(self, model: str) -> None:
        s = self._settings
        m = model.lower()
        if m.startswith("gpt-") or "openai" in m:
            if s.openai_api_key:
                litellm.openai_key = s.openai_api_key
            if s.openai_base_url:
                litellm.api_base = s.openai_base_url
        elif "deepseek" in m:
            if s.deepseek_api_key:
                litellm.deepseek_key = s.deepseek_api_key
        # ollama / qwen 本地不需要 key

    def _hash_messages(self, messages: list[dict[str, str]]) -> str:
        blob = json.dumps(messages, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


_router: LLMRouter | None = None


def get_llm_router() -> LLMRouter:
    global _router
    if _router is None:
        _router = LLMRouter(get_settings())
    return _router


def reset_llm_router() -> None:
    """测试 / 重新加载 .env 时用: 丢弃单例让下次 get_llm_router 重建."""
    global _router
    _router = None
