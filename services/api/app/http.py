"""HTTP boundaries: explicit owner auth, sanitized errors and transaction lifetime."""

import hmac
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated, cast

from counterforge_contracts.versions import ErrorCode
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError as DatabaseIntegrityError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from .artifacts import IntegrityError, InvalidImage
from .body_limit import BodyLimitMiddleware
from .config import Settings, validate_origin
from .db import IdempotencyConflict, create_database_engine, create_uow_factory
from .errors import NotImplementedOperation, ServiceError, envelope
from .service import Service

bearer = HTTPBearer(auto_error=False, scheme_name="OwnerToken")
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def settings_for(request: Request) -> Settings:
    settings = request.app.state.settings
    if settings is None:
        try:
            settings = Settings.from_env()
        except ValueError:
            raise ServiceError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "Server configuration is unavailable.", 503
            ) from None
        request.app.state.settings = settings
    return cast(Settings, settings)


async def require_owner(request: Request, credentials: Credentials) -> str:
    settings = settings_for(request)
    expected = settings.owner_auth_token.get_secret_value()
    if (
        not expected
        or credentials is None
        or not hmac.compare_digest(expected.encode(), credentials.credentials.encode())
    ):
        raise ServiceError(ErrorCode.UNAUTHORIZED, "Owner authentication is required.", 401)
    return settings.owner_label


async def service_for(request: Request) -> AsyncIterator[Service]:
    settings = settings_for(request)
    factory = request.app.state.uow_factory
    if factory is None:
        try:
            engine = request.app.state.engine
            if engine is None:
                engine = create_database_engine(settings)
                request.app.state.engine = engine
            factory = create_uow_factory(engine)
        except (ValueError, SQLAlchemyError):
            raise ServiceError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "Persistence is unavailable.", 503
            ) from None
        request.app.state.uow_factory = factory
    with factory() as uow:
        uow.background_jobs.max_pending_jobs = settings.max_pending_jobs
        yield Service(uow, settings)
        uow.commit()
        pending = list(uow.background_jobs.new_job_ids)
    if settings.enqueue_jobs and pending:
        from services.worker.app.queue import enqueue_ids

        enqueue_ids(settings, pending)


Services = Annotated[Service, Depends(service_for, scope="function")]


def base_app(settings: Settings | None, engine: Engine | None) -> FastAPI:
    app = FastAPI(
        title="CounterForge API",
        version="1.0.0",
        description="Owner-authenticated evidence persistence and bounded setup jobs.",
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.uow_factory = None
    try:
        origin = validate_origin(
            settings.web_origin
            if settings
            else os.environ.get("WEB_ORIGIN", "http://localhost:3000")
        )
        origins = [origin]
    except ValueError:
        origins = []
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "Last-Event-ID"],
    )

    @app.exception_handler(ServiceError)
    async def domain_error(request: Request, exc: ServiceError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return JSONResponse(
            status_code=exc.status,
            content=envelope(exc.code, exc.message).model_dump(mode="json"),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=envelope(
                ErrorCode.INVALID_INPUT, "Request does not match the API contract."
            ).model_dump(mode="json"),
        )

    @app.exception_handler(NotImplementedOperation)
    async def disabled(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=501,
            content=envelope(
                ErrorCode.NOT_IMPLEMENTED, "This operation is planned for a later phase."
            ).model_dump(mode="json"),
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code = {
            401: ErrorCode.UNAUTHORIZED,
            403: ErrorCode.FORBIDDEN,
            404: ErrorCode.NOT_FOUND,
            409: ErrorCode.INVALID_STATE,
        }.get(exc.status_code, ErrorCode.INVALID_INPUT)
        return JSONResponse(
            status_code=exc.status_code,
            content=envelope(code, "Request could not be handled.").model_dump(mode="json"),
        )

    @app.exception_handler(InvalidImage)
    async def image_error(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=envelope(
                ErrorCode.INVALID_INPUT, "Image could not be safely accepted."
            ).model_dump(mode="json"),
        )

    @app.exception_handler(IntegrityError)
    async def artifact_error(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=envelope(
                ErrorCode.ARTIFACT_CHECKSUM_MISMATCH, "Artifact integrity check failed."
            ).model_dump(mode="json"),
        )

    @app.exception_handler(IdempotencyConflict)
    async def conflict(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=envelope(
                ErrorCode.IDEMPOTENCY_CONFLICT, "Idempotency key identifies a different request."
            ).model_dump(mode="json"),
        )

    @app.exception_handler(DatabaseIntegrityError)
    async def constraint(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=envelope(
                ErrorCode.INVALID_STATE, "Evidence constraints prevent this change."
            ).model_dump(mode="json"),
        )

    @app.exception_handler(SQLAlchemyError)
    async def unavailable(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content=envelope(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "Persistence is unavailable."
            ).model_dump(mode="json"),
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=envelope(
                ErrorCode.INTERNAL_ERROR, "Request could not be completed."
            ).model_dump(mode="json"),
        )

    @app.middleware("http")
    async def phase_header(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-CounterForge-Phase"] = "03"
        return response

    return app
