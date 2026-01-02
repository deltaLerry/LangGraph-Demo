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

    # Canon 初始化（阶段2.2）
    canon_init_used_llm: bool

    planner_result: Dict[str, Any]
    planner_json: str
    planner_used_llm: bool
    planner_tasks: List[Dict[str, str]]

    writer_result: str
    writer_version: int
    writer_used_llm: bool

    editor_decision: str  # "审核通过" | "审核不通过"
    editor_feedback: List[str]
    needs_rewrite: bool
    editor_used_llm: bool

    # 章节记忆（审核通过后生成，用于长期一致性）
    chapter_memory: Dict[str, Any]
    memory_used_llm: bool

