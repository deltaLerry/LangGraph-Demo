from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from debug_log import truncate_text
from llm_call import invoke_with_retry
from llm_meta import extract_finish_reason_and_usage
from json_utils import extract_first_json_object
from storage import read_json, write_json, load_canon_bundle, normalize_canon_bundle


def _arc_filename(start_chapter: int, end_chapter: int) -> str:
    return f"arc_{start_chapter:03d}-{end_chapter:03d}.json"


def _load_chapter_memory(project_dir: str, chapter_index: int) -> Dict[str, Any]:
    p = os.path.join(project_dir, "memory", "chapters", f"{chapter_index:03d}.memory.json")
    return read_json(p) or {}


def generate_arc_summary(
    *,
    llm: Any,
    project_dir: str,
    start_chapter: int,
    end_chapter: int,
    logger: Any = None,
    llm_max_attempts: int = 3,
    llm_retry_base_sleep_s: float = 1.0,
) -> Dict[str, Any]:
    """
    生成一个 Arc（分卷/中程）摘要：用于 150 章规模下控制 prompt 膨胀与一致性。
    输出会落盘到 projects/<project>/memory/arcs/arc_XXX-YYY.json（由调用方写入）。
    """
    start_chapter = int(start_chapter)
    end_chapter = int(end_chapter)
    if end_chapter < start_chapter:
        return {}

    # 读取章节记忆（只使用 approved=True 的章，避免把失败稿污染中程摘要）
    memories: List[Dict[str, Any]] = []
    for i in range(start_chapter, end_chapter + 1):
        m = _load_chapter_memory(project_dir, i)
        if not isinstance(m, dict) or not m:
            continue
        if m.get("approved", True) is False:
            continue
        memories.append(m)

    if not memories:
        return {}

    canon0 = load_canon_bundle(project_dir)
    canon = normalize_canon_bundle(canon0)
    canon_text = truncate_text(
        json.dumps(
            {
                "world": canon.get("world", {}) or {},
                "characters": canon.get("characters", {}) or {},
                "timeline": canon.get("timeline", {}) or {},
            },
            ensure_ascii=False,
            indent=2,
        ),
        max_chars=4500,
    )

    # 压缩输入：只塞每章 summary + open_threads（避免 token 爆炸）
    packed = []
    for m in memories:
        packed.append(
            {
                "chapter_index": m.get("chapter_index"),
                "summary": str(m.get("summary", "") or "")[:800],
                "open_threads": m.get("open_threads", []) if isinstance(m.get("open_threads"), list) else [],
                "character_updates": m.get("character_updates", []) if isinstance(m.get("character_updates"), list) else [],
                "new_facts": m.get("new_facts", []) if isinstance(m.get("new_facts"), list) else [],
            }
        )

    try:
        from langchain_core.messages import SystemMessage, HumanMessage  # type: ignore
    except Exception:
        return {}

    system = SystemMessage(
        content=(
            "你是小说项目的“分卷摘要整理员（Arc Summarizer）”。你将把一段章节范围的 chapter memory 汇总为中程摘要，"
            "用于后续写作与一致性检查。\n"
            "你必须且仅输出一个严格 JSON 对象（不要解释、不要 markdown）。\n"
            "JSON schema：\n"
            "{\n"
            '  "start_chapter": number,\n'
            '  "end_chapter": number,\n'
            '  "summary": "string",\n'
            '  "key_facts": ["string"],\n'
            '  "character_states": {"角色名":"状态/动机/关系变化"},\n'
            '  "open_threads": ["string"]\n'
            "}\n"
            "要求：\n"
            "- summary：800~1500字（中文字符近似），必须覆盖主线推进与关键转折。\n"
            "- key_facts：8~20条，只写后续会用到的硬事实/规则/伏笔。\n"
            "- character_states：只列 5~12 个关键角色。\n"
            "- open_threads：5~15条，保持可承接。\n"
            "- 必须遵守 Canon：若与 Canon 冲突，以 Canon 为准并在 key_facts 中用保守表述。\n"
        )
    )
    human = HumanMessage(
        content=(
            f"章节范围：{start_chapter}~{end_chapter}\n\n"
            "【Canon（真值来源）】\n"
            f"{canon_text}\n\n"
            "【章节记忆（输入）】\n"
            f"{truncate_text(json.dumps(packed, ensure_ascii=False, indent=2), max_chars=8000)}\n"
        )
    )

    if logger:
        with logger.llm_call(
            node="arc_summary",
            chapter_index=end_chapter,
            messages=[system, human],
            model=getattr(llm, "model_name", None) or getattr(llm, "model", None),
            base_url=str(getattr(llm, "base_url", "") or ""),
            extra={"start_chapter": start_chapter, "end_chapter": end_chapter},
        ):
            resp = invoke_with_retry(
                llm,
                [system, human],
                max_attempts=llm_max_attempts,
                base_sleep_s=llm_retry_base_sleep_s,
                logger=logger,
                node="arc_summary",
                chapter_index=end_chapter,
                extra={"start_chapter": start_chapter, "end_chapter": end_chapter},
            )
    else:
        resp = invoke_with_retry(
            llm,
            [system, human],
            max_attempts=llm_max_attempts,
            base_sleep_s=llm_retry_base_sleep_s,
        )

    text = (getattr(resp, "content", "") or "").strip()
    if logger:
        fr, usage = extract_finish_reason_and_usage(resp)
        logger.event(
            "llm_response",
            node="arc_summary",
            chapter_index=end_chapter,
            content=truncate_text(text, max_chars=getattr(logger, "max_chars", 20000)),
            finish_reason=fr,
            token_usage=usage,
        )

    obj = extract_first_json_object(text) or {}
    if not isinstance(obj, dict) or not obj:
        return {}
    obj["start_chapter"] = start_chapter
    obj["end_chapter"] = end_chapter
    obj["generated_at"] = datetime.now().isoformat(timespec="seconds")
    return obj


def write_arc_summary(project_dir: str, start_chapter: int, end_chapter: int, arc: Dict[str, Any]) -> str:
    """
    写入 arc summary 文件，返回写入路径。已存在则覆盖（同名 arc 视为同一范围）。
    """
    arcs_dir = os.path.join(project_dir, "memory", "arcs")
    os.makedirs(arcs_dir, exist_ok=True)
    path = os.path.join(arcs_dir, _arc_filename(int(start_chapter), int(end_chapter)))
    write_json(path, arc)
    return path


