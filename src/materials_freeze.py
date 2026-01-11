from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from storage import read_json, write_json, write_text
from anchors import ensure_anchor_ids_and_build_index


def ensure_materials_pack_dirs(project_dir: str) -> Dict[str, str]:
    """
    创建项目级材料包目录结构（drafts/frozen/reviews/digests/anchors + index.json）。
    """
    base = os.path.join(project_dir, "materials")
    drafts = os.path.join(base, "drafts")
    frozen = os.path.join(base, "frozen")
    reviews = os.path.join(base, "reviews")
    digests = os.path.join(base, "digests")
    anchors_dir = os.path.join(base, "anchors")
    os.makedirs(drafts, exist_ok=True)
    os.makedirs(frozen, exist_ok=True)
    os.makedirs(reviews, exist_ok=True)
    os.makedirs(digests, exist_ok=True)
    os.makedirs(anchors_dir, exist_ok=True)
    index_path = os.path.join(base, "index.json")
    if not os.path.exists(index_path):
        write_json(index_path, {"current_frozen_version": "", "updated_at": ""})
    return {
        "base": base,
        "drafts": drafts,
        "frozen": frozen,
        "reviews": reviews,
        "digests": digests,
        "anchors": anchors_dir,
        "index": index_path,
    }


def count_open_question_blockers(draft_obj: Dict[str, Any]) -> tuple[int, list[dict]]:
    """
    冻结 DoD 门禁（最小实现）：统计 open_questions 中的 blocker 数量。
    约定：
    - severity == "blocker" 视为 blocker
    - blocking == True 视为 blocker
    兼容位置：
    - risk.open_questions
    - execution.open_questions（向后兼容）
    """
    obj = draft_obj if isinstance(draft_obj, dict) else {}
    out: list[dict] = []

    def _collect(path: str) -> None:
        cur = obj
        for part in path.split("."):
            if not isinstance(cur, dict):
                return
            cur = cur.get(part)
        if isinstance(cur, list):
            for it in cur:
                if isinstance(it, dict):
                    out.append(dict(it))

    _collect("risk.open_questions")
    _collect("execution.open_questions")

    blockers = 0
    picked: list[dict] = []
    for it in out:
        sev = str(it.get("severity", "") or "").strip().lower()
        blocking = it.get("blocking", None)
        is_block = (sev == "blocker") or (blocking is True)
        if is_block:
            blockers += 1
            picked.append(it)
    return blockers, picked


def _next_vnnn(dir_path: str, *, prefix: str) -> str:
    """
    找到下一个 vNNN（按目录内同前缀文件推断）。
    """
    mx = 0
    if os.path.exists(dir_path):
        for name in os.listdir(dir_path):
            if not name.startswith(prefix):
                continue
            # prefix.vNNN.json
            parts = name.split(".")
            for p in parts:
                if p.startswith("v") and p[1:].isdigit():
                    mx = max(mx, int(p[1:]))
    return f"v{mx+1:03d}"


def build_execution_from_materials_bundle(materials_bundle: Dict[str, Any], *, settings_meta: Dict[str, Any]) -> Dict[str, Any]:
    """
    将现有 materials_bundle/materials_pack 产物提升为“Execution 层”：
    - decisions/checklists/risks 来自 materials_pack
    - constraints/glossary/open_questions 先给最小可用（后续由总编完善）
    """
    mb = materials_bundle if isinstance(materials_bundle, dict) else {}
    pack = mb.get("materials_pack") if isinstance(mb.get("materials_pack"), dict) else {}

    decisions = pack.get("decisions") if isinstance(pack.get("decisions"), list) else []
    checklists = pack.get("checklists") if isinstance(pack.get("checklists"), dict) else {"global": [], "per_arc": [], "per_chapter": []}
    risks = pack.get("risks") if isinstance(pack.get("risks"), list) else []

    # glossary：用 Canon/材料中已有专名做“种子”，避免命名漂移；desc 允许为空
    glossary: Dict[str, Any] = {"characters": [], "factions": [], "places": [], "rules": []}
    world = mb.get("world") if isinstance(mb.get("world"), dict) else {}
    chars = mb.get("characters") if isinstance(mb.get("characters"), dict) else {}
    for it in (chars.get("characters") if isinstance(chars.get("characters"), list) else []):
        if isinstance(it, dict):
            n = str(it.get("name", "") or "").strip()
            if n:
                glossary["characters"].append({"term": n, "desc": ""})
    for k, cat in (("factions", "factions"), ("places", "places"), ("rules", "rules")):
        for it in (world.get(k) if isinstance(world.get(k), list) else []):
            if isinstance(it, dict):
                n = str(it.get("name", "") or "").strip()
                if n:
                    glossary[cat].append({"term": n, "desc": str(it.get("desc", "") or it.get("detail", "") or "").strip()})

    constraints = {
        "target_words": int(settings_meta.get("target_words", 0) or 0),
        "writer_min_ratio": float(settings_meta.get("writer_min_ratio", 0.0) or 0.0),
        "writer_max_ratio": float(settings_meta.get("writer_max_ratio", 0.0) or 0.0),
        "style_override": str(settings_meta.get("style_override", "") or ""),
        "paragraph_rules": str(settings_meta.get("paragraph_rules", "") or ""),
        "naming_policy": "除非在 canon/materials/glossary/已知名词中出现，否则禁止新增硬设定专有名词；必须引入时用模糊描述不命名。",
    }

    # open_questions：由人/agent 逐步填充；阻塞项必须为 0 才能冻结
    open_questions = []

    return {
        "decisions": decisions,
        "checklists": checklists,
        "glossary": glossary,
        "constraints": constraints,
        "risks": risks,
        "open_questions": open_questions,
    }


