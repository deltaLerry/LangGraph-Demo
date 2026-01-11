from __future__ import annotations

import json
import os
import time
import traceback
from dataclasses import dataclass, field
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


def _preview_text(s: str, preview_chars: int) -> str:
    s = s or ""
    n = len(s)
    if n <= preview_chars:
        return s
    remain = max(0, n - preview_chars)
    return s[:preview_chars] + f"...(剩余约{remain}字符)"


def _safe_filename(s: str, max_len: int = 120) -> str:
    s = str(s or "").strip() or "payload"
    out = []
    for ch in s:
        if ch.isalnum() or ch in ("-", "_", "."):
            out.append(ch)
        else:
            out.append("_")
    x = "".join(out).strip("._")
    if not x:
        x = "payload"
    return x[:max_len]


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
                # 不在这里截断：交给 RunLogger.event 的“preview + payload 落盘”统一处理，确保 payload 永远是全量
                "content": str(content) if content is not None else "",
            }
        )
    return out


@dataclass
class RunLogger:
    path: str
    # 轻量索引日志（实时查询用）。为空则不写。
    index_path: str = ""
    enabled: bool = True
    max_chars: int = 20000
    # jsonl 内联预览长度（超出则写入 debug_payloads/* 并在 jsonl 中仅保留 preview + 指针）
    preview_chars: int = 100
    payload_dirname: str = "debug_payloads"
    _seq: int = field(default=0, init=False, repr=False)

    def _write_to_path(self, path: str, obj: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        if not path:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def _write(self, obj: Dict[str, Any]) -> None:
        self._write_to_path(self.path, obj)

    def _write_index(self, obj: Dict[str, Any]) -> None:
        """
        写入轻量索引日志（可重建、适合实时过滤）。
        只保留高价值字段与指针，不写入大字段。
        """
        if not self.index_path:
            return
        # 兼容：旧事件未必有这些字段，尽量“有则写、无则略”
        keep_keys = (
            "ts",
            "event",
            "node",
            "name",
            "chapter_index",
            "duration_ms",
            "skipped",
            "reason",
            "error_type",
            "error",
            "finish_reason",
        )
        idx = {k: obj.get(k) for k in keep_keys if k in obj}
        # 常用：把少数计数类字段也保留（用于 dashboard/过滤）
        for k in ("writer_version", "writer_chars", "planner_json_chars", "feedback_count", "canon_suggestions_count", "suggestions_count"):
            if k in obj:
                idx[k] = obj.get(k)
        # 顾问审计：保留短摘要与计数，便于实时过滤“高风险章节”
        for k in (
            "advisor_suggested_action",
            "advisor_findings_count",
            "advisor_rewrite_count",
            "advisor_digest",
            "advisor_path",
        ):
            if k in obj:
                idx[k] = obj.get(k)
        # payload 指针（compact 过程会写入 *.__full_path / __chars）
        for k in list(obj.keys()):
            if k.endswith("__full_path") or k.endswith("__chars"):
                idx[k] = obj.get(k)
        self._write_to_path(self.index_path, idx)

    def _payload_dir(self) -> str:
        base = os.path.dirname(self.path)
        return os.path.join(base, self.payload_dirname)

    def _write_payload(self, *, content: Any, ext: str, hint: str) -> Dict[str, Any]:
        """
        写入全量 payload 文件，并返回可写入 jsonl 的元信息。
        - ext: "txt" or "json"
        """
        try:
            os.makedirs(self._payload_dir(), exist_ok=True)
        except Exception:
            # 若无法创建目录，直接降级为内联
            return {"full_path": "", "chars": 0, "bytes": 0}

        self._seq += 1
        ts = datetime.now().strftime("%Y%m%d-%H%M%S.%f")[:-3]  # 毫秒
        fname = f"{ts}_{self._seq:04d}_{_safe_filename(hint)}.{ext}"
        full_path = os.path.join(self._payload_dir(), fname)
        try:
            if ext == "txt":
                text = str(content or "")
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(text)
                size_bytes = os.path.getsize(full_path) if os.path.exists(full_path) else 0
                return {
                    "full_path": os.path.relpath(full_path, os.path.dirname(self.path)).replace("\\", "/"),
                    "chars": len(text),
                    "bytes": int(size_bytes),
                }
            # json
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(content, ensure_ascii=False))
            size_bytes = os.path.getsize(full_path) if os.path.exists(full_path) else 0
            return {
                "full_path": os.path.relpath(full_path, os.path.dirname(self.path)).replace("\\", "/"),
                "chars": len(json.dumps(content, ensure_ascii=False)),
                "bytes": int(size_bytes),
            }
        except Exception:
            return {"full_path": "", "chars": 0, "bytes": 0}

    def _compact_inplace(self, obj: Any, hint_prefix: str) -> Any:
        """
        递归压缩日志对象：
        - 仅对 LLM 相关事件的“正文类字段”做截断（messages/content/response/raw/traceback 等）：
          - 超长 string：写入 payload 文件，jsonl 只保留 preview，并附带 __full_path/__chars
        - list/dict：递归处理；list 中的超长 string 会替换成带元信息的 dict（否则无处放 sibling keys）
        """
        # 注意：这里的 hint_prefix 会被 event() 传入 event 名（例如 llm_request），
        # 并在递归时拼接成类似 "llm_request.messages[0].content" 的路径。
        # 我们用这个路径来决定“是否需要截断”，确保元数据（ts/model/token_usage 等）保持完整。
        pc = int(self.preview_chars or 0)
        pc = 100 if pc <= 0 else pc

        def _should_compact_str(path: str) -> bool:
            # 仅对 llm_* 事件做截断；其它事件字段保持完整（避免元数据被截断）
            if not str(hint_prefix or "").startswith("llm_"):
                return False
            p = str(path or "")
            # 只截断“正文类字段”，例如 request messages、response/raw、traceback 等
            if ".messages" in p or p.endswith(".messages") or p.startswith("llm_request.messages"):
                return True
            if any(x in p for x in (".content", ".prompt", ".response", ".raw", ".text", ".traceback")):
                return True
            if p.endswith((".content", ".prompt", ".response", ".raw", ".text", ".traceback")):
                return True
            return False

        if isinstance(obj, dict):
            # 先处理当前层的键
            for k in list(obj.keys()):
                v = obj.get(k)
                key_hint = f"{hint_prefix}.{k}" if hint_prefix else str(k)
                if isinstance(v, str):
                    if len(v) > pc and _should_compact_str(key_hint):
                        meta = self._write_payload(content=v, ext="txt", hint=key_hint)
                        obj[k] = _preview_text(v, pc)
                        obj[f"{k}__full_path"] = meta.get("full_path", "")
                        obj[f"{k}__chars"] = int(meta.get("chars", 0) or 0)
                    continue
                if isinstance(v, (dict, list)):
                    obj[k] = self._compact_inplace(v, key_hint)
                else:
                    obj[k] = v
            return obj

        if isinstance(obj, list):
            out = []
            for i, it in enumerate(obj):
                item_hint = f"{hint_prefix}[{i}]"
                if isinstance(it, str):
                    if len(it) > pc and _should_compact_str(item_hint):
                        meta = self._write_payload(content=it, ext="txt", hint=item_hint)
                        out.append(
                            {
                                "__preview": _preview_text(it, pc),
                                "__full_path": meta.get("full_path", ""),
                                "__chars": int(meta.get("chars", 0) or 0),
                            }
                        )
                    else:
                        out.append(it)
                    continue
                if isinstance(it, (dict, list)):
                    out.append(self._compact_inplace(it, item_hint))
                else:
                    out.append(it)
            return out

        # 其他类型不处理
        return obj

    def event(self, event: str, **data: Any) -> None:
        obj = {"ts": _now_iso(), "event": event, **data}
        # 统一压缩：避免 llm request/response/traceback 等把 jsonl 冲爆
        try:
            obj = self._compact_inplace(obj, hint_prefix=str(event))
        except Exception:
            pass
        self._write(obj)
        self._write_index(obj)

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


