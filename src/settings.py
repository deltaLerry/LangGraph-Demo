from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

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

    cfg_idea = str(cfg_app.get("idea", "") or "").strip() or AppSettings.idea
    cfg_output_base = str(cfg_app.get("output_base", "") or "").strip() or AppSettings.output_base
    cfg_stage = str(cfg_app.get("stage", "") or "").strip() or AppSettings.stage
    cfg_llm_mode = str(cfg_app.get("llm_mode", "") or "").strip().lower() or AppSettings.llm_mode
    cfg_debug = bool(cfg_app.get("debug", AppSettings.debug))

    cfg_target_words = int(cfg_gen.get("target_words", GenerationSettings.target_words))
    cfg_chapters = int(cfg_gen.get("chapters", GenerationSettings.chapters))
    cfg_max_rewrites = int(cfg_gen.get("max_rewrites", GenerationSettings.max_rewrites))

    # 环境变量（非LLM部分）
    env_idea = (os.getenv("IDEA", "") or "").strip()
    env_output_base = (os.getenv("OUTPUT_BASE", "") or "").strip()
    env_stage = (os.getenv("STAGE", "") or os.getenv("APP_STAGE", "") or "").strip()
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
        llm_mode=final_llm_mode,
        debug=final_debug,
        gen=GenerationSettings(
            target_words=final_target_words,
            chapters=final_chapters,
            max_rewrites=final_max_rewrites,
        ),
        llm=llm_cfg,
    )


