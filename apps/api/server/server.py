"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import fastapi_mongo_base.sql.models as sql_models
import fastapi_mongo_base.sql.session as sql_session
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi_mongo_base.core import app_factory
from sqlalchemy import text

from apps.auth.middleware import jwt_guard
from apps.auth.routes import router as auth_router
from apps.provider_connections.routes import router as provider_connections_router
from server.config import Settings
from server.database import create_engine, create_session_factory
from server.scheduler import build_scheduler
from utils.crypto import CredentialCipher


def create_application(*, start_scheduler: bool = True) -> FastAPI:
    """Create an isolated application instance."""
    settings = Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_uri)
        session_factory = create_session_factory(engine)
        sql_models.async_session = session_factory
        sql_session.async_session = session_factory
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.credential_cipher = CredentialCipher.from_environment(
            data_dir=settings.data_dir,
            env_key=settings.master_key,
        )
        scheduler = build_scheduler()
        app.state.scheduler = scheduler
        if start_scheduler:
            scheduler.start()
        try:
            yield
        finally:
            if scheduler.running:
                scheduler.shutdown(wait=False)
            await engine.dispose()

    app = app_factory.create_app(
        settings=settings,
        lifespan_func=lifespan,
        title="Universal Media Manager API",
    )
    app.middleware("http")(jwt_guard)
    app.include_router(auth_router, prefix=settings.base_path)
    app.include_router(provider_connections_router, prefix=settings.base_path)

    @app.get("/api/v1/health", tags=["System"])
    async def health(request: Request) -> dict[str, str]:
        async with request.app.state.session_factory() as session:
            await session.execute(text("SELECT 1"))
        return {"service": "umedia-api", "status": "ok", "database": "ok"}

    @app.exception_handler(404)
    async def not_found(_: Request, __: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={
                "code": "not_found",
                "message": "Resource not found",
                "details": {},
            },
        )

    return app
