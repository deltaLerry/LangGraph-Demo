# LangGraph-Demo
一个LangGraph的demo。

## 快速开始（Windows）

### 安装依赖

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 运行（模板模式：无需配置大模型）

```bash
python src\main.py --idea '一个普通人意外进入修仙世界，被迫卷入宗门纷争'
```

运行后会写入：
- `outputs/current/`：本次尝试输出（每次运行覆盖）
- `outputs/projects/<project>/`：持久化目录（Canon / chapter memory / 手动归档）

如果控制台中文出现乱码，可先执行：

```bash
chcp 65001
```

### 运行（LLM模式：可选）

本项目用 **OpenAI兼容方式** 接入DeepSeek（通过`langchain-openai`）。

#### 方式A：Windows PowerShell 直接设置环境变量（推荐）

```bash
$env:LLM_BASE_URL = "https://api.deepseek.com"   # 程序会自动补 /v1
$env:LLM_API_KEY  = "你的DeepSeek API Key"
$env:LLM_MODEL    = "deepseek-chat"
python src\main.py --idea '...' --target-words 800 --chapters 3 --max-rewrites 2
```

#### 方式B：配置文件

复制`env.example`为`.env`（或直接设置系统环境变量）并填写：
- `LLM_BASE_URL`（例如`https://api.deepseek.com`或`https://api.deepseek.com/v1`）
- `LLM_API_KEY`
- `LLM_MODEL`（例如`deepseek-chat`）

然后执行：

```bash
python src\main.py --idea "..." --target-words 800 --chapters 3 --max-rewrites 2
```

`.env`推荐放在**项目根目录**（与`config.toml`同级）。程序启动时会自动按以下顺序查找并加载：
- `--config`指定的配置文件同目录下的`.env`
- 项目根目录的`.env`
- 当前工作目录的`.env`（兜底）

## 最小配置示例（推荐做法：config.toml + .env）

目标：把**非敏感默认值**放进 `config.toml`，把 **API Key** 放进 `.env`（不要提交到 git）。

### 1) `config.toml`（示例）

（仓库已自带一个 `config.toml`，你也可以新建/修改）

```toml
[app]
idea = "一个普通人意外进入修仙世界，被迫卷入宗门纷争"
output_base = "outputs"
stage = "stage1"
memory_recent_k = 3
llm_mode = "auto" # auto/llm/template
debug = true

[generation]
target_words = 800
chapters = 1
max_rewrites = 2
```

### 2) `.env`（示例）

在项目根目录创建 `.env`，写入：

```bash
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=sk-REPLACE_ME
LLM_MODEL=deepseek-chat
```

### 3) 运行

- 自动模式（推荐：有 LLM 就用 LLM，没有就走模板）：

```bash
python src\main.py
```

- 强制走 LLM（LLM 配置不完整会直接报错）：

```bash
python src\main.py --llm-mode llm
```

## 输出目录说明（落盘结构）

- `outputs/current/`（一次尝试）
  - `planner.json`
  - `run_meta.json`
  - `chapters/001.md`
  - `chapters/001.editor.md`（可读版）
  - `chapters/001.editor.json`（结构化主编报告）
  - `chapters/001.canon_suggestions.json`（可选：主编建议“更新设定”的补丁，仅供 review）
  - `chapters/001.memory.json`（仅“审核通过”时生成 memory）
  - `debug.jsonl` / `call_graph.md`（开启 debug 时）
- `outputs/projects/<project>/`（持久化）
  - `canon/`：`world.json` / `characters.json` / `timeline.json` / `style.md`
  - `memory/chapters/`：长期 chapter memory（用于续写/一致性）
  - `stages/<stage>/runs/<run_id>/`：手动归档后的快照（review 通过后再入库）
  - `project_meta.json`：项目元信息（用于 `--resume` 复用策划）

## Debug日志与节点调用图
开启debug后，每次运行会在输出目录生成：
- `debug.jsonl`：结构化运行日志（节点开始/结束、LLM输入输出、耗时、异常等）
- `call_graph.md`：基于日志自动生成的“节点调用图”（Mermaid）

开启方式：

```bash
python src\main.py --debug
```

## 默认值配置（config.toml）
项目根目录提供了一个可选的`config.toml`，用于集中管理默认值（模型/API、字数、章节数、返工次数、输出目录等）。

- **优先级**：`config.toml < 环境变量 < CLI参数`
- **指定配置文件路径**：

```bash
python src\main.py --config config.toml
```

### 常用 CLI 参数

