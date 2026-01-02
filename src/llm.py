from __future__ import annotations

from typing import Optional

from typing import TYPE_CHECKING

from config import LLMConfig

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI  # pragma: no cover


def build_chat_llm(cfg: LLMConfig) -> ChatOpenAI:
    """
    构建 OpenAI 兼容 Chat 模型（DeepSeek / 通义千问等）。
    注意：这里做了“延迟导入”，避免未安装 langchain-openai 时直接报错（模板模式仍可运行）。
    """
    try:
        from langchain_openai import ChatOpenAI  # type: ignore
    except ModuleNotFoundError as e:  # pragma: no cover
        raise ModuleNotFoundError(
            "未安装可选依赖 langchain-openai。若要启用LLM模式，请先执行：pip install -r requirements.txt"
        ) from e

    base_kwargs = {
        "base_url": cfg.base_url,
        "api_key": cfg.api_key,
        "model": cfg.model,
        "temperature": cfg.temperature,
    }

    # 显式参数（避免塞进 model_kwargs 触发 warning）
    explicit = {}
    if cfg.max_tokens is not None:
        explicit["max_tokens"] = cfg.max_tokens
    if cfg.top_p is not None:
        explicit["top_p"] = cfg.top_p
    if cfg.presence_penalty is not None:
        explicit["presence_penalty"] = cfg.presence_penalty
    if cfg.frequency_penalty is not None:
        explicit["frequency_penalty"] = cfg.frequency_penalty
    if cfg.timeout is not None:
        explicit["timeout"] = cfg.timeout

    # 其它参数仍允许通过 model_kwargs 传递（不要放 max_tokens/top_p/timeout）
    model_kwargs = dict(cfg.model_kwargs or {})

    def _try_build(extra_kwargs):
        kwargs = dict(base_kwargs)
        kwargs.update(extra_kwargs)
        if model_kwargs:
            kwargs["model_kwargs"] = model_kwargs
        return ChatOpenAI(**kwargs)

    # 尝试 1：直接用 timeout
    try:
        return _try_build(explicit)
    except TypeError:
        pass

    # 尝试 2：有些版本/底层用 request_timeout
    if "timeout" in explicit:
        explicit2 = dict(explicit)
        explicit2["request_timeout"] = explicit2.pop("timeout")
        try:
            return _try_build(explicit2)
        except TypeError:
            pass

    # 兜底：不再把这些字段塞回 model_kwargs，避免 warning；直接不带这些参数初始化
    return _try_build({})


def try_get_chat_llm(cfg: Optional[LLMConfig]) -> Optional[ChatOpenAI]:
    if cfg is None:
        return None
    try:
        return build_chat_llm(cfg)
    except Exception:
        # 不阻塞模板模式运行
        return None


