import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response

from src.apps.api.routes import router as webhook_router
from src.packages.shared.config import settings
from src.packages.shared.logging import configure_logging, get_logger

logger = get_logger("akesis.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifecycle management."""
    configure_logging(settings.log_level)
    logger.info(
        "Akesis Ingestion Gateway initialized",
        environment=settings.environment,
        log_level=settings.log_level,
    )
    yield
    logger.info("Akesis Ingestion Gateway shutting down")


def create_app() -> FastAPI:
    """Factory creating the configured FastAPI application."""
    app = FastAPI(
        title="Akesis API Gateway",
        description="CI/CD Failure Ingestion and Deterministic Log Signal Extraction",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_correlation_middleware(
        request: Request,
        call_next: Callable[[Request], Any],
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:8]}"
        response = await call_next(request)
        if isinstance(response, Response):
            response.headers["X-Request-ID"] = request_id
            return response
        res = Response(content=response)
        res.headers["X-Request-ID"] = request_id
        return res

    @app.get("/health/liveness", tags=["Health"])
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/readiness", tags=["Health"])
    async def readiness() -> dict[str, str]:
        return {"status": "ready", "environment": settings.environment}

    app.include_router(webhook_router)
    return app


app = create_app()