- `--idea-file path/to/idea.txt`：从文件读取用户点子（UTF-8），优先级最高（覆盖 `--idea/config/env`）
- `--stage stage1`：归档阶段名（用于 `stages/<stage>/...`）
- `--memory-recent-k 3`：注入最近章节“梗概记忆”的数量（只注入 summary）
- `--include-unapproved-memories`：注入记忆时包含“未审核通过”的章节（默认不包含，避免污染；仅调试时用）
- `--style "..."` / `--style-file path/to/style.txt`：本次运行的文风覆盖（注入 writer/editor；不自动写入 `canon/style.md`）
- `--paragraph-rules "..."`：段落/结构规则（例如每段<=120字、多对话、少旁白等）
- `--editor-min-issues 2`：主编拒稿时至少给出多少条 issues（默认2）
- `--editor-retry-on-invalid 1`：主编 JSON 不合法/issue过少时自动修复重试次数（默认1）
- `--stop-on-error`：遇到单章异常时立即中止（默认：记录错误并继续跑后续章节，适合后台批量生成）
- `--llm-max-attempts 3` / `--llm-retry-base-sleep-s 1.0`：LLM 调用重试（抗限流/网络抖动，适合无人值守批量生成）
- `--disable-arc-summary`：禁用分卷/Arc摘要（默认启用）
- `--arc-every-n 10`：每 N 章生成一个 Arc 摘要（默认10，设为0等同禁用生成）
- `--arc-recent-k 2`：写作/审稿注入最近 K 个 Arc 摘要（默认2）
- `--auto-apply-updates safe`：无人值守自动应用沉淀建议（默认 off）。safe 只自动应用低风险补丁（`world.notes`/`style.md` 追加 + `materials` 幂等追加），便于 150 章批量生成时逐章增强一致性。

## idea-file「点子包」格式（推荐）
你可以把**项目名/文风/段落规则/点子正文**写在同一个文件里交给策划（planner）解析并注入工作流。

示例：`examples/idea_pack_example.txt`

## 无人值守批量生成（后台运行）
目标：**除了准备好 `--idea-file` 与 LLM 配置外，不需要人工参与**，可后台连续生成（例如 150 章 × 5000 字）。

### 前置条件
- **LLM 已配置**：推荐用 `.env` 或 `config.toml` 的 `[llm]`（示例见本文上方“最小配置示例”）。
- **点子包文件**：推荐按 `examples/idea_pack_example.txt` 的格式写（可含项目名/文风/段落规则/点子）。

推荐直接拷贝本仓库提供的长跑模板（职责分离）：
- `examples/config_150x5000.toml`：只放生成/稳定性/记忆/沉淀等**非敏感参数**
- `examples/env_150x5000.example`：只放 **LLM_*（含 API Key）**

### 推荐：无人值守批量生成（稳 + 可追溯 + 低风险自动沉淀）
特点：
- **默认不停机**：单章异常会落盘 `chapters/XXX.error.json` 并继续后续章节（避免长跑“全盘崩”）。
- **自动沉淀（safe）**：逐章把低风险建议写回项目资产，后续章节立刻受益（减少长程矛盾）。

PowerShell 示例（把路径/项目名改成你的）：

```bash
python main.py \
  --config "config.toml" \
  --llm-mode llm \
  --idea-file "test-idea.md" \
  --project "无限：规则怪谈" \
  --target-words 3500 \
  --chapters 200 \
  --max-rewrites 1 \
  --editor-min-issues 2 \
  --editor-retry-on-invalid 1 \
  --llm-max-attempts 3 \
  --llm-retry-base-sleep-s 10.0 \
  --memory-recent-k 3 \
  --arc-every-n 10 \
  --arc-recent-k 2 \
  --auto-apply-updates safe \
  --debug \
  --archive \
  --yes
```

说明：
- `--auto-apply-updates safe`：只自动应用低风险补丁（`world.notes`/`style.md` 追加 + `materials` 幂等追加）。
- `--archive --yes`：运行结束自动归档（无人值守不弹确认）。

### 更严格：遇到异常立即中止（省成本，适合“必须 0 故障”）

```bash
python src\main.py ^
  --config "config.toml" ^
  --llm-mode llm ^
  --idea-file "D:\path\to\idea_pack.txt" ^
  --project "你的项目名" ^
  --target-words 5000 ^
  --chapters 150 ^
  --max-rewrites 1 ^
  --auto-apply-updates safe ^
  --stop-on-error ^
  --debug ^
  --archive ^
  --yes
```

### 真后台运行（PowerShell，输出重定向到文件）
不会占用当前窗口；stdout/stderr 写入日志文件，便于排障。

```bash
Start-Process -NoNewWindow -FilePath python -ArgumentList @(
  "src\main.py",
  "--config","config.toml",
  "--llm-mode","llm",
  "--idea-file","D:\path\to\idea_pack.txt",
  "--project","你的项目名",
  "--target-words","5000",
  "--chapters","150",
  "--max-rewrites","1",
  "--auto-apply-updates","safe",
  "--debug",
  "--archive",
  "--yes"
) -RedirectStandardOutput "outputs\batch_stdout.log" -RedirectStandardError "outputs\batch_stderr.log"
```
- `--archive`：运行结束后自动归档（默认不归档，建议先 review）
- `--archive-only`：只归档当前 `outputs/current`（review 通过后手动入库）
- `--project "<name>"`：指定项目名（用于续写/固定 `projects/<project>`）
- `--resume`：续写模式（复用 `project_meta.json`，起始章自动为已有最大章+1）
- `--start-chapter 101`：显式指定从第101章开始写（不依赖自动推断）

