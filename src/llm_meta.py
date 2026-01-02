from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def extract_finish_reason_and_usage(resp: Any) -> Tuple[Optional[str], Dict[str, Any]]:
    """
    兼容 langchain-openai / OpenAI兼容返回结构：
    - finish_reason 常见为 "stop" / "length" / "content_filter"
    - token_usage 常见为 {"prompt_tokens":..., "completion_tokens":..., "total_tokens":...}
    """
    meta = getattr(resp, "response_metadata", None) or {}
    if not isinstance(meta, dict):
        meta = {}

    finish_reason = meta.get("finish_reason", None)
    if finish_reason is None:
        # 有些实现会放在 nested 的 "generation_info" 或者 "choices"
        gen = meta.get("generation_info")
        if isinstance(gen, dict) and gen.get("finish_reason"):
            finish_reason = gen.get("finish_reason")

    usage = meta.get("token_usage", None) or meta.get("usage", None) or {}
    if not isinstance(usage, dict):
        usage = {}

    return (str(finish_reason) if finish_reason is not None else None, usage)


