from __future__ import annotations

from typing import Any, Dict, List, Tuple


def _get(d: Any, path: str) -> Any:
    cur = d
    for part in (path or "").split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _is_nonempty_str(x: Any) -> bool:
    return isinstance(x, str) and bool(x.strip())


def _is_nonempty_list(x: Any) -> bool:
    return isinstance(x, list) and len(x) > 0


def _num(x: Any) -> Tuple[bool, float]:
    try:
        return True, float(x)
    except Exception:
        return False, 0.0


def validate_materials_pack_dod(pack: Any) -> Dict[str, Any]:
    """
    材料包冻结 DoD（可执行契约）。
    - 目标：冻结时确保“写作可执行、结构稳定、可追溯”，避免“能冻就冻”。
    - 输出：结构化 issues（含 severity/path/message），供 CLI/日志/审阅使用。

    约定：
    - severity: blocker | major | minor | warn
    - ok: 仅当 blocker=0 且 major=0
    """
    obj = pack if isinstance(pack, dict) else {}

    issues: List[Dict[str, Any]] = []

    def add(issue_id: str, *, severity: str, path: str, message: str, hint: str = "") -> None:
        issues.append(
            {
                "id": issue_id,
                "severity": severity,
                "path": path,
                "message": message,
                "hint": hint,
            }
        )

    # 0) 顶层结构
    for p in ("meta", "canon", "planning", "execution", "risk"):
        v = obj.get(p)
        if not isinstance(v, dict):
            add(
                f"DOD-ROOT-{p.upper()}",
                severity="blocker",
                path=p,
                message=f"缺少或类型不正确：{p} 必须是 dict",
                hint="请确保材料包包含 canon/planning/execution/risk 四层结构以及 meta。",
            )

    meta = obj.get("meta") if isinstance(obj.get("meta"), dict) else {}
    ver = str(meta.get("version", "") or "").strip()
    if not ver:
        add("DOD-META-VERSION", severity="major", path="meta.version", message="meta.version 为空", hint="建议使用 vNNN 版本号。")

    # 1) Canon 最小可用：存在 world/characters/timeline dict（不强制数量，避免过度约束）
    for p in ("canon.world", "canon.characters", "canon.timeline"):
        if not isinstance(_get(obj, p), dict):
            add(
                f"DOD-CANON-{p.split('.')[-1].upper()}",
                severity="major",
                path=p,
                message=f"{p} 必须存在且为 dict",
                hint="冻结口径要求 Canon 有最小闭环：世界/角色/时间线。",
            )

    # 2) Planning：outline 必须有 chapters（至少 1 章），否则写作不可执行
    outline = _get(obj, "planning.outline")
    if not isinstance(outline, dict):
        add("DOD-PLAN-OUTLINE", severity="blocker", path="planning.outline", message="planning.outline 缺失或类型不对（需 dict）")
    else:
        chs = outline.get("chapters")
        if not _is_nonempty_list(chs):
            add(
                "DOD-PLAN-OUTLINE-CHAPTERS",
                severity="blocker",
                path="planning.outline.chapters",
                message="planning.outline.chapters 为空（至少需要 1 章细纲/概述）",
                hint="请让编剧/策划补齐章节列表；否则写作阶段无法稳定推进。",
            )

    tone = _get(obj, "planning.tone")
    if not isinstance(tone, dict):
        add("DOD-PLAN-TONE", severity="major", path="planning.tone", message="planning.tone 缺失或类型不对（需 dict）")

    # 3) Execution：constraints 必须含 target_words + ratio（影响写作/审稿口径一致性）
    exe = obj.get("execution") if isinstance(obj.get("execution"), dict) else {}
    constraints = exe.get("constraints") if isinstance(exe.get("constraints"), dict) else {}
    if not isinstance(constraints, dict) or not constraints:
        add("DOD-EXEC-CONSTRAINTS", severity="blocker", path="execution.constraints", message="execution.constraints 缺失或为空")
    else:
        ok_tw, tw = _num(constraints.get("target_words", None))
        if (not ok_tw) or tw <= 0:
            add(
                "DOD-EXEC-TARGET_WORDS",
                severity="major",
                path="execution.constraints.target_words",
                message="target_words 缺失或 <= 0（会导致字数口径不稳定）",
                hint="建议明确每章 target_words，保持 writer/editor/advisor 口径一致。",
            )
        ok_min, wmin = _num(constraints.get("writer_min_ratio", None))
        ok_max, wmax = _num(constraints.get("writer_max_ratio", None))
        if not ok_min or wmin <= 0:
            add("DOD-EXEC-WRITER_MIN_RATIO", severity="major", path="execution.constraints.writer_min_ratio", message="writer_min_ratio 缺失或 <= 0")
        if not ok_max or wmax <= 0:
            add("DOD-EXEC-WRITER_MAX_RATIO", severity="major", path="execution.constraints.writer_max_ratio", message="writer_max_ratio 缺失或 <= 0")
        if ok_min and ok_max and wmin > 0 and wmax > 0 and wmin >= wmax:
            add(
                "DOD-EXEC-RATIO_RANGE",
                severity="major",
                path="execution.constraints",
                message=f"writer_min_ratio >= writer_max_ratio（min={wmin}, max={wmax}）",
                hint="请调整为 min < max。",
            )

        naming = constraints.get("naming_policy", None)
        if not _is_nonempty_str(naming):
            add(
                "DOD-EXEC-NAMING_POLICY",
                severity="minor",
                path="execution.constraints.naming_policy",
                message="naming_policy 为空（可能导致命名漂移）",
                hint="建议明确是否允许新增专名、如何引用术语表。",
            )

    # decisions/checklists/glossary：允许为空但提示（由总编逐步完善）
    if not isinstance(exe.get("decisions"), list):
        add("DOD-EXEC-DECISIONS-TYPE", severity="major", path="execution.decisions", message="execution.decisions 类型应为 list")
    elif len(exe.get("decisions") or []) == 0:
        add("DOD-EXEC-DECISIONS-EMPTY", severity="warn", path="execution.decisions", message="execution.decisions 为空（建议至少沉淀几条关键决策）")

    if not isinstance(exe.get("checklists"), dict):
        add("DOD-EXEC-CHECKLISTS-TYPE", severity="major", path="execution.checklists", message="execution.checklists 类型应为 dict")

    if not isinstance(exe.get("glossary"), dict):
        add("DOD-EXEC-GLOSSARY-TYPE", severity="major", path="execution.glossary", message="execution.glossary 类型应为 dict")

    # 4) Risk：open_questions 中 blocker 必须为 0
    oq = _get(obj, "risk.open_questions")
    if oq is None:
        add("DOD-RISK-OQ-MISSING", severity="major", path="risk.open_questions", message="risk.open_questions 缺失（建议至少为空列表）")
    elif not isinstance(oq, list):
        add("DOD-RISK-OQ-TYPE", severity="major", path="risk.open_questions", message="risk.open_questions 类型应为 list")
    else:
        blockers = 0
        for it in oq:
            if not isinstance(it, dict):
                continue
            sev = str(it.get("severity", "") or "").strip().lower()
            blocking = it.get("blocking", None)
            if sev == "blocker" or blocking is True:
                blockers += 1
        if blockers > 0:
            add(
                "DOD-RISK-OQ-BLOCKERS",
                severity="blocker",
                path="risk.open_questions",
                message=f"存在 blocker open_questions：{blockers}（必须清零才能冻结）",
                hint="请先回答/降级 blocker；冻结后修改必须走变更提案。",
            )

    # 汇总
    counts = {"blocker": 0, "major": 0, "minor": 0, "warn": 0}
    for it in issues:
        sev = str(it.get("severity", "") or "").strip().lower()
        if sev in counts:
            counts[sev] += 1
    ok = (counts["blocker"] == 0) and (counts["major"] == 0)

    return {"ok": ok, "counts": counts, "issues": issues}


def dod_one_line(report: Any) -> str:
    rep = report if isinstance(report, dict) else {}
    ok = bool(rep.get("ok", False))
    c = rep.get("counts") if isinstance(rep.get("counts"), dict) else {}
    b = int(c.get("blocker", 0) or 0)
    m = int(c.get("major", 0) or 0)
    n = int(c.get("minor", 0) or 0)
    w = int(c.get("warn", 0) or 0)
    return f"DoD={'PASS' if ok else 'FAIL'} blocker={b} major={m} minor={n} warn={w}"


