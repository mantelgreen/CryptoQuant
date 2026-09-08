"""M1 ARQ worker 启动脚本。

使用：
    python -m app.job.run_worker
"""
from __future__ import annotations

import asyncio

import arq

from .arq_app import WorkerSettings


async def main() -> None:
    redis = await arq.create_pool(WorkerSettings.redis_settings)
    if redis is None:
        raise RuntimeError("redis_unavailable")
    await redis.aclose()

    # ARQ CLI 启动：交给 arq.cmd
    from arq import run_worker

    await run_worker(WorkerSettings)  # type: ignore[arg-type]


if __name__ == "__main__":
    asyncio.run(main())