## 运行模式：LLM vs 模板
为了方便“纯工作流验证”，新增了运行模式开关：
- `template`：强制走模板（不初始化LLM）
- `llm`：强制走LLM（如果LLM配置/依赖不完整会直接报错）
- `auto`：自动（默认，有LLM就用LLM，否则模板）

你可以用环境变量或CLI覆盖：

```bash
python src\main.py --llm-mode template
```

## 字数/Token 的说明（避免被截断）

- `--target-words` 在本项目中表示**每章目标“字数”**，实现上按**中文字符数（含标点/空白）近似**做约束。
- LLM 有时会因 `max_tokens` 或上下文长度限制导致**输出被截断**（finish_reason=length）。
- 现在开启 debug 后，会在 `outputs/current/debug.jsonl` 的每条 `llm_response` 里记录：
  - `finish_reason`
  - `token_usage`（如果模型/网关返回）
- 写手节点已增加自动处理：
  - **过短/被截断**：自动续写补全（最多 2 段）
  - **过长**：不自动压缩（避免二次改写导致风格漂移），仅在日志中记录告警

## 两个“需要你确认”的介入点（推荐工作流）

本项目默认偏“安全”：**不会未经确认就修改 Canon，也不会未经确认就归档**（除非你显式 `--yes`）。

### 1) 应用 Canon 建议（主编给出的设定补丁）

- 生成时只会落盘建议：`outputs/current/chapters/*.canon_suggestions.json`
- 你 review 之后再应用（交互确认）：

```bash
python src\main.py --apply-canon-only
```

- 交互按键（更快）：`y=应用本条 / s=跳过 / a=全部应用 / p=详情 / q=退出 / ?=帮助`

- 运行结束后自动进入“预览→确认→应用”的交互流程（推荐）：

```bash
python src\main.py --apply-canon-suggestions
```

- 预览但不写入（dry-run）：

```bash
python src\main.py --apply-canon-only --dry-run
```

### 2) 归档（把 current 复制入项目 stages）

- 运行结束时归档前确认（推荐）：

```bash
python src\main.py --archive --archive-confirm
```

- 你 review 完再手动归档（最稳）：

```bash
python src\main.py --archive-only
```

### 自动化（无人值守）

如果你确实要跳过所有确认：

```bash
python src\main.py --apply-canon-suggestions --archive --yes
```

## Windows PowerShell 小贴士（避免命令行引号坑）
如果你的 `--style/--paragraph-rules` 里包含很多双引号/特殊符号，PowerShell 可能会解析出错。更稳的做法：
- 用单引号包住参数值：`--style 'xxx'`
- 或用文件：`--style-file path/to/style.txt`

## 续写（例如已有100章，继续写第101章）

推荐用 `--project + --resume`：

```bash
python src\main.py --project "你的项目名" --resume --chapters 1
```

或显式指定起始章节：

```bash
python src\main.py --project "你的项目名" --start-chapter 101 --chapters 1
```

## 文档
- 需求与角色设定：`产品设计文档.md`
- 最新架构与阶段规划：`项目架构与阶段规划.md`


## Tools
### 归档工具
为了不影响cursor（这个打包工具代码很多，会拖慢cursor的速度）我将归档工具放入了这个目录：
> /d/projects/scripts/linglongwenxin/archive.py

使用方法：
在手动归档时，调用这个工具。需要先进入到归档的目录中（或者手动指定归档的源目录）
（注意！！！不需要放到自动化节点里面！！！ 后续有需要再加）

使用示例：
```bash
86188@LAPTOP-MM3TJMQK MINGW64 /d/projects/lang-graph/LangGraph-Demo/outputs/projects/迷雾重重/stages/stage1/runs/20260103-071908/chapters (main)
$ python /d/projects/scripts/linglongwenxin/archive.py
=== MD文件归档工具 ===
Archive MD Files Tool
========================================
请输入源目录路径（留空使用当前目录）:
请输入备份目录名称（留空使用默认: md_archive_20260103）:

是否生成HTML目录网页？(y/n, 默认: y): y
是否创建压缩包？(y/n, 默认: y): y

========================================
� 归档配置摘要
========================================
� 源目录: D:\projects\lang-graph\LangGraph-Demo\outputs\projects\迷雾重重\stages\stage1\runs\20260103-071908\chapters
� 备份目录: D:\projects\lang-graph\LangGraph-Demo\outputs\projects\迷雾重重\stages\stage1\runs\20260103-071908\md_archive_20260103
� 生成HTML目录: 是
� 创建压缩包: 是
========================================

确认开始归档？(y/n): y
```
