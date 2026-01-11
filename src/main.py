from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import replace
from datetime import datetime

from llm import try_get_chat_llm
from state import StoryState
from storage import (
    archive_run,
    apply_canon_suggestions,
    apply_materials_suggestions,
    ensure_canon_files,
    ensure_memory_dirs,
    ensure_materials_files,
    get_project_dir,
    get_max_chapter_memory_index,
    make_current_dir,
    load_materials_bundle,
    load_canon_bundle,
    preview_materials_suggestions,
    preview_canon_suggestions,
    read_canon_suggestions_from_dir,
    read_materials_suggestions_from_dir,
    read_json,
    write_json,
    write_text,
)
from agents.planner import planner_agent
from agents.canon_init import canon_init_agent
from agents.architect import architect_agent
from agents.character_director import character_director_agent
from agents.screenwriter import screenwriter_agent
from agents.tone import tone_agent
from agents.materials_aggregator import materials_aggregator_agent
from agents.materials_pack_loop import materials_pack_loop_agent
from agents.materials_init import materials_init_agent
from materials_freeze import (
    create_materials_pack_draft,
    freeze_materials_pack,
    load_current_frozen_materials_pack,
    snapshot_frozen_to_run,
    frozen_pack_to_materials_bundle,
    count_open_question_blockers,
)
from human_cli import (
    prompt_choice,
    prompt_multiline,
    print_json_preview,
    print_chapter_review_card,
    print_materials_review_card,
)
from change_proposals import create_change_proposal_skeleton
from change_proposals import (
    write_advisor_review,
    write_human_decision,
    append_migration_log,
    create_refreeze_draft_from_current_frozen,
    finalize_refreeze_from_draft,
)
from advisor import advisor_digest_line, build_advisor_report
from settings import load_settings
from workflow import build_chapter_app
from debug_log import RunLogger, load_events, build_call_graph_mermaid_by_chapter
from arc_summary import generate_arc_summary, write_arc_summary
from materials import pick_outline_for_chapter, build_materials_bundle


