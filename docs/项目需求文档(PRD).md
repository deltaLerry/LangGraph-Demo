### LangGraph 写作助手（半自动 Human-in-the-loop）项目需求文档（PRD）

### 1. 背景与目标
- **背景**：一次性全自动生成大量章节无法达到稳定效果，需要转向“高级写作助手”，支持强人工介入与强一致性约束。
- **本期目标**：以“稳定性/一致性/可追溯”为第一优先级，将全自动流程改造为半自动工作流（写作前冻结材料包；写作中每章必审；冻结后变更必须走提案）。
- **后续方向（非本期）**：降低人工介入、提高质量、支持更大章节数、效率优化、Web 工作台等。

### 2. 角色与职责（Actors）
- **总编（人）**：材料包冻结、每章最终验收、变更提案审批的唯一拍板者。
- **写手 Agent**：基于冻结材料包写章节；按“重写指令单”重写。
- **主编 Agent（审稿）**：对章节输出结构化 issues/冲突/建议；不得绕过门禁直接修改冻结材料/Canon。
- **材料专家 Agents**：世界观/人物/细纲/基调等材料产出与互审收敛。
- **顾问 Agent（咨询/风控/一致性审计）**：回答总编问题；做一致性审计；输出“指令单草案”；不得绕过变更提案修改冻结材料。
- **系统（工作流）**：状态机编排、门禁阻塞、落盘、日志与索引。

### 3. 关键约束（必须满足）
- **强门禁**
  - **写作门禁**：无 `materials_pack.frozen.vN`（冻结材料包）→ 禁止进入第 1 章写作。
  - **章节门禁**：每章必须有 `human_review`（总编决策）→ 才允许沉淀 memory / 进入下一章。
  - **冻结后变更门禁**：任何影响后续写作的修改 → 一律走 **变更提案（Change Proposal）**。
- **每章必看**：每章必须经过“Agent审稿 + 总编验收”，不可跳过。
- **项目隔离**：不同书稿必须是不同 project 目录，严禁跨项目读写 Canon/Materials/Memory/Changes。

### 4. 核心工作流（业务流程）

### 4.1 写作前：材料包工作流（Materials FSM）
目标：产出可长期引用的冻结材料包，作为写作期的唯一上游口径。
- **Intake（人输入）**：点子/题材定位/禁忌/字数与章节规划/风格要求/硬约束。
- **Draft（agent 首版）**：产出材料包 v1。
- **Agent Review Loop（反复讨论收敛）**：专家互审、发现冲突/缺口、提出裁决与补齐；迭代 v2/v3…
- **Human Review Gate（总编必审）**：
  - **Approve & Freeze**：冻结为 `materials_pack.frozen.vN`
  - **Request Changes**：给“材料包修改指令单”，退回继续迭代
  - **Answer Questions**：回答 `open_questions`（blocker 必须清零）

### 4.2 写作中：章节工作流（Chapter FSM）
目标：每章稳定产出并通过总编验收，才允许沉淀与继续。
- **Draft（写手）**：产出章节稿
- **Agent Review（主编审稿）**：输出结构化 `editor_report`（issues/冲突/建议）
- **Human Review Gate（总编必审）**：
  - **Accept**：通过本章（允许沉淀 memory、进入下一章）
  - **Request Rewrite**：提交重写指令单（引用材料锚点），进入下一轮
  - **Waive/Skip**：认为该 issue 不成立/不必改（记录原因，不重写）
  - **Escalate → Change Proposal**：发现需要调整冻结材料/Canon，暂停章节流，进入提案流
- **Deposit**：仅在 Accept 后执行：写入章节 memory、归档快照（按配置/确认）。

### 4.3 冻结后变更：变更提案工作流（Change Proposal FSM）
目标：冻结后任何影响后续写作的改动，必须可评估、可迁移、可回滚。
- **Draft Proposal**：提出 what/why/impact/migration/alternatives
- **Advisor Review（顾问审）**：给风险与影响面评估、提示迁移雷区
- **Human Approve/Reject（总编拍板）**
- **Migration**：执行迁移（必要时重审/重写/回滚记忆）
- **Re-Freeze**：产出新的 `materials_pack.frozen.vN+1`，并记录提案链路

### 5. 材料包范围（“所有影响后续写作的材料”）
材料包按四层组织（冻结必备）：
- **Canon（真值层）**：`world / characters / timeline / style_guide`
- **Planning（计划层）**：`outline（至少可支撑未来 3~5 章写作） / tone`
- **Execution（裁剪层）**：`decisions / checklists / glossary / constraints`
- **Risk & Questions（风控层）**：`risks / open_questions（含 blocker/high/low + 默认假设 + 影响面）`

### 6. 引用机制（Anchors）
目标：总编能精确引用材料进行指示，避免口头歧义与模型“自说自话”。
- **锚点 ID**：对可引用条目分配稳定 ID，如 `DEC-017`、`CON-003`、`GLO-012`、`CHAR-004`、`WR-005`、`TL-009`。
- **章节重写指令单**：必须包含 `anchors[] + evidence_quote + required_fix + acceptance_criteria`。

### 7. 冻结 DoD（完备/一致/可执行/无阻塞）
冻结不以单纯数量阈值为准，采用硬条件清单：
- **完备性**：Required 字段齐全；可推进未来 3~5 章；核心术语入 glossary（缺失→open_questions）。
- **一致性**：冲突为 0 或全部裁决入 decisions；Planning/Execution 不得覆盖 Canon。
- **可执行性**：decisions/checklists 可验收（能判定“满足/不满足”）。
- **不确定项**：blocker=0；high/low 必须写清默认假设与影响面。

### 8. 日志需求（双日志）
- **全量日志**：完整可追溯（含全文/请求响应/差异/堆栈），支持大字段外置 payload。
- **查询日志**：实时过滤/聚合（短字段 + 指针），可重建。

### 9. 非目标（本期不做）
- Web 工作台 UI（但数据与接口需为未来迁移预留）
- 追求最少人工介入/最高效率（后续迭代）
- 超大规模章节数与性能极限优化（先稳定）


