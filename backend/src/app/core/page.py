"""统一分页参数与响应包装。"""
from __future__ import annotations

from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=500)


class Envelope(BaseModel, Generic[T]):
    """统一响应包装：{code, message, data}。"""

    code: str = "OK"
    message: str = "ok"
    data: Optional[T] = None


def make_page(
    items: list[T],
    total: int,
    page: int,
    page_size: int,
) -> Page[T]:
    return Page(items=items, total=total, page=page, page_size=page_size)
