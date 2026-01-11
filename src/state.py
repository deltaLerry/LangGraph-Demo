from typing import TypedDict, Dict, Any, List, Optional

class StoryState(TypedDict, total=False):
    llm: Any
    llm_mode: str
    force_llm: bool
    debug: bool
    logger: Any
    user_input: str
    target_words: int
    max_rewrites: int
    chapter_index: int
    chapters_total: int

    output_dir: str
    # 持久化项目目录（outputs/projects/<project>），用于读取 canon/memory
    project_dir: str
    stage: str
    # 写作/审核注入最近章节记忆数量（只用梗概 summary）
    memory_recent_k: int
    # 是否允许注入“未审核通过”的章节记忆（默认 False，避免污染）
    include_unapproved_memories: bool

    # 用户风格覆盖/段落规则（便于无需手改 style.md 就能控制文风与段落结构）
    style_override: str
    paragraph_rules: str
    # 重写/重申时用户额外指导意见（只影响本次 rewrite/restate，不自动写入 Canon）
    rewrite_instructions: str

    # === Human-in-the-loop（总编门禁）===
    # 总编对本章的最终决策：accept / request_rewrite / waive / escalate_proposal
    human_decision: str
    # 总编是否通过本章（用于记忆沉淀门禁；优先于 editor_decision）
    human_approved: bool
    # 总编审阅备注/指令（原文，便于追溯）
    human_notes: str
    # 总编判定的冲突锚点引用（可选：用于追溯与后续自动审计）
    conflict_anchors: List[str]

    # idea-file 点子包原文与解析结果（用于追溯/调试）
    idea_source_text: str
    idea_file_path: str
    idea_intake: Dict[str, Any]
    project_name_hint: str

    # editor 稳定性参数
    editor_min_issues: int
    editor_retry_on_invalid: int

    # LLM 调用重试（抗网络/限流抖动）
    llm_max_attempts: int
    llm_retry_base_sleep_s: float

    # writer 字数阈值（触发自动续写/缩稿）
    writer_min_ratio: float
    writer_max_ratio: float

    # materials_pack 总编打磨
    materials_pack_max_rounds: int
    materials_pack_min_decisions: int

    # Arc summaries（中程记忆）
    enable_arc_summary: bool
    arc_every_n: int
    arc_recent_k: int

    # 无人值守：自动应用沉淀建议（off/safe）
    auto_apply_updates: str

    # Canon 初始化（阶段2.2）
    canon_init_used_llm: bool
    # Canon 增量更新（阶段2：从 chapter memory 沉淀回 canon）
    canon_update_used: bool
    # Canon 增量更新建议（从 chapter memory 提炼出的可应用补丁；默认只落盘，需用户确认后再 apply）
    canon_update_suggestions: List[Dict[str, Any]]

    planner_result: Dict[str, Any]
    planner_json: str
    planner_used_llm: bool
    planner_tasks: List[Dict[str, str]]

    writer_result: str
    writer_version: int
    writer_used_llm: bool

    editor_decision: str  # "审核通过" | "审核不通过"
    editor_feedback: List[str]
    # editor 的结构化报告（用于落盘与后续自动化处理）
    editor_report: Dict[str, Any]
    # 建议更新 Canon 的结构化条目（默认不自动应用，只落盘供人工 review）
    canon_suggestions: List[Dict[str, Any]]
    needs_rewrite: bool
    editor_used_llm: bool

    # 章节记忆（审核通过后生成，用于长期一致性）
    chapter_memory: Dict[str, Any]
    memory_used_llm: bool

    # === 阶段3：多角色并行材料（run级别产出，供后续章节写作/审核注入） ===
    architect_result: Dict[str, Any]         # 世界观/规则/势力/地点（结构化）
    character_director_result: Dict[str, Any] # 人物卡（结构化）
    screenwriter_result: Dict[str, Any]      # 主线+章节细纲（结构化）
    tone_result: Dict[str, Any]              # 开篇基调/文风约束（结构化）

    # 汇总后的“材料包”（写手只读这一份，避免上游多源拼 prompt）
    materials_bundle: Dict[str, Any]
    materials_used_llm: bool

    # （可选）便于 debug/追溯：各专家是否使用了 LLM
    architect_used_llm: bool
    character_director_used_llm: bool
    screenwriter_used_llm: bool
    tone_used_llm: bool

    # === 阶段3：材料复盘会议（materials_update） ===
    # 说明：update 默认不直接写 materials/canon，只产出建议，走“预览→确认→应用”
    materials_update_used: bool
    materials_update_suggestions: List[Dict[str, Any]]

