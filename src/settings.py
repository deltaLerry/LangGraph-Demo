from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, List

from config import LLMConfig, load_llm_config_from_env


@dataclass(frozen=True)
class GenerationSettings:
    target_words: int = 800
    chapters: int = 1
    max_rewrites: int = 2


@dataclass(frozen=True)
class AppSettings:
    # 基础
    idea: str = "一个普通人意外进入修仙世界，被迫卷入宗门纷争"
    output_base: str = "outputs"
    # 阶段：用于归档/持久化记录（例如 stage1 / stage2 / stage3）
    stage: str = "stage1"
    # 写作/审核时注入“最近章节记忆”的数量（只用梗概，不塞全文）
    memory_recent_k: int = 3
    # 是否允许注入“未审核通过”的章节记忆（默认 False，避免污染后续写作/审稿）
    include_unapproved_memories: bool = False

    # 用户风格覆盖/段落规则（便于无需手改 style.md 就能控制文风与段落结构）
    style_override: str = ""
    paragraph_rules: str = ""

    # editor 稳定性参数
    editor_min_issues: int = 2
    editor_retry_on_invalid: int = 1

    # LLM 调用重试（抗网络/限流抖动）
    llm_max_attempts: int = 3
    llm_retry_base_sleep_s: float = 1.0

    # Arc summaries（中程记忆）
    enable_arc_summary: bool = True
    arc_every_n: int = 10
    arc_recent_k: int = 2

    # 无人值守：自动应用沉淀建议（默认关闭）
    # - off：不自动应用（默认，保持人工确认）
    # - safe：只自动应用低风险补丁（例如 world.notes/style.md、tone/outline 的幂等追加）
    auto_apply_updates: str = "off"

    # planner 任务槽位：根据当前启用的 agent 决定拆分哪些任务
    planner_tasks: List[Dict[str, str]] = None  # type: ignore[assignment]

    # 运行模式：template / llm / auto
    llm_mode: str = "auto"

    # debug：写入运行日志/调用图
    debug: bool = False

    # 生成参数
    gen: GenerationSettings = GenerationSettings()

    # LLM（可选：未配则走模板模式）
    llm: Optional[LLMConfig] = None


def _read_toml(path: str) -> Dict[str, Any]:
    if not path or not os.path.exists(path):
        return {}
    with open(path, "rb") as f:
        try:
            import tomllib  # py311+
        except ModuleNotFoundError:  # pragma: no cover
            return {}
        return tomllib.load(f)


