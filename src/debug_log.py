from __future__ import annotations

import json
import os
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _now_iso() -> str:
    # 带毫秒，方便排查耗时
    return datetime.now().isoformat(timespec="milliseconds")


def _truncate(s: str, max_chars: int) -> str:
    s = s or ""
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 50] + "\n...[truncated]...\n" + s[-50:]


def truncate_text(text: str, max_chars: int = 20000) -> str:
    return _truncate(text or "", max_chars=max_chars)


def _safe_serialize_messages(messages: Any, max_chars: int) -> Any:
    """
    将 LangChain message 列表安全序列化成可写入日志的结构。
    """
    if messages is None:
        return None
    if not isinstance(messages, (list, tuple)):
        return str(messages)
    out = []
    for m in messages:
        role = getattr(m, "type", None) or m.__class__.__name__
        content = getattr(m, "content", None)
        out.append(
            {
                "role": role,
                "content": _truncate(str(content) if content is not None else "", max_chars=max_chars),
            }
        )
    return out


@dataclass
class RunLogger:
    path: str
    enabled: bool = True
    max_chars: int = 20000

    def _write(self, obj: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def event(self, event: str, **data: Any) -> None:
        self._write({"ts": _now_iso(), "event": event, **data})

    def span(self, name: str, **data: Any):
        return _Span(self, name=name, data=data)

    def llm_call(
        self,
        *,
        node: str,
        chapter_index: Optional[int],
        messages: Any,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ):
        return _LLMCall(
            self,
            node=node,
            chapter_index=chapter_index,
            messages=messages,
            model=model,
            base_url=base_url,
            extra=extra or {},
        )


class _Span:
    def __init__(self, logger: RunLogger, name: str, data: Dict[str, Any]):
        self.logger = logger
        self.name = name
        self.data = data
        self.t0 = 0.0

    def __enter__(self):
        self.t0 = time.perf_counter()
        self.logger.event("span_start", name=self.name, **self.data)
        return self

    def __exit__(self, exc_type, exc, tb):
        dt_ms = int((time.perf_counter() - self.t0) * 1000)
        if exc is not None:
            self.logger.event(
                "span_error",
                name=self.name,
                duration_ms=dt_ms,
                error_type=getattr(exc_type, "__name__", str(exc_type)),
                error=str(exc),
                traceback="".join(traceback.format_exception(exc_type, exc, tb)),
                **self.data,
            )
        else:
            self.logger.event("span_end", name=self.name, duration_ms=dt_ms, **self.data)
        return False


class _LLMCall:
    def __init__(
        self,
        logger: RunLogger,
        *,
        node: str,
        chapter_index: Optional[int],
        messages: Any,
        model: Optional[str],
        base_url: Optional[str],
        extra: Dict[str, Any],
    ):
        self.logger = logger
        self.node = node
        self.chapter_index = chapter_index
        self.messages = messages
        self.model = model
        self.base_url = base_url
        self.extra = extra
        self.t0 = 0.0

    def __enter__(self):
        self.t0 = time.perf_counter()
        self.logger.event(
            "llm_request",
            node=self.node,
            chapter_index=self.chapter_index,
            model=self.model,
            base_url=self.base_url,
            messages=_safe_serialize_messages(self.messages, max_chars=self.logger.max_chars),
            **(self.extra or {}),
        )
        return self

    def __exit__(self, exc_type, exc, tb):
        dt_ms = int((time.perf_counter() - self.t0) * 1000)
        if exc is not None:
            self.logger.event(
                "llm_error",
                node=self.node,
                chapter_index=self.chapter_index,
                model=self.model,
                base_url=self.base_url,
                duration_ms=dt_ms,
                error_type=getattr(exc_type, "__name__", str(exc_type)),
                error=str(exc),
                traceback="".join(traceback.format_exception(exc_type, exc, tb)),
            )
        else:
            self.logger.event(
                "llm_ok",
                node=self.node,
                chapter_index=self.chapter_index,
                model=self.model,
                base_url=self.base_url,
                duration_ms=dt_ms,
            )
        return False


def load_events(path: str) -> List[Dict[str, Any]]:
    if not path or not os.path.exists(path):
        return []
    events: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except Exception:
                continue
    return events


def build_node_call_graph_mermaid(events: Iterable[Dict[str, Any]]) -> str:
    """
    从日志事件里统计节点间的调用边，并输出 Mermaid flowchart。
    统计来源：node_start 事件的全局时间顺序（跨章节/返工按真实执行顺序）。
    """
    seq: List[str] = []
    for e in events:
        if e.get("event") == "node_start":
            seq.append(str(e.get("node")))

    edge_counts: Dict[Tuple[str, str], int] = {}
    prev = "START"
    for node in seq:
        edge_counts[(prev, node)] = edge_counts.get((prev, node), 0) + 1
        prev = node
    if seq:
        edge_counts[(prev, "END")] = edge_counts.get((prev, "END"), 0) + 1

    lines = ["flowchart TD"]
    # 定义节点（固定 + 可能新增）
    nodes = set(["START", "END"])
    for a, b in edge_counts.keys():
        nodes.add(a)
        nodes.add(b)

    # 画边（带次数）
    for (a, b), c in sorted(edge_counts.items(), key=lambda x: (x[0][0], x[0][1])):
        label = f"|{c}|" if c > 1 else ""
        lines.append(f"  {a} -->{label} {b}")

    return "\n".join(lines) + "\n"


def build_call_graph_mermaid_by_chapter(events: Iterable[Dict[str, Any]]) -> str:
    """
    生成“按章节分组”的 Mermaid 调用图：
    - chapter_index=0 视为 planner 阶段
    - chapter_index>=1 为各章节子流程（writer/editor 及返工回边）
    """
    # 收集 node_start 按章排序（保持日志顺序）
    by_chapter: Dict[int, List[str]] = {}
    for e in events:
        if e.get("event") != "node_start":
            continue
        chap = int(e.get("chapter_index", 0) or 0)
        node = str(e.get("node"))
        by_chapter.setdefault(chap, []).append(node)

    def _count_edges(seq: List[str]) -> Dict[Tuple[str, str], int]:
        counts: Dict[Tuple[str, str], int] = {}
        prev = "START"
        for n in seq:
            counts[(prev, n)] = counts.get((prev, n), 0) + 1
            prev = n
        if seq:
            counts[(prev, "END")] = counts.get((prev, "END"), 0) + 1
        return counts

    lines: List[str] = ["flowchart TD"]

    # 全局：planner
    planner_seq = by_chapter.get(0, [])
    if planner_seq:
        counts = _count_edges(planner_seq)
        lines.append("  subgraph CH0[策划阶段]")
        lines.append("    direction TD")
        for (a, b), c in sorted(counts.items()):
            a_id = f"CH0_{a}"
            b_id = f"CH0_{b}"
            label = f"|{c}|" if c > 1 else ""
            lines.append(f"    {a_id}[{a}] -->{label} {b_id}[{b}]")
        lines.append("  end")
        # 全局入口
        lines.append("  START --> CH0_START")

    # 各章节
    for chap in sorted([c for c in by_chapter.keys() if c >= 1]):
        seq = by_chapter.get(chap, [])
        counts = _count_edges(seq)
        lines.append(f"  subgraph CH{chap}[第{chap}章]")
        lines.append("    direction TD")
        for (a, b), c in sorted(counts.items()):
            a_id = f"CH{chap}_{a}"
            b_id = f"CH{chap}_{b}"
            label = f"|{c}|" if c > 1 else ""
            lines.append(f"    {a_id}[{a}] -->{label} {b_id}[{b}]")
        lines.append("  end")
        # 章节入口：如果存在planner阶段，则从planner END连到每章 START；否则从全局 START 连
        if planner_seq:
            lines.append(f"  CH0_END --> CH{chap}_START")
        else:
            lines.append(f"  START --> CH{chap}_START")

    # 如果完全没有节点，仍给个占位
    if len(lines) == 1:
        lines.append("  START --> END")

    return "\n".join(lines) + "\n"


