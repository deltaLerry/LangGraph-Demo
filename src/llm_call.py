from __future__ import annotations

import random
import time
import traceback
from typing import Any, List, Optional


def _is_retryable_error(e: BaseException) -> bool:
    """
    尽量不绑定具体 SDK 类型：用异常类型名/消息做启发式判断。
    """
    name = e.__class__.__name__.lower()
    msg = str(e).lower()
    retry_names = (
        "timeout",
        "timeouterror",
        "readtimeout",
        "connecttimeout",
        "connectionerror",
        "apierror",
        "ratelimit",
        "ratelimiterror",
        "serviceunavailable",
        "temporarilyunavailable",
    )
    if any(x in name for x in retry_names):
        return True
    retry_msgs = (
        "timeout",
        "timed out",
        "connection reset",
        "connection aborted",
        "connection refused",
        "remote end closed",
        "rate limit",
        "too many requests",
        "overloaded",
        "temporarily unavailable",
        "service unavailable",
        "502",
        "503",
        "504",
    )
    return any(x in msg for x in retry_msgs)


def invoke_with_retry(
    llm: Any,
    messages: List[Any],
    *,
    max_attempts: int = 3,
    base_sleep_s: float = 1.0,
    max_sleep_s: float = 12.0,
    logger: Any = None,
    node: str = "llm",
    chapter_index: Optional[int] = None,
    extra: Optional[dict] = None,
) -> Any:
    """
    对 llm.invoke 做轻量重试（避免网络抖动/限流导致整章崩溃）。
    - 只重试“看起来可重试”的异常
    - 指数退避 + 少量随机抖动
    - 失败会抛出最后一次异常（由上层决定降级还是中止）
    """
    attempts = max(1, int(max_attempts))
    base = max(0.1, float(base_sleep_s))

    last_err: BaseException | None = None
    for i in range(1, attempts + 1):
        try:
            return llm.invoke(messages)
        except BaseException as e:  # noqa: BLE001
            last_err = e
            retryable = _is_retryable_error(e)
            if logger:
                try:
                    logger.event(
                        "llm_error",
                        node=node,
                        chapter_index=chapter_index,
                        attempt=i,
                        max_attempts=attempts,
                        retryable=retryable,
                        error_type=e.__class__.__name__,
                        error=str(e),
                        traceback="".join(traceback.format_exception(type(e), e, e.__traceback__))[:8000],
                        extra=extra or {},
                    )
                except Exception:
                    pass

            if (not retryable) or i >= attempts:
                raise

            sleep_s = min(max_sleep_s, base * (2 ** (i - 1)))
            sleep_s = sleep_s * (0.7 + random.random() * 0.6)  # 0.7~1.3 抖动
            time.sleep(sleep_s)

    # 理论不会到这里
    if last_err:
        raise last_err
    raise RuntimeError("invoke_with_retry: unexpected state")


