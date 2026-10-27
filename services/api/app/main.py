from collections.abc import Awaitable, Callable
from typing import Annotated, Any, NoReturn
from uuid import UUID

from counterforge_contracts.requests import (
    ConfirmRequest,
    CreateProject,
    CreateTargetConfig,
    RetestRequest,
    ReviewDecision,
    SegmentationRequest,
    StartExperiment,
)
from counterforge_contracts.responses import (
    Contract,
    DemoManifest,
    ErrorEnvelope,
    ExperimentRef,
    ExperimentView,
    ExportRef,
    Finding,
    Health,
    JobRef,
    Mask,
    Metrics,
    Mutation,
    Page,
    Project,
    Seed,
    Target,
    ValidationReport,
)
from counterforge_contracts.versions import ErrorCode, FindingStatus, MutationState
from counterforge_contracts.vtc import VisualTestContract
from fastapi import FastAPI, File, Form, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.exceptions import HTTPException

from .errors import NotImplementedOperation, envelope

app = FastAPI(
    title="CounterForge API",
    version="1.0.0",
    description="Phase 01 contract surface. Business operations return 501 until implemented.",
)


def disabled() -> NoReturn:
    raise NotImplementedOperation


@app.exception_handler(NotImplementedOperation)
async def not_implemented(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=501,
        content=envelope(
            ErrorCode.NOT_IMPLEMENTED, "This operation is not implemented in Phase 01."
        ).model_dump(mode="json"),
    )


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Pydantic errors can embed submitted values: never return them wholesale.
    return JSONResponse(
        status_code=422,
        content=envelope(
            ErrorCode.INVALID_INPUT, "Request does not match the API contract."
        ).model_dump(mode="json"),
    )


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    code = ErrorCode.NOT_FOUND if exc.status_code == 404 else ErrorCode.INVALID_INPUT
    return JSONResponse(
        status_code=exc.status_code,
        content=envelope(
            code, "Route not found." if exc.status_code == 404 else "Request could not be handled."
        ).model_dump(mode="json"),
    )


@app.middleware("http")
async def correlation_header(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    response.headers["X-CounterForge-Phase"] = "01"
    return response


ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorEnvelope}
    for status in (401, 403, 404, 409, 422, 429, 500, 501, 502, 503)
}
PREFIX = "/api/v1"
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]
Limit = Annotated[int, Query(ge=1, le=100)]


@app.get(PREFIX + "/health/live", response_model=Health)
async def live() -> Health:
    return Health()


@app.get(PREFIX + "/health/ready", responses=ERRORS)
async def ready() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content=envelope(
            ErrorCode.NOT_IMPLEMENTED, "Persistence and queue readiness are not implemented."
        ).model_dump(mode="json"),
    )


@app.post(PREFIX + "/projects", response_model=Project, status_code=201, responses=ERRORS)
async def create_project(body: CreateProject) -> NoReturn:
    disabled()


@app.get(PREFIX + "/projects/{project_id}", response_model=Project, responses=ERRORS)
async def get_project(project_id: UUID) -> NoReturn:
    disabled()


@app.get(
    PREFIX + "/assets/{asset_id}/content",
    response_model=None,
    response_class=Response,
    responses={
        **ERRORS,
        200: {"content": {"image/png": {"schema": {"type": "string", "format": "binary"}}}},
    },
)
async def get_asset(asset_id: UUID) -> NoReturn:
    disabled()


@app.post(
    PREFIX + "/projects/{project_id}/seeds", response_model=Seed, status_code=201, responses=ERRORS
)
async def upload_seed(
    project_id: UUID,
    file: Annotated[UploadFile, File()],
    question: Annotated[str, Form(min_length=10, max_length=300)],
    expected_count: Annotated[int, Form(ge=1, le=16)],
    license_ack: Annotated[bool, Form()],
) -> NoReturn:
    disabled()


