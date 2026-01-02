from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

from llm import try_get_chat_llm
from state import StoryState
from storage import (
    archive_run,
    ensure_canon_files,
    ensure_memory_dirs,
    get_project_dir,
    make_current_dir,
    read_json,
    write_json,
    write_text,
)
from agents.planner import planner_agent
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
    parser.add_argument("--target-words", type=int, default=None, help="每章目标字数（覆盖配置）")
    parser.add_argument("--chapters", type=int, default=None, help="章节数（覆盖配置）")
    parser.add_argument("--max-rewrites", type=int, default=None, help="每章最多返工次数（覆盖配置）")
    parser.add_argument("--output-base", type=str, default="", help="输出根目录（覆盖配置）")
    parser.add_argument("--stage", type=str, default="", help="阶段名（用于归档，覆盖配置）")
    parser.add_argument("--archive", action="store_true", help="运行结束后自动归档（默认不归档，便于先review）")
    parser.add_argument(
        "--archive-only",
        action="store_true",
        help="只归档当前 outputs/current（用于你review之后手动入库）；不会执行生成流程",
    )
    parser.add_argument("--llm-mode", type=str, default="", help="运行模式：template / llm / auto（覆盖配置）")
    parser.add_argument("--debug", action="store_true", help="开启debug日志（写入debug.jsonl与call_graph.md）")
    args = parser.parse_args()

    config_abs = os.path.abspath(args.config)

    settings = load_settings(
        args.config,
        idea=args.idea,
        output_base=args.output_base,
        stage=args.stage,
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
        if rel_project_dir:
            project_dir = os.path.join(output_base, rel_project_dir.replace("/", os.sep))
        else:
            # 兜底：用 planner.json 的项目名来定位
            planner = read_json(os.path.join(current_dir, "planner.json")) or {}
            project_name = str(planner.get("项目名称") or settings.idea or "story")
            project_dir = get_project_dir(output_base, project_name)

        ensure_canon_files(project_dir)
        ensure_memory_dirs(project_dir)
        archived_dir = archive_run(
            base_dir=output_base,
            project_dir=project_dir,
            stage=stage,
            current_dir=current_dir,
            run_id=run_id,
        )
        print(f"\n已将 current 手动归档到：{archived_dir}")
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

    # 初始化 state（先跑策划一次）
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
    }

    planned_state = planner_agent(base_state)
    planner_result = planned_state.get("planner_result", {})
    planner_json = planned_state.get("planner_json", "")
    project_name = planner_result.get("项目名称") if isinstance(planner_result, dict) else None
    project_name_final = str(project_name or settings.idea or "story")
    project_dir = get_project_dir(output_base, project_name_final)
    ensure_canon_files(project_dir)
    mem_dirs = ensure_memory_dirs(project_dir)
    # 记录真实项目名（不改目录名，仅写入元数据/日志）
    logger.event("project_name", project_name=str(project_name or ""))

    # 落盘策划结果
    write_json(os.path.join(current_dir, "planner.json"), planner_result if isinstance(planner_result, dict) else {})
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
    for idx in range(1, int(settings.gen.chapters) + 1):
        chapter_state: StoryState = {
            **planned_state,
            "chapter_index": idx,
            "writer_version": 0,
            "needs_rewrite": False,
            "editor_feedback": [],
            "editor_decision": "",
            "writer_used_llm": False,
            "editor_used_llm": False,
            "chapter_memory": {},
            "memory_used_llm": False,
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
        if decision == "审核通过":
            write_text(os.path.join(chapters_dir_current, f"{chap_id}.editor.md"), "审核通过")
        else:
            lines = ["审核不通过", "", *[f"- {x}" for x in feedback]]
            write_text(os.path.join(chapters_dir_current, f"{chap_id}.editor.md"), "\n".join(lines).strip())

        # chapter memory：写入 current + 持久化 projects
        mem = final_state.get("chapter_memory") or {}
        if isinstance(mem, dict) and mem:
            write_json(os.path.join(chapters_dir_current, f"{chap_id}.memory.json"), mem)
            # 持久化：项目的 chapter memories
            write_json(os.path.join(mem_dirs["chapters_dir"], f"{chap_id}.memory.json"), mem)

    # 控制台输出
    print("=== Planner Result ===")
    print(planner_json or planner_result)

    for idx in range(1, int(settings.gen.chapters) + 1):
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
    if args.archive:
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

