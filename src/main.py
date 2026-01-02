from __future__ import annotations

import argparse
import os
import sys

from llm import try_get_chat_llm
from state import StoryState
from storage import make_run_dir, write_json, write_text
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
    parser.add_argument("--llm-mode", type=str, default="", help="运行模式：template / llm / auto（覆盖配置）")
    parser.add_argument("--debug", action="store_true", help="开启debug日志（写入debug.jsonl与call_graph.md）")
    args = parser.parse_args()

    settings = load_settings(
        args.config,
        idea=args.idea,
        output_base=args.output_base,
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
    # 建 run 目录（尽早创建，便于记录 planner 等全程日志）
    os.makedirs(settings.output_base, exist_ok=True)
    run_dir = make_run_dir(settings.output_base, project_name=str(settings.idea)[:40])

    logger = RunLogger(path=os.path.join(run_dir, "debug.jsonl"), enabled=bool(settings.debug))
    logger.event(
        "run_start",
        llm_mode=settings.llm_mode,
        debug=bool(settings.debug),
        target_words=settings.gen.target_words,
        chapters=settings.gen.chapters,
        max_rewrites=settings.gen.max_rewrites,
        output_dir=run_dir,
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
        "output_dir": run_dir,
    }

    planned_state = planner_agent(base_state)
    planner_result = planned_state.get("planner_result", {})
    planner_json = planned_state.get("planner_json", "")
    project_name = planner_result.get("项目名称") if isinstance(planner_result, dict) else None
    # 记录真实项目名（不改目录名，仅写入元数据/日志）
    logger.event("project_name", project_name=str(project_name or ""))

    # 落盘策划结果
    write_json(os.path.join(run_dir, "planner.json"), planner_result if isinstance(planner_result, dict) else {})
    write_json(
        os.path.join(run_dir, "run_meta.json"),
        {
            "llm_mode": settings.llm_mode,
            "planner_used_llm": bool(planned_state.get("planner_used_llm", False)),
            "target_words": settings.gen.target_words,
            "chapters": settings.gen.chapters,
            "max_rewrites": settings.gen.max_rewrites,
            "debug": bool(settings.debug),
            "project_name": str(project_name or ""),
        },
    )

    chapter_app = build_chapter_app()
    last_state: StoryState = planned_state

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
        write_text(os.path.join(run_dir, f"chapter_{idx}.md"), final_state.get("writer_result", ""))
        decision = final_state.get("editor_decision", "")
        feedback = final_state.get("editor_feedback", [])
        if decision == "审核通过":
            write_text(os.path.join(run_dir, f"editor_{idx}.md"), "审核通过")
        else:
            lines = ["审核不通过", "", *[f"- {x}" for x in feedback]]
            write_text(os.path.join(run_dir, f"editor_{idx}.md"), "\n".join(lines).strip())

    # 控制台输出
    print("=== Planner Result ===")
    print(planner_json or planner_result)

    for idx in range(1, int(settings.gen.chapters) + 1):
        # 只输出每章标题提示；正文/意见请看 outputs 落盘文件
        print(f"\n=== Chapter {idx} ===")
        print(f"chapter_{idx}.md / editor_{idx}.md")

    # debug：基于日志生成节点调用图
    if settings.debug:
        events = load_events(os.path.join(run_dir, "debug.jsonl"))
        mermaid = build_call_graph_mermaid_by_chapter(events)
        write_text(os.path.join(run_dir, "call_graph.md"), "```mermaid\n" + mermaid + "```\n")

    logger.event("run_end")
    print(f"\n输出目录：{run_dir}")


if __name__ == "__main__":
    main()

