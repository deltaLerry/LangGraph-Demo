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

运行后会在`outputs/`下生成一次运行目录，包含：
- `planner.json`
- `chapter_1.md`（以及多章节时的`chapter_2.md`...）
- `editor_1.md`（以及多章节时的`editor_2.md`...）

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

## 运行模式：LLM vs 模板
为了方便“纯工作流验证”，新增了运行模式开关：
- `template`：强制走模板（不初始化LLM）
- `llm`：强制走LLM（如果LLM配置/依赖不完整会直接报错）
- `auto`：自动（默认，有LLM就用LLM，否则模板）

你可以用环境变量或CLI覆盖：

```bash
python src\main.py --llm-mode template
```

## 文档
- 需求与角色设定：`产品设计文档.md`
- 最新架构与阶段规划：`项目架构与阶段规划.md`

