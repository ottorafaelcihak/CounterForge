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
    TargetCheckRequest,
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
from fastapi import APIRouter, Depends, FastAPI, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.engine import Engine

from .artifacts import MAX_UPLOAD_BYTES, InvalidImage
from .config import Settings
from .db.readiness import probe_dependencies
from .errors import NotImplementedOperation, ServiceError, envelope
from .http import Credentials, Services, base_app, require_owner, settings_for

router = APIRouter(dependencies=[Depends(require_owner)])
public_router = APIRouter()


def disabled() -> NoReturn:
    raise NotImplementedOperation


ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorEnvelope}
    for status in (401, 403, 404, 409, 422, 429, 500, 501, 502, 503)
}
PREFIX = "/api/v1"
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]
OptionalKey = Annotated[str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)]
Limit = Annotated[int, Query(ge=1, le=100)]


@public_router.get(PREFIX + "/health/live", response_model=Health)
async def live() -> Health:
    return Health()


@router.get(PREFIX + "/health/ready", responses=ERRORS)
async def ready(request: Request) -> JSONResponse:
    result = probe_dependencies(settings_for(request), request.app.state.engine)
    if result.ready:
        return JSONResponse(status_code=200, content={"status": "ready", "api_version": 1})
    return JSONResponse(
        status_code=503,
        content=envelope(
            ErrorCode.DEPENDENCY_UNAVAILABLE, "Required services are not ready."
        ).model_dump(mode="json"),
    )


@router.post(PREFIX + "/projects", response_model=Project, status_code=201, responses=ERRORS)
async def create_project(body: CreateProject, service: Services) -> Project:
    return service.create_project(body)


@router.get(PREFIX + "/projects/{project_id}", response_model=Project, responses=ERRORS)
async def get_project(project_id: UUID, service: Services) -> Project:
    return service.project_dto(service.project(project_id))


@public_router.get(
    PREFIX + "/assets/{asset_id}/content",
    response_model=None,
    response_class=Response,
    responses={
        **ERRORS,
        200: {"content": {"image/png": {"schema": {"type": "string", "format": "binary"}}}},
    },
)
async def get_asset(
    asset_id: UUID, request: Request, credentials: Credentials, service: Services
) -> Response:
    try:
        await require_owner(request, credentials)
        row = service.asset(asset_id)
    except ServiceError as exc:
        if exc.status != 401:
            raise
        whitelist = service.t.public_asset_whitelist
        if (
            service.db.scalar(select(whitelist.c.asset_id).where(whitelist.c.asset_id == asset_id))
            is None
        ):
            raise
        row = service.row(service.t.artifact_assets, asset_id)
        if row["media_type"] not in {"image/png", "image/jpeg", "image/webp"}:
            raise ServiceError(
                ErrorCode.FORBIDDEN, "This asset cannot be published.", 403
            ) from None
    data = service.artifacts.read_verified(asset_id)
    return Response(
        content=data,
        media_type=row["media_type"],
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Content-Disposition": "inline"
            if row["media_type"].startswith("image/")
            else "attachment",
        },
    )


@router.post(
    PREFIX + "/projects/{project_id}/seeds", response_model=Seed, status_code=201, responses=ERRORS
)
async def upload_seed(
    project_id: UUID,
    service: Services,
    file: Annotated[UploadFile, File()],
    question: Annotated[str, Form(min_length=10, max_length=300)],
    expected_count: Annotated[int, Form(ge=1, le=16)],
    license_ack: Annotated[bool, Form()],
    label_reviewer: Annotated[str, Form(min_length=1, max_length=100)],
) -> Seed:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise InvalidImage("Upload too large")
    return service.create_seed(
        project_id,
        data,
        file.content_type or "",
        question=question,
        expected_count=expected_count,
        license_ack=license_ack,
        label_reviewer=label_reviewer,
    )


