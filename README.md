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