@app.post(
    PREFIX + "/seeds/{seed_id}/segment", response_model=JobRef, status_code=202, responses=ERRORS
)
async def segment(seed_id: UUID, body: SegmentationRequest) -> NoReturn:
    disabled()


@app.post(
    PREFIX + "/seeds/{seed_id}/masks/{mask_id}/approve", response_model=Mask, responses=ERRORS
)
async def approve_mask(seed_id: UUID, mask_id: UUID, body: ReviewDecision) -> NoReturn:
    disabled()


@app.post(
    PREFIX + "/seeds/{seed_id}/contracts",
    response_model=Contract,
    status_code=201,
    responses=ERRORS,
)
async def create_contract(seed_id: UUID, body: VisualTestContract) -> NoReturn:
    disabled()


@app.post(PREFIX + "/contracts/validate", response_model=ValidationReport, responses=ERRORS)
async def validate_contract(body: VisualTestContract) -> NoReturn:
    disabled()


@app.post(
    PREFIX + "/projects/{project_id}/targets",
    response_model=Target,
    status_code=201,
    responses=ERRORS,
)
async def create_target(project_id: UUID, body: CreateTargetConfig) -> NoReturn:
    disabled()


@app.post(
    PREFIX + "/targets/{target_id}/check", response_model=JobRef, status_code=202, responses=ERRORS
)
async def check_target(target_id: UUID) -> NoReturn:
    disabled()


@app.post(PREFIX + "/experiments", response_model=ExperimentRef, status_code=202, responses=ERRORS)
async def start_experiment(body: StartExperiment, idempotency_key: Key) -> NoReturn:
    disabled()


@app.get(PREFIX + "/experiments/{experiment_id}", response_model=ExperimentView, responses=ERRORS)
async def get_experiment(experiment_id: UUID) -> NoReturn:
    disabled()


