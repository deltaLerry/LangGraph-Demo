from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


def _get(d: Any, path: str) -> Any:
    cur = d
    for part in (path or "").split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _count_blockers(open_questions: Any) -> int:
    if not isinstance(open_questions, list):
        return 0
    n = 0
    for it in open_questions:
        if not isinstance(it, dict):
            continue
        sev = str(it.get("severity", "") or "").strip().lower()
        blocking = it.get("blocking", None)
        if sev == "blocker" or blocking is True:
            n += 1
    return n


def _pick_blockers(open_questions: Any, *, max_items: int = 20) -> List[Dict[str, Any]]:
    """
    从 open_questions 中挑出 blocker 项（不猜字段结构，原样保留 dict），用于审计/追溯。
    """
    if not isinstance(open_questions, list):
        return []
    out: List[Dict[str, Any]] = []
    for it in open_questions:
        if not isinstance(it, dict):
            continue
        sev = str(it.get("severity", "") or "").strip().lower()
        blocking = it.get("blocking", None)
        if sev == "blocker" or blocking is True:
            out.append(dict(it))
        if len(out) >= int(max_items):
            break
    return out


def _extract_constraints(frozen_pack: Dict[str, Any]) -> Dict[str, Any]:
    exe = frozen_pack.get("execution") if isinstance(frozen_pack.get("execution"), dict) else {}
    c = exe.get("constraints") if isinstance(exe.get("constraints"), dict) else {}
    return dict(c)


def resolve_anchor_details(anchors_index: Any, anchor_ids: List[str]) -> List[Dict[str, str]]:
    """
    不猜 anchors：只解析你手工给出的 anchors 列表，返回可读的 title/path。
    anchors_index 期望结构：{"anchors": {"DEC-001": {"path": "...", "title": "..."}, ...}}
    """
    idx = anchors_index if isinstance(anchors_index, dict) else {}
    by = idx.get("anchors") if isinstance(idx.get("anchors"), dict) else {}
    out: List[Dict[str, str]] = []
    for a in anchor_ids:
        key = str(a or "").strip()
        if not key:
            continue
        info = by.get(key) if isinstance(by.get(key), dict) else {}
        out.append(
            {
                "id": key,
                "path": str(info.get("path", "") or ""),
                "title": str(info.get("title", "") or ""),
            }
        )
    return out


def advisor_digest_line(advisor_report: Any, *, max_len: int = 120) -> str:
    """
    将顾问报告压成一行摘要，适合塞进审阅卡。
    """
    rep = advisor_report if isinstance(advisor_report, dict) else {}
    act = str(rep.get("suggested_action", "") or "").strip() or "N/A"
    risk_level = str(rep.get("risk_level", "") or "").strip()
    findings = rep.get("findings") if isinstance(rep.get("findings"), list) else []
    top = ""
    if findings and isinstance(findings[0], dict):
        top = str(findings[0].get("message", "") or "").strip()
    s = f"建议={act}"
    if risk_level:
        s += f" | 风险={risk_level}"
    if top:
        s += f" | Top={top}"
    if len(s) > int(max_len):
        s = s[: max(0, int(max_len) - 1)].rstrip() + "…"
    return s


def _extract_glossary_terms(frozen_pack: Dict[str, Any]) -> List[str]:
    exe = frozen_pack.get("execution") if isinstance(frozen_pack.get("execution"), dict) else {}
    g = exe.get("glossary") if isinstance(exe.get("glossary"), dict) else {}
    terms: List[str] = []
    for _cat, arr in g.items():
        if not isinstance(arr, list):
            continue
        for it in arr:
            if isinstance(it, dict):
                t = str(it.get("term", "") or "").strip()
                if t:
                    terms.append(t)
            else:
                t = str(it or "").strip()
                if t:
                    terms.append(t)
    return terms


