"""弹性调用：重试、降级、兜底。

外部 API 不可靠，统一在这里处理"失败怎么办"，业务代码不重复写 try/except。
- 重试只针对"瞬时错误"（网络抖动、超时、连接错误、5xx、429）
- 参数错误、认证失败这类重试无意义，应直接抛出
"""
import time
from typing import Callable


def with_retry(
    fn: Callable,
    *args,
    retries: int = 2,
    base_delay: float = 0.5,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    fallback: Callable[[BaseException], object] | None = None,
    **kwargs,
):
    """
    调用 fn；遇到 retry_on 类型的异常按指数退避重试，最多 retries 次。
    全部失败后：
    - 提供 fallback：返回 fallback(最后一次异常)
    - 否则抛出最后一次异常
    """
    last_exc: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            return fn(*args, **kwargs)
        except retry_on as e:
            last_exc = e
            if attempt < retries:
                time.sleep(base_delay * (2 ** attempt))   # 0.5 -> 1 -> 2
    if fallback is not None:
        return fallback(last_exc)
    raise last_exc
