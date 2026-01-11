from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Optional

from storage import write_json, safe_filename
from storage import read_json
from materials_freeze import ensure_materials_pack_dirs, freeze_materials_pack, load_current_frozen_materials_pack


def _now_compact() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def ensure_changes_dirs(project_dir: str) -> Dict[str, str]:
    """
    确保项目级 changes 目录存在：
    outputs/projects/<project>/changes/proposals/CP-.../
    """
    base = os.path.join(project_dir, "changes")
    proposals = os.path.join(base, "proposals")
    os.makedirs(proposals, exist_ok=True)
    return {"base": base, "proposals": proposals}


def new_proposal_id(*, now: Optional[str] = None, seq: int = 1) -> str:
    """
    生成提案 ID：CP-YYYYMMDD-NNNN
    seq 默认 1；实际落盘时会根据目录扫描避免冲突。
    """
    if not now:
        now = datetime.now().strftime("%Y%m%d")
    return f"CP-{now}-{int(seq):04d}"


def _next_seq(proposals_dir: str, *, day: str) -> int:
    mx = 0
    if os.path.exists(proposals_dir):
        for name in os.listdir(proposals_dir):
            if not name.startswith(f"CP-{day}-"):
                continue
            tail = name.split("-")[-1]
            if tail.isdigit():
                mx = max(mx, int(tail))
    return mx + 1


