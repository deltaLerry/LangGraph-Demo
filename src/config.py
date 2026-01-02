from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse
from dataclasses import dataclass
from dataclasses import field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class LLMConfig:
    """
    统一用 OpenAI 兼容参数接 DeepSeek / 通义千问等。
    - base_url: 例如 DeepSeek / DashScope 的 OpenAI 兼容地址
    - api_key: 对应平台的 key
    - model:    模型名
    """

    base_url: str
    api_key: str
    model: str
    temperature: float = 0.7
    # OpenAI 兼容的常用参数（用“显式字段”承载，避免落到 model_kwargs 触发 warning）
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    timeout: Optional[float] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None

    # 其它 OpenAI 兼容的额外参数（尽量少用；避免把 max_tokens/top_p/timeout 再塞回这里）
    model_kwargs: Dict[str, Any] = field(default_factory=dict)


def load_llm_config_from_env() -> LLMConfig | None:
    base_url = os.getenv("LLM_BASE_URL", "").strip()
    api_key = os.getenv("LLM_API_KEY", "").strip()
    model = os.getenv("LLM_MODEL", "").strip()
    if not (base_url and api_key and model):
        return None

    # 容错：如果用户只填了域名（没有 /v1），自动补齐为 OpenAI 兼容的 /v1
    # 例如：https://api.deepseek.com  -> https://api.deepseek.com/v1
    try:
        parsed = urlparse(base_url)
        if parsed.scheme and parsed.netloc and parsed.path in ("", "/"):
            base_url = urlunparse((parsed.scheme, parsed.netloc, "/v1", "", "", ""))
    except Exception:
        pass

    try:
        temperature = float(os.getenv("LLM_TEMPERATURE", "0.7"))
    except ValueError:
        temperature = 0.7

    def _env_int(name: str) -> Optional[int]:
        v = os.getenv(name, "").strip()
        if not v:
            return None
        try:
            return int(v)
        except ValueError:
            return None

    def _env_float(name: str) -> Optional[float]:
        v = os.getenv(name, "").strip()
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            return None

    max_tokens = _env_int("LLM_MAX_TOKENS")

    top_p = _env_float("LLM_TOP_P")

    presence_penalty = _env_float("LLM_PRESENCE_PENALTY")

    frequency_penalty = _env_float("LLM_FREQUENCY_PENALTY")

    timeout = _env_float("LLM_TIMEOUT")
    return LLMConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
        presence_penalty=presence_penalty,
        frequency_penalty=frequency_penalty,
        timeout=timeout,
    )


