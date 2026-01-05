from __future__ import annotations

from typing import Any, Dict

from state import StoryState
from storage import ensure_materials_files, read_json, write_json


def _is_placeholder_outline(outline: Dict[str, Any]) -> bool:
    if not isinstance(outline, dict):
        return True
    # 允许 main_arc/themes 为空，但 chapters 为空基本等价于“未初始化”
    ch = outline.get("chapters")
    return (not isinstance(ch, list)) or (len(ch) == 0)


def _is_placeholder_tone(tone: Dict[str, Any]) -> bool:
    if not isinstance(tone, dict):
        return True
    sc = tone.get("style_constraints")
    av = tone.get("avoid")
    # style_constraints/avoid 都为空时视为占位
    return (not isinstance(sc, list) or len(sc) == 0) and (not isinstance(av, list) or len(av) == 0)

def _merge_keep_existing(existing: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """
    保守合并：existing 有值则保留，否则用 new（只做一层 dict）。
    """
    out = dict(existing or {})
    for k, v in (new or {}).items():
        if k not in out or out.get(k) in (None, "", [], {}):
            out[k] = v
    return out


def materials_init_agent(state: StoryState) -> StoryState:
    """
    阶段3：Materials 初始化（同步会议）
    - 初始化 projects/<project>/materials/ 下的 outline.json / tone.json
    - 只在 materials 仍为占位/空时写入，避免覆盖人工维护内容

    设计选择（更安全）：
    - 优先复用本次运行已生成的 screenwriter_result / tone_result（它们生成时已注入 Canon/style 约束）
    - template 模式下不会把“模板产物”写入长期 materials（避免污染项目资产）
    """
    logger = state.get("logger")
    if logger:
        logger.event("node_start", node="materials_init", chapter_index=0)

    project_dir = str(state.get("project_dir", "") or "")
    if not project_dir:
        if logger:
            logger.event("node_end", node="materials_init", chapter_index=0, skipped=True, reason="missing_project_dir")
        return state

    paths = ensure_materials_files(project_dir)
    outline_path = paths["outline"]
    tone_path = paths["tone"]

    existing_outline = read_json(outline_path) or {}
    existing_tone = read_json(tone_path) or {}

    need_outline = _is_placeholder_outline(existing_outline)
    need_tone = _is_placeholder_tone(existing_tone)

    if not (need_outline or need_tone):
        if logger:
            logger.event("node_end", node="materials_init", chapter_index=0, skipped=True, reason="materials_already_filled")
        return state

    # 只在 LLM 产出过专家材料时，才将其写入长期 materials（避免 template 产物污染）
    used_llm = bool(state.get("screenwriter_used_llm", False) or state.get("tone_used_llm", False))
    if not used_llm:
        if logger:
            logger.event("node_end", node="materials_init", chapter_index=0, skipped=True, reason="no_llm_materials")
        return state

    outline_new = state.get("screenwriter_result") if isinstance(state.get("screenwriter_result"), dict) else {}
    tone_new = state.get("tone_result") if isinstance(state.get("tone_result"), dict) else {}

    wrote_outline = False
    wrote_tone = False
    if need_outline and isinstance(outline_new, dict) and outline_new:
        write_json(outline_path, _merge_keep_existing(existing_outline, outline_new))
        wrote_outline = True
    if need_tone and isinstance(tone_new, dict) and tone_new:
        write_json(tone_path, _merge_keep_existing(existing_tone, tone_new))
        wrote_tone = True

    if logger:
        logger.event(
            "node_end",
            node="materials_init",
            chapter_index=0,
            used_llm=True,
            wrote_outline=wrote_outline,
            wrote_tone=wrote_tone,
        )
    return state