def build_advisor_report(
    *,
    chapter_text: str,
    editor_report: Any,
    frozen_pack: Dict[str, Any],
    anchors_index: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    顾问审计（规则化，偏稳定性/一致性）：
    - 不自动猜 anchors
    - 输出 suggested_action: accept|rewrite|escalate（仅建议）
    """
    text = str(chapter_text or "")
    rep = editor_report if isinstance(editor_report, dict) else {}
    editor_decision = str(rep.get("decision", "") or "").strip()

    constraints = _extract_constraints(frozen_pack if isinstance(frozen_pack, dict) else {})
    target_words = int(constraints.get("target_words", 0) or 0)
    min_ratio = float(constraints.get("writer_min_ratio", 0.0) or 0.0) or 0.7
    max_ratio = float(constraints.get("writer_max_ratio", 0.0) or 0.0) or 1.5
    min_chars = int(target_words * min_ratio) if target_words > 0 else 0
    max_chars = int(target_words * max_ratio) if target_words > 0 else 0
    chars = len(text)

    # blocker 兜底检查（理论上冻结门禁已阻止）
    oq = _get(frozen_pack, "risk.open_questions")
    blocker_items = _pick_blockers(oq)
    blockers = len(blocker_items)

    findings: List[Dict[str, Any]] = []

    # 1) 字数区间（规则化建议：轻微超限只提示；严重超限才建议 rewrite）
    if target_words > 0 and (min_chars > 0 and max_chars > 0):
        if chars < min_chars:
            findings.append(
                {
                    "type": "length",
                    "severity": "medium",
                    "message": f"正文偏短：{chars} < {min_chars}（target_words={target_words}, ratio={min_ratio}）",
                    "suggest": "note",
                }
            )
        elif chars > max_chars:
            # 严重超限：> 上限的 20% 才建议 rewrite
            severe = chars > int(max_chars * 1.2)
            findings.append(
                {
                    "type": "length",
                    "severity": "high" if severe else "medium",
                    "message": f"正文偏长：{chars} > {max_chars}（target_words={target_words}, ratio={max_ratio}）",
                    "suggest": "rewrite" if severe else "note",
                }
            )

    # 2) 明显 AI/元话语（硬伤）
    bad_phrases = [
        "作为AI",
        "作为一个AI",
        "我无法",
        "我不能",
        "以下将",
        "接下来将",
        "本文将",
    ]
    hit = [p for p in bad_phrases if p in text]
    if hit:
        findings.append(
            {
                "type": "meta",
                "severity": "high",
                "message": f"疑似元话语/AI腔命中：{', '.join(hit[:5])}",
                "suggest": "rewrite",
            }
        )

    # 2.1) 可选：材料包执行层禁用/必须词（只在材料包提供时启用，避免拍脑袋）
    prohibited = constraints.get("prohibited_phrases")
    if isinstance(prohibited, list):
        bad = [str(x or "").strip() for x in prohibited if str(x or "").strip()]
        hit2 = [p for p in bad if p and p in text]
        if hit2:
            findings.append(
                {
                    "type": "prohibited_phrases",
                    "severity": "high",
                    "message": f"命中冻结材料禁用词：{', '.join(hit2[:6])}",
                    "suggest": "rewrite",
                }
            )

    required = constraints.get("required_phrases")
    if isinstance(required, list):
        req = [str(x or "").strip() for x in required if str(x or "").strip()]
        missing = [p for p in req if p and (p not in text)]
        if missing:
            findings.append(
                {
                    "type": "required_phrases",
                    "severity": "low",
                    "message": f"可能缺少必须词（材料包要求）：{', '.join(missing[:6])}",
                    "suggest": "note",
                }
            )

    # 2.2) 可选：POV（材料包提供才启用）
    pov = str(constraints.get("pov", "") or "").strip().lower()
    if pov in {"third", "3rd", "第三人称"}:
        if any(p in text for p in ("我", "我们", "俺", "咱")):
            findings.append(
                {
                    "type": "pov",
                    "severity": "medium",
                    "message": "疑似人称漂移：材料包要求第三人称，但正文出现第一人称（如“我/我们”）",
                    "suggest": "rewrite",
                }
            )

    # 2.3) 命名漂移（轻量、低误报版）：仅检查英文专名（naming_policy 提示“禁止新增专名”时启用）
    naming_policy = str(constraints.get("naming_policy", "") or "").strip()
    if naming_policy and ("禁止" in naming_policy or "严禁" in naming_policy):
        allowed = set(t.lower() for t in _extract_glossary_terms(frozen_pack))
        # 仅英文/数字专名：避免中文分词误报
        candidates = re.findall(r"\b[A-Z][A-Za-z0-9_-]{2,}\b", text)
        unknown = [w for w in candidates if w.lower() not in allowed]
        if unknown:
            findings.append(
                {
                    "type": "naming_drift",
                    "severity": "medium",
                    "message": f"疑似新增英文专名（未在冻结glossary中）：{', '.join(unknown[:6])}",
                    "suggest": "note",
                }
            )

    # 3) 冻结材料包 blocker（应先处理）
    if blockers > 0:
        findings.append(
            {
                "type": "materials_blocker",
                "severity": "blocker",
                "message": f"冻结材料包仍存在 blocker open_questions：{blockers}（应先处理后再写作）",
                "suggest": "escalate",
            }
        )

    # 4) 主编结论（参考）
    if editor_decision and editor_decision != "审核通过":
        findings.append(
            {
                "type": "editor",
                "severity": "high",
                "message": f"主编未通过：{editor_decision}",
                "suggest": "rewrite",
            }
        )

    # 建议动作：blocker>rewrite>accept（note 不影响建议动作）
    suggested_action = "accept"
    if any(f.get("suggest") == "escalate" for f in findings):
        suggested_action = "escalate"
    elif any(f.get("suggest") == "rewrite" for f in findings):
        suggested_action = "rewrite"

    # 风险分级（轻量、可查询）：blocker > high > medium > low
    # - blocker：材料包仍有 blocker open_questions（写作应暂停）
    # - high：建议 rewrite（明显冲突/硬伤）
    # - medium：有 findings 但不要求 rewrite
    # - low：无 findings
    risk_level = "low"
    if blockers > 0:
        risk_level = "blocker"
    elif suggested_action == "escalate":
        risk_level = "high"
    elif suggested_action == "rewrite":
        risk_level = "high"
    elif len(findings) > 0:
        risk_level = "medium"

    return {
        "suggested_action": suggested_action,
        "risk_level": risk_level,
        "materials_blockers_count": int(blockers),
        "materials_blockers": blocker_items,
        "digest": advisor_digest_line({"suggested_action": suggested_action, "risk_level": risk_level, "findings": findings}),
        "stats": {
            "chars": chars,
            "target_words": target_words,
            "min_chars": min_chars,
            "max_chars": max_chars,
        },
        "editor_decision": editor_decision,
        "findings": findings,
    }


