from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple


def prompt_choice(prompt: str, *, choices: Dict[str, str], default: str = "") -> str:
    """
    choices: key -> description
    返回用户输入的 key（小写）。
    """
    keys = "/".join(choices.keys())
    while True:
        try:
            ans = input(f"{prompt} [{keys}] ").strip().lower()
        except EOFError:
            # 非交互环境（例如 CI / 无 stdin）：降级走默认值，避免崩溃
            return (default or "q").strip().lower()
        if not ans and default:
            ans = default.lower()
        if ans in choices:
            return ans
        print("未识别指令，可选：")
        for k, d in choices.items():
            print(f"- {k}: {d}")


def prompt_multiline(prompt: str, *, end_token: str = ".") -> str:
    """
    读取多行输入，直到用户输入 end_token（单独一行）。
    默认 end_token='.'，适合中文输入法。
    """
    print(prompt)
    print(f"（输入 {end_token} 单独一行结束）")
    lines: List[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            # 非交互环境：返回已收集内容（通常为空）
            break
        if line.strip() == end_token:
            break
        lines.append(line.rstrip("\n"))
    return "\n".join(lines).strip()


def print_json_preview(obj: Any, max_chars: int = 5000) -> None:
    try:
        s = json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        s = str(obj)
    if len(s) > max_chars:
        s = s[: max_chars - 20] + "\n...(truncated)...\n"
    print(s)


def ensure_dir_note(s: str) -> str:
    return (s or "").replace("\\", "/")


def summarize_editor_report(editor_report: Any, *, max_items: int = 6) -> Tuple[str, List[str]]:
    """
    返回：(decision, issue_lines)
    issue_lines 为适合直接打印的单行摘要列表。
    """
    rep = editor_report if isinstance(editor_report, dict) else {}
    decision = str(rep.get("decision", "") or "").strip()
    issues0 = rep.get("issues") if isinstance(rep.get("issues"), list) else []
    out: List[str] = []
    for i, it in enumerate(issues0[: max(0, int(max_items))], start=1):
        if not isinstance(it, dict):
            continue
        t = str(it.get("type", "") or "").strip() or "N/A"
        issue = str(it.get("issue", "") or "").strip()
        canon_key = str(it.get("canon_key", "") or "").strip() or "N/A"
        quote = str(it.get("quote", "") or "").strip()
        s = f"[{i}] ({t}) {issue[:120]}".strip()
        if canon_key and canon_key != "N/A":
            s += f" | canon_key={canon_key}"
        if quote:
            s += f" | quote={quote[:40]}"
        out.append(s)
    return decision, out


def print_chapter_review_card(
    *,
    chapter_index: int,
    chap_id: str,
    chapter_text: str,
    editor_report: Any,
    materials_frozen_version: str,
    chapter_md_path: str,
    editor_json_path: str,
    snapshot_dir: str,
    extra_paths: Optional[Dict[str, str]] = None,
    advisor_digest: str = "",
) -> None:
    """
    打印“总编审阅卡（digest）”：默认一屏内，便于每章拍板。
    """
    extra_paths = extra_paths or {}
    decision, issue_lines = summarize_editor_report(editor_report, max_items=8)
    chars = len(chapter_text or "")
    print("\n--- 总编审阅卡（digest）---")
    print(f"- 章节：第{int(chapter_index)}章（{chap_id}）")
    print(f"- 字数(近似字符数)：{chars}")
    print(f"- 主编结论：{decision or '（无）'}")
    print(f"- 冻结材料版本：{materials_frozen_version or '（未知）'}")
    print(f"- 正文文件：{ensure_dir_note(chapter_md_path)}")
    print(f"- 审稿JSON：{ensure_dir_note(editor_json_path)}")
    if snapshot_dir:
        print(f"- 材料快照：{ensure_dir_note(snapshot_dir)}")
    for k, v in extra_paths.items():
        if v:
            print(f"- {k}：{ensure_dir_note(v)}")
    if advisor_digest:
        print(f"- 顾问摘要：{advisor_digest}")
    # 正文开头预览
    head = (chapter_text or "").strip().replace("\r\n", "\n")
    head = head[:300].rstrip()
    if head:
        print("\n【正文预览】")
        print(head + ("…" if len((chapter_text or "").strip()) > 300 else ""))
    # issues 预览
    print("\n【主编 issues（Top）】")
    if issue_lines:
        for s in issue_lines:
            print("- " + s)
    else:
        print("- （无）")
    print("\n可用查看指令：f=全文  j=完整审稿JSON  k=顾问报告  d=重新显示digest")


def _count_glossary_terms(glossary: Any) -> int:
    g = glossary if isinstance(glossary, dict) else {}
    n = 0
    for _k, v in g.items():
        if isinstance(v, list):
            n += len(v)
    return n


def print_materials_review_card(
    *,
    draft_obj: Any,
    draft_path: str,
    project_dir: str,
    current_frozen_version: str,
) -> None:
    """
    打印“材料包审阅卡（digest）”：用于冻结门禁前快速评审。
    """
    obj = draft_obj if isinstance(draft_obj, dict) else {}
    meta = obj.get("meta") if isinstance(obj.get("meta"), dict) else {}
    ver = str(meta.get("version", "") or "").strip()
    canon = obj.get("canon") if isinstance(obj.get("canon"), dict) else {}
    planning = obj.get("planning") if isinstance(obj.get("planning"), dict) else {}
    execution = obj.get("execution") if isinstance(obj.get("execution"), dict) else {}
    risk = obj.get("risk") if isinstance(obj.get("risk"), dict) else {}

    decisions = execution.get("decisions") if isinstance(execution.get("decisions"), list) else []
    checklists = execution.get("checklists") if isinstance(execution.get("checklists"), dict) else {}
    glossary = execution.get("glossary") if isinstance(execution.get("glossary"), dict) else {}
    constraints = execution.get("constraints") if isinstance(execution.get("constraints"), dict) else {}

    oq = risk.get("open_questions") if isinstance(risk.get("open_questions"), list) else []
    # blocker 统计：severity==blocker 或 blocking==true
    blockers = 0
    for it in oq:
        if not isinstance(it, dict):
            continue
        sev = str(it.get("severity", "") or "").strip().lower()
        if sev == "blocker" or (it.get("blocking", None) is True):
            blockers += 1

    # Canon 粗略计数
    world = canon.get("world") if isinstance(canon.get("world"), dict) else {}
    chars = canon.get("characters") if isinstance(canon.get("characters"), dict) else {}
    tl = canon.get("timeline") if isinstance(canon.get("timeline"), dict) else {}
    n_rules = len(world.get("rules")) if isinstance(world.get("rules"), list) else 0
    n_factions = len(world.get("factions")) if isinstance(world.get("factions"), list) else 0
    n_places = len(world.get("places")) if isinstance(world.get("places"), list) else 0
    n_chars = len(chars.get("characters")) if isinstance(chars.get("characters"), list) else 0
    n_events = len(tl.get("events")) if isinstance(tl.get("events"), list) else 0

    # Planning
    outline = planning.get("outline") if isinstance(planning.get("outline"), dict) else {}
    tone = planning.get("tone") if isinstance(planning.get("tone"), dict) else {}
    n_outline_ch = len(outline.get("chapters")) if isinstance(outline.get("chapters"), list) else 0
    n_style_constraints = len(tone.get("style_constraints")) if isinstance(tone.get("style_constraints"), list) else 0
    n_avoid = len(tone.get("avoid")) if isinstance(tone.get("avoid"), list) else 0

    print("\n--- 材料包审阅卡（digest）---")
    print(f"- draft_version：{ver or '（未知）'}")
    print(f"- 当前生效 frozen：{current_frozen_version or '（无）'}")
    print(f"- draft_path：{ensure_dir_note(draft_path)}")
    print(f"- project_dir：{ensure_dir_note(project_dir)}")

    print("\n【完备性概览】")
    print(f"- Canon：rules={n_rules} factions={n_factions} places={n_places} characters={n_chars} timeline_events={n_events}")
    print(f"- Planning：outline.chapters={n_outline_ch} tone.style_constraints={n_style_constraints} tone.avoid={n_avoid}")
    print(f"- Execution：decisions={len(decisions)} glossary_terms={_count_glossary_terms(glossary)}")
    print(f"- Risk：open_questions={len(oq)} blockers={blockers}")

    # DoD 契约校验摘要（可执行、可追溯；冻结必须 PASS）
    try:
        from materials_dod import validate_materials_pack_dod, dod_one_line

        dod = validate_materials_pack_dod(obj)
        print("\n【DoD（冻结门禁）】")
        print("- " + dod_one_line(dod))
        issues0 = dod.get("issues") if isinstance(dod.get("issues"), list) else []
        if issues0:
            # 只展示 Top，避免刷屏；详细见 v=全文 JSON 或 digests/dod_report.vNNN.json
            shown = 0
            for it in issues0:
                if not isinstance(it, dict):
                    continue
                sev = str(it.get("severity", "") or "").strip()
                path = str(it.get("path", "") or "").strip()
                msg = str(it.get("message", "") or "").strip()
                print(f"  - [{sev}] {path}: {msg}")
                shown += 1
                if shown >= 6:
                    break
    except Exception:
        pass

    print("\n【约束（constraints）】")
    tw = constraints.get("target_words", None)
    wmin = constraints.get("writer_min_ratio", None)
    wmax = constraints.get("writer_max_ratio", None)
    naming = str(constraints.get("naming_policy", "") or "").strip()
    print(f"- target_words={tw} writer_min_ratio={wmin} writer_max_ratio={wmax}")
    if naming:
        print(f"- naming_policy={naming[:220]}")

    # decisions top
    print("\n【decisions（Top）】")
    if decisions:
        for i, it in enumerate(decisions[:6], start=1):
            if not isinstance(it, dict):
                continue
            topic = str(it.get("topic", "") or "").strip()
            dec = str(it.get("decision", "") or "").strip()
            print(f"- [{i}] {topic[:60]}：{dec[:120]}")
    else:
        print("- （无）")

    # blocker questions top
    if blockers > 0:
        print("\n【blocker open_questions（Top）】")
        shown = 0
        for it in oq:
            if not isinstance(it, dict):
                continue
            sev = str(it.get("severity", "") or "").strip().lower()
            if not (sev == "blocker" or (it.get("blocking", None) is True)):
                continue
            q = str(it.get("question", "") or it.get("q", "") or it.get("topic", "") or "").strip()
            impact = str(it.get("impact", "") or "").strip()
            print(f"- {q or '（未命名问题）'}")
            if impact:
                print(f"  impact: {impact[:200]}")
            shown += 1
            if shown >= 5:
                break

    # checklists 简略
    print("\n【checklists 概览】")
    for k in ("global", "per_arc", "per_chapter"):
        arr = checklists.get(k) if isinstance(checklists.get(k), list) else []
        print(f"- {k}: {len(arr)}")

    print("\n可用查看指令：v=查看draft全文JSON  d=重新显示digest")


