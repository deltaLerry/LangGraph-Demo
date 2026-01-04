from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from debug_log import truncate_text
from json_utils import extract_first_json_object_with_error
from llm_call import invoke_with_retry
from llm_meta import extract_finish_reason_and_usage


def _safe_content(resp: Any) -> str:
    return (getattr(resp, "content", "") or "").strip()


def _log_llm_response(logger: Any, *, node: str, chapter_index: int, content: str, finish_reason: str, token_usage: Dict[str, Any]):
    if not logger:
        return
    try:
        logger.event(
            "llm_response",
            node=node,
            chapter_index=chapter_index,
            content=truncate_text(content, max_chars=getattr(logger, "max_chars", 20000)),
            finish_reason=finish_reason,
            token_usage=token_usage,
        )
    except Exception:
        pass

def bind_json_response_format(llm: Any) -> Any:
    """
    为支持 OpenAI 兼容 response_format 的模型启用 JSON Output：
    - DeepSeek 文档要求：response_format={'type':'json_object'} 且 prompt 中包含 'json'
    - LangChain ChatOpenAI 支持通过 .bind(...) 传参（若不支持则原样返回）
    """
    try:
        return llm.bind(response_format={"type": "json_object"})
    except Exception:
        return llm


def invoke_json_with_repair(
    *,
    llm: Any,
    messages: List[Any],
    schema_text: str,
    node: str,
    chapter_index: int = 0,
    logger: Any = None,
    max_attempts: int = 3,
    base_sleep_s: float = 1.0,
    validate: Optional[Callable[[Dict[str, Any]], str]] = None,
    max_fix_chars: int = 12000,
) -> Tuple[Dict[str, Any], str, str, Dict[str, Any]]:
    """
    调用 LLM 并解析第一个 JSON object：
    - 第一次：正常调用 -> 解析
    - 如果解析失败（或 validate 不通过）：第二次调用“JSON 修复器”，把错误原因+原始输出回传给 LLM，只修格式/缺字段

    返回：(obj, raw_text, finish_reason, token_usage)
    - obj 解析成功则为 dict，否则为空 dict
    """
    llm0 = bind_json_response_format(llm)
    # DeepSeek 要求 prompt 中含有 json 字样且给出 schema 示例：
    # 这里在“第一次调用”也注入 schema_text，确保即使 agent 忘记写 schema 也能稳定输出 json。
    try:
        from langchain_core.messages import SystemMessage
        prefix = SystemMessage(
            content=(
                "json 输出：你必须返回合法的 json 对象。\n"
                "你必须严格遵循以下 schema（只输出一个 json object，不要解释，不要 markdown）：\n"
                f"{schema_text}\n"
            )
        )
        messages0 = [prefix, *messages]
    except Exception:
        messages0 = messages

    resp = invoke_with_retry(
        llm0,
        messages0,
        max_attempts=max(1, int(max_attempts)),
        base_sleep_s=float(base_sleep_s),
        logger=logger,
        node=node,
        chapter_index=int(chapter_index or 0),
    )
    raw = _safe_content(resp)
    finish_reason, token_usage = extract_finish_reason_and_usage(resp)
    _log_llm_response(
        logger,
        node=node,
        chapter_index=int(chapter_index or 0),
        content=raw,
        finish_reason=str(finish_reason or ""),
        token_usage=token_usage or {},
    )
    obj, err = extract_first_json_object_with_error(raw)
    if obj and validate:
        verr = (validate(obj) or "").strip()
        if verr:
            err = f"validation_failed: {verr}"
            obj = {}

    if obj:
        return obj, raw, str(finish_reason or ""), token_usage or {}

    # 第二次：把“解析/校验错误”回传给 LLM，要求只输出 JSON（并继续启用 response_format）
    try:
        from langchain_core.messages import SystemMessage, HumanMessage
    except Exception:
        return {}, raw, str(finish_reason or ""), token_usage or {}

    fix_system = SystemMessage(
        content=(
            "你是 JSON 修复器。你只负责把给定输出修复为一个严格 json 对象。\n"
            "必须且仅输出 json（不要解释、不要 markdown、不要代码块标记```）。\n"
            "注意：不要新增无关内容；仅做格式修复、补齐缺失字段、删除多余文字。\n"
            "目标 schema：\n"
            f"{schema_text}\n"
        )
    )
    fix_human = HumanMessage(
        content=(
            "解析/校验失败原因：\n"
            f"{err}\n\n"
            "原始输出（需要修复为严格 JSON）：\n"
            f"{truncate_text(raw, max_chars=max_fix_chars)}\n\n"
            "请输出修复后的 JSON："
        )
    )
    resp2 = invoke_with_retry(
        llm0,
        [fix_system, fix_human],
        max_attempts=max(1, int(max_attempts)),
        base_sleep_s=float(base_sleep_s),
        logger=logger,
        node=f"{node}_fix_json",
        chapter_index=int(chapter_index or 0),
    )
    raw2 = _safe_content(resp2)
    fr2, usage2 = extract_finish_reason_and_usage(resp2)
    _log_llm_response(
        logger,
        node=f"{node}_fix_json",
        chapter_index=int(chapter_index or 0),
        content=raw2,
        finish_reason=str(fr2 or ""),
        token_usage=usage2 or {},
    )
    obj2, err2 = extract_first_json_object_with_error(raw2)
    if obj2 and validate:
        verr2 = (validate(obj2) or "").strip()
        if verr2:
            obj2 = {}
    return obj2 if obj2 else {}, raw2, str(fr2 or ""), usage2 or {}