@router.post(
    PREFIX + "/seeds/{seed_id}/segment", response_model=JobRef, status_code=202, responses=ERRORS
)
async def segment(
    seed_id: UUID, body: SegmentationRequest, service: Services, idempotency_key: OptionalKey = None
) -> JobRef:
    return service.segment(seed_id, body, idempotency_key)


@router.post(
    PREFIX + "/seeds/{seed_id}/masks/{mask_id}/approve", response_model=Mask, responses=ERRORS
)
async def approve_mask(
    seed_id: UUID, mask_id: UUID, body: ReviewDecision, service: Services
) -> Mask:
    return service.approve_mask(seed_id, mask_id, body)


@router.post(
    PREFIX + "/seeds/{seed_id}/contracts",
    response_model=Contract,
    status_code=201,
    responses=ERRORS,
)
async def create_contract(seed_id: UUID, body: VisualTestContract, service: Services) -> Contract:
    return service.create_contract(seed_id, body)


@router.post(PREFIX + "/contracts/validate", response_model=ValidationReport, responses=ERRORS)
async def validate_contract(body: VisualTestContract, service: Services) -> ValidationReport:
    return service.validate_contract(body)


@router.post(
    PREFIX + "/projects/{project_id}/targets",
    response_model=Target,
    status_code=201,
    responses=ERRORS,
)
async def create_target(project_id: UUID, body: CreateTargetConfig, service: Services) -> Target:
    return service.create_target(project_id, body)


@router.post(
    PREFIX + "/targets/{target_id}/check", response_model=JobRef, status_code=202, responses=ERRORS
)
async def check_target(
    target_id: UUID,
    service: Services,
    body: TargetCheckRequest | None = None,
    idempotency_key: OptionalKey = None,
) -> JobRef:
    """Queues a provider check. A body may state the owner-authorized job limits
    that accounting requires before any dispatch; omitting it still only queues."""

    return service.check_target(target_id, idempotency_key, body)


@router.post(
    PREFIX + "/experiments", response_model=ExperimentRef, status_code=202, responses=ERRORS
)
async def start_experiment(
    body: StartExperiment, idempotency_key: Key, service: Services
) -> ExperimentRef:
    return service.start_experiment(body, idempotency_key)


@router.get(
    PREFIX + "/experiments/{experiment_id}", response_model=ExperimentView, responses=ERRORS
)
async def get_experiment(experiment_id: UUID, service: Services) -> ExperimentView:
    return service.experiment_dto(service.experiment(experiment_id))


