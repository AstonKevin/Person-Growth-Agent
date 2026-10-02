"""自定义业务异常 + 全局异常处理器

上层代码只管 raise 自定义异常，格式化返回统一交给 handler：
- 抛什么错（业务语义）和 怎么返回（JSON 格式）解耦
- 全项目错误响应保持同一结构：{"error_code": ..., "message": ..., "path": ...}
"""
from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """项目异常基类：所有自定义异常都继承它"""

    status_code = 500
    error_code = "INTERNAL_ERROR"
    headers: dict | None = None

    def __init__(self, message: str = "服务内部错误"):
        self.message = message
        super().__init__(message)


# ========== 认证相关 ==========
class UnauthorizedError(AppError):
    """未认证或认证失败（登录失败/token无效/用户不存在）
    OAuth2 规范要求 401 必须带 WWW-Authenticate header"""

    status_code = 401
    error_code = "UNAUTHORIZED"
    headers = {"WWW-Authenticate": "Bearer"}

    def __init__(self, message: str = "未认证或认证已过期"):
        super().__init__(message)


# ========== 资源相关 ==========
class NotFoundError(AppError):
    """资源不存在或无权访问（统一返回404，不泄露资源是否存在）"""

    status_code = 404
    error_code = "NOT_FOUND"

    def __init__(self, message: str = "资源不存在或无权访问"):
        super().__init__(message)


class ConflictError(AppError):
    """资源冲突（用户名重复、重复打卡等）"""

    status_code = 409
    error_code = "CONFLICT"

    def __init__(self, message: str = "资源冲突"):
        super().__init__(message)


# ========== LLM 相关（原有，保留） ==========
class LLMError(AppError):
    """LLM 调用失败基类（网络断开/服务端错误等，重试后仍失败）"""

    status_code = 502
    error_code = "LLM_SERVICE_UNAVAILABLE"

    def __init__(self, message: str = "AI 服务暂时不可用，请稍后再试"):
        super().__init__(message)


class LLMTimeoutError(LLMError):
    """LLM 请求超时（超过设定时间无响应）"""

    error_code = "LLM_TIMEOUT"

    def __init__(self, message: str = "AI 服务响应超时，请稍后再试"):
        super().__init__(message)


class LLMRateLimitError(LLMError):
    """触发限流（429，重试后仍被限流）"""

    status_code = 429
    error_code = "LLM_RATE_LIMITED"

    def __init__(self, message: str = "AI 请求过于频繁，请稍后再试"):
        super().__init__(message)


class LLMAuthError(LLMError):
    """API Key 无效/未授权（不可重试，属于配置错误）"""

    status_code = 500
    error_code = "LLM_AUTH_FAILED"

    def __init__(self, message: str = "AI 服务鉴权失败，请检查服务端配置"):
        super().__init__(message)


class LLMResponseFormatError(LLMError):
    """LLM 返回内容不符合预期格式（如 JSON 解析失败）"""

    error_code = "LLM_RESPONSE_FORMAT_ERROR"

    def __init__(self, message: str = "AI 返回内容格式异常，请重试"):
        super().__init__(message)


def register_exception_handlers(app) -> None:
    """在 main.py 调用一次，全局生效"""

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "message": exc.message,
                "path": str(request.url.path),
            },
            headers=exc.headers,  # 透传自定义 header（如 401 的 WWW-Authenticate）
        )