def create_change_proposal_skeleton(
    *,
    project_dir: str,
    chapter_index: int,
    materials_frozen_version: str,
    reason: str,
    anchors: Optional[list[str]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    创建变更提案目录骨架（最小可用）并写入占位文件：
    - proposal.json
    - advisor_review.json
    - human_decision.json
    - migration_plan.json
    - migration_log.json
    - diff.patch.json（可选占位）
    返回：{proposal_id, dir, files{...}}
    """
    dirs = ensure_changes_dirs(project_dir)
    day = datetime.now().strftime("%Y%m%d")
    seq = _next_seq(dirs["proposals"], day=day)
    pid = new_proposal_id(now=day, seq=seq)
    pdir = os.path.join(dirs["proposals"], pid)
    os.makedirs(pdir, exist_ok=True)

    proposal = {
        "proposal_id": pid,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "project_dir": project_dir,
        "trigger": {
            "chapter_index": int(chapter_index),
            "materials_frozen_version": str(materials_frozen_version or ""),
            "anchors": anchors or [],
            "reason": str(reason or "").strip(),
        },
        # 提案正文（由总编/顾问补齐）
        "what": {"paths": [], "description": ""},
        "why": {"evidence": []},
        "impact": {"chapters": [], "memory": {"needs_rollback": False, "notes": ""}},
        "migration_plan": {"steps": []},
        "alternatives": [],
        "extra": extra or {},
    }
    write_json(os.path.join(pdir, "proposal.json"), proposal)
    write_json(os.path.join(pdir, "advisor_review.json"), {"proposal_id": pid, "status": "pending", "notes": "", "created_at": ""})
    write_json(os.path.join(pdir, "human_decision.json"), {"proposal_id": pid, "status": "pending", "decision": "", "notes": "", "created_at": ""})
    write_json(os.path.join(pdir, "migration_plan.json"), {"proposal_id": pid, "status": "pending", "steps": [], "created_at": ""})
    write_json(os.path.join(pdir, "migration_log.json"), {"proposal_id": pid, "status": "pending", "logs": [], "created_at": ""})
    write_json(os.path.join(pdir, "diff.patch.json"), {"proposal_id": pid, "patches": []})

    return {
        "proposal_id": pid,
        "dir": pdir,
        "files": {
            "proposal": os.path.join(pdir, "proposal.json"),
            "advisor_review": os.path.join(pdir, "advisor_review.json"),
            "human_decision": os.path.join(pdir, "human_decision.json"),
            "migration_plan": os.path.join(pdir, "migration_plan.json"),
            "migration_log": os.path.join(pdir, "migration_log.json"),
            "diff": os.path.join(pdir, "diff.patch.json"),
        },
    }


def get_proposal_dir(project_dir: str, proposal_id: str) -> str:
    d = ensure_changes_dirs(project_dir)
    pid = safe_filename(str(proposal_id or "").strip(), fallback="CP-UNKNOWN")
    return os.path.join(d["proposals"], pid)


def load_proposal(project_dir: str, proposal_id: str) -> Dict[str, Any]:
    pdir = get_proposal_dir(project_dir, proposal_id)
    return read_json(os.path.join(pdir, "proposal.json")) or {}


def write_advisor_review(project_dir: str, proposal_id: str, *, notes: str, status: str = "reviewed") -> str:
    pdir = get_proposal_dir(project_dir, proposal_id)
    path = os.path.join(pdir, "advisor_review.json")
    obj = read_json(path) or {"proposal_id": proposal_id}
    obj["proposal_id"] = proposal_id
    obj["status"] = str(status or "reviewed")
    obj["notes"] = str(notes or "").strip()
    obj["created_at"] = datetime.now().isoformat(timespec="seconds")
    write_json(path, obj)
    return path


def write_human_decision(project_dir: str, proposal_id: str, *, decision: str, notes: str) -> str:
    pdir = get_proposal_dir(project_dir, proposal_id)
    path = os.path.join(pdir, "human_decision.json")
    obj = read_json(path) or {"proposal_id": proposal_id}
    obj["proposal_id"] = proposal_id
    obj["status"] = "done"
    obj["decision"] = str(decision or "").strip()
    obj["notes"] = str(notes or "").strip()
    obj["created_at"] = datetime.now().isoformat(timespec="seconds")
    write_json(path, obj)
    return path


def append_migration_log(project_dir: str, proposal_id: str, *, line: str) -> str:
    pdir = get_proposal_dir(project_dir, proposal_id)
    path = os.path.join(pdir, "migration_log.json")
    obj = read_json(path) or {"proposal_id": proposal_id, "logs": []}
    logs = obj.get("logs") if isinstance(obj.get("logs"), list) else []
    logs.append({"ts": datetime.now().isoformat(timespec="seconds"), "line": str(line or "").strip()})
    obj["proposal_id"] = proposal_id
    obj["status"] = "in_progress"
    obj["logs"] = logs
    if not obj.get("created_at"):
        obj["created_at"] = datetime.now().isoformat(timespec="seconds")
    write_json(path, obj)
    return path


def create_refreeze_draft_from_current_frozen(project_dir: str, proposal_id: str) -> Dict[str, Any]:
    """
    以当前生效 frozen 材料包为基底，创建一个可编辑的 draft（projects/<project>/materials/drafts/）。
    迁移/修改由人手工编辑该 draft JSON 完成，随后调用 finalize_refreeze_from_draft() 冻结。
    """
    ver, frozen_obj, _anchors = load_current_frozen_materials_pack(project_dir)
    if not ver or not frozen_obj:
        raise ValueError("当前项目没有可用的 frozen 材料包（index.json.current_frozen_version 为空）")

    mdirs = ensure_materials_pack_dirs(project_dir)
    # draft 版本号：沿用 materials_freeze 的逻辑（扫描 drafts/materials_pack.vNNN.json）
    # 这里直接复用 next_v 逻辑：找最大 vNNN + 1
    mx = 0
    for name in os.listdir(mdirs["drafts"]):
        if not name.startswith("materials_pack.v"):
            continue
        parts = name.split(".")
        for p in parts:
            if p.startswith("v") and p[1:].isdigit():
                mx = max(mx, int(p[1:]))
    draft_version = f"v{mx+1:03d}"
    draft_path = os.path.join(mdirs["drafts"], f"materials_pack.{draft_version}.json")

    # draft：基于 frozen 拷贝，并记录来源
    obj = dict(frozen_obj)
    meta = obj.get("meta") if isinstance(obj.get("meta"), dict) else {}
    meta = dict(meta)
    meta["derived_from_frozen_version"] = str(ver)
    meta["derived_from_proposal_id"] = str(proposal_id)
    meta["created_at"] = datetime.now().isoformat(timespec="seconds")
    meta["version"] = draft_version
    # 清理冻结字段（避免误导）
    meta.pop("frozen_at", None)
    meta.pop("frozen_version", None)
    obj["meta"] = meta

    write_json(draft_path, obj)

    # 回填 proposal.json 的 refreeze 信息
    pdir = get_proposal_dir(project_dir, proposal_id)
    prop_path = os.path.join(pdir, "proposal.json")
    prop = read_json(prop_path) or {"proposal_id": proposal_id}
    prop["proposal_id"] = proposal_id
    prop.setdefault("refreeze", {})
    if isinstance(prop.get("refreeze"), dict):
        prop["refreeze"]["draft_version"] = draft_version
        prop["refreeze"]["draft_path"] = draft_path.replace("\\", "/")
        prop["refreeze"]["base_frozen_version"] = str(ver)
        prop["refreeze"]["created_at"] = datetime.now().isoformat(timespec="seconds")
    write_json(prop_path, prop)

    return {"proposal_id": proposal_id, "draft_version": draft_version, "draft_path": draft_path, "base_frozen_version": ver}


def finalize_refreeze_from_draft(project_dir: str, proposal_id: str, *, draft_version: str, human_notes: str = "") -> Dict[str, Any]:
    """
    将指定 draft 冻结为新的 frozen 版本，并更新 materials/index.json。
    """
    mdirs = ensure_materials_pack_dirs(project_dir)
    draft_path = os.path.join(mdirs["drafts"], f"materials_pack.{draft_version}.json")
    draft_obj = read_json(draft_path) or {}
    if not draft_obj:
        raise FileNotFoundError(f"未找到 draft 或内容为空：{draft_path}")

    # 复用 materials_freeze 的 freeze_materials_pack（会生成 anchors 并更新 index.json）
    human_review = {
        "version": str(draft_version),
        "decision": "approve_and_refreeze",
        "notes": str(human_notes or "").strip(),
        "proposal_id": str(proposal_id),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    new_ver, frozen_path, anchors_path = freeze_materials_pack(
        project_dir=project_dir,
        draft_version=str(draft_version),
        draft_obj=draft_obj,
        human_review=human_review,
    )

    # 回填 proposal.json 的 refreeze 信息
    pdir = get_proposal_dir(project_dir, proposal_id)
    prop_path = os.path.join(pdir, "proposal.json")
    prop = read_json(prop_path) or {"proposal_id": proposal_id}
    prop["proposal_id"] = proposal_id
    prop.setdefault("refreeze", {})
    if isinstance(prop.get("refreeze"), dict):
        prop["refreeze"]["new_frozen_version"] = new_ver
        prop["refreeze"]["frozen_path"] = frozen_path.replace("\\", "/")
        prop["refreeze"]["anchors_path"] = anchors_path.replace("\\", "/")
        prop["refreeze"]["completed_at"] = datetime.now().isoformat(timespec="seconds")
    write_json(prop_path, prop)

    return {
        "proposal_id": proposal_id,
        "new_frozen_version": new_ver,
        "frozen_path": frozen_path,
        "anchors_path": anchors_path,
        "draft_version": draft_version,
    }


