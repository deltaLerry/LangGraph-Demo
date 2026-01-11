### LangGraph 写作助手（半自动 Human-in-the-loop）项目开发文档（Tech Spec）

### 1. 技术目标与原则
- **单一真值来源**：冻结材料包（`materials_pack.frozen.vN`）为写作期唯一可引用上游口径。
- **强门禁**：无冻结材料包不可写；每章必须有人审决策才可沉淀与继续；冻结后变更必须走提案。
- **强可追溯**：所有关键产物、决策、提案、迁移均落盘；日志可重放。
- **强隔离**：每本书一个 project 目录；严禁跨项目读写与污染。
- **可扩展**：CLI 先行；数据结构可迁移到 Web 工作台；查询日志可升级为 SQLite。

### 2. 状态机设计（两层 + 变更提案）

### 2.1 Materials FSM（写作前）
状态：`Intake → Draft(v1) → AgentReviewLoop(vN) → HumanReviewGate → Freeze(frozen.vN)`
- **阻塞条件（blocked）**：
  - `open_questions.blocker > 0`
  - Required 字段缺失或为空
  - 存在未裁决冲突（conflict 未 resolved）

### 2.2 Chapter FSM（写作中）
状态：`Draft → AgentReview → HumanReviewGate → Deposit → NextChapter`
- **升级条件（escalate）**：发现需要调整冻结材料/Canon 的事项 → 进入 Change Proposal FSM 并暂停章节推进。

### 2.3 Change Proposal FSM（冻结后）
状态：`DraftProposal → AdvisorReview → HumanApprove/Reject → Migration → ReFreeze(frozen.vN+1)`

### 3. 存储结构与约束（本地文件系统）
根：`outputs/`

### 3.1 项目长期资产（唯一长期入口）
`outputs/projects/<project>/`
- **`canon/`**：真值层（仅通过变更提案迁移后写入）
  - `world.json` `characters.json` `timeline.json` `style.md`（或 `style_guide.json`）
- **`materials/`**：材料包与版本史
  - `drafts/`：`materials_pack.vNNN.json`
  - `frozen/`：`materials_pack.frozen.vNNN.json`
  - `reviews/`：`agent_review.vNNN.json`、`human_review.vNNN.json`
  - `digests/`：`*.digest.json`（审阅卡摘要）
  - `anchors/`：`anchors.vNNN.json`
  - `index.json`：当前生效冻结版本指针（`current_frozen_version`）
- **`changes/`**：冻结后唯一修改入口
  - `proposals/CP-YYYYMMDD-NNNN/`：提案/顾问审/人审/迁移/日志/diff
  - `backlog.json`（可选）：被总编“先记着”的候选项
- **`memory/`**：仅 Accept 后写入
  - `chapters/001.memory.json`
  - `arcs/arc_001-010.json`
- **`stages/<stage>/runs/<run_id>/`**：归档快照（包含材料快照、章节产物、日志）

### 3.2 会话工作区（可丢弃/可重跑）
`outputs/current/`
- `run_meta.json`：绑定 `project_dir`、`frozen_version`、`proposals_used` 等
- `materials_snapshot/`：本次会话引用的冻结材料包快照（只读）
- `chapters/`
  - `001.md`
  - `001.editor.json`
  - `001.human_review.json`
  - `001.rewrites/v1.md`、`v2.md`（可选）
- `logs/`：全量/索引日志与 payload

### 4. 数据契约（核心 JSON 结构）

### 4.1 冻结材料包：`materials_pack.frozen.vNNN.json`（建议顶层结构）
- **meta**
  - `project` `version` `frozen_at` `source_runs[]` `proposals_applied[]`
- **canon**：`world` `characters` `timeline` `style_guide`
- **planning**：`outline` `tone`
- **execution**
  - `decisions[]`
  - `checklists`（`global/per_arc/per_chapter`）
  - `glossary`（分类字典或数组均可，但必须可索引）
  - `constraints`（字数区间/段落规则/POV/敏感边界等）
- **risk**
  - `risks[]`
  - `open_questions[]`（含 `severity`、`default_assumption`、`impact`、`blocking`）
- **changelog[]**
  - 每轮裁决与原因（可包含 `anchors[]` 关联）

### 4.2 锚点索引：`anchors.vNNN.json`
目标：将 `DEC/CON/GLO/CHK/CHAR/WR/TL/...` 映射到稳定路径，便于引用与审计。
- `anchors`: `{ "DEC-017": {"path":"execution.decisions[?id==DEC-017]", "title":"..."}, ... }`
- （可选）`reverse_index`：path → id

### 4.3 章节人审：`chapters/001.human_review.json`
字段（最小可用）：
- `chapter`
- `decision`: `accept | request_rewrite | waive | escalate_proposal`
- `conflicts[]`: `{anchors[], evidence_quote, why_conflict}`
- `required_fix[]`
- `acceptance_criteria[]`
- `waived_issues[]`: `{issue_id, reason}`
- `notes`

### 4.4 变更提案：`changes/proposals/<id>/proposal.json`
字段（最小可用）：
- `proposal_id`
- `what`：字段路径级别变更说明
- `why`：触发证据（章节号/issue/quote）
- `impact`：影响章节范围/是否回滚/是否污染记忆
- `migration_plan`
- `alternatives`

### 5. 日志系统（双日志 + payload 外置）

### 5.1 文件布局
`outputs/<current_or_run>/logs/`
- `events.full.jsonl`：全量事件流（只追加）
- `events.index.jsonl`：可查询事件流（只追加；可重建）
- `payloads/`：大字段（prompt/全文/差异/traceback）外置
- `run.summary.json`、`chapters/001.summary.json`（可选派生）

### 5.2 统一事件字段（index 与 full 共享核心字段）
- `event_id` `ts` `seq`
- `run_id` `project` `stage` `chapter`（材料包/全局用 0）
- `phase`：`materials | chapter | change_proposal | archive`
- `node`：writer/editor/materials_pack_loop/...
- `event_type`：见 5.3
- `actor`：`agent | human | system`
- `status`：`start | end | ok | error | blocked`
- `duration_ms`（end 事件写）
- `anchors[]`
- `artifact_paths[]`
- `payload_refs`（`{field: {path, chars, sha1?}}`）
- `message`（短消息，index 用）

### 5.3 event_type 枚举（最小集合）
- **Span**：`run_start/run_end`、`phase_start/phase_end`、`node_start/node_end`
- **LLM**：`llm_request/llm_response`（正文进入 payload）
- **Gate**：`human_gate_open`、`human_decision`、`blocked`
- **质量/一致性**：`conflict_detected`、`open_question_raised`、`checklist_failed`、`glossary_drift`
- **提案**：`proposal_created`、`proposal_advisor_reviewed`、`proposal_approved/rejected`、`migration_started/completed`、`refreeze_completed`
- **落盘/归档**：`artifact_written`、`archive_started/completed`

### 6. 人工介入交互（CLI 先行，Web 可迁移）
- **动作收敛为 4 类按钮**：Accept / Request Changes(Rewrite) / Waive(Skip) / Escalate Proposal
- **默认 digest**：审阅卡 1 屏内；支持展开全文（full）与引用定位（anchors）
- **指令单输入**：结构化模板（anchors + evidence_quote + required_fix + acceptance_criteria）
- **顾问预审**：在你看到章节/材料前先输出“冲突候选 + 建议动作 + 指令单草案”