def load_settings(
    config_path: str = "config.toml",
    *,
    idea: Optional[str] = None,
    output_base: Optional[str] = None,
    stage: Optional[str] = None,
    memory_recent_k: Optional[int] = None,
    include_unapproved_memories: Optional[bool] = None,
    style_override: Optional[str] = None,
    paragraph_rules: Optional[str] = None,
    editor_min_issues: Optional[int] = None,
    editor_retry_on_invalid: Optional[int] = None,
    llm_max_attempts: Optional[int] = None,
    llm_retry_base_sleep_s: Optional[float] = None,
    enable_arc_summary: Optional[bool] = None,
    arc_every_n: Optional[int] = None,
    arc_recent_k: Optional[int] = None,
    auto_apply_updates: Optional[str] = None,
    target_words: Optional[int] = None,
    chapters: Optional[int] = None,
    max_rewrites: Optional[int] = None,
) -> AppSettings:
    """
    配置优先级：config.toml < 环境变量 < CLI覆盖
    - 环境变量：LLM_* 由 load_llm_config_from_env() 负责；其余用以下变量名
      - IDEA, OUTPUT_BASE, TARGET_WORDS, CHAPTERS, MAX_REWRITES
    """
    # 自动加载 .env（可选依赖；不存在/未安装都不影响运行）
    # 查找顺序：
    # 1) config.toml 同目录的 .env（方便多环境/多配置文件）
    # 2) 项目根目录的 .env（默认推荐：与 config.toml 同级）
    # 3) 当前工作目录的 .env（兜底）
    try:
        from dotenv import load_dotenv  # type: ignore

        candidates = []
        try:
            cfg_dir = os.path.dirname(os.path.abspath(config_path)) if config_path else ""
            if cfg_dir:
                candidates.append(os.path.join(cfg_dir, ".env"))
        except Exception:
            pass

        try:
            repo_root_env = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
            candidates.append(repo_root_env)
        except Exception:
            pass

        candidates.append(os.path.abspath(".env"))

        for p in candidates:
            if p and os.path.exists(p):
                load_dotenv(p, override=False)
                break
    except Exception:
        pass

    raw = _read_toml(config_path)

    # config.toml
    cfg_app = raw.get("app", {}) if isinstance(raw.get("app", {}), dict) else {}
    cfg_llm = raw.get("llm", {}) if isinstance(raw.get("llm", {}), dict) else {}
    cfg_gen = raw.get("generation", {}) if isinstance(raw.get("generation", {}), dict) else {}
    cfg_planner = raw.get("planner", {}) if isinstance(raw.get("planner", {}), dict) else {}

    cfg_idea = str(cfg_app.get("idea", "") or "").strip() or AppSettings.idea
    cfg_output_base = str(cfg_app.get("output_base", "") or "").strip() or AppSettings.output_base
    cfg_stage = str(cfg_app.get("stage", "") or "").strip() or AppSettings.stage
    cfg_memory_recent_k = int(cfg_app.get("memory_recent_k", AppSettings.memory_recent_k))
    cfg_include_unapproved_memories = bool(cfg_app.get("include_unapproved_memories", AppSettings.include_unapproved_memories))
    cfg_style_override = str(cfg_app.get("style_override", "") or "").strip()
    cfg_paragraph_rules = str(cfg_app.get("paragraph_rules", "") or "").strip()
    cfg_editor_min_issues = int(cfg_app.get("editor_min_issues", AppSettings.editor_min_issues))
    cfg_editor_retry_on_invalid = int(cfg_app.get("editor_retry_on_invalid", AppSettings.editor_retry_on_invalid))
    cfg_llm_max_attempts = int(cfg_app.get("llm_max_attempts", AppSettings.llm_max_attempts))
    try:
        cfg_llm_retry_base_sleep_s = float(cfg_app.get("llm_retry_base_sleep_s", AppSettings.llm_retry_base_sleep_s))
    except ValueError:
        cfg_llm_retry_base_sleep_s = AppSettings.llm_retry_base_sleep_s
    cfg_enable_arc_summary = bool(cfg_app.get("enable_arc_summary", AppSettings.enable_arc_summary))
    cfg_arc_every_n = int(cfg_app.get("arc_every_n", AppSettings.arc_every_n))
    cfg_arc_recent_k = int(cfg_app.get("arc_recent_k", AppSettings.arc_recent_k))
    cfg_auto_apply_updates = str(cfg_app.get("auto_apply_updates", AppSettings.auto_apply_updates) or "").strip().lower()
    default_planner_tasks: List[Dict[str, str]] = [
        {"task_name": "世界观设定", "executor": "架构师", "hint": "构建世界背景、规则、势力与核心冲突"},
        {"task_name": "核心角色", "executor": "角色导演", "hint": "产出主要人物卡：性格、动机、能力、禁忌、关系网"},
        {"task_name": "主线脉络", "executor": "编剧", "hint": "给出主线推进的关键节点与前期节奏安排"},
        {"task_name": "开篇基调", "executor": "策划", "hint": "明确文风/视角/节奏/情绪基调"},
    ]

    cfg_tasks_raw = cfg_planner.get("tasks", None)
    cfg_planner_tasks: List[Dict[str, str]] = default_planner_tasks
    if isinstance(cfg_tasks_raw, list) and cfg_tasks_raw:
        cleaned: List[Dict[str, str]] = []
        for it in cfg_tasks_raw:
            if not isinstance(it, dict):
                continue
            tn = str(it.get("task_name", "") or "").strip()
            ex = str(it.get("executor", "") or "").strip()
            hint = str(it.get("hint", "") or "").strip()
            if tn and ex:
                cleaned.append({"task_name": tn, "executor": ex, "hint": hint})
        if cleaned:
            cfg_planner_tasks = cleaned
    cfg_llm_mode = str(cfg_app.get("llm_mode", "") or "").strip().lower() or AppSettings.llm_mode
    cfg_debug = bool(cfg_app.get("debug", AppSettings.debug))

    cfg_target_words = int(cfg_gen.get("target_words", GenerationSettings.target_words))
    cfg_chapters = int(cfg_gen.get("chapters", GenerationSettings.chapters))
    cfg_max_rewrites = int(cfg_gen.get("max_rewrites", GenerationSettings.max_rewrites))

    # 环境变量（非LLM部分）
    env_idea = (os.getenv("IDEA", "") or "").strip()
    env_output_base = (os.getenv("OUTPUT_BASE", "") or "").strip()
    env_stage = (os.getenv("STAGE", "") or os.getenv("APP_STAGE", "") or "").strip()
    env_memory_recent_k = (os.getenv("MEMORY_RECENT_K", "") or "").strip()
    env_include_unapproved_memories = (os.getenv("INCLUDE_UNAPPROVED_MEMORIES", "") or "").strip().lower()
    env_style_override = (os.getenv("STYLE_OVERRIDE", "") or "").strip()
    env_paragraph_rules = (os.getenv("PARAGRAPH_RULES", "") or "").strip()
    env_editor_min_issues = (os.getenv("EDITOR_MIN_ISSUES", "") or "").strip()
    env_editor_retry_on_invalid = (os.getenv("EDITOR_RETRY_ON_INVALID", "") or "").strip()
    env_llm_max_attempts = (os.getenv("LLM_MAX_ATTEMPTS", "") or "").strip()
    env_llm_retry_base_sleep_s = (os.getenv("LLM_RETRY_BASE_SLEEP_S", "") or "").strip()
    env_enable_arc_summary = (os.getenv("ENABLE_ARC_SUMMARY", "") or "").strip().lower()
    env_arc_every_n = (os.getenv("ARC_EVERY_N", "") or "").strip()
    env_arc_recent_k = (os.getenv("ARC_RECENT_K", "") or "").strip()
    env_auto_apply_updates = (os.getenv("AUTO_APPLY_UPDATES", "") or "").strip().lower()
    env_planner_tasks = (os.getenv("PLANNER_TASKS_JSON", "") or "").strip()
    env_llm_mode = (os.getenv("LLM_MODE", "") or "").strip().lower()
    env_debug = (os.getenv("DEBUG", "") or os.getenv("APP_DEBUG", "") or "").strip().lower()
    env_target_words = os.getenv("TARGET_WORDS", "").strip()
    env_chapters = os.getenv("CHAPTERS", "").strip()
    env_max_rewrites = os.getenv("MAX_REWRITES", "").strip()

    def _env_int(v: str, fallback: int) -> int:
        if not v:
            return fallback
        try:
            return int(v)
        except ValueError:
            return fallback

    final_idea = env_idea or cfg_idea
    final_output_base = env_output_base or cfg_output_base
    final_stage = env_stage or cfg_stage
    final_memory_recent_k = cfg_memory_recent_k
    final_include_unapproved_memories = cfg_include_unapproved_memories
    final_style_override = cfg_style_override
    final_paragraph_rules = cfg_paragraph_rules
    final_editor_min_issues = cfg_editor_min_issues
    final_editor_retry_on_invalid = cfg_editor_retry_on_invalid
    final_llm_max_attempts = cfg_llm_max_attempts
    final_llm_retry_base_sleep_s = cfg_llm_retry_base_sleep_s
    final_enable_arc_summary = cfg_enable_arc_summary
    final_arc_every_n = cfg_arc_every_n
    final_arc_recent_k = cfg_arc_recent_k
    final_auto_apply_updates = cfg_auto_apply_updates or AppSettings.auto_apply_updates
    final_planner_tasks = cfg_planner_tasks
    if env_memory_recent_k:
        try:
            final_memory_recent_k = int(env_memory_recent_k)
        except ValueError:
            final_memory_recent_k = cfg_memory_recent_k

    if env_include_unapproved_memories in ("1", "true", "yes", "on"):
        final_include_unapproved_memories = True
    if env_include_unapproved_memories in ("0", "false", "no", "off"):
        final_include_unapproved_memories = False

    if env_style_override:
        final_style_override = env_style_override
    if env_paragraph_rules:
        final_paragraph_rules = env_paragraph_rules

    if env_editor_min_issues:
        try:
            final_editor_min_issues = int(env_editor_min_issues)
        except ValueError:
            final_editor_min_issues = cfg_editor_min_issues
    if env_editor_retry_on_invalid:
        try:
            final_editor_retry_on_invalid = int(env_editor_retry_on_invalid)
        except ValueError:
            final_editor_retry_on_invalid = cfg_editor_retry_on_invalid

    if env_llm_max_attempts:
        try:
            final_llm_max_attempts = int(env_llm_max_attempts)
        except ValueError:
            final_llm_max_attempts = cfg_llm_max_attempts
    if env_llm_retry_base_sleep_s:
        try:
            final_llm_retry_base_sleep_s = float(env_llm_retry_base_sleep_s)
        except ValueError:
            final_llm_retry_base_sleep_s = cfg_llm_retry_base_sleep_s

    if env_enable_arc_summary in ("1", "true", "yes", "on"):
        final_enable_arc_summary = True
    if env_enable_arc_summary in ("0", "false", "no", "off"):
        final_enable_arc_summary = False
    if env_arc_every_n:
        try:
            final_arc_every_n = int(env_arc_every_n)
        except ValueError:
            final_arc_every_n = cfg_arc_every_n
    if env_arc_recent_k:
        try:
            final_arc_recent_k = int(env_arc_recent_k)
        except ValueError:
            final_arc_recent_k = cfg_arc_recent_k

    if env_auto_apply_updates:
        final_auto_apply_updates = env_auto_apply_updates

    if env_planner_tasks:
        try:
            obj = __import__("json").loads(env_planner_tasks)
            if isinstance(obj, list) and obj:
                cleaned: List[Dict[str, str]] = []
                for it in obj:
                    if not isinstance(it, dict):
                        continue
                    tn = str(it.get("task_name", "") or "").strip()
                    ex = str(it.get("executor", "") or "").strip()
                    hint = str(it.get("hint", "") or "").strip()
                    if tn and ex:
                        cleaned.append({"task_name": tn, "executor": ex, "hint": hint})
                if cleaned:
                    final_planner_tasks = cleaned
        except Exception:
            pass
    final_llm_mode = env_llm_mode or cfg_llm_mode
    final_debug = cfg_debug
    if env_debug in ("1", "true", "yes", "on"):
        final_debug = True
    if env_debug in ("0", "false", "no", "off"):
        final_debug = False
    final_target_words = _env_int(env_target_words, cfg_target_words)
    final_chapters = _env_int(env_chapters, cfg_chapters)
    final_max_rewrites = _env_int(env_max_rewrites, cfg_max_rewrites)

    # CLI覆盖（最后生效）
    if idea is not None and idea.strip():
        final_idea = idea.strip()
    if output_base is not None and output_base.strip():
        final_output_base = output_base.strip()
    if stage is not None and stage.strip():
        final_stage = stage.strip()
    if memory_recent_k is not None:
        final_memory_recent_k = int(memory_recent_k)
    if include_unapproved_memories is not None:
        final_include_unapproved_memories = bool(include_unapproved_memories)
    if style_override is not None and style_override.strip():
        final_style_override = style_override.strip()
    if paragraph_rules is not None and paragraph_rules.strip():
        final_paragraph_rules = paragraph_rules.strip()
    if editor_min_issues is not None:
        final_editor_min_issues = int(editor_min_issues)
    if editor_retry_on_invalid is not None:
        final_editor_retry_on_invalid = int(editor_retry_on_invalid)
    if llm_max_attempts is not None:
        final_llm_max_attempts = int(llm_max_attempts)
    if llm_retry_base_sleep_s is not None:
        final_llm_retry_base_sleep_s = float(llm_retry_base_sleep_s)
    if enable_arc_summary is not None:
        final_enable_arc_summary = bool(enable_arc_summary)
    if arc_every_n is not None:
        final_arc_every_n = int(arc_every_n)
    if arc_recent_k is not None:
        final_arc_recent_k = int(arc_recent_k)
    if auto_apply_updates is not None and str(auto_apply_updates).strip():
        final_auto_apply_updates = str(auto_apply_updates).strip().lower()
    if target_words is not None:
        final_target_words = int(target_words)
    if chapters is not None:
        final_chapters = int(chapters)
    if max_rewrites is not None:
        final_max_rewrites = int(max_rewrites)

    # 约束
    final_target_words = max(50, final_target_words)
    final_chapters = max(1, final_chapters)
    final_max_rewrites = max(0, final_max_rewrites)
    final_memory_recent_k = max(0, min(20, int(final_memory_recent_k)))
    final_editor_min_issues = max(0, min(10, int(final_editor_min_issues)))
    final_editor_retry_on_invalid = max(0, min(3, int(final_editor_retry_on_invalid)))
    final_llm_max_attempts = max(1, min(6, int(final_llm_max_attempts)))
    final_llm_retry_base_sleep_s = max(0.2, min(10.0, float(final_llm_retry_base_sleep_s)))
    final_arc_every_n = max(0, min(50, int(final_arc_every_n)))
    final_arc_recent_k = max(0, min(10, int(final_arc_recent_k)))
    if final_auto_apply_updates not in ("off", "safe"):
        final_auto_apply_updates = "off"
    if final_llm_mode not in ("template", "llm", "auto"):
        final_llm_mode = "auto"

    # LLM：优先 env（复用既有逻辑），否则用 toml 的 [llm]
    llm_cfg = load_llm_config_from_env()
    if llm_cfg is None and cfg_llm:
        base_url = str(cfg_llm.get("base_url", "") or "").strip()
        api_key = str(cfg_llm.get("api_key", "") or "").strip()
        model = str(cfg_llm.get("model", "") or "").strip()
        try:
            temperature = float(cfg_llm.get("temperature", 0.7))
        except ValueError:
            temperature = 0.7
        # DeepSeek/OpenAI兼容参数（可选）
        max_tokens = cfg_llm.get("max_tokens", None)
        top_p = cfg_llm.get("top_p", None)
        presence_penalty = cfg_llm.get("presence_penalty", None)
        frequency_penalty = cfg_llm.get("frequency_penalty", None)
        timeout = cfg_llm.get("timeout", None)
        if base_url and api_key and model:
            llm_cfg = LLMConfig(
                base_url=base_url,
                api_key=api_key,
                model=model,
                temperature=temperature,
                max_tokens=int(max_tokens) if max_tokens is not None else None,
                top_p=float(top_p) if top_p is not None else None,
                presence_penalty=float(presence_penalty) if presence_penalty is not None else None,
                frequency_penalty=float(frequency_penalty) if frequency_penalty is not None else None,
                timeout=float(timeout) if timeout is not None else None,
            )

    return AppSettings(
        idea=final_idea,
        output_base=final_output_base,
        stage=final_stage,
        memory_recent_k=final_memory_recent_k,
        include_unapproved_memories=final_include_unapproved_memories,
        style_override=final_style_override,
        paragraph_rules=final_paragraph_rules,
        editor_min_issues=final_editor_min_issues,
        editor_retry_on_invalid=final_editor_retry_on_invalid,
        llm_max_attempts=final_llm_max_attempts,
        llm_retry_base_sleep_s=final_llm_retry_base_sleep_s,
        enable_arc_summary=final_enable_arc_summary,
        arc_every_n=final_arc_every_n,
        arc_recent_k=final_arc_recent_k,
        auto_apply_updates=final_auto_apply_updates,
        planner_tasks=final_planner_tasks,
        llm_mode=final_llm_mode,
        debug=final_debug,
        gen=GenerationSettings(
            target_words=final_target_words,
            chapters=final_chapters,
            max_rewrites=final_max_rewrites,
        ),
        llm=llm_cfg,
    )


