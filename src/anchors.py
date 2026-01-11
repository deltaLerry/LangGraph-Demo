from __future__ import annotations

from typing import Any, Dict, List, Tuple


def _ensure_id_list(
    items: Any,
    *,
    prefix: str,
    start: int = 1,
    id_field: str = "id",
) -> Tuple[List[Dict[str, Any]], int]:
    """
    确保 list[dict] 中每个元素都有稳定 id：
    - 已有 id：保留
    - 缺失 id：按 prefix-XXX 递增补齐
    返回：规范化后的 list 与 next_counter
    """
    out: List[Dict[str, Any]] = []
    counter = int(start)
    if not isinstance(items, list):
        return out, counter
    for it in items:
        if not isinstance(it, dict):
            continue
        d = dict(it)
        cur = str(d.get(id_field, "") or "").strip()
        if not cur:
            d[id_field] = f"{prefix}-{counter:03d}"
            counter += 1
        out.append(d)
    return out, counter


def ensure_anchor_ids_and_build_index(frozen_pack: Dict[str, Any]) -> Dict[str, Any]:
    """
    对冻结材料包做两件事：
    1) 给可引用条目补齐稳定 ID（DEC/CON/GLO/CHAR/WR/TL/CHK）
    2) 生成 anchors 索引（id -> json_path）

    说明：这是“最小可用”的索引实现，后续可扩展为更精确的 jsonpath。
    """
    pack = frozen_pack if isinstance(frozen_pack, dict) else {}
    anchors: Dict[str, Any] = {}

    # execution.decisions
    exec0 = pack.get("execution") if isinstance(pack.get("execution"), dict) else {}
    decisions, _ = _ensure_id_list(exec0.get("decisions"), prefix="DEC", start=1, id_field="id")
    exec0 = dict(exec0)
    exec0["decisions"] = decisions
    for i, it in enumerate(decisions):
        anchors[str(it.get("id"))] = {"path": f"execution.decisions[{i}]", "title": str(it.get("topic", "") or "").strip()}

    # constraints（dict：把 key 视作 anchor；仅用于引用，不强制每条都有 id）
    constraints = exec0.get("constraints") if isinstance(exec0.get("constraints"), dict) else {}
    for k in list(constraints.keys()):
        kid = f"CON-{k}"
        anchors[kid] = {"path": f"execution.constraints.{k}", "title": k}

    # glossary（dict：categories -> list[dict] 或 list[str]）
    glossary = exec0.get("glossary") if isinstance(exec0.get("glossary"), dict) else {}
    g_counter = 1
    glossary_out: Dict[str, Any] = {}
    for cat, arr in glossary.items():
        if isinstance(arr, list):
            # list[dict]
            if arr and isinstance(arr[0], dict):
                fixed, g_counter = _ensure_id_list(arr, prefix="GLO", start=g_counter, id_field="id")
                glossary_out[cat] = fixed
                for i, it in enumerate(fixed):
                    anchors[str(it.get("id"))] = {"path": f"execution.glossary.{cat}[{i}]", "title": str(it.get("term", "") or "").strip()}
            else:
                # list[str] -> list[dict]
                fixed2: List[Dict[str, Any]] = []
                for s in arr:
                    t = str(s or "").strip()
                    if not t:
                        continue
                    gid = f"GLO-{g_counter:03d}"
                    g_counter += 1
                    fixed2.append({"id": gid, "term": t, "desc": ""})
                    anchors[gid] = {"path": f"execution.glossary.{cat}[{len(fixed2)-1}]", "title": t}
                glossary_out[cat] = fixed2
        else:
            continue
    if glossary_out:
        exec0["glossary"] = glossary_out

    # checklists（global/per_arc/per_chapter：仅生成引用锚点，不改变内容）
    chk = exec0.get("checklists") if isinstance(exec0.get("checklists"), dict) else {}
    for key in ("global", "per_arc", "per_chapter"):
        arr = chk.get(key) if isinstance(chk.get(key), list) else []
        for i, s in enumerate(arr[:99]):
            cid = f"CHK-{key.upper()}-{i+1:03d}"
            anchors[cid] = {"path": f"execution.checklists.{key}[{i}]", "title": str(s or "").strip()}

    pack["execution"] = exec0

    # canon/world/characters/timeline 简易锚点
    canon0 = pack.get("canon") if isinstance(pack.get("canon"), dict) else {}
    world = canon0.get("world") if isinstance(canon0.get("world"), dict) else {}
    rules = world.get("rules") if isinstance(world.get("rules"), list) else []
    for i, it in enumerate(rules[:99]):
        if isinstance(it, dict):
            name = str(it.get("name", "") or "").strip()
            if name:
                anchors[f"WR-{i+1:03d}"] = {"path": f"canon.world.rules[{i}]", "title": name}
    chars = canon0.get("characters") if isinstance(canon0.get("characters"), dict) else {}
    carr = chars.get("characters") if isinstance(chars.get("characters"), list) else []
    for i, it in enumerate(carr[:199]):
        if isinstance(it, dict):
            name = str(it.get("name", "") or "").strip()
            if name:
                anchors[f"CHAR-{i+1:03d}"] = {"path": f"canon.characters.characters[{i}]", "title": name}
    tl = canon0.get("timeline") if isinstance(canon0.get("timeline"), dict) else {}
    evs = tl.get("events") if isinstance(tl.get("events"), list) else []
    for i, it in enumerate(evs[:199]):
        if isinstance(it, dict):
            title = str(it.get("event", "") or it.get("name", "") or "").strip()
            if title:
                anchors[f"TL-{i+1:03d}"] = {"path": f"canon.timeline.events[{i}]", "title": title}

    return {"frozen_pack": pack, "anchors": {"anchors": anchors}}


