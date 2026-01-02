from __future__ import annotations

import json
import re
from typing import Any, Dict


def extract_first_json_object(text: str) -> Dict[str, Any]:
    """
    从一段文本中提取“第一个 JSON object（{...}）”并解析为 dict。

    设计意图：
    - 兼容 LLM 输出带 ```json ... ``` 或前后解释文字的情况（阶段2常见）
    - 只返回 dict；若解析到的不是 dict 或解析失败，则返回空 dict
    """
    s = (text or "").strip()
    if not s:
        return {}

    # 1) 直接解析（最理想：LLM 只输出 JSON）
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        pass

    # 2) 抽取第一个 {...} 片段（容错：LLM 多说了话 / 包了代码块）
    m = re.search(r"\{[\s\S]*\}", s)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


