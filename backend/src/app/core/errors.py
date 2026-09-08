"""统一错误体系。

前端响应统一为 {code, message, data, request_id}。
错误码对照见 doc/04-系统设计方案-v2-review.md §21。
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException, status


class AppError(Exception):
    """业务异常基类。"""

    code: str = "INTERNAL_ERROR"
    http_status: int = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        http_status: Optional[int] = None,
        detail: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if http_status:
            self.http_status = http_status
        self.detail = detail or {}


class BadRequest(AppError):
    code = "BAD_REQUEST"
    http_status = status.HTTP_400_BAD_REQUEST


class Unauthorized(AppError):
    code = "UNAUTHORIZED"
    http_status = status.HTTP_401_UNAUTHORIZED


class Forbidden(AppError):
    code = "FORBIDDEN"
    http_status = status.HTTP_403_FORBIDDEN


class NotFound(AppError):
    code = "NOT_FOUND"
    http_status = status.HTTP_404_NOT_FOUND


class Conflict(AppError):
    code = "CONFLICT"
    http_status = status.HTTP_409_CONFLICT


class RateLimited(AppError):
    code = "RATE_LIMITED"
    http_status = status.HTTP_429_TOO_MANY_REQUESTS


class UpstreamError(AppError):
    code = "UPSTREAM_ERROR"
    http_status = status.HTTP_502_BAD_GATEWAY


class Timeout(AppError):
    code = "TIMEOUT"
    http_status = status.HTTP_504_GATEWAY_TIMEOUT


class JobFailed(AppError):
    code = "JOB_FAILED"
    http_status = status.HTTP_500_INTERNAL_SERVER_ERROR


class RiskRejected(AppError):
    code = "RISK_REJECTED"
    http_status = status.HTTP_422_UNPROCESSABLE_CONTENT


def to_http_exception(err: AppError) -> HTTPException:
    """将 AppError 转成 FastAPI HTTPException，便于全局异常处理器复用。"""
    payload: dict[str, Any] = {"code": err.code, "message": err.message}
    if err.detail:
        payload["detail"] = err.detail
    return HTTPException(status_code=err.http_status, detail=payload)