def repair_json_only(
    *,
    llm: Any,
    bad_text: str,
    err: str,
    schema_text: str,
    node: str,
    chapter_index: int = 0,
    logger: Any = None,
    max_attempts: int = 3,
    base_sleep_s: float = 1.0,
    validate: Optional[Callable[[Dict[str, Any]], str]] = None,
    max_fix_chars: int = 12000,
) -> Dict[str, Any]:
    """
    仅执行“JSON 修复器”一步：
    - 适用于：你已经有一次原始输出 bad_text，但解析失败；希望下一次把错误原因回传给 LLM 做针对性修复。
    返回解析后的 dict（失败则空 dict）。
    """
    try:
        from langchain_core.messages import SystemMessage, HumanMessage
    except Exception:
        return {}

    llm0 = bind_json_response_format(llm)

    fix_system = SystemMessage(
        content=(
            "你是 JSON 修复器。你只负责把给定输出修复为一个严格 json 对象。\n"
            "必须且仅输出 json（不要解释、不要 markdown、不要代码块标记```）。\n"
            "注意：不要新增无关内容；仅做格式修复、补齐缺失字段、删除多余文字。\n"
            "目标 schema：\n"
            f"{schema_text}\n"
        )
    )
    fix_human = HumanMessage(
        content=(
            "解析/校验失败原因：\n"
            f"{err}\n\n"
            "原始输出（需要修复为严格 JSON）：\n"
            f"{truncate_text(bad_text, max_chars=max_fix_chars)}\n\n"
            "请输出修复后的 JSON："
        )
    )
    resp = invoke_with_retry(
        llm0,
        [fix_system, fix_human],
        max_attempts=max(1, int(max_attempts)),
        base_sleep_s=float(base_sleep_s),
        logger=logger,
        node=node,
        chapter_index=int(chapter_index or 0),
    )
    raw = _safe_content(resp)
    fr0, usage0 = extract_finish_reason_and_usage(resp)
    _log_llm_response(
        logger,
        node=node,
        chapter_index=int(chapter_index or 0),
        content=raw,
        finish_reason=str(fr0 or ""),
        token_usage=usage0 or {},
    )
    obj, _err2 = extract_first_json_object_with_error(raw)
    if obj and validate:
        verr = (validate(obj) or "").strip()
        if verr:
            return {}
    return obj if obj else {}


