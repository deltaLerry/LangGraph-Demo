from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, Optional


def safe_filename(name: str, fallback: str = "project") -> str:
    name = (name or "").strip() or fallback
    # Windows 文件名非法字符：\ / : * ? " < > |
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:80] if len(name) > 80 else name


def make_run_dir(base_dir: str, project_name: Optional[str] = None) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    pn = safe_filename(project_name or "", fallback="story")
    run_dir = os.path.join(base_dir, f"{ts}-{pn}")
    os.makedirs(run_dir, exist_ok=True)
    return run_dir


def write_text(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def write_json(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


