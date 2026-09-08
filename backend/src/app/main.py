"""FastAPI app 工厂与 lifespan。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.v1 import data as data_router
from .api.v1 import agent as agent_router
from .api.v1 import analytics as analytics_router
from .api.v1 import factor as factor_router
from .api.v1 import health as health_router
from .api.v1 import llm as llm_router
from .api.v1 import market as market_router
from .api.v1 import sim as sim_router
from .api.v1 import strategy as strategy_router
from .core.config import get_settings
from .core.errors import AppError
from .core.logging import configure_logging, get_logger
from .exchange.adapters import register_adapter  # noqa: F401  确保 binance 适配器注册
from .market.hub import start_default_hub, stop_default_hub
from .market.ws_router import router as market_ws_router

logger = get_logger(__name__)

DEFAULT_STREAM_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
]


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    settings = get_settings()
    configure_logging(level=settings.APP_LOG_LEVEL, json_output=settings.APP_ENV != "dev")
    logger.info("app.start", env=settings.APP_ENV, name=settings.APP_NAME)
    try:
        await start_default_hub(DEFAULT_STREAM_SYMBOLS, interval="1m")
    except Exception as exc:
        logger.warning("streamhub.start_failed", error=str(exc))
    yield
    await stop_default_hub()
    logger.info("app.stop")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 统一异常处理
    @app.exception_handler(AppError)
    async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "message": exc.message, "data": exc.detail or None},
        )

    # 路由
    app.include_router(health_router.router)
    app.include_router(market_router.router, prefix="/api/v1")
    app.include_router(data_router.router, prefix="/api/v1")
    app.include_router(strategy_router.router, prefix="/api/v1")
    app.include_router(factor_router.router, prefix="/api/v1")
    app.include_router(agent_router.router, prefix="/api/v1")
    app.include_router(analytics_router.router, prefix="/api/v1")
    app.include_router(sim_router.router, prefix="/api/v1")
    app.include_router(llm_router.router, prefix="/api/v1")
    app.include_router(market_ws_router)

    return app


app = create_app()