def main():
    # Windows 控制台默认编码可能导致中文乱码；显式切换到 UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="LangGraph 小说MVP：策划一次 -> 多章节写作/审核（可返工）")
    parser.add_argument("--config", type=str, default="config.toml", help="配置文件路径（TOML，可选）")
    parser.add_argument("--idea", type=str, default="", help="用户点子（覆盖配置；留空则使用配置/默认值）")
    parser.add_argument("--idea-file", type=str, default="", help="从文件读取用户点子（UTF-8）。优先级最高，覆盖 --idea/config/env")
    parser.add_argument("--target-words", type=int, default=None, help="每章目标字数（覆盖配置）")
    parser.add_argument("--chapters", type=int, default=None, help="章节数（覆盖配置）")
    parser.add_argument("--max-rewrites", type=int, default=None, help="每章最多返工次数（覆盖配置）")
    parser.add_argument("--output-base", type=str, default="", help="输出根目录（覆盖配置）")
    parser.add_argument("--stage", type=str, default="", help="阶段名（用于归档，覆盖配置）")
    parser.add_argument("--memory-recent-k", type=int, default=None, help="注入最近章节记忆数量（只注入梗概）")
    parser.add_argument(
        "--include-unapproved-memories",
        action="store_true",
        help="注入记忆时包含“未审核通过”的章节（默认不包含，避免污染；仅调试/强需求时使用）",
    )
    parser.add_argument("--style", type=str, default="", help="用户文风覆盖（本次运行注入 writer/editor；不自动写入 style.md）")
    parser.add_argument("--style-file", type=str, default="", help="从文件读取用户文风覆盖（UTF-8），优先级高于 --style")
    parser.add_argument("--paragraph-rules", type=str, default="", help="段落/结构规则（例如每段<=120字、多对话、少旁白等）")
    parser.add_argument("--editor-min-issues", type=int, default=None, help="主编拒稿时至少给出多少条 issues（默认2）")
    parser.add_argument("--editor-retry-on-invalid", type=int, default=None, help="主编 JSON 不合法/issue过少时自动修复重试次数（默认1）")
    parser.add_argument("--stop-on-error", action="store_true", help="遇到单章异常时立即中止（默认：记录错误并继续跑后续章节）")
    parser.add_argument("--llm-max-attempts", type=int, default=None, help="LLM调用最大重试次数（默认3，抗限流/网络抖动）")
    parser.add_argument("--llm-retry-base-sleep-s", type=float, default=None, help="LLM重试基础退避秒数（默认1.0）")
    parser.add_argument("--disable-arc-summary", action="store_true", help="禁用分卷/Arc摘要（默认启用，减少150章规模的记忆膨胀与矛盾）")
    parser.add_argument("--arc-every-n", type=int, default=None, help="每N章生成一个Arc摘要（默认10；设为0表示不生成）")
    parser.add_argument("--arc-recent-k", type=int, default=None, help="写作/审稿注入最近K个Arc摘要（默认2）")
    parser.add_argument(
        "--auto-apply-updates",
        type=str,
        default="",
        help="无人值守：自动应用沉淀建议（off|safe）。safe 仅应用低风险补丁（world.notes/style.md + tone/outline 幂等追加）。",
    )
    parser.add_argument("--project", type=str, default="", help="项目名（用于续写/固定 projects/<project>；建议与现有目录名一致）")
    parser.add_argument("--resume", action="store_true", help="续写模式：优先复用 projects/<project>/project_meta.json，并自动从下一章开始")
    parser.add_argument("--start-chapter", type=int, default=None, help="起始章节号（例如 101）。不填则 resume 模式自动推断为已有最大章+1")
    parser.add_argument("--archive", action="store_true", help="运行结束后自动归档（默认不归档，便于先review）")
    parser.add_argument(
        "--archive-confirm",
        action="store_true",
        help="归档前需要你确认（推荐开启；可配合 --yes 跳过确认）",
    )
    parser.add_argument(
        "--archive-only",
        action="store_true",
        help="只归档当前 outputs/current（用于你review之后手动入库）；不会执行生成流程",
    )
    parser.add_argument(
        "--apply-canon-suggestions",
        action="store_true",
        help="运行结束后，读取 outputs/current/chapters/*canon_suggestions.json 并在你确认后应用到 projects/<project>/canon（默认不自动应用）",
    )
    parser.add_argument(
        "--apply-canon-only",
        action="store_true",
        help="只应用当前 outputs/current 的 canon_suggestions（不运行生成流程）。用于你review后再执行。",
    )
    parser.add_argument(
        "--apply-materials-suggestions",
        action="store_true",
        help="运行结束后，读取 outputs/current/chapters/*materials_update_suggestions.json 并在你确认后应用到 projects/<project>/materials（默认不自动应用）",
    )
    parser.add_argument(
        "--apply-materials-only",
        action="store_true",
        help="只应用当前 outputs/current 的 materials_update_suggestions（不运行生成流程）。用于你review后再执行。",
    )
    parser.add_argument("--dry-run", action="store_true", help="只预览将要执行的变更，不实际写入（用于 apply-canon / archive 确认）")
    parser.add_argument("--yes", action="store_true", help="跳过所有确认（危险：会直接应用/归档）。适合自动化")
    parser.add_argument("--llm-mode", type=str, default="", help="运行模式：template / llm / auto（覆盖配置）")
    parser.add_argument("--debug", action="store_true", help="开启debug日志（写入debug.jsonl与call_graph.md）")
    parser.add_argument("--advisor", action="store_true", help="启用顾问审计：每章生成 chapters/XXX.advisor.json（规则化一致性检查，不自动猜 anchors）")
    # 重申：对 outputs/current 的既有章节做“同等要求”的审稿+改写（逐章）
    parser.add_argument(
        "--restate",
        action="store_true",
        help="重申模式：以隔离工作区运行（rewrites/*），逐章审稿并按需改写（通过则不改写）",
    )
    parser.add_argument("--restate-max-reviews", type=int, default=3, help="重申：每章最多审稿次数（>=2；包含最终验收审稿）")
    parser.add_argument("--restate-start", type=int, default=None, help="重申：只处理从该章开始（含）")
    parser.add_argument("--restate-end", type=int, default=None, help="重申：只处理到该章结束（含）")
    parser.add_argument("--rewrite-file", type=str, default="", help="重写/重申：用户额外指导意见文件（UTF-8），注入 writer/editor")
    parser.add_argument(
        "--materials-only",
        action="store_true",
        help="只运行材料包阶段并进入冻结门禁（不进入章节写作）。用于总编先把材料包打磨冻结。",
    )
    # 变更提案（项目级操作，不进入生成流程）
    parser.add_argument("--proposal-id", type=str, default="", help="变更提案ID（例如 CP-20260108-0001）。需配合 --project 使用。")
    parser.add_argument("--proposal-advisor-review", action="store_true", help="顾问审：写入 changes/proposals/<id>/advisor_review.json")
    parser.add_argument("--proposal-approve", action="store_true", help="总编审批通过：写入 changes/proposals/<id>/human_decision.json")
    parser.add_argument("--proposal-reject", action="store_true", help="总编驳回：写入 changes/proposals/<id>/human_decision.json")
    parser.add_argument("--proposal-migration-log", action="store_true", help="追加一条迁移日志到 migration_log.json")
    parser.add_argument("--proposal-create-draft", action="store_true", help="从当前 frozen 生成一个可编辑 draft，并回填 proposal.refreeze.draft_version")
    parser.add_argument("--proposal-refreeze", action="store_true", help="将指定 draft 冻结为新 frozen（需 --proposal-draft-version）")
    parser.add_argument("--proposal-draft-version", type=str, default="", help="提案 refreeze 指定的 draft 版本（例如 v003）")
    args = parser.parse_args()

    # 统一相对路径解析基准：
    # - 如果传入的 config（默认 config.toml）在 CWD 不存在，但在 repo 根目录存在，则使用 repo 根目录的 config
    # - 这样无论从哪里运行（例如从 src/ 目录运行），outputs 也会稳定落在 repo 根目录下
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    def _resolve_config_path(p: str) -> str:
        p = (p or "").strip() or "config.toml"
        if os.path.isabs(p):
            return p
        cand_cwd = os.path.abspath(p)
        if os.path.exists(cand_cwd):
            return cand_cwd
        cand_repo = os.path.join(repo_root, p)
        if os.path.exists(cand_repo):
            return cand_repo
        # 不存在时仍返回 CWD 下的绝对路径，便于后续报错信息一致
        return cand_cwd

    config_abs = _resolve_config_path(args.config)
    config_dir = os.path.dirname(config_abs) if os.path.exists(config_abs) else repo_root

    # idea 支持从文件读取（优先级最高）
    idea_from_file: str | None = None
    idea_file_path: str = ""
    def _resolve_user_path(p: str, *, base_dir: str) -> str:
        """
        解析用户输入路径：
        - 绝对路径：直接使用
        - 相对路径：优先按当前工作目录(CWD)解析；若不存在，再按 base_dir（通常为 config_dir）解析
        """
        p = (p or "").strip()
        if not p:
            return ""
        if os.path.isabs(p):
            return p
        cand_cwd = os.path.abspath(p)
        if os.path.exists(cand_cwd):
            return cand_cwd
        cand_base = os.path.abspath(os.path.join(base_dir, p))
        return cand_base

    if args.idea_file and args.idea_file.strip():
        idea_path = _resolve_user_path(args.idea_file.strip(), base_dir=config_dir)
        if not os.path.exists(idea_path):
            raise FileNotFoundError(f"未找到 idea 文件：{idea_path}")
        # 支持 UTF-8 BOM
        with open(idea_path, "r", encoding="utf-8-sig") as f:
            idea_from_file = f.read().strip()
        idea_file_path = idea_path
        if not idea_from_file:
            raise ValueError(f"idea 文件内容为空：{idea_path}")

    # style 支持从文件读取（优先级高于 --style）
    style_from_file: str | None = None
    if args.style_file and args.style_file.strip():
        style_path = _resolve_user_path(args.style_file.strip(), base_dir=config_dir)
        if not os.path.exists(style_path):
            raise FileNotFoundError(f"未找到 style 文件：{style_path}")
        with open(style_path, "r", encoding="utf-8-sig") as f:
            style_from_file = f.read().strip()
        if style_from_file is not None and not style_from_file.strip():
            style_from_file = ""

    # rewrite 指导：从文件读取（用于 --restate；不写入 Canon）
    rewrite_from_file: str | None = None
    rewrite_file_path: str = ""
    if args.rewrite_file and args.rewrite_file.strip():
        rp = _resolve_user_path(args.rewrite_file.strip(), base_dir=config_dir)
        if not os.path.exists(rp):
            raise FileNotFoundError(f"未找到 rewrite 指导文件：{rp}")
        with open(rp, "r", encoding="utf-8-sig") as f:
            rewrite_from_file = f.read().strip()
        rewrite_file_path = rp
        if rewrite_from_file is not None and not rewrite_from_file.strip():
            rewrite_from_file = ""

    settings = load_settings(
        config_abs,
        idea=idea_from_file if idea_from_file is not None else args.idea,
        output_base=args.output_base,
        stage=args.stage,
        memory_recent_k=args.memory_recent_k,
        include_unapproved_memories=bool(args.include_unapproved_memories),
        style_override=style_from_file if style_from_file is not None else args.style,
        paragraph_rules=args.paragraph_rules,
        editor_min_issues=args.editor_min_issues,
        editor_retry_on_invalid=args.editor_retry_on_invalid,
        llm_max_attempts=args.llm_max_attempts,
        llm_retry_base_sleep_s=args.llm_retry_base_sleep_s,
        enable_arc_summary=(False if bool(args.disable_arc_summary) else None),
        arc_every_n=args.arc_every_n,
        arc_recent_k=args.arc_recent_k,
        auto_apply_updates=args.auto_apply_updates,
        target_words=args.target_words,
        chapters=args.chapters,
        max_rewrites=args.max_rewrites,
    )
    if args.llm_mode.strip():
        settings = replace(settings, llm_mode=args.llm_mode.strip().lower())
    if args.debug:
        settings = replace(settings, debug=True)
    # output_base 若为相对路径，则优先相对 config.toml 所在目录解析；若 config 不存在则相对 repo 根目录解析。
    output_base = settings.output_base
    if not os.path.isabs(output_base):
        output_base = os.path.join(config_dir, output_base)
    output_base = os.path.abspath(output_base)
    os.makedirs(output_base, exist_ok=True)

    if args.resume and not args.project.strip():
        raise ValueError("续写模式必须指定 --project（用于定位 outputs/projects/<project>）")

    def _confirm(msg: str) -> bool:
        if args.yes:
            return True
        ans = input(msg).strip().lower()
        return ans in ("y", "yes", "是", "确认")

    # ============================
    # 变更提案 CLI（项目级操作）
    # ============================
    if args.proposal_id and (
        args.proposal_advisor_review
        or args.proposal_approve
        or args.proposal_reject
        or args.proposal_migration_log
        or args.proposal_create_draft
        or args.proposal_refreeze
    ):
        if not args.project.strip():
            raise ValueError("变更提案操作必须指定 --project（用于定位 projects/<project>/changes）")
        project_dir = get_project_dir(output_base, args.project.strip())
        pid = str(args.proposal_id or "").strip()

        if args.proposal_advisor_review:
            notes = prompt_multiline("请输入顾问审意见（风险/影响面/迁移雷区；输入 . 结束）：", end_token=".")
            path = write_advisor_review(project_dir, pid, notes=notes, status="reviewed")
            print(f"\n已写入顾问审：{path}")
            return

        if args.proposal_approve or args.proposal_reject:
            decision = "approve" if bool(args.proposal_approve) else "reject"
            notes = prompt_multiline(f"请输入总编{('通过' if decision=='approve' else '驳回')}说明（输入 . 结束）：", end_token=".")
            path = write_human_decision(project_dir, pid, decision=decision, notes=notes)
            print(f"\n已写入总编决策：{path}")
            return

        if args.proposal_migration_log:
            line = prompt_multiline("请输入迁移日志（一段即可；输入 . 结束）：", end_token=".")
            path = append_migration_log(project_dir, pid, line=line)
            print(f"\n已追加迁移日志：{path}")
            return

        if args.proposal_create_draft:
            res = create_refreeze_draft_from_current_frozen(project_dir, pid)
            print("\n=== 已生成 refreeze draft（请手工编辑该 JSON 完成迁移）===")
            print(f"- base_frozen_version：{res.get('base_frozen_version')}")
            print(f"- draft_version：{res.get('draft_version')}")
            print(f"- draft_path：{res.get('draft_path')}")
            print("\n编辑完成后执行：")
            print(f"  python src/main.py --project \"{args.project}\" --proposal-id \"{pid}\" --proposal-refreeze --proposal-draft-version \"{res.get('draft_version')}\"")
            return

        if args.proposal_refreeze:
            dv = str(args.proposal_draft_version or "").strip()
            if not dv:
                raise ValueError("--proposal-refreeze 需要 --proposal-draft-version（例如 v003）")
            notes = prompt_multiline("（可选）再冻结备注（输入 . 结束）：", end_token=".")
            out = finalize_refreeze_from_draft(project_dir, pid, draft_version=dv, human_notes=notes)
            print("\n=== 再冻结完成 ===")
            print(f"- new_frozen_version：{out.get('new_frozen_version')}")
            print(f"- frozen_path：{out.get('frozen_path')}")
            print(f"- anchors_path：{out.get('anchors_path')}")
            return

    def _human_gate_materials(*, project_dir: str, planned_state: StoryState) -> tuple[bool, str, dict, dict]:
        """
        材料包门禁（总编必审）：
        - 写入 drafts/materials_pack.vNNN.json
        - 总编选择 freeze / request_changes / quit
        返回：(frozen_ok, frozen_version, frozen_obj, anchors_obj)
        """
        # 绑定总编指令（若上次留下了材料包指令，可手动复用：从当前 output_dir 读取 materials_change_requests.txt）
        mb = planned_state.get("materials_bundle") if isinstance(planned_state.get("materials_bundle"), dict) else {}
        canon_bundle = {}
        try:
            canon_bundle = load_canon_bundle(project_dir)
        except Exception:
            canon_bundle = {"world": {}, "characters": {}, "timeline": {}, "style": ""}

        # 产出一个 draft 版本（项目级）
        settings_meta = {
            "target_words": int(planned_state.get("target_words", settings.gen.target_words) or settings.gen.target_words),
            "writer_min_ratio": float(getattr(settings, "writer_min_ratio", 0.7)),
            "writer_max_ratio": float(getattr(settings, "writer_max_ratio", 1.5)),
            "style_override": str(planned_state.get("style_override", "") or ""),
            "paragraph_rules": str(planned_state.get("paragraph_rules", "") or ""),
        }
        ver, draft_path = create_materials_pack_draft(
            project_dir=project_dir,
            materials_bundle=mb if isinstance(mb, dict) else {},
            canon_bundle=canon_bundle if isinstance(canon_bundle, dict) else {},
            settings_meta=settings_meta,
            agent_review=planned_state.get("materials_pack_loop_last_review") if isinstance(planned_state.get("materials_pack_loop_last_review"), dict) else None,
        )

        print("\n=== 材料包门禁（总编必审）===")
        print(f"- draft 已写入：{draft_path}")
        print("- 建议先查看 draft（含 canon/planning/execution/risk 四层）再决定是否冻结。")
        try:
            obj = read_json(draft_path) or {}
        except Exception:
            obj = {}
        # 默认打印 digest 审阅卡（更适合“每次都要审”的节奏）
        try:
            cur_ver, _fobj, _aobj = load_current_frozen_materials_pack(project_dir)
        except Exception:
            cur_ver = ""
        if obj:
            try:
                print_materials_review_card(
                    draft_obj=obj,
                    draft_path=draft_path,
                    project_dir=project_dir,
                    current_frozen_version=str(cur_ver or ""),
                )
            except Exception:
                print("\n--- draft 预览（截断）---")
                print_json_preview(obj, max_chars=3500)

        while True:
            action = prompt_choice(
                "请选择动作",
                choices={
                    "f": "冻结通过（生成 materials_pack.frozen.vNNN + anchors.vNNN，打开写作门禁）",
                    "r": "退回修改（写入总编指令单，后续重新跑材料包收敛）",
                    "v": "查看 draft 全文 JSON（materials_pack.vNNN.json）",
                    "d": "重新显示 digest",
                    "q": "退出（不冻结，不进入写作）",
                },
                # 默认强人审；--yes 用于自动化/回归测试（明确跳过门禁交互）
                default=("f" if bool(args.yes) else "q"),
            )
            if action == "v":
                print("\n--- draft 全文 JSON ---")
                print_json_preview(obj, max_chars=20000)
                continue
            if action == "d":
                try:
                    print_materials_review_card(
                        draft_obj=obj,
                        draft_path=draft_path,
                        project_dir=project_dir,
                        current_frozen_version=str(cur_ver or ""),
                    )
                except Exception:
                    pass
                continue
            break
        if action == "q":
            return False, "", {}, {}
        if action == "r":
            req = prompt_multiline("请输入材料包修改指令单（尽量具体，可引用 DEC/CON/GLO/…；用于驱动下一轮收敛）：", end_token=".")
            # 写入总编人审记录（项目级）
            human_review = {
                "version": ver,
                "decision": "request_changes",
                "notes": req,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            # 记录到 reviews/human_review.vNNN.json
            ensure_materials_files(project_dir)  # 保底
            # 同时把指令放回 state，便于本次 run 的后续节点（若继续跑）使用
            planned_state["materials_human_requests"] = req
            # 直接把人审记录落盘到 project materials/reviews（与 freeze 逻辑一致）
            try:
                from materials_freeze import ensure_materials_pack_dirs

                p = ensure_materials_pack_dirs(project_dir)
                write_json(os.path.join(p["reviews"], f"human_review.{ver}.json"), human_review)
            except Exception:
                pass
            print("\n已记录修改指令。请重新运行一次材料包阶段以收敛后再冻结。")
            return False, "", {}, {}

        # action == "f"
        # 冻结前 DoD 门禁：blocker open_questions 必须为 0
        try:
            blockers, picked = count_open_question_blockers(obj if isinstance(obj, dict) else {})
        except Exception:
            blockers, picked = 0, []
        if blockers > 0:
            print("\n材料包存在 blocker open_questions，禁止冻结。请先回答/降级这些问题：")
            for i, it in enumerate(picked[:10], start=1):
                q = str(it.get("question", "") or it.get("q", "") or it.get("topic", "") or "").strip()
                impact = str(it.get("impact", "") or "").strip()
                print(f"- [{i}] {q or '（未命名问题）'}")
                if impact:
                    print(f"    impact: {impact[:200]}")
            human_review = {
                "version": ver,
                "decision": "request_changes",
                "notes": "DoD阻塞：存在 blocker open_questions，已拒绝冻结。请先补齐/降级后再冻结。",
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            try:
                from materials_freeze import ensure_materials_pack_dirs

                p = ensure_materials_pack_dirs(project_dir)
                write_json(os.path.join(p["reviews"], f"human_review.{ver}.json"), human_review)
            except Exception:
                pass
            return False, "", {}, {}

        human_notes = prompt_multiline("（可选）冻结备注（直接回车然后输入 . 结束）：", end_token=".")
        human_review = {
            "version": ver,
            "decision": "approve_and_freeze",
            "notes": human_notes,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        frozen_version, frozen_path, anchors_path = freeze_materials_pack(
            project_dir=project_dir,
            draft_version=ver,
            draft_obj=obj,
            human_review=human_review,
        )
        print("\n=== 冻结完成 ===")
        print(f"- frozen：{frozen_path}")
        print(f"- anchors：{anchors_path}")
        ver2, frozen_obj, anchors_obj = load_current_frozen_materials_pack(project_dir)
        return True, ver2, frozen_obj, anchors_obj

    # ============================
    # 重申模式：复审 + 改写 outputs/current
    # ============================
    if args.restate:
        max_reviews = int(args.restate_max_reviews or 0)
        if max_reviews < 2:
            raise ValueError("--restate-max-reviews 必须 >= 2（至少需要：1次审稿 + 1次改写后的验收审稿）")

        # === 重申隔离工作区：先克隆 current -> rewrites/...，后续只在 rewrites 下读写，避免影响原 current ===
        src_run_dir = os.path.join(output_base, "current")
        if not os.path.exists(src_run_dir):
            raise FileNotFoundError(f"未找到尝试输出目录：{src_run_dir}（请先运行一次生成流程）")

        # 关键：项目资产目录必须跟随 outputs/current/run_meta.json 里记录的 project_dir，
        # 否则会读错 canon/memory/materials（例如 arc summaries 明明在 projects/<project>/memory/arcs 但这里读不到）。
        src_run_meta = read_json(os.path.join(src_run_dir, "run_meta.json")) or {}
        rel_project_dir = str(src_run_meta.get("project_dir") or "").strip()
        if not rel_project_dir:
            raise ValueError("重申模式需要 outputs/current/run_meta.json 的 project_dir（请先用最新流程跑一次生成以写入 run_meta.json）")
        src_proj_dir = os.path.join(output_base, rel_project_dir.replace("/", os.sep))
        if not os.path.exists(src_proj_dir):
            raise FileNotFoundError(f"未找到项目目录：{src_proj_dir}（来自 run_meta.json.project_dir={rel_project_dir}）")

        # rewrites 与 outputs 平级：<repo>/outputs + <repo>/rewrites
        rewrites_root = os.path.join(os.path.dirname(output_base), "rewrites")
        dst_run_dir = os.path.join(rewrites_root, "outputs", "current")
        # 让 rewrites 下的 projects 结构与原 project_dir 保持一致（例如 projects/<project>）
        dst_proj_dir = os.path.join(rewrites_root, rel_project_dir.replace("/", os.sep))

        def _clone_dir(src: str, dst: str) -> None:
            # 若 dst 已存在，则先备份改名（避免覆盖用户之前的 rewrite 结果）
            if os.path.exists(dst):
                ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                bak = f"{dst}.bak.{ts}"
                try:
                    os.rename(dst, bak)
                except Exception:
                    shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst, dirs_exist_ok=True)

        _clone_dir(src_run_dir, dst_run_dir)
        _clone_dir(src_proj_dir, dst_proj_dir)

        current_dir = dst_run_dir
        project_dir = dst_proj_dir
        chapters_dir_current = os.path.join(current_dir, "chapters")
        if not os.path.exists(chapters_dir_current):
            raise FileNotFoundError(f"未找到章节目录：{chapters_dir_current}（请先确认 outputs/current/chapters 存在）")

        # 读取 run_meta（用于 target_words/章节数等默认参数；重申依赖资产以 projects/rewrite 为准）
        meta = read_json(os.path.join(current_dir, "run_meta.json")) or {}

        ensure_canon_files(project_dir)
        mem_dirs = ensure_memory_dirs(project_dir)
        ensure_materials_files(project_dir)

        # planner_result 优先从 project_dir/project_meta.json 取（项目级口径）；缺失再回退到 outputs/current
        planner_result: dict = {}
        pm_current = read_json(os.path.join(project_dir, "project_meta.json")) or {}
        if isinstance(pm_current.get("planner_result"), dict):
            planner_result = pm_current.get("planner_result") or {}
        if not planner_result:
            planner_result = read_json(os.path.join(current_dir, "planner.json")) or {}
        if not planner_result:
            raise ValueError(
                "重申模式需要 planner_result：未找到 project_meta.json 的 planner_result，且 outputs/current/planner.json 也不存在"
            )

        # materials_bundle：以 project_dir 的 canon+materials 组装为准（与生成模式一致的“项目级资产口径”）
        materials_bundle: dict = {}
        try:
            world = read_json(os.path.join(project_dir, "canon", "world.json")) or {}
            characters = read_json(os.path.join(project_dir, "canon", "characters.json")) or {}
            outline = read_json(os.path.join(project_dir, "materials", "outline.json")) or {}
            tone = read_json(os.path.join(project_dir, "materials", "tone.json")) or {}
            project_name = str((pm_current.get("project_name") or "")).strip() or str(planner_result.get("项目名称", "") or "").strip()
            idea_text = str((pm_current.get("idea") or "")).strip() or str(settings.idea or "")
            materials_bundle = build_materials_bundle(
                project_name=project_name,
                idea=idea_text,
                world=world,
                characters=characters,
                outline=outline,
                tone=tone,
                materials_pack=None,
                version="restate_v1",
            )
        except Exception:
            materials_bundle = {}
        if not materials_bundle:
            # 兜底：按旧逻辑回退
            try:
                materials_bundle = load_materials_bundle(project_dir)
            except Exception:
                materials_bundle = {}

        # 初始化 LLM（重申也遵循 llm_mode/force_llm 等规则）
        llm = None
        force_llm = settings.llm_mode == "llm"
        try:
            # 重申日志单独落盘：full + index（便于实时过滤/查询）
            restate_logs_dir = os.path.join(current_dir, "logs")
            logger = RunLogger(
                path=os.path.join(restate_logs_dir, "restate.events.full.jsonl"),
                index_path=os.path.join(restate_logs_dir, "restate.events.index.jsonl"),
                enabled=bool(settings.debug),
                preview_chars=int(getattr(settings, "debug_preview_chars", 100) or 100),
                payload_dirname="payloads",
            )
            with logger.span("llm_init", llm_mode=settings.llm_mode):
                if settings.llm_mode == "template":
                    llm = None
                elif settings.llm_mode == "llm":
                    llm = try_get_chat_llm(settings.llm)
                    if llm is None:
                        raise RuntimeError(
                            "LLM_MODE=llm 但未能初始化LLM（请检查LLM_*环境变量或config.toml的[llm]配置与依赖安装）"
                        )
                else:  # auto
                    llm = try_get_chat_llm(settings.llm)
        except Exception:
            raise

        # 章节枚举：只处理 3位编号 md（001.md）
        def _list_existing_chapter_ids() -> list[int]:
            out: list[int] = []
            for name in os.listdir(chapters_dir_current):
                if not name.endswith(".md"):
                    continue
                if name.endswith(".editor.md"):
                    continue
                base = name[:-3]
                if not base.isdigit():
                    continue
                try:
                    idx = int(base)
                except Exception:
                    continue
                if idx > 0:
                    out.append(idx)
            out.sort()
            return out

        existing_ids = _list_existing_chapter_ids()
        if not existing_ids:
            raise ValueError(f"重申模式未找到章节正文：{chapters_dir_current} 下不存在 001.md 这类文件")

        # 目标章节数：优先使用本次命令行 --chapters；否则用 run_meta；最后才回退到已有最大章
        chapters_total = 0
        if args.chapters is not None:
            chapters_total = int(args.chapters or 0)
        if chapters_total <= 0:
            chapters_total = int(meta.get("chapters", 0) or 0)
        if chapters_total <= 0:
            chapters_total = max(existing_ids)
        chapters_total = max(1, int(chapters_total))

        s = int(args.restate_start) if args.restate_start is not None else 1
        e = int(args.restate_end) if args.restate_end is not None else int(chapters_total)
        s = max(1, int(s))
        e = max(s, min(int(chapters_total), int(e)))
        chapter_ids = list(range(s, e + 1))

        # 基础 state（尽量与生成流程一致）
        base_state: StoryState = {
            "user_input": str(settings.idea or ""),
            "idea_source_text": "",
            "idea_file_path": str(meta.get("idea_file_path", "") or ""),
            "target_words": int(meta.get("target_words", settings.gen.target_words) or settings.gen.target_words),
            "max_rewrites": max_reviews - 1,  # 适配既有 writer/editor “返工”语义：1次初稿后最多返工 N 次
            "chapters_total": int(chapters_total),
            "writer_version": 0,
            "llm": llm,
            "llm_mode": settings.llm_mode,
            "force_llm": force_llm,
            "debug": bool(settings.debug),
            "logger": logger,
            "output_dir": current_dir,
            "project_dir": project_dir,
            "stage": str(meta.get("stage", settings.stage) or settings.stage),
            "memory_recent_k": int(meta.get("memory_recent_k", settings.memory_recent_k) or settings.memory_recent_k),
            "include_unapproved_memories": bool(meta.get("include_unapproved_memories", False)),
            "style_override": str(meta.get("style_override", "") or ""),
            "paragraph_rules": str(meta.get("paragraph_rules", "") or ""),
            "editor_min_issues": int(meta.get("editor_min_issues", settings.editor_min_issues) or settings.editor_min_issues),
            "editor_retry_on_invalid": int(meta.get("editor_retry_on_invalid", settings.editor_retry_on_invalid) or settings.editor_retry_on_invalid),
            "llm_max_attempts": int(meta.get("llm_max_attempts", settings.llm_max_attempts) or settings.llm_max_attempts),
            "llm_retry_base_sleep_s": float(meta.get("llm_retry_base_sleep_s", settings.llm_retry_base_sleep_s) or settings.llm_retry_base_sleep_s),
            "writer_min_ratio": float(meta.get("writer_min_ratio", getattr(settings, "writer_min_ratio", 0.75)) or getattr(settings, "writer_min_ratio", 0.75)),
            "writer_max_ratio": float(meta.get("writer_max_ratio", getattr(settings, "writer_max_ratio", 1.25)) or getattr(settings, "writer_max_ratio", 1.25)),
            "enable_arc_summary": bool(meta.get("enable_arc_summary", settings.enable_arc_summary)),
            "arc_every_n": int(meta.get("arc_every_n", settings.arc_every_n) or settings.arc_every_n),
            "arc_recent_k": int(meta.get("arc_recent_k", settings.arc_recent_k) or settings.arc_recent_k),
            # 重申模式也支持安全自动沉淀（写入 rewrites/projects/...，不影响原 projects）
            # 注意：这里优先使用“本次运行的 settings/config/CLI”，不要继承旧 run_meta，
            # 否则你在 config.toml 改了 auto_apply_updates 也不会在 restate 生效。
            "auto_apply_updates": str(settings.auto_apply_updates or "off"),
            "planner_result": planner_result if isinstance(planner_result, dict) else {},
            "planner_json": json.dumps(planner_result, ensure_ascii=False, indent=2) if isinstance(planner_result, dict) else "",
            "planner_used_llm": bool(meta.get("planner_used_llm", False)),
            "materials_bundle": materials_bundle if isinstance(materials_bundle, dict) else {},
            "rewrite_instructions": str(rewrite_from_file or ""),
        }

        # 记录重写指导（便于追溯）
        try:
            if rewrite_from_file is not None:
                write_text(os.path.join(current_dir, "rewrite_instructions.txt"), str(rewrite_from_file or ""))
                write_text(os.path.join(current_dir, "rewrite_instructions.path.txt"), str(rewrite_file_path or ""))
        except Exception:
            pass

        # 重申产物目录：保存中间稿（不影响最终覆盖的 chapters/*.md）
        restate_dir = os.path.join(current_dir, "restate")
        restate_ch_dir = os.path.join(restate_dir, "chapters")
        os.makedirs(restate_ch_dir, exist_ok=True)

        def _backup(path: str) -> str:
            if not os.path.exists(path):
                return ""
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            bak = f"{path}.bak.{ts}"
            try:
                import shutil as _sh
                _sh.copy2(path, bak)
            except Exception:
                return ""
            return bak

        # 逐章：已有正文 -> 复审+按需改写；缺失/失败 -> 走正常生成（补齐章节）
        from agents.editor import editor_agent
        from agents.writer import writer_agent
        from agents.memory import memory_agent
        from agents.canon_update import canon_update_agent
        from agents.materials_update import materials_update_agent

        chapter_app = build_chapter_app()

        logger.event(
            "restate_start",
            chapters=len(chapter_ids),
            max_reviews=max_reviews,
            chapters_dir=chapters_dir_current,
            chapters_total=int(chapters_total),
            range_start=int(s),
            range_end=int(e),
        )

        # 资产可见性诊断：一眼确认“arc/memory/materials 是否真的存在且可读”
        try:
            arcs_dir = os.path.join(project_dir, "memory", "arcs")
            chmem_dir = os.path.join(project_dir, "memory", "chapters")
            arcs_n = len([x for x in os.listdir(arcs_dir)]) if os.path.exists(arcs_dir) else 0
            chmem_n = len([x for x in os.listdir(chmem_dir)]) if os.path.exists(chmem_dir) else 0
            outline_ok = os.path.exists(os.path.join(project_dir, "materials", "outline.json"))
            tone_ok = os.path.exists(os.path.join(project_dir, "materials", "tone.json"))
            logger.event(
                "restate_assets",
                project_dir=project_dir,
                arcs_dir=arcs_dir,
                arcs_count=arcs_n,
                chapter_memories_dir=chmem_dir,
                chapter_memories_count=chmem_n,
                outline_exists=bool(outline_ok),
                tone_exists=bool(tone_ok),
                auto_apply_updates=str(settings.auto_apply_updates or "off"),
            )
        except Exception:
            pass

        planned_state: StoryState = dict(base_state)

        def _refresh_materials_bundle() -> None:
            # 若 materials/canon 被自动沉淀更新，则刷新 materials_bundle 让后续章节 prompt 立即受益
            try:
                pmx = read_json(os.path.join(project_dir, "project_meta.json")) or {}
                prx = planned_state.get("planner_result") if isinstance(planned_state.get("planner_result"), dict) else {}
                world = read_json(os.path.join(project_dir, "canon", "world.json")) or {}
                characters = read_json(os.path.join(project_dir, "canon", "characters.json")) or {}
                outline = read_json(os.path.join(project_dir, "materials", "outline.json")) or {}
                tone = read_json(os.path.join(project_dir, "materials", "tone.json")) or {}
                project_name = str((pmx.get("project_name") or "")).strip() or str((prx or {}).get("项目名称", "") or "").strip()
                idea_text = str((pmx.get("idea") or "")).strip() or str(settings.idea or "")
                mb = build_materials_bundle(
                    project_name=project_name,
                    idea=idea_text,
                    world=world,
                    characters=characters,
                    outline=outline,
                    tone=tone,
                    materials_pack=None,
                    version="restate_v1",
                )
                if isinstance(mb, dict) and mb:
                    planned_state["materials_bundle"] = mb
            except Exception:
                return

        def _maybe_auto_apply_updates(*, chap_id: str, canon_suggestions: list, canon_update_suggestions: list, materials_update_suggestions: list) -> None:
            # 复用主流程 safe 策略：在隔离的 rewrites/projects 下自动应用低风险补丁，让后续章节立即受益
            try:
                mode = str(planned_state.get("auto_apply_updates", "off") or "off").strip().lower()
                if mode != "safe":
                    return

                # 1) materials_patch（安全幂等追加）——直接应用到 project_dir/materials
                mats_items: list[dict] = []
                if isinstance(materials_update_suggestions, list):
                    for it in materials_update_suggestions:
                        if not isinstance(it, dict):
                            continue
                        if str(it.get("action", "") or "").strip() == "materials_patch":
                            mats_items.append(it)
                mats_stats = {"applied": 0, "skipped": 0, "backups": []}
                if mats_items:
                    mats_stats = apply_materials_suggestions(project_dir=project_dir, items=mats_items, yes=True, dry_run=False)

                # 2) canon_patch（严格白名单：只允许 world.notes / style.md 追加）
                canon_items_all: list[dict] = []
                for arr in (canon_suggestions, canon_update_suggestions, materials_update_suggestions):
                    if isinstance(arr, list):
                        for it in arr:
                            if isinstance(it, dict):
                                canon_items_all.append(it)

                def _is_safe_canon_patch(it: dict) -> bool:
                    act = str(it.get("action", "") or "").strip()
                    if act != "canon_patch":
                        return False
                    cp = it.get("canon_patch") if isinstance(it.get("canon_patch"), dict) else {}
                    target = str(cp.get("target", "") or "").strip()
                    op = str(cp.get("op", "") or "").strip()
                    path = str(cp.get("path", "") or "").strip()
                    if target == "world.json" and op == "note" and (path in ("notes", "")):
                        return True
                    if target == "style.md" and op == "append":
                        return True
                    return False

                canon_items = [it for it in canon_items_all if _is_safe_canon_patch(it)]
                canon_stats = {"applied": 0, "skipped": 0, "backups": []}
                if canon_items:
                    canon_stats = apply_canon_suggestions(project_dir=project_dir, items=canon_items, yes=True, dry_run=False)

                write_json(
                    os.path.join(chapters_dir_current, f"{chap_id}.auto_apply.json"),
                    {
                        "mode": mode,
                        "materials": {"items": len(mats_items), "stats": mats_stats},
                        "canon": {"items": len(canon_items), "stats": canon_stats},
                    },
                )
                try:
                    logger.event(
                        "auto_apply_updates",
                        chapter_index=int(chap_id),
                        mode=mode,
                        materials_items=len(mats_items),
                        canon_items=len(canon_items),
                        materials_applied=int(mats_stats.get("applied", 0) or 0),
                        canon_applied=int(canon_stats.get("applied", 0) or 0),
                    )
                except Exception:
                    pass

                # 应用后刷新材料包（让后续章节立即受益）
                _refresh_materials_bundle()
            except Exception as e:
                try:
                    logger.event("auto_apply_error", chapter_index=int(chap_id), error_type=e.__class__.__name__, error=str(e))
                except Exception:
                    pass

        def _clear_error_file(chap_id: str) -> None:
            err_path = os.path.join(chapters_dir_current, f"{chap_id}.error.json")
            if not os.path.exists(err_path):
                return
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            try:
                os.rename(err_path, f"{err_path}.bak.{ts}")
            except Exception:
                try:
                    os.remove(err_path)
                except Exception:
                    pass

        def _maybe_extend_outline(chapter_index: int) -> None:
            # 复用主流程的“分块生成细纲”策略：缺细纲时才扩展
            try:
                mb0 = planned_state.get("materials_bundle") if isinstance(planned_state.get("materials_bundle"), dict) else {}
                if not isinstance(mb0, dict):
                    return
                chap_outline = pick_outline_for_chapter(mb0, int(chapter_index))
                if chap_outline:
                    return

                block = 20
                start_i = int(chapter_index)
                end_i = min(int(chapters_total), start_i + block - 1)
                tmp_state = dict(planned_state)
                tmp_state["outline_start"] = start_i
                tmp_state["outline_end"] = end_i
                tmp_state["materials_bundle"] = mb0
                tmp_state = screenwriter_agent(tmp_state)
                new_outline = tmp_state.get("screenwriter_result") if isinstance(tmp_state.get("screenwriter_result"), dict) else {}
                if not isinstance(new_outline, dict) or not new_outline:
                    return

                outline0 = mb0.get("outline") if isinstance(mb0.get("outline"), dict) else {}
                chs0 = outline0.get("chapters") if isinstance(outline0.get("chapters"), list) else []
                by_idx: dict[int, dict] = {}
                for it in chs0:
                    if isinstance(it, dict):
                        try:
                            by_idx[int(it.get("chapter_index", 0) or 0)] = it
                        except Exception:
                            pass
                for it in (new_outline.get("chapters") if isinstance(new_outline.get("chapters"), list) else []):
                    if isinstance(it, dict):
                        try:
                            by_idx[int(it.get("chapter_index", 0) or 0)] = it
                        except Exception:
                            pass

                merged_chs = [by_idx[k] for k in sorted([k for k in by_idx.keys() if k > 0])]
                outline0 = dict(outline0)
                if not str(outline0.get("main_arc", "") or "").strip() and str(new_outline.get("main_arc", "") or "").strip():
                    outline0["main_arc"] = new_outline.get("main_arc", "")
                if (not outline0.get("themes")) and new_outline.get("themes"):
                    outline0["themes"] = new_outline.get("themes", [])
                outline0["chapters"] = merged_chs

                mb0 = dict(mb0)
                mb0["outline"] = outline0
                planned_state["materials_bundle"] = mb0
                planned_state["screenwriter_result"] = outline0
                try:
                    materials_dir_current = os.path.join(current_dir, "materials")
                    os.makedirs(materials_dir_current, exist_ok=True)
                    write_json(os.path.join(materials_dir_current, "outline.json"), outline0)  # type: ignore[arg-type]
                    write_json(os.path.join(materials_dir_current, "materials_bundle.json"), mb0)  # type: ignore[arg-type]
                except Exception:
                    pass
            except Exception:
                # 细纲扩展失败不阻断后续生成
                return

        def _maybe_write_arc_summary(idx: int) -> None:
            # 与主流程一致：arc 结束点优先，否则每 N 章兜底
            try:
                enable_arc = bool(planned_state.get("enable_arc_summary", True))
                every_n = int(planned_state.get("arc_every_n", 10) or 10)
                if not (enable_arc and llm):
                    return
                should_write = False
                start_arc = None
                try:
                    mbx = planned_state.get("materials_bundle") if isinstance(planned_state.get("materials_bundle"), dict) else {}
                    outx = mbx.get("outline") if isinstance(mbx.get("outline"), dict) else {}
                    chs = outx.get("chapters") if isinstance(outx.get("chapters"), list) else []
                    cur = None
                    nxt = None
                    for it in chs:
                        if isinstance(it, dict) and int(it.get("chapter_index", 0) or 0) == int(idx):
                            cur = it
                        if isinstance(it, dict) and int(it.get("chapter_index", 0) or 0) == int(idx) + 1:
                            nxt = it
                    cur_arc = str((cur or {}).get("arc_id", "") or "").strip()
                    nxt_arc = str((nxt or {}).get("arc_id", "") or "").strip()
                    if cur_arc and nxt is not None and nxt_arc and (cur_arc != nxt_arc):
                        should_write = True
                        s0 = int(idx)
                        for it in chs:
                            if not isinstance(it, dict):
                                continue
                            if str(it.get("arc_id", "") or "").strip() != cur_arc:
                                continue
                            try:
                                ci = int(it.get("chapter_index", 0) or 0)
                            except Exception:
                                continue
                            if 0 < ci < s0:
                                s0 = ci
                        start_arc = s0
                except Exception:
                    should_write = False
                    start_arc = None
                if (not should_write) and every_n > 0 and (idx % every_n == 0):
                    should_write = True
                    start_arc = max(1, idx - every_n + 1)
                if should_write and start_arc:
                    arc_name = f"arc_{int(start_arc):03d}-{int(idx):03d}.json"
                    arc_path = os.path.join(project_dir, "memory", "arcs", arc_name)
                    if not os.path.exists(arc_path):
                        arc = generate_arc_summary(
                            llm=llm,
                            project_dir=project_dir,
                            start_chapter=int(start_arc),
                            end_chapter=int(idx),
                            logger=logger,
                            llm_max_attempts=int(planned_state.get("llm_max_attempts", 3) or 3),
                            llm_retry_base_sleep_s=float(planned_state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
                        )
                        if isinstance(arc, dict) and arc:
                            p = write_arc_summary(project_dir, int(start_arc), int(idx), arc)
                            try:
                                logger.event(
                                    "arc_summary_written",
                                    chapter_index=idx,
                                    start_chapter=int(start_arc),
                                    end_chapter=idx,
                                    path=os.path.relpath(p, output_base).replace("\\", "/"),
                                )
                            except Exception:
                                pass
            except Exception as e:
                try:
                    logger.event("arc_summary_error", chapter_index=idx, error_type=e.__class__.__name__, error=str(e))
                except Exception:
                    pass

        for idx in chapter_ids:
            chap_id = f"{int(idx):03d}"
            md_path = os.path.join(chapters_dir_current, f"{chap_id}.md")
            err_path = os.path.join(chapters_dir_current, f"{chap_id}.error.json")

            logger.event("restate_chapter_start", chapter_index=int(idx))

            # 判断：有正文就走“复审”；缺失/空/只有 error.json 就走“生成”
            cur_text = ""
            if os.path.exists(md_path):
                try:
                    with open(md_path, "r", encoding="utf-8") as f:
                        cur_text = f.read().strip()
                except Exception:
                    cur_text = ""
            need_generate = (not cur_text) and (int(idx) <= int(chapters_total))
            if need_generate or (os.path.exists(err_path) and (not cur_text)):
                # === 生成缺失/失败章节 ===
                try:
                    _maybe_extend_outline(int(idx))
                    chapter_state: StoryState = {
                        **planned_state,
                        "chapter_index": int(idx),
                        "writer_version": 0,
                        "needs_rewrite": False,
                        "editor_feedback": [],
                        "editor_decision": "",
                        "editor_report": {},
                        "canon_suggestions": [],
                        "canon_update_suggestions": [],
                        "writer_used_llm": False,
                        "editor_used_llm": False,
                        "chapter_memory": {},
                        "memory_used_llm": False,
                        "materials_update_used": False,
                        "materials_update_suggestions": [],
                        "project_dir": project_dir,
                    }
                    st = chapter_app.invoke(chapter_state, config={"recursion_limit": 50})
                except Exception as e:
                    import traceback as _tb

                    err = {
                        "chapter_index": int(idx),
                        "error_type": e.__class__.__name__,
                        "error": str(e),
                        "traceback": "".join(_tb.format_exception(type(e), e, e.__traceback__))[:20000],
                    }
                    try:
                        logger.event(
                            "restate_chapter_error",
                            chapter_index=int(idx),
                            error_type=err["error_type"],
                            error=err["error"],
                            action="generate",
                        )
                    except Exception:
                        pass
                    try:
                        write_json(os.path.join(chapters_dir_current, f"{chap_id}.error.json"), err)  # type: ignore[arg-type]
                    except Exception:
                        pass
                    continue

                # 落盘最终稿（生成）
                try:
                    _backup(md_path)
                    write_text(md_path, str((st or {}).get("writer_result", "") or ""))
                    _clear_error_file(chap_id)
                except Exception:
                    pass

                decision = (st or {}).get("editor_decision", "")
                feedback = (st or {}).get("editor_feedback", []) or []
                editor_report = (st or {}).get("editor_report") or {}
                canon_suggestions = (st or {}).get("canon_suggestions") or []
                canon_update_suggestions = (st or {}).get("canon_update_suggestions") or []
                materials_update_suggestions = (st or {}).get("materials_update_suggestions") or []

                try:
                    if str(decision).strip() == "审核通过":
                        write_text(os.path.join(chapters_dir_current, f"{chap_id}.editor.md"), "审核通过")
                    else:
                        lines = ["审核不通过", "", *[f"- {x}" for x in feedback]]
                        write_text(os.path.join(chapters_dir_current, f"{chap_id}.editor.md"), "\n".join(lines).strip())
                    if isinstance(editor_report, dict) and editor_report:
                        _backup(os.path.join(chapters_dir_current, f"{chap_id}.editor.json"))
                        write_json(os.path.join(chapters_dir_current, f"{chap_id}.editor.json"), editor_report)
                except Exception:
                    pass

                # memory：current + projects
                try:
                    mem = (st or {}).get("chapter_memory") or {}
                    if isinstance(mem, dict) and mem:
                        _backup(os.path.join(chapters_dir_current, f"{chap_id}.memory.json"))
                        write_json(os.path.join(chapters_dir_current, f"{chap_id}.memory.json"), mem)
                        write_json(os.path.join(mem_dirs["chapters_dir"], f"{chap_id}.memory.json"), mem)
                except Exception:
                    pass

                # suggestions：只落盘
                try:
                    if isinstance(canon_suggestions, list) and canon_suggestions:
                        write_json(os.path.join(chapters_dir_current, f"{chap_id}.canon_suggestions.json"), {"items": canon_suggestions})
                    if isinstance(canon_update_suggestions, list) and canon_update_suggestions:
                        write_json(
                            os.path.join(chapters_dir_current, f"{chap_id}.canon_update_suggestions.json"),
                            {"items": canon_update_suggestions},
                        )
                    if isinstance(materials_update_suggestions, list) and materials_update_suggestions:
                        write_json(
                            os.path.join(chapters_dir_current, f"{chap_id}.materials_update_suggestions.json"),
                            {"items": materials_update_suggestions},
                        )
                except Exception:
                    pass

                _maybe_auto_apply_updates(
                    chap_id=str(chap_id),
                    canon_suggestions=canon_suggestions if isinstance(canon_suggestions, list) else [],
                    canon_update_suggestions=canon_update_suggestions if isinstance(canon_update_suggestions, list) else [],
                    materials_update_suggestions=materials_update_suggestions if isinstance(materials_update_suggestions, list) else [],
                )
                _maybe_write_arc_summary(int(idx))

                logger.event(
                    "restate_chapter_end",
                    chapter_index=int(idx),
                    mode="generate",
                    writer_version=int((st or {}).get("writer_version", 0) or 0),
                    editor_decision=str((st or {}).get("editor_decision", "") or ""),
                    writer_chars=len(str((st or {}).get("writer_result", "") or "")),
                )
                continue

            if not cur_text:
                # 没有正文且不在目标范围（理论上不会发生），跳过
                continue

            # === 复审已有正文 ===
            try:
                # 保存原稿快照
                write_text(os.path.join(restate_ch_dir, f"{chap_id}.v0.md"), cur_text)

                st2: StoryState = dict(base_state)
                st2["chapter_index"] = int(idx)
                st2["writer_result"] = cur_text
                # 视作“已存在初稿=第1版”，便于 editor 轮次策略与后续 rewrite 语义一致
                st2["writer_version"] = 1
                st2["needs_rewrite"] = False
                st2["editor_feedback"] = []
                st2["editor_report"] = {}
                st2["editor_decision"] = ""
                st2["canon_suggestions"] = []
                st2["canon_update_suggestions"] = []
                st2["materials_update_suggestions"] = []

                reviews_used = 0
                st2["editor_strict_mode"] = True
                st2 = editor_agent(st2)
                st2["editor_strict_mode"] = False
                reviews_used += 1
                try:
                    write_json(os.path.join(restate_ch_dir, f"{chap_id}.v0.editor.json"), st2.get("editor_report") or {})
                except Exception:
                    pass

                while str(st2.get("editor_decision", "") or "").strip() != "审核通过":
                    if reviews_used >= max_reviews:
                        break
                    st2["needs_rewrite"] = True
                    st2 = writer_agent(st2)
                    cur_text2 = str(st2.get("writer_result", "") or "").strip()
                    v2 = int(st2.get("writer_version", 0) or 0)
                    write_text(os.path.join(restate_ch_dir, f"{chap_id}.v{v2}.md"), cur_text2)

                    st2["editor_strict_mode"] = (reviews_used < (max_reviews - 1))
                    st2 = editor_agent(st2)
                    st2["editor_strict_mode"] = False
                    reviews_used += 1
                    v = int(st2.get("writer_version", 0) or 0)
                    try:
                        write_json(os.path.join(restate_ch_dir, f"{chap_id}.v{v}.editor.json"), st2.get("editor_report") or {})
                    except Exception:
                        pass

                try:
                    st2 = memory_agent(st2)
                    st2 = canon_update_agent(st2)
                    st2 = materials_update_agent(st2)
                except Exception:
                    pass

                _backup(md_path)
                write_text(md_path, str(st2.get("writer_result", "") or ""))
                _clear_error_file(chap_id)

                decision2 = str(st2.get("editor_decision", "") or "")
                feedback2 = st2.get("editor_feedback") or []
                editor_report2 = st2.get("editor_report") or {}
                if decision2 == "审核通过":
                    write_text(os.path.join(chapters_dir_current, f"{chap_id}.editor.md"), "审核通过")
                else:
                    lines = ["审核不通过", "", *[f"- {x}" for x in feedback2]]
                    write_text(os.path.join(chapters_dir_current, f"{chap_id}.editor.md"), "\n".join(lines).strip())
                if isinstance(editor_report2, dict) and editor_report2:
                    _backup(os.path.join(chapters_dir_current, f"{chap_id}.editor.json"))
                    write_json(os.path.join(chapters_dir_current, f"{chap_id}.editor.json"), editor_report2)

                mem2 = st2.get("chapter_memory") or {}
                if isinstance(mem2, dict) and mem2:
                    _backup(os.path.join(chapters_dir_current, f"{chap_id}.memory.json"))
                    write_json(os.path.join(chapters_dir_current, f"{chap_id}.memory.json"), mem2)
                    write_json(os.path.join(mem_dirs["chapters_dir"], f"{chap_id}.memory.json"), mem2)

                canon_suggestions2 = st2.get("canon_suggestions") or []
                if isinstance(canon_suggestions2, list) and canon_suggestions2:
                    write_json(os.path.join(chapters_dir_current, f"{chap_id}.canon_suggestions.json"), {"items": canon_suggestions2})
                canon_update_suggestions2 = st2.get("canon_update_suggestions") or []
                if isinstance(canon_update_suggestions2, list) and canon_update_suggestions2:
                    write_json(os.path.join(chapters_dir_current, f"{chap_id}.canon_update_suggestions.json"), {"items": canon_update_suggestions2})
                materials_update_suggestions2 = st2.get("materials_update_suggestions") or []
                if isinstance(materials_update_suggestions2, list) and materials_update_suggestions2:
                    write_json(os.path.join(chapters_dir_current, f"{chap_id}.materials_update_suggestions.json"), {"items": materials_update_suggestions2})

                _maybe_auto_apply_updates(
                    chap_id=str(chap_id),
                    canon_suggestions=canon_suggestions2 if isinstance(canon_suggestions2, list) else [],
                    canon_update_suggestions=canon_update_suggestions2 if isinstance(canon_update_suggestions2, list) else [],
                    materials_update_suggestions=materials_update_suggestions2 if isinstance(materials_update_suggestions2, list) else [],
                )
                _maybe_write_arc_summary(int(idx))

                logger.event(
                    "restate_chapter_end",
                    chapter_index=int(idx),
                    mode="review",
                reviews_used=reviews_used,
                    writer_version=int(st2.get("writer_version", 0) or 0),
                    editor_decision=str(st2.get("editor_decision", "") or ""),
                    writer_chars=len(str(st2.get("writer_result", "") or "")),
            )
            except Exception as e:
                import traceback as _tb

                err = {
                    "chapter_index": int(idx),
                    "error_type": e.__class__.__name__,
                    "error": str(e),
                    "traceback": "".join(_tb.format_exception(type(e), e, e.__traceback__))[:20000],
                }
                try:
                    logger.event(
                        "restate_chapter_error",
                        chapter_index=int(idx),
                        error_type=err["error_type"],
                        error=err["error"],
                        action="review",
                    )
                except Exception:
                    pass
                try:
                    write_json(os.path.join(chapters_dir_current, f"{chap_id}.error.json"), err)  # type: ignore[arg-type]
                except Exception:
                    pass
                continue

        logger.event("restate_end")
        print(f"\n重申完成（不影响原 current）。")
        print(f"- 运行产物：{current_dir}")
        print(f"- 项目资产：{project_dir}")
        return

    # 手动归档模式：你review完 outputs/current 后再跑一次这个命令即可入库
    if args.archive_only:
        current_dir = os.path.join(output_base, "current")
        if not os.path.exists(current_dir):
            raise FileNotFoundError(f"未找到尝试输出目录：{current_dir}（请先运行一次生成流程）")
        meta = read_json(os.path.join(current_dir, "run_meta.json")) or {}
        run_id = str(meta.get("run_id") or "").strip() or datetime.now().strftime("%Y%m%d-%H%M%S")
        stage = settings.stage or str(meta.get("stage") or "stage1")

        # 优先使用 run_meta.json 中记录的 project_dir
        rel_project_dir = str(meta.get("project_dir") or "").strip()
        if not rel_project_dir:
            raise ValueError("run_meta.json 缺少 project_dir：请使用最新流程先运行一次生成以写入 run_meta.json")
        project_dir = os.path.join(output_base, rel_project_dir.replace("/", os.sep))

        ensure_canon_files(project_dir)
        ensure_memory_dirs(project_dir)
        if args.archive_confirm and not _confirm("\n确认将 outputs/current 归档到项目 stages？(y/N) "):
            print("已取消归档（未归档）。")
            return
        if args.dry_run:
            print("dry-run：已跳过实际归档（未复制文件）。")
            return
        archived_dir = archive_run(
            base_dir=output_base,
            project_dir=project_dir,
            stage=stage,
            current_dir=current_dir,
            run_id=run_id,
        )
        print(f"\n已将 current 手动归档到：{archived_dir}")
        return

    # 只应用 Canon 建议（不生成）
    if args.apply_canon_only:
        current_dir = os.path.join(output_base, "current")
        if not os.path.exists(current_dir):
            raise FileNotFoundError(f"未找到尝试输出目录：{current_dir}（请先运行一次生成流程）")
        meta = read_json(os.path.join(current_dir, "run_meta.json")) or {}
        rel_project_dir = str(meta.get("project_dir") or "").strip()
        if not rel_project_dir:
            raise ValueError("outputs/current/run_meta.json 缺少 project_dir，无法定位项目 canon 目录")
        project_dir = os.path.join(output_base, rel_project_dir.replace("/", os.sep))
        chapters_dir = os.path.join(current_dir, "chapters")
        items = read_canon_suggestions_from_dir(chapters_dir)
        print("\n=== Canon Suggestions Preview ===")
        print(preview_canon_suggestions(items))
        if not items:
            print("\n（没有可应用的 canon_suggestions）")
            return
        stats = apply_canon_suggestions(project_dir=project_dir, items=items, yes=bool(args.yes), dry_run=bool(args.dry_run))
        print(f"\n已处理 Canon 建议：applied={stats.get('applied')} skipped={stats.get('skipped')}")
        if stats.get("backups"):
            print("已生成备份：")
            for b in stats["backups"]:
                print(f"- {b}")
        return

    # 只应用 Materials 建议（不生成）
    if args.apply_materials_only:
        current_dir = os.path.join(output_base, "current")
        if not os.path.exists(current_dir):
            raise FileNotFoundError(f"未找到尝试输出目录：{current_dir}（请先运行一次生成流程）")
        meta = read_json(os.path.join(current_dir, "run_meta.json")) or {}
        rel_project_dir = str(meta.get("project_dir") or "").strip()
        if not rel_project_dir:
            raise ValueError("outputs/current/run_meta.json 缺少 project_dir，无法定位项目 materials 目录")
        project_dir = os.path.join(output_base, rel_project_dir.replace("/", os.sep))
        ensure_materials_files(project_dir)
        chapters_dir = os.path.join(current_dir, "chapters")
        items = read_materials_suggestions_from_dir(chapters_dir)
        print("\n=== Materials Suggestions Preview ===")
        print(preview_materials_suggestions(items))
        if not items:
            print("\n（没有可应用的 materials_update_suggestions）")
            return
        stats = apply_materials_suggestions(project_dir=project_dir, items=items, yes=bool(args.yes), dry_run=bool(args.dry_run))
        print(f"\n已处理 Materials 建议：applied={stats.get('applied')} skipped={stats.get('skipped')}")
        if stats.get("backups"):
            print("已生成备份：")
            for b in stats["backups"]:
                print(f"- {b}")
        return

    # “尝试目录”：只保留一个 current，每次覆盖写入
    current_dir = make_current_dir(output_base)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")

    logs_dir = os.path.join(current_dir, "logs")
    logger = RunLogger(
        path=os.path.join(logs_dir, "events.full.jsonl"),
        index_path=os.path.join(logs_dir, "events.index.jsonl"),
        enabled=bool(settings.debug),
        preview_chars=int(getattr(settings, "debug_preview_chars", 100) or 100),
        payload_dirname="payloads",
    )
    logger.event(
        "run_start",
        llm_mode=settings.llm_mode,
        debug=bool(settings.debug),
        target_words=settings.gen.target_words,
        chapters=settings.gen.chapters,
        max_rewrites=settings.gen.max_rewrites,
        output_dir=current_dir,
        stage=settings.stage,
        run_id=run_id,
    )

    # 初始化 LLM（如果失败也写入日志）
    llm = None
    force_llm = settings.llm_mode == "llm"
    try:
        with logger.span("llm_init", llm_mode=settings.llm_mode):
            if settings.llm_mode == "template":
                llm = None
            elif settings.llm_mode == "llm":
                llm = try_get_chat_llm(settings.llm)
                if llm is None:
                    raise RuntimeError(
                        "LLM_MODE=llm 但未能初始化LLM（请检查LLM_*环境变量或config.toml的[llm]配置与依赖安装）"
                    )
            else:  # auto
                llm = try_get_chat_llm(settings.llm)
    except Exception:
        # 让异常继续向上抛出，但日志已记录 span_error
        raise

    # 初始化 state（先跑策划一次 / 或 resume 复用）
    base_state: StoryState = {
        "user_input": settings.idea,
        # idea-file 模式：把原始文本也塞进 state，交给 planner 做结构化抽取/合并
        "idea_source_text": str(idea_from_file or ""),
        "idea_file_path": str(idea_file_path or ""),
        "target_words": int(settings.gen.target_words),
        "max_rewrites": int(settings.gen.max_rewrites),
        "chapters_total": int(settings.gen.chapters),
        "writer_version": 0,
        "llm": llm,
        "llm_mode": settings.llm_mode,
        "force_llm": force_llm,
        "debug": bool(settings.debug),
        "logger": logger,
        "output_dir": current_dir,
        "stage": settings.stage,
        "memory_recent_k": int(settings.memory_recent_k),
        "include_unapproved_memories": bool(settings.include_unapproved_memories),
        "style_override": str(settings.style_override or ""),
        "paragraph_rules": str(settings.paragraph_rules or ""),
        "editor_min_issues": int(settings.editor_min_issues),
        "editor_retry_on_invalid": int(settings.editor_retry_on_invalid),
        "llm_max_attempts": int(settings.llm_max_attempts),
        "llm_retry_base_sleep_s": float(settings.llm_retry_base_sleep_s),
        "enable_arc_summary": bool(settings.enable_arc_summary),
        "arc_every_n": int(settings.arc_every_n),
        "arc_recent_k": int(settings.arc_recent_k),
        "auto_apply_updates": str(settings.auto_apply_updates or "off"),
        "planner_tasks": settings.planner_tasks or [],
        "materials_pack_max_rounds": int(getattr(settings, "materials_pack_max_rounds", 2)),
        "materials_pack_min_decisions": int(getattr(settings, "materials_pack_min_decisions", 1)),
    }

    planned_state: StoryState
    if args.resume:
        # resume：先用 --project 定位项目目录，再复用 planner_result
        project_dir = get_project_dir(output_base, args.project.strip())
        ensure_canon_files(project_dir)
        mem_dirs = ensure_memory_dirs(project_dir)
        project_meta_path = os.path.join(project_dir, "project_meta.json")
        meta = read_json(project_meta_path) or {}
        meta_planner = meta.get("planner_result") if isinstance(meta.get("planner_result"), dict) else None
        meta_name = str(meta.get("project_name") or "").strip()
        if meta_planner:
            planned_state = {**base_state, "project_dir": project_dir}
            planned_state["planner_result"] = meta_planner
            planned_state["planner_json"] = json.dumps(meta_planner, ensure_ascii=False, indent=2)
            planned_state["planner_used_llm"] = bool(meta.get("planner_used_llm", False))
            logger.event("planner_resume", project_name=meta_name)
        else:
            planned_state = planner_agent(base_state)
    else:
        planned_state = planner_agent(base_state)

    planner_result = planned_state.get("planner_result", {})
    planner_json = planned_state.get("planner_json", "")
    project_name = planner_result.get("项目名称") if isinstance(planner_result, dict) else None

    # 如果未显式指定 --project，则使用 planner 的项目名作为持久化目录
    if not args.project.strip():
        project_name_final = str(project_name or settings.idea or "story")
        project_dir = get_project_dir(output_base, project_name_final)
    else:
        project_dir = get_project_dir(output_base, args.project.strip())

    ensure_canon_files(project_dir)
    mem_dirs = ensure_memory_dirs(project_dir)
    ensure_materials_files(project_dir)
    planned_state["project_dir"] = project_dir

    # 兼容：不再要求 canon/style.md；文风应由“材料包（tone/style_constraints/avoid）+ 用户覆盖”驱动

    # 阶段2.2：初始化 Canon（仅在占位时写入，避免覆盖人工维护）
    planned_state["project_dir"] = project_dir
    planned_state["stage"] = settings.stage
    planned_state["memory_recent_k"] = int(settings.memory_recent_k)
    planned_state = canon_init_agent(planned_state)

    # 阶段3：多角色材料包（先串行，稳定后再升级为 LangGraph 并行分支）
    # 先加载项目长期 materials（outline/tone），作为本次材料包的“基底”（计划类约束）
    try:
        long_materials = load_materials_bundle(project_dir)
    except Exception:
        long_materials = {"outline": {}, "tone": {}}
    planned_state["long_materials"] = long_materials  # 仅用于调试/追溯（不强依赖）

    planned_state = architect_agent(planned_state)
    planned_state = character_director_agent(planned_state)
    planned_state = screenwriter_agent(planned_state)
    planned_state = tone_agent(planned_state)
    planned_state = materials_aggregator_agent(planned_state)
    planned_state = materials_pack_loop_agent(planned_state)
    # 将长期 materials 合并进 materials_bundle（不覆盖本次专家更具体的产出，只填空）
    try:
        mb = planned_state.get("materials_bundle") if isinstance(planned_state.get("materials_bundle"), dict) else {}
        if isinstance(mb, dict) and mb:
            if isinstance(long_materials, dict):
                if "outline" in long_materials and (not mb.get("outline")):
                    mb["outline"] = long_materials.get("outline") or {}
                if "tone" in long_materials and (not mb.get("tone")):
                    mb["tone"] = long_materials.get("tone") or {}
            planned_state["materials_bundle"] = mb
    except Exception:
        pass
    # 阶段3：materials_init（同步会议）——仅在项目 materials 为空/占位时初始化（受 Canon 硬约束）
    planned_state = materials_init_agent(planned_state)

    # ============================
    # 材料包冻结门禁（写作前必须）
    # ============================
    frozen_ok = False
    frozen_version = ""
    frozen_obj: dict = {}
    anchors_obj: dict = {}
    # 若项目已有冻结材料包，可直接复用；否则进入总编门禁
    try:
        frozen_version, frozen_obj, anchors_obj = load_current_frozen_materials_pack(project_dir)
        frozen_ok = bool(frozen_version and isinstance(frozen_obj, dict) and frozen_obj)
    except Exception:
        frozen_ok = False

    if not frozen_ok:
        frozen_ok, frozen_version, frozen_obj, anchors_obj = _human_gate_materials(project_dir=project_dir, planned_state=planned_state)
        if not frozen_ok:
            print("\n材料包未冻结：已停止（未进入章节写作）。")
            return

    # 将冻结材料包快照到本次 run 目录（追溯）
    try:
        snapshot_frozen_to_run(current_dir, frozen_version=frozen_version, frozen_obj=frozen_obj, anchors_obj=anchors_obj)
    except Exception:
        pass

    # 写作/审稿注入：以冻结材料包转换后的 bundle 为准（强一致性口径）
    try:
        planned_state["materials_bundle"] = frozen_pack_to_materials_bundle(frozen_obj, idea=str(settings.idea or ""))
    except Exception:
        pass

    # 持久化项目元信息（用于 resume）
    write_json(
        os.path.join(project_dir, "project_meta.json"),
        {
            "project_name": str(project_name or args.project.strip() or ""),
            "idea": str(settings.idea or ""),
            "planner_result": planner_result if isinstance(planner_result, dict) else {},
            "planner_used_llm": bool(planned_state.get("planner_used_llm", False)),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        },
    )
    # 记录真实项目名（不改目录名，仅写入元数据/日志）
    logger.event("project_name", project_name=str(project_name or ""))

    # 落盘策划结果
    write_json(os.path.join(current_dir, "planner.json"), planner_result if isinstance(planner_result, dict) else {})

    # 落盘阶段3材料包（outputs/current/materials）
    materials_dir_current = os.path.join(current_dir, "materials")
    os.makedirs(materials_dir_current, exist_ok=True)
    if isinstance(planned_state.get("architect_result"), dict) and planned_state.get("architect_result"):
        write_json(os.path.join(materials_dir_current, "world.json"), planned_state.get("architect_result") or {})
    if isinstance(planned_state.get("character_director_result"), dict) and planned_state.get("character_director_result"):
        write_json(os.path.join(materials_dir_current, "characters.json"), planned_state.get("character_director_result") or {})
    if isinstance(planned_state.get("screenwriter_result"), dict) and planned_state.get("screenwriter_result"):
        write_json(os.path.join(materials_dir_current, "outline.json"), planned_state.get("screenwriter_result") or {})
    if isinstance(planned_state.get("tone_result"), dict) and planned_state.get("tone_result"):
        write_json(os.path.join(materials_dir_current, "tone.json"), planned_state.get("tone_result") or {})
    if isinstance(planned_state.get("materials_bundle"), dict) and planned_state.get("materials_bundle"):
        write_json(os.path.join(materials_dir_current, "materials_bundle.json"), planned_state.get("materials_bundle") or {})
    write_json(
        os.path.join(current_dir, "run_meta.json"),
        {
            "run_id": run_id,
            "stage": settings.stage,
            "llm_mode": settings.llm_mode,
            "planner_used_llm": bool(planned_state.get("planner_used_llm", False)),
            "target_words": settings.gen.target_words,
            "chapters": settings.gen.chapters,
            "max_rewrites": settings.gen.max_rewrites,
            "debug": bool(settings.debug),
            "project_name": str(project_name or ""),
            "project_dir": os.path.relpath(project_dir, output_base).replace("\\", "/"),
            "idea_file_path": str(idea_file_path or ""),
            "memory_recent_k": int(settings.memory_recent_k),
            "include_unapproved_memories": bool(settings.include_unapproved_memories),
            # 注意：style/paragraph 可能来自 idea-file 的点子包解析（planner 内回填），这里记录“生效值”而非仅 settings
            "style_override": str(planned_state.get("style_override", "") or settings.style_override or ""),
            "paragraph_rules": str(planned_state.get("paragraph_rules", "") or settings.paragraph_rules or ""),
            "editor_min_issues": int(settings.editor_min_issues),
            "editor_retry_on_invalid": int(settings.editor_retry_on_invalid),
            "llm_max_attempts": int(settings.llm_max_attempts),
            "llm_retry_base_sleep_s": float(settings.llm_retry_base_sleep_s),
            "writer_min_ratio": float(getattr(settings, "writer_min_ratio", 0.75)),
            "writer_max_ratio": float(getattr(settings, "writer_max_ratio", 1.25)),
            "enable_arc_summary": bool(settings.enable_arc_summary),
            "arc_every_n": int(settings.arc_every_n),
            "arc_recent_k": int(settings.arc_recent_k),
            "auto_apply_updates": str(settings.auto_apply_updates or "off"),
            "materials_frozen_version": str(frozen_version or ""),
        },
    )

    # materials-only：总编先冻结材料包，不进入章节写作
    if bool(args.materials_only):
        print("\n已完成材料包冻结门禁（materials-only）。")
        print(f"- 项目：{project_dir}")
        print(f"- frozen_version：{frozen_version}")
        print(f"- 会话目录：{current_dir}")
        return

    # Human-in-the-loop：章节不再自动返工/自动沉淀，改为每章“写手+主编”后暂停等总编决策
    from agents.writer import writer_agent
    from agents.editor import editor_agent
    from agents.memory import memory_agent
    from agents.canon_update import canon_update_agent
    from agents.materials_update import materials_update_agent

    last_state: StoryState = planned_state

    chapters_dir_current = os.path.join(current_dir, "chapters")
    os.makedirs(chapters_dir_current, exist_ok=True)

    # 多章节：每章执行 写手->主编（可返工）
    start_chapter = int(args.start_chapter) if args.start_chapter is not None else None
    if start_chapter is None and args.resume:
        start_chapter = get_max_chapter_memory_index(project_dir) + 1
    if start_chapter is None:
        start_chapter = 1
    start_chapter = max(1, int(start_chapter))
    end_chapter = start_chapter + int(settings.gen.chapters) - 1
    planned_state["chapters_total"] = int(end_chapter)

    for idx in range(start_chapter, end_chapter + 1):
        # === 章节细纲自动扩展（长篇分块生成） ===
        try:
            mb0 = planned_state.get("materials_bundle") if isinstance(planned_state.get("materials_bundle"), dict) else {}
            if isinstance(mb0, dict):
                chap_outline = pick_outline_for_chapter(mb0, int(idx))
                if not chap_outline:
                    # 默认按 20 章一块（适配“每个副本10~20章”的规则），避免一次生成 200 章导致模型偷懒/截断
                    block = 20
                    start_i = int(idx)
                    end_i = min(int(end_chapter), start_i + block - 1)
                    tmp_state = dict(planned_state)
                    tmp_state["outline_start"] = start_i
                    tmp_state["outline_end"] = end_i
                    tmp_state["materials_bundle"] = mb0
                    tmp_state = screenwriter_agent(tmp_state)
                    new_outline = tmp_state.get("screenwriter_result") if isinstance(tmp_state.get("screenwriter_result"), dict) else {}
                    if isinstance(new_outline, dict):
                        # 合并回 materials_bundle.outline（按 chapter_index upsert）
                        outline0 = mb0.get("outline") if isinstance(mb0.get("outline"), dict) else {}
                        chs0 = outline0.get("chapters") if isinstance(outline0.get("chapters"), list) else []
                        by_idx: dict[int, dict] = {}
                        for it in chs0:
                            if isinstance(it, dict):
                                try:
                                    by_idx[int(it.get("chapter_index", 0) or 0)] = it
                                except Exception:
                                    pass
                        for it in (new_outline.get("chapters") if isinstance(new_outline.get("chapters"), list) else []):
                            if isinstance(it, dict):
                                try:
                                    by_idx[int(it.get("chapter_index", 0) or 0)] = it
                                except Exception:
                                    pass
                        merged_chs = [by_idx[k] for k in sorted([k for k in by_idx.keys() if k > 0])]
                        outline0 = dict(outline0)
                        if not str(outline0.get("main_arc", "") or "").strip() and str(new_outline.get("main_arc", "") or "").strip():
                            outline0["main_arc"] = new_outline.get("main_arc", "")
                        if (not outline0.get("themes")) and new_outline.get("themes"):
                            outline0["themes"] = new_outline.get("themes", [])
                        outline0["chapters"] = merged_chs
                        mb0 = dict(mb0)
                        mb0["outline"] = outline0
                        planned_state["materials_bundle"] = mb0
                        planned_state["screenwriter_result"] = outline0
                        # 同步更新 current/materials/outline.json 与 materials_bundle.json（便于你 review）
                        try:
                            materials_dir_current = os.path.join(current_dir, "materials")
                            os.makedirs(materials_dir_current, exist_ok=True)
                            write_json(os.path.join(materials_dir_current, "outline.json"), outline0)  # type: ignore[arg-type]
                            write_json(os.path.join(materials_dir_current, "materials_bundle.json"), mb0)  # type: ignore[arg-type]
                        except Exception:
                            pass
        except Exception:
            # 细纲扩展失败不阻断章节生成（writer 仍可在无细纲情况下工作，只是质量会降）
            pass

        chapter_state: StoryState = {
            **planned_state,
            "chapter_index": idx,
            "writer_version": 0,
            "needs_rewrite": False,
            "editor_feedback": [],
            "editor_decision": "",
            "editor_report": {},
            "canon_suggestions": [],
            "canon_update_suggestions": [],
            "writer_used_llm": False,
            "editor_used_llm": False,
            "chapter_memory": {},
            "memory_used_llm": False,
            "materials_update_used": False,
            "materials_update_suggestions": [],
            "project_dir": project_dir,
            "stage": settings.stage,
            "memory_recent_k": int(settings.memory_recent_k),
            "include_unapproved_memories": bool(settings.include_unapproved_memories),
            # 注意：planner 可能从点子包回填 style/段落规则；这里要用“生效值”，不要覆盖回 settings
            "style_override": str(planned_state.get("style_override", "") or ""),
            "paragraph_rules": str(planned_state.get("paragraph_rules", "") or ""),
            "editor_min_issues": int(settings.editor_min_issues),
            "editor_retry_on_invalid": int(settings.editor_retry_on_invalid),
            "llm_max_attempts": int(settings.llm_max_attempts),
            "llm_retry_base_sleep_s": float(settings.llm_retry_base_sleep_s),
            "writer_min_ratio": float(getattr(settings, "writer_min_ratio", 0.75)),
            "writer_max_ratio": float(getattr(settings, "writer_max_ratio", 1.25)),
            "enable_arc_summary": bool(settings.enable_arc_summary),
            "arc_every_n": int(settings.arc_every_n),
            "arc_recent_k": int(settings.arc_recent_k),
            "auto_apply_updates": str(settings.auto_apply_updates or "off"),
        }
        logger.event("chapter_start", chapter_index=idx)
        chap_id = f"{idx:03d}"
        # === 章节：写手 + 主编（不自动返工） ===
        final_state: StoryState | None = None
        try:
            st = writer_agent(dict(chapter_state))
            st = editor_agent(st)
            final_state = st
            last_state = final_state
        except Exception as e:
            # 关键稳定性：单章失败不拖死整次批量生成
            import traceback as _tb

            err = {
                "chapter_index": idx,
                "error_type": e.__class__.__name__,
                "error": str(e),
                "traceback": "".join(_tb.format_exception(type(e), e, e.__traceback__))[:20000],
            }
            try:
                logger.event(
                    "chapter_error",
                    chapter_index=idx,
                    error_type=err["error_type"],
                    error=err["error"],
                )
            except Exception:
                pass
            try:
                write_json(os.path.join(chapters_dir_current, f"{chap_id}.error.json"), err)  # type: ignore[arg-type]
            except Exception:
                pass
            if bool(args.stop_on_error):
                raise
            # 继续下一章
            continue
        logger.event(
            "chapter_end",
            chapter_index=idx,
            writer_used_llm=bool((final_state or {}).get("writer_used_llm", False)),
            editor_used_llm=bool((final_state or {}).get("editor_used_llm", False)),
            editor_decision=str((final_state or {}).get("editor_decision", "")),
            writer_chars=len((final_state or {}).get("writer_result", "") or ""),
        )

        # 每章落盘
        try:
            write_text(os.path.join(chapters_dir_current, f"{chap_id}.md"), (final_state or {}).get("writer_result", ""))
        except Exception:
            # 落盘失败也不应该拖死整次运行
            if bool(args.stop_on_error):
                raise
            continue

        decision = (final_state or {}).get("editor_decision", "")
        feedback = (final_state or {}).get("editor_feedback", [])
        editor_report = (final_state or {}).get("editor_report") or {}
        canon_suggestions = (final_state or {}).get("canon_suggestions") or []
        canon_update_suggestions = (final_state or {}).get("canon_update_suggestions") or []
        materials_update_suggestions = (final_state or {}).get("materials_update_suggestions") or []
        if decision == "审核通过":
            write_text(os.path.join(chapters_dir_current, f"{chap_id}.editor.md"), "审核通过")
        else:
            lines = ["审核不通过", "", *[f"- {x}" for x in feedback]]
            write_text(os.path.join(chapters_dir_current, f"{chap_id}.editor.md"), "\n".join(lines).strip())

        # 结构化落盘：editor_report（便于后续自动化/追溯）
        if isinstance(editor_report, dict) and editor_report:
            write_json(os.path.join(chapters_dir_current, f"{chap_id}.editor.json"), editor_report)

        # 结构化落盘：canon_suggestions（默认不自动应用，仅供 review）
        if isinstance(canon_suggestions, list) and canon_suggestions:
            write_json(os.path.join(chapters_dir_current, f"{chap_id}.canon_suggestions.json"), {"items": canon_suggestions})

        # 结构化落盘：canon_update_suggestions（来自 chapter memory 的沉淀建议；默认不自动应用）
        if isinstance(canon_update_suggestions, list) and canon_update_suggestions:
            write_json(
                os.path.join(chapters_dir_current, f"{chap_id}.canon_update_suggestions.json"),
                {"items": canon_update_suggestions},
            )

        # 结构化落盘：materials_update_suggestions（复盘会议对“计划类材料”的更新建议；默认不自动应用）
        if isinstance(materials_update_suggestions, list) and materials_update_suggestions:
            write_json(
                os.path.join(chapters_dir_current, f"{chap_id}.materials_update_suggestions.json"),
                {"items": materials_update_suggestions},
            )

        # === 顾问审计（可选） ===
        advisor_path = ""
        advisor_report: dict = {}
        if bool(args.advisor) and isinstance(frozen_obj, dict) and frozen_obj:
            try:
                advisor_report = build_advisor_report(
                    chapter_text=str((final_state or {}).get("writer_result", "") or ""),
                    editor_report=(final_state or {}).get("editor_report"),
                    frozen_pack=frozen_obj,
                    anchors_index=(anchors_obj if isinstance(anchors_obj, dict) else None),
                )
                advisor_path = os.path.join(chapters_dir_current, f"{chap_id}.advisor.json")
                write_json(advisor_path, advisor_report)  # type: ignore[arg-type]
                try:
                    findings0 = advisor_report.get("findings") if isinstance(advisor_report, dict) else []
                    findings_list = findings0 if isinstance(findings0, list) else []
                    rewrite_count = sum(
                        1 for it in findings_list if isinstance(it, dict) and str(it.get("suggest", "") or "") == "rewrite"
                    )
                    digest = str(advisor_report.get("digest", "") or "").strip() or advisor_digest_line(advisor_report)
                    rel_path = os.path.relpath(advisor_path, current_dir).replace("\\", "/")
                    logger.event(
                        "advisor_audit",
                        node="advisor",
                        chapter_index=int(idx),
                        advisor_suggested_action=str(advisor_report.get("suggested_action", "") or "").strip(),
                        advisor_findings_count=len(findings_list),
                        advisor_rewrite_count=int(rewrite_count),
                        advisor_digest=digest,
                        advisor_path=rel_path,
                    )
                except Exception:
                    pass
            except Exception:
                advisor_path = ""
                advisor_report = {}

        # === 总编人审门禁（每章必看） ===
        md_path = os.path.join(chapters_dir_current, f"{chap_id}.md")
        editor_json_path = os.path.join(chapters_dir_current, f"{chap_id}.editor.json")
        snapshot_dir = os.path.join(current_dir, "materials_snapshot")
        # 打印 digest 审阅卡
        try:
            print_chapter_review_card(
                chapter_index=int(idx),
                chap_id=str(chap_id),
                chapter_text=str((final_state or {}).get("writer_result", "") or ""),
                editor_report=(final_state or {}).get("editor_report"),
                materials_frozen_version=str(frozen_version or ""),
                chapter_md_path=md_path,
                editor_json_path=editor_json_path,
                snapshot_dir=snapshot_dir if os.path.exists(snapshot_dir) else "",
                extra_paths={"顾问报告": advisor_path} if advisor_path else {},
                advisor_digest=advisor_digest_line(advisor_report) if advisor_report else "",
            )
        except Exception:
            pass

        # 交互：允许先查看全文/JSON，再做决策
        while True:
            action = prompt_choice(
                "请选择动作",
                choices={
                    "a": "Accept：通过本章（允许沉淀 memory/建议）并进入下一章",
                    "r": "Request Rewrite：给重写指令单，进入下一轮写手+主编",
                    "w": "Waive：认为部分 issues 不成立/不必改（记录原因），然后你仍需 Accept 或继续 Rewrite",
                    "e": "Escalate：触发变更提案（冻结材料/Canon 需要调整），暂停写作并落盘提案请求",
                    "f": "查看全文（chapters/XXX.md）",
                    "j": "查看完整审稿JSON（chapters/XXX.editor.json）",
                    "k": "查看顾问报告JSON（chapters/XXX.advisor.json）",
                    "d": "重新显示 digest",
                    "q": "Quit：退出（保留已产出文件；下次可继续）",
                },
                # 默认强人审；--yes 用于自动化/回归测试（明确跳过门禁交互）
                default=("a" if bool(args.yes) else "q"),
            )
            if action == "f":
                print("\n--- 全文（md）---")
                print(str((final_state or {}).get("writer_result", "") or ""))
                continue
            if action == "j":
                print("\n--- 完整审稿JSON（editor_report）---")
                print_json_preview((final_state or {}).get("editor_report") or {}, max_chars=12000)
                continue
            if action == "k":
                if advisor_report:
                    print("\n--- 顾问报告（advisor_report）---")
                    print_json_preview(advisor_report, max_chars=12000)
                elif advisor_path and os.path.exists(advisor_path):
                    print("\n--- 顾问报告文件路径 ---")
                    print(advisor_path)
                else:
                    print("\n（本章未生成顾问报告；可使用 --advisor 启用）")
                continue
            if action == "d":
                try:
                    print_chapter_review_card(
                        chapter_index=int(idx),
                        chap_id=str(chap_id),
                        chapter_text=str((final_state or {}).get("writer_result", "") or ""),
                        editor_report=(final_state or {}).get("editor_report"),
                        materials_frozen_version=str(frozen_version or ""),
                        chapter_md_path=md_path,
                        editor_json_path=editor_json_path,
                        snapshot_dir=snapshot_dir if os.path.exists(snapshot_dir) else "",
                        extra_paths={"顾问报告": advisor_path} if advisor_path else {},
                        advisor_digest=advisor_digest_line(advisor_report) if advisor_report else "",
                    )
                except Exception:
                    pass
                continue
            break

        if action == "q":
            print("\n已退出：你可以基于 outputs/current 继续。")
            return

        if action == "e":
            # 最小可用：先落盘“提案触发请求”，并停止后续章节推进（后续再实现完整提案FSM）
            reason = prompt_multiline("请输入触发变更提案的原因与影响面（引用冲突点/材料锚点更佳；输入 . 结束）：", end_token=".")
            anchors_raw = prompt_multiline(
                "请输入关联的 anchors（手工填写，不自动猜；可用空格/逗号/换行分隔；输入 . 结束）：",
                end_token=".",
            )
            anchors: list[str] = []
            for part in str(anchors_raw or "").replace(",", " ").replace("，", " ").replace("；", " ").replace(";", " ").split():
                p = part.strip()
                if p and (p not in anchors):
                    anchors.append(p)
            req_obj = {
                "chapter": idx,
                "decision": "escalate_proposal",
                "reason": reason,
                "anchors": anchors,
                "materials_frozen_version": str(frozen_version or ""),
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            write_json(os.path.join(chapters_dir_current, f"{chap_id}.change_proposal_request.json"), req_obj)
            # 项目级提案目录骨架（CP-YYYYMMDD-NNNN）
            try:
                created = create_change_proposal_skeleton(
                    project_dir=project_dir,
                    chapter_index=int(idx),
                    materials_frozen_version=str(frozen_version or ""),
                    reason=str(reason or ""),
                    anchors=anchors,
                    extra={"run_id": str(run_id or ""), "output_dir": str(current_dir or "")},
                )
                write_json(os.path.join(chapters_dir_current, f"{chap_id}.change_proposal_created.json"), created)
                print("\n=== 已创建变更提案目录 ===")
                print(f"- proposal_id：{created.get('proposal_id')}")
                print(f"- dir：{created.get('dir')}")
            except Exception as e:
                print(f"\n（提案目录创建失败：{e}；已保留章节请求文件，稍后可手动创建提案目录）")
            write_json(
                os.path.join(chapters_dir_current, f"{chap_id}.human_review.json"),
                {
                    "chapter": idx,
                    "decision": "escalate_proposal",
                    "notes": reason,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                },
            )
            print("\n已触发变更提案：已暂停写作推进（请先处理提案并重新冻结材料包后再继续）。")
            return

        if action == "w":
            # Waive：记录你认为不必改的 issues（按序号），然后你仍需选择 Accept 或 Rewrite
            rep = (final_state or {}).get("editor_report") if isinstance((final_state or {}).get("editor_report"), dict) else {}
            issues0 = rep.get("issues") if isinstance(rep.get("issues"), list) else []
            print("\n--- 主编 issues（供选择 waive）---")
            for i, it in enumerate(issues0, start=1):
                if not isinstance(it, dict):
                    continue
                t = str(it.get("type", "") or "").strip() or "N/A"
                issue = str(it.get("issue", "") or "").strip()
                print(f"[{i}] ({t}) {issue[:120]}")
            sel = prompt_multiline("请输入要 waive 的 issue 序号（可多行），以及原因（可写在最后几行）；输入 . 结束：", end_token=".")
            write_json(
                os.path.join(chapters_dir_current, f"{chap_id}.human_review.json"),
                {
                    "chapter": idx,
                    "decision": "waive",
                    "waived_issues_raw": sel,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                },
            )
            action = prompt_choice(
                "已记录 waive。接下来请选择：",
                choices={"a": "Accept", "r": "Request Rewrite", "q": "Quit"},
                default=("a" if bool(args.yes) else "a"),
            )
            if action == "q":
                print("\n已退出：你可以基于 outputs/current 继续。")
                return

        # 重写回路（由人驱动，不再自动返工）
        rewrites_used = 0
        while action == "r":
            rewrites_used += 1
            instr = prompt_multiline("请输入本章重写指令单（用于 writer_agent；输入 . 结束）：", end_token=".")
            # 记录人审（request_rewrite）
            write_json(
                os.path.join(chapters_dir_current, f"{chap_id}.human_review.json"),
                {
                    "chapter": idx,
                    "decision": "request_rewrite",
                    "required_fix": [x.strip() for x in instr.splitlines() if x.strip()],
                    "notes": instr,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                },
            )
            # 保存当前版本快照
            try:
                rw_dir = os.path.join(chapters_dir_current, f"{chap_id}.rewrites")
                os.makedirs(rw_dir, exist_ok=True)
                v = int((final_state or {}).get("writer_version", 1) or 1)
                write_text(os.path.join(rw_dir, f"v{v}.md"), str((final_state or {}).get("writer_result", "") or ""))
            except Exception:
                pass

            # 驱动重写
            st2: StoryState = dict(final_state or {})
            st2["needs_rewrite"] = True
            st2["rewrite_instructions"] = instr
            st2["human_decision"] = "request_rewrite"
            st2["human_approved"] = False
            st2 = writer_agent(st2)
            st2 = editor_agent(st2)
            final_state = st2
            # 更新落盘正文与主编报告
            write_text(os.path.join(chapters_dir_current, f"{chap_id}.md"), (final_state or {}).get("writer_result", ""))
            editor_report = (final_state or {}).get("editor_report") or {}
            if isinstance(editor_report, dict) and editor_report:
                write_json(os.path.join(chapters_dir_current, f"{chap_id}.editor.json"), editor_report)
            decision = (final_state or {}).get("editor_decision", "")
            feedback = (final_state or {}).get("editor_feedback", [])
            if decision == "审核通过":
                write_text(os.path.join(chapters_dir_current, f"{chap_id}.editor.md"), "审核通过")
            else:
                lines = ["审核不通过", "", *[f"- {x}" for x in feedback]]
                write_text(os.path.join(chapters_dir_current, f"{chap_id}.editor.md"), "\n".join(lines).strip())

            action = prompt_choice(
                "重写完成，是否通过？",
                choices={"a": "Accept", "r": "继续重写", "q": "退出"},
                default="a",
            )
            if action == "q":
                print("\n已退出：你可以基于 outputs/current 继续。")
                return

        # action == "a"：总编通过，允许沉淀
        write_json(
            os.path.join(chapters_dir_current, f"{chap_id}.human_review.json"),
            {
                "chapter": idx,
                "decision": "accept",
                "created_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        if final_state is None:
            continue
        final_state["human_decision"] = "accept"
        final_state["human_approved"] = True

        # === 通过后才生成/落盘 memory 与沉淀建议 ===
        try:
            final_state = memory_agent(final_state)
            final_state = canon_update_agent(final_state)
            final_state = materials_update_agent(final_state)
        except Exception:
            pass

        mem = (final_state or {}).get("chapter_memory") or {}
        if isinstance(mem, dict) and mem:
            write_json(os.path.join(chapters_dir_current, f"{chap_id}.memory.json"), mem)
            write_json(os.path.join(mem_dirs["chapters_dir"], f"{chap_id}.memory.json"), mem)

        # suggestions（只落盘，后续仍走你已有的 apply-* 交互）
        canon_update_suggestions = (final_state or {}).get("canon_update_suggestions") or []
        materials_update_suggestions = (final_state or {}).get("materials_update_suggestions") or []
        if isinstance(canon_update_suggestions, list) and canon_update_suggestions:
            write_json(os.path.join(chapters_dir_current, f"{chap_id}.canon_update_suggestions.json"), {"items": canon_update_suggestions})
        if isinstance(materials_update_suggestions, list) and materials_update_suggestions:
            write_json(os.path.join(chapters_dir_current, f"{chap_id}.materials_update_suggestions.json"), {"items": materials_update_suggestions})

        # 重要：每章必看模式下，默认不做无人值守自动应用（保持总编控制）

        # === Arc summaries：优先在“Arc 结束”时生成（更贴合卷/副本节奏）；否则每 N 章兜底 ===
        try:
            enable_arc = bool(settings.enable_arc_summary)
            every_n = int(settings.arc_every_n)
            if enable_arc and llm:
                # 1) 优先：基于 materials_bundle.outline 的 arc_id 检测“当前章是否为本Arc最后一章”
                should_write = False
                start_arc = None
                try:
                    mbx = planned_state.get("materials_bundle") if isinstance(planned_state.get("materials_bundle"), dict) else {}
                    outx = mbx.get("outline") if isinstance(mbx.get("outline"), dict) else {}
                    chs = outx.get("chapters") if isinstance(outx.get("chapters"), list) else []
                    cur = None
                    nxt = None
                    for it in chs:
                        if isinstance(it, dict) and int(it.get("chapter_index", 0) or 0) == int(idx):
                            cur = it
                        if isinstance(it, dict) and int(it.get("chapter_index", 0) or 0) == int(idx) + 1:
                            nxt = it
                    cur_arc = str((cur or {}).get("arc_id", "") or "").strip()
                    nxt_arc = str((nxt or {}).get("arc_id", "") or "").strip()
                    # 若下一章存在且 arc_id 不同：说明 idx 是 arc 结束点
                    if cur_arc and nxt is not None and nxt_arc and (cur_arc != nxt_arc):
                        should_write = True
                        # 找到该 arc_id 的最小 chapter_index 作为 start_arc
                        s0 = int(idx)
                        for it in chs:
                            if not isinstance(it, dict):
                                continue
                            if str(it.get("arc_id", "") or "").strip() != cur_arc:
                                continue
                            try:
                                ci = int(it.get("chapter_index", 0) or 0)
                            except Exception:
                                continue
                            if 0 < ci < s0:
                                s0 = ci
                        start_arc = s0
                except Exception:
                    should_write = False
                    start_arc = None

                # 2) 兜底：每 N 章写一次（即使没有 arc_id）
                if (not should_write) and every_n > 0 and (idx % every_n == 0):
                    should_write = True
                    start_arc = max(1, idx - every_n + 1)

                if should_write and start_arc:
                    arc_name = f"arc_{int(start_arc):03d}-{int(idx):03d}.json"
                    arc_path = os.path.join(project_dir, "memory", "arcs", arc_name)
                    if not os.path.exists(arc_path):
                        arc = generate_arc_summary(
                            llm=llm,
                            project_dir=project_dir,
                            start_chapter=int(start_arc),
                            end_chapter=int(idx),
                            logger=logger,
                            llm_max_attempts=int(settings.llm_max_attempts),
                            llm_retry_base_sleep_s=float(settings.llm_retry_base_sleep_s),
                        )
                        if isinstance(arc, dict) and arc:
                            p = write_arc_summary(project_dir, int(start_arc), int(idx), arc)
                            try:
                                logger.event(
                                    "arc_summary_written",
                                    chapter_index=idx,
                                    start_chapter=int(start_arc),
                                    end_chapter=idx,
                                    path=os.path.relpath(p, output_base).replace("\\", "/"),
                                )
                            except Exception:
                                pass
        except Exception as e:
            # Arc 摘要失败不应阻断批量生成
            try:
                logger.event("arc_summary_error", chapter_index=idx, error_type=e.__class__.__name__, error=str(e))
            except Exception:
                pass

    # 控制台输出
    print("=== Planner Result ===")
    print(planner_json or planner_result)

    for idx in range(start_chapter, end_chapter + 1):
        # 只输出每章标题提示；正文/意见请看 outputs 落盘文件
        print(f"\n=== Chapter {idx} ===")
        chap_id = f"{idx:03d}"
        print(f"chapters/{chap_id}.md / chapters/{chap_id}.editor.md / chapters/{chap_id}.memory.json")

    # debug：基于日志生成节点调用图
    if settings.debug:
        events = load_events(os.path.join(current_dir, "logs", "events.full.jsonl"))
        mermaid = build_call_graph_mermaid_by_chapter(events)
        write_text(os.path.join(current_dir, "call_graph.md"), "```mermaid\n" + mermaid + "```\n")

    logger.event("run_end")
    print(f"\n尝试输出目录：{current_dir}")
    # 1) 可选：先应用 Canon 建议（用户确认点 #1）
    if args.apply_canon_suggestions:
        chapters_dir = os.path.join(current_dir, "chapters")
        items = read_canon_suggestions_from_dir(chapters_dir)
        print("\n=== Canon Suggestions Preview ===")
        print(preview_canon_suggestions(items))
        if items:
            stats = apply_canon_suggestions(project_dir=project_dir, items=items, yes=bool(args.yes), dry_run=bool(args.dry_run))
            print(f"已处理 Canon 建议：applied={stats.get('applied')} skipped={stats.get('skipped')}")
            if stats.get("backups"):
                print("已生成备份：")
                for b in stats["backups"]:
                    print(f"- {b}")
        else:
            print("（没有可应用的 canon_suggestions）")

    # 1.5) 可选：应用 Materials 建议（用户确认点）
    if args.apply_materials_suggestions:
        chapters_dir = os.path.join(current_dir, "chapters")
        items = read_materials_suggestions_from_dir(chapters_dir)
        print("\n=== Materials Suggestions Preview ===")
        print(preview_materials_suggestions(items))
        if items:
            stats = apply_materials_suggestions(project_dir=project_dir, items=items, yes=bool(args.yes), dry_run=bool(args.dry_run))
            print(f"已处理 Materials 建议：applied={stats.get('applied')} skipped={stats.get('skipped')}")
            if stats.get("backups"):
                print("已生成备份：")
                for b in stats["backups"]:
                    print(f"- {b}")
        else:
            print("（没有可应用的 materials_update_suggestions）")

    # 2) 可选：归档（用户确认点 #2）
    if args.archive:
        if args.archive_confirm and not _confirm("\n确认将 outputs/current 归档到项目 stages？(y/N) "):
            print("已取消归档（未归档）。")
            return
        if args.dry_run:
            print("dry-run：已跳过实际归档（未复制文件）。")
            return
        archived_dir = archive_run(
            base_dir=output_base,
            project_dir=project_dir,
            stage=settings.stage,
            current_dir=current_dir,
            run_id=run_id,
        )
        print(f"已归档到：{archived_dir}")
    else:
        print("未自动归档（建议先review）。如需归档：")
        print(f'  python src/main.py --config "{args.config}" --archive-only --stage "{settings.stage}"')


if __name__ == "__main__":
    main()