def create_materials_pack_draft(
    *,
    project_dir: str,
    materials_bundle: Dict[str, Any],
    canon_bundle: Dict[str, Any],
    settings_meta: Dict[str, Any],
    agent_review: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """
    写入项目级 materials draft：
    - drafts/materials_pack.vNNN.json
    - reviews/agent_review.vNNN.json（可选）
    返回：(version, draft_path)
    """
    paths = ensure_materials_pack_dirs(project_dir)
    ver = _next_vnnn(paths["drafts"], prefix="materials_pack.")

    draft_obj = {
        "meta": {
            "project_dir": project_dir,
            "version": ver,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        },
        "canon": canon_bundle if isinstance(canon_bundle, dict) else {},
        "planning": {
            "outline": (materials_bundle.get("outline") if isinstance(materials_bundle.get("outline"), dict) else {}),
            "tone": (materials_bundle.get("tone") if isinstance(materials_bundle.get("tone"), dict) else {}),
        },
        "execution": build_execution_from_materials_bundle(materials_bundle, settings_meta=settings_meta),
        "risk": {
            "risks": [],
            "open_questions": [],
        },
        "changelog": [],
    }

    draft_path = os.path.join(paths["drafts"], f"materials_pack.{ver}.json")
    write_json(draft_path, draft_obj)

    if isinstance(agent_review, dict):
        write_json(os.path.join(paths["reviews"], f"agent_review.{ver}.json"), agent_review)

    return ver, draft_path


def freeze_materials_pack(
    *,
    project_dir: str,
    draft_version: str,
    draft_obj: Dict[str, Any],
    human_review: Dict[str, Any],
) -> Tuple[str, str, str]:
    """
    冻结 draft 为 frozen：
    - frozen/materials_pack.frozen.vNNN.json（NNN 与 draft_version 一致）
    - anchors/anchors.vNNN.json
    - index.json 指向 current_frozen_version
    返回：(frozen_version, frozen_path, anchors_path)
    """
    paths = ensure_materials_pack_dirs(project_dir)
    frozen_version = str(draft_version)
    frozen_path = os.path.join(paths["frozen"], f"materials_pack.frozen.{frozen_version}.json")

    # 增补冻结元信息
    obj = dict(draft_obj or {})
    meta = obj.get("meta") if isinstance(obj.get("meta"), dict) else {}
    meta = dict(meta)
    meta["frozen_at"] = datetime.now().isoformat(timespec="seconds")
    meta["frozen_version"] = frozen_version
    obj["meta"] = meta

    # anchors + id 补齐
    res = ensure_anchor_ids_and_build_index(obj)
    frozen_obj = res.get("frozen_pack") if isinstance(res.get("frozen_pack"), dict) else obj
    anchors_obj = res.get("anchors") if isinstance(res.get("anchors"), dict) else {"anchors": {}}

    write_json(frozen_path, frozen_obj)
    anchors_path = os.path.join(paths["anchors"], f"anchors.{frozen_version}.json")
    write_json(anchors_path, anchors_obj)

    # 写人审记录
    write_json(os.path.join(paths["reviews"], f"human_review.{frozen_version}.json"), human_review)

    # 更新 index
    idx = read_json(paths["index"]) or {}
    idx["current_frozen_version"] = frozen_version
    idx["updated_at"] = datetime.now().isoformat(timespec="seconds")
    write_json(paths["index"], idx)

    return frozen_version, frozen_path, anchors_path


def load_current_frozen_materials_pack(project_dir: str) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """
    返回：(version, frozen_pack_obj, anchors_obj)
    """
    paths = ensure_materials_pack_dirs(project_dir)
    idx = read_json(paths["index"]) or {}
    ver = str(idx.get("current_frozen_version") or "").strip()
    if not ver:
        return "", {}, {}
    frozen_path = os.path.join(paths["frozen"], f"materials_pack.frozen.{ver}.json")
    anchors_path = os.path.join(paths["anchors"], f"anchors.{ver}.json")
    return ver, (read_json(frozen_path) or {}), (read_json(anchors_path) or {})


def snapshot_frozen_to_run(output_dir: str, *, frozen_version: str, frozen_obj: Dict[str, Any], anchors_obj: Dict[str, Any]) -> str:
    """
    将当前冻结材料包快照到 outputs/current/materials_snapshot（便于追溯本次会话的上游口径）。
    返回 snapshot_dir
    """
    snap_dir = os.path.join(output_dir, "materials_snapshot")
    os.makedirs(snap_dir, exist_ok=True)
    write_json(os.path.join(snap_dir, f"materials_pack.frozen.{frozen_version}.json"), frozen_obj)
    write_json(os.path.join(snap_dir, f"anchors.{frozen_version}.json"), anchors_obj if isinstance(anchors_obj, dict) else {})
    write_text(os.path.join(snap_dir, "current_frozen_version.txt"), str(frozen_version))
    return snap_dir


def frozen_pack_to_materials_bundle(frozen_pack: Dict[str, Any], *, idea: str = "") -> Dict[str, Any]:
    """
    将冻结材料包（四层结构）转换为现有 writer/editor 期望的 materials_bundle 形态。
    保持向后兼容：writer/editor 主要依赖 world/characters/outline/tone/materials_pack。
    """
    fp = frozen_pack if isinstance(frozen_pack, dict) else {}
    canon = fp.get("canon") if isinstance(fp.get("canon"), dict) else {}
    planning = fp.get("planning") if isinstance(fp.get("planning"), dict) else {}
    execution = fp.get("execution") if isinstance(fp.get("execution"), dict) else {}
    risk = fp.get("risk") if isinstance(fp.get("risk"), dict) else {}

    # materials_pack：沿用 execution.decisions/checklists/risks 与 planning/tone/world_building 等的最小并集
    # （当前 materials_pack 在现有系统里是单独字段；冻结包里我们把它视为 execution 的一部分）
    pack = fp.get("materials_pack") if isinstance(fp.get("materials_pack"), dict) else {}
    if not pack:
        pack = {
            "version": "pack_v1",
            "logline": "",
            "creative_brief": "",
            "pacing_plan": "",
            "arc_plan": [],
            "world_building": "",
            "growth_system": "",
            "style_guide": {},
            "conflicts_found": [],
            "decisions": execution.get("decisions", []) if isinstance(execution.get("decisions"), list) else [],
            "checklists": execution.get("checklists", {}) if isinstance(execution.get("checklists"), dict) else {},
            "risks": execution.get("risks", []) if isinstance(execution.get("risks"), list) else [],
        }

    return {
        "project_name": str(fp.get("meta", {}).get("project_name", "") if isinstance(fp.get("meta"), dict) else "").strip(),
        "idea": str(idea or "").strip(),
        "world": canon.get("world", {}) if isinstance(canon.get("world"), dict) else {},
        "characters": canon.get("characters", {}) if isinstance(canon.get("characters"), dict) else {},
        "outline": planning.get("outline", {}) if isinstance(planning.get("outline"), dict) else {},
        "tone": planning.get("tone", {}) if isinstance(planning.get("tone"), dict) else {},
        "materials_pack": pack,
        # 新增 execution/risk 层供 prompt digest 使用
        "constraints": execution.get("constraints", {}) if isinstance(execution.get("constraints"), dict) else {},
        "glossary": execution.get("glossary", {}) if isinstance(execution.get("glossary"), dict) else {},
        "open_questions": risk.get("open_questions", []) if isinstance(risk.get("open_questions"), list) else [],
    }


