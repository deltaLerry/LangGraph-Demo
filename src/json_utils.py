from __future__ import annotations

import json
import re
import ast
from typing import Any, Dict, Tuple


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


def extract_first_json_object_with_error(text: str) -> Tuple[Dict[str, Any], str]:
    """
    与 extract_first_json_object 类似，但会返回解析失败原因（用于“把错误反馈给 LLM 修复”）。
    返回：(obj, error_message)；当 obj 非空时 error_message 为空字符串。
    """
    s = (text or "").strip()
    if not s:
        return {}, "empty_output"

    def _strip_code_fence(x: str) -> str:
        x = (x or "").strip()
        # ```json ... ``` / ``` ... ```
        x = re.sub(r"(?is)^```(?:json)?\s*", "", x)
        x = re.sub(r"(?is)\s*```$", "", x)
        return x.strip()

    def _remove_trailing_commas(x: str) -> str:
        # 移除 }/]/, 前的尾逗号（常见 JSON 错误）
        x = re.sub(r",\s*(\}|\])", r"\1", x)
        return x

    def _try_ast_eval_jsonish(x: str) -> Dict[str, Any]:
        """
        宽松解析：允许单引号、True/False/None 等（用于本地修复）
        """
        x2 = x
        x2 = x2.replace("null", "None").replace("true", "True").replace("false", "False")
        try:
            obj = ast.literal_eval(x2)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    # 1) 直接解析（先去代码块与尾逗号）
    try:
        s0 = _strip_code_fence(s)
        s0 = _remove_trailing_commas(s0)
        obj = json.loads(s0)
        if isinstance(obj, dict):
            return obj, ""
        return {}, f"json_root_not_object(type={type(obj).__name__})"
    except Exception as e1:
        err1 = f"json_loads_failed: {e1.__class__.__name__}: {str(e1)}"
        # ast 兜底（本地宽松修复）
        obj_ast = _try_ast_eval_jsonish(_strip_code_fence(s))
        if obj_ast:
            return obj_ast, ""

    # 2) 抽取第一个 {...} 片段
    m = re.search(r"\{[\s\S]*\}", s)
    if not m:
        return {}, err1 + " ; no_object_braces_found"
    snippet = m.group(0)
    try:
        snippet0 = _remove_trailing_commas(_strip_code_fence(snippet))
        obj = json.loads(snippet0)
        if isinstance(obj, dict):
            return obj, ""
        return {}, f"extracted_json_root_not_object(type={type(obj).__name__})"
    except Exception as e2:
        obj_ast2 = _try_ast_eval_jsonish(_strip_code_fence(snippet))
        if obj_ast2:
            return obj_ast2, ""
        return {}, err1 + f" ; extracted_json_loads_failed: {e2.__class__.__name__}: {str(e2)}"