@router.get(
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


@router.get(
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


@router.post(PREFIX + "/mutations/{mutation_id}/review", response_model=Mutation, responses=ERRORS)
async def review_mutation(mutation_id: UUID, body: ReviewDecision) -> NoReturn:
    disabled()


@router.post(
    PREFIX + "/experiments/{experiment_id}/pause",
    response_model=ExperimentRef,
    status_code=202,
    responses=ERRORS,
)
async def pause(experiment_id: UUID) -> NoReturn:
    disabled()


@router.post(
    PREFIX + "/experiments/{experiment_id}/resume",
    response_model=ExperimentRef,
    status_code=202,
    responses=ERRORS,
)
async def resume(experiment_id: UUID) -> NoReturn:
    disabled()


@router.post(
    PREFIX + "/experiments/{experiment_id}/cancel",
    response_model=ExperimentRef,
    status_code=202,
    responses=ERRORS,
)
async def cancel(experiment_id: UUID) -> NoReturn:
    disabled()


@router.get(
    PREFIX + "/experiments/{experiment_id}/findings", response_model=Page[Finding], responses=ERRORS
)
async def get_findings(
    experiment_id: UUID,
    service: Services,
    limit: Limit = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    status: FindingStatus | None = None,
) -> Page[Finding]:
    return service.findings(experiment_id, limit, cursor, status)


@router.post(
    PREFIX + "/findings/{finding_id}/confirm",
    response_model=JobRef,
    status_code=202,
    responses=ERRORS,
)
async def confirm(finding_id: UUID, body: ConfirmRequest) -> NoReturn:
    disabled()


@router.post(
    PREFIX + "/findings/{finding_id}/exports",
    response_model=ExportRef,
    status_code=202,
    responses=ERRORS,
)
async def export(finding_id: UUID, idempotency_key: Key, service: Services) -> ExportRef:
    return service.request_export(finding_id, idempotency_key)


@router.get(PREFIX + "/exports/{export_id}", response_model=ExportRef, responses=ERRORS)
async def get_export(export_id: UUID, service: Services) -> ExportRef:
    return service.get_export(export_id)


@router.get(
    PREFIX + "/exports/{export_id}/download",
    response_model=None,
    response_class=Response,
    responses={
        **ERRORS,
        200: {"content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}},
    },
)
async def download_export(export_id: UUID, service: Services) -> Response:
    return Response(
        content=service.download_export(export_id),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="counterforge-{export_id}.zip"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.post(
    PREFIX + "/regressions/{export_id}/retest",
    response_model=JobRef,
    status_code=202,
    responses=ERRORS,
)
async def retest(export_id: UUID, body: RetestRequest) -> NoReturn:
    disabled()


@router.get(
    PREFIX + "/experiments/{experiment_id}/metrics", response_model=Metrics, responses=ERRORS
)
async def metrics(experiment_id: UUID) -> NoReturn:
    disabled()


@public_router.get(PREFIX + "/public/demo", response_model=DemoManifest, responses=ERRORS)
async def demo() -> NoReturn:
    disabled()


# Stable owner-scoped read APIs.
@router.get(PREFIX + "/projects", response_model=Page[Project], responses=ERRORS)
async def list_projects(
    service: Services,
    limit: Limit = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[Project]:
    return service.projects(limit, cursor)


@router.get(PREFIX + "/projects/{project_id}/seeds", response_model=Page[Seed], responses=ERRORS)
async def list_seeds(
    project_id: UUID,
    service: Services,
    limit: Limit = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[Seed]:
    return service.seeds(project_id, limit, cursor)


@router.get(PREFIX + "/seeds/{seed_id}", response_model=Seed, responses=ERRORS)
async def get_seed(seed_id: UUID, service: Services) -> Seed:
    return service.seed_dto(service.seed(seed_id))


@router.get(PREFIX + "/seeds/{seed_id}/masks", response_model=Page[Mask], responses=ERRORS)
async def list_masks(
    seed_id: UUID,
    service: Services,
    limit: Limit = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[Mask]:
    return service.masks(seed_id, limit, cursor)


@router.post(
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


@router.get(PREFIX + "/contracts/{contract_id}", response_model=Contract, responses=ERRORS)
async def get_contract(contract_id: UUID, service: Services) -> Contract:
    return service.contract_dto(service.contract(contract_id))


@router.get(
    PREFIX + "/projects/{project_id}/targets", response_model=Page[Target], responses=ERRORS
)
async def list_targets(
    project_id: UUID,
    service: Services,
    limit: Limit = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[Target]:
    return service.targets(project_id, limit, cursor)


@router.get(PREFIX + "/jobs/{job_id}", response_model=JobRef, responses=ERRORS)
async def get_job(job_id: UUID, service: Services) -> JobRef:
    return service.job_dto(service.job(job_id))


@router.get(PREFIX + "/findings/{finding_id}", response_model=Finding, responses=ERRORS)
async def get_finding(finding_id: UUID, service: Services) -> Finding:
    return service.finding_dto(service.finding(finding_id))


@router.get(PREFIX + "/regressions", response_model=Page[ExportRef], responses=ERRORS)
async def list_regressions(
    service: Services,
    limit: Limit = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[ExportRef]:
    return service.regressions(limit, cursor)


def create_app(*, settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    application = base_app(settings, engine)
    application.include_router(public_router)
    application.include_router(router)
    return application


app = create_app()
