from __future__ import annotations

import argparse
import json
import os
import sys
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
from settings import load_settings
from workflow import build_chapter_app
from debug_log import RunLogger, load_events, build_call_graph_mermaid_by_chapter
from arc_summary import generate_arc_summary, write_arc_summary
from materials import pick_outline_for_chapter


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
        settings = settings.__class__(
            idea=settings.idea,
            output_base=settings.output_base,
            llm_mode=args.llm_mode.strip().lower(),
            debug=settings.debug,
            gen=settings.gen,
            llm=settings.llm,
        )
    if args.debug:
        settings = settings.__class__(
            idea=settings.idea,
            output_base=settings.output_base,
            llm_mode=settings.llm_mode,
            debug=True,
            gen=settings.gen,
            llm=settings.llm,
        )
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

    logger = RunLogger(path=os.path.join(current_dir, "debug.jsonl"), enabled=bool(settings.debug))
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
        },
    )

    chapter_app = build_chapter_app()
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
        final_state: StoryState | None = None
        try:
            final_state = chapter_app.invoke(chapter_state, config={"recursion_limit": 50})
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

        # chapter memory：写入 current + 持久化 projects
        mem = (final_state or {}).get("chapter_memory") or {}
        if isinstance(mem, dict) and mem:
            write_json(os.path.join(chapters_dir_current, f"{chap_id}.memory.json"), mem)
            # 持久化：项目的 chapter memories
            write_json(os.path.join(mem_dirs["chapters_dir"], f"{chap_id}.memory.json"), mem)

        # === 无人值守：安全自动应用沉淀建议（让后续章节立即受益） ===
        try:
            mode = str(settings.auto_apply_updates or "off").strip().lower()
            if mode == "safe":
                # 1) materials_patch（安全幂等追加）——直接应用到 projects/<project>/materials
                mats_items: list[dict] = []
                if isinstance(materials_update_suggestions, list):
                    for it in materials_update_suggestions:
                        if not isinstance(it, dict):
                            continue
                        act = str(it.get("action", "") or "").strip()
                        if act == "materials_patch":
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
                        chapter_index=idx,
                        mode=mode,
                        materials_items=len(mats_items),
                        canon_items=len(canon_items),
                        materials_applied=int(mats_stats.get("applied", 0) or 0),
                        canon_applied=int(canon_stats.get("applied", 0) or 0),
                    )
                except Exception:
                    pass
        except Exception as e:
            # 自动应用失败不阻断批量生成
            try:
                logger.event("auto_apply_error", chapter_index=idx, error_type=e.__class__.__name__, error=str(e))
            except Exception:
                pass

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
        events = load_events(os.path.join(current_dir, "debug.jsonl"))
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