@app.get(
    PREFIX + "/experiments/{experiment_id}/events",
    response_model=None,
    response_class=StreamingResponse,
    responses={**ERRORS, 200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def get_events(
    experiment_id: UUID,
    last_event_id: Annotated[int | None, Header(alias="Last-Event-ID", ge=0)] = None,
) -> NoReturn:
    disabled()


@app.get(
    PREFIX + "/experiments/{experiment_id}/mutations",
    response_model=Page[Mutation],
    responses=ERRORS,
)
async def get_mutations(
    experiment_id: UUID,
    limit: Limit = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    status: MutationState | None = None,
) -> NoReturn:
    disabled()


@app.post(PREFIX + "/mutations/{mutation_id}/review", response_model=Mutation, responses=ERRORS)
async def review_mutation(mutation_id: UUID, body: ReviewDecision) -> NoReturn:
    disabled()


@app.post(
    PREFIX + "/experiments/{experiment_id}/pause",
    response_model=ExperimentRef,
    status_code=202,
    responses=ERRORS,
)
async def pause(experiment_id: UUID) -> NoReturn:
    disabled()


@app.post(
    PREFIX + "/experiments/{experiment_id}/resume",
    response_model=ExperimentRef,
    status_code=202,
    responses=ERRORS,
)
async def resume(experiment_id: UUID) -> NoReturn:
    disabled()


@app.post(
    PREFIX + "/experiments/{experiment_id}/cancel",
    response_model=ExperimentRef,
    status_code=202,
    responses=ERRORS,
)
async def cancel(experiment_id: UUID) -> NoReturn:
    disabled()


@app.get(
    PREFIX + "/experiments/{experiment_id}/findings", response_model=Page[Finding], responses=ERRORS
)
async def get_findings(
    experiment_id: UUID,
    limit: Limit = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    status: FindingStatus | None = None,
) -> NoReturn:
    disabled()


@app.post(
    PREFIX + "/findings/{finding_id}/confirm",
    response_model=JobRef,
    status_code=202,
    responses=ERRORS,
)
async def confirm(finding_id: UUID, body: ConfirmRequest) -> NoReturn:
    disabled()


@app.post(
    PREFIX + "/findings/{finding_id}/exports",
    response_model=ExportRef,
    status_code=202,
    responses=ERRORS,
)
async def export(finding_id: UUID, idempotency_key: Key) -> NoReturn:
    disabled()


@app.get(PREFIX + "/exports/{export_id}", response_model=ExportRef, responses=ERRORS)
async def get_export(export_id: UUID) -> NoReturn:
    disabled()


@app.get(
    PREFIX + "/exports/{export_id}/download",
    response_model=None,
    response_class=Response,
    responses={
        **ERRORS,
        200: {"content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}},
    },
)
async def download_export(export_id: UUID) -> NoReturn:
    disabled()


@app.post(
    PREFIX + "/regressions/{export_id}/retest",
    response_model=JobRef,
    status_code=202,
    responses=ERRORS,
)
async def retest(export_id: UUID, body: RetestRequest) -> NoReturn:
    disabled()


@app.get(PREFIX + "/experiments/{experiment_id}/metrics", response_model=Metrics, responses=ERRORS)
async def metrics(experiment_id: UUID) -> NoReturn:
    disabled()


@app.get(PREFIX + "/public/demo", response_model=DemoManifest, responses=ERRORS)
async def demo() -> NoReturn:
    disabled()


# Reserved Phase 02 UI contracts: declarations only, no business behavior.
@app.get(PREFIX + "/projects", response_model=Page[Project], responses=ERRORS)
async def list_projects(
    limit: Limit = 20, cursor: Annotated[str | None, Query(max_length=512)] = None
) -> NoReturn:
    disabled()


@app.get(PREFIX + "/projects/{project_id}/seeds", response_model=Page[Seed], responses=ERRORS)
async def list_seeds(
    project_id: UUID, limit: Limit = 20, cursor: Annotated[str | None, Query(max_length=512)] = None
) -> NoReturn:
    disabled()


@app.get(PREFIX + "/seeds/{seed_id}", response_model=Seed, responses=ERRORS)
async def get_seed(seed_id: UUID) -> NoReturn:
    disabled()


@app.get(PREFIX + "/seeds/{seed_id}/masks", response_model=Page[Mask], responses=ERRORS)
async def list_masks(
    seed_id: UUID, limit: Limit = 20, cursor: Annotated[str | None, Query(max_length=512)] = None
) -> NoReturn:
    disabled()


@app.post(
    PREFIX + "/seeds/{seed_id}/masks/{mask_id}/revisions",
    response_model=Mask,
    status_code=201,
    responses=ERRORS,
)
async def revise_mask(
    seed_id: UUID,
    mask_id: UUID,
    file: Annotated[UploadFile, File()],
    notes: Annotated[str, Form(min_length=1, max_length=2000)],
) -> NoReturn:
    disabled()


@app.get(PREFIX + "/contracts/{contract_id}", response_model=Contract, responses=ERRORS)
async def get_contract(contract_id: UUID) -> NoReturn:
    disabled()


@app.get(PREFIX + "/projects/{project_id}/targets", response_model=Page[Target], responses=ERRORS)
async def list_targets(
    project_id: UUID, limit: Limit = 20, cursor: Annotated[str | None, Query(max_length=512)] = None
) -> NoReturn:
    disabled()


@app.get(PREFIX + "/jobs/{job_id}", response_model=JobRef, responses=ERRORS)
async def get_job(job_id: UUID) -> NoReturn:
    disabled()


@app.get(PREFIX + "/findings/{finding_id}", response_model=Finding, responses=ERRORS)
async def get_finding(finding_id: UUID) -> NoReturn:
    disabled()


@app.get(PREFIX + "/regressions", response_model=Page[ExportRef], responses=ERRORS)
async def list_regressions(
    limit: Limit = 20, cursor: Annotated[str | None, Query(max_length=512)] = None
) -> NoReturn:
    disabled()
