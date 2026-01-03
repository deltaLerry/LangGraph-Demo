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
from agents.materials_init import materials_init_agent
from settings import load_settings
from workflow import build_chapter_app
from debug_log import RunLogger, load_events, build_call_graph_mermaid_by_chapter


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

    config_abs = os.path.abspath(args.config)

    # idea 支持从文件读取（优先级最高）
    idea_from_file: str | None = None
    if args.idea_file and args.idea_file.strip():
        idea_path = args.idea_file.strip()
        if not os.path.isabs(idea_path):
            idea_path = os.path.join(os.path.dirname(config_abs), idea_path)
        if not os.path.exists(idea_path):
            raise FileNotFoundError(f"未找到 idea 文件：{idea_path}")
        # 支持 UTF-8 BOM
        with open(idea_path, "r", encoding="utf-8-sig") as f:
            idea_from_file = f.read().strip()
        if not idea_from_file:
            raise ValueError(f"idea 文件内容为空：{idea_path}")

    settings = load_settings(
        args.config,
        idea=idea_from_file if idea_from_file is not None else args.idea,
        output_base=args.output_base,
        stage=args.stage,
        memory_recent_k=args.memory_recent_k,
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
    # output_base 若为相对路径，则相对 config.toml 所在目录解析（避免从 src/ 运行跑到 src/outputs）
    output_base = settings.output_base
    if not os.path.isabs(output_base):
        output_base = os.path.join(os.path.dirname(config_abs), output_base)
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
        "planner_tasks": settings.planner_tasks or [],
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
        }
        logger.event("chapter_start", chapter_index=idx)
        final_state = chapter_app.invoke(chapter_state, config={"recursion_limit": 50})
        last_state = final_state
        logger.event(
            "chapter_end",
            chapter_index=idx,
            writer_used_llm=bool(final_state.get("writer_used_llm", False)),
            editor_used_llm=bool(final_state.get("editor_used_llm", False)),
            editor_decision=str(final_state.get("editor_decision", "")),
            writer_chars=len(final_state.get("writer_result", "") or ""),
        )

        # 每章落盘
        chap_id = f"{idx:03d}"
        write_text(os.path.join(chapters_dir_current, f"{chap_id}.md"), final_state.get("writer_result", ""))
        decision = final_state.get("editor_decision", "")
        feedback = final_state.get("editor_feedback", [])
        editor_report = final_state.get("editor_report") or {}
        canon_suggestions = final_state.get("canon_suggestions") or []
        canon_update_suggestions = final_state.get("canon_update_suggestions") or []
        materials_update_suggestions = final_state.get("materials_update_suggestions") or []
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
        mem = final_state.get("chapter_memory") or {}
        if isinstance(mem, dict) and mem:
            write_json(os.path.join(chapters_dir_current, f"{chap_id}.memory.json"), mem)
            # 持久化：项目的 chapter memories
            write_json(os.path.join(mem_dirs["chapters_dir"], f"{chap_id}.memory.json"), mem)

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

