import asyncio
import hashlib
import hmac
import json
import math
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs

import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.packages.database.models import WebhookEventModel
from src.packages.database.repositories import IncidentRepository, PipelineRepository
from src.packages.database.session import get_db_session
from src.packages.database.webhook_events import DeliveryConflictError, WebhookEventRepository
from src.packages.sdk.github_app_auth import GitHubAppAuth
from src.packages.sdk.slack_client import verify_slack_signature
from src.packages.shared.approval_service import (
    ApprovalNotFoundError,
    IllegalStateTransitionError,
    approval_service,
)
from src.packages.shared.config import settings
from src.packages.shared.logging import get_logger
from src.packages.shared.models import (
    IncidentDetail,
    IncidentSummary,
    IngestionResponse,
    PipelineSummary,
    WorkflowRunConclusion,
    WorkflowRunEvent,
)
from src.packages.shared.remediation_orchestrator import (
    RemediationOrchestrator,
)
from src.packages.shared.security import verify_read_token
from src.packages.shared.webhook_admission import admit_verified_delivery
from src.packages.shared.webhook_retry import RetrySchedule, classify_retry
from src.packages.shared.workflow_provenance import (
    ProvenanceVerificationError,
    TransientProvenanceError,
    WorkflowRunProvenanceVerifier,
)

logger = get_logger("akesis.api.routes")
router = APIRouter()
orchestrator = RemediationOrchestrator()


async def _safe_resume_approval(approval_id: str) -> None:
    """Executes approval resumption in background, catching all exceptions."""
    try:
        await orchestrator.resume_approval(approval_id)
    except Exception as err:
        logger.error("background_resume_approval_error", error=str(err), approval_id=approval_id)


def verify_github_signature(
    payload_bytes: bytes,
    signature_header: str | None,
    secret: str,
) -> bool:
    """Verifies the GitHub X-Hub-Signature-256 HMAC header in constant time."""
    if not signature_header or not secret:
        return False

    if not signature_header.startswith("sha256="):
        return False

    expected_signature = signature_header.removeprefix("sha256=")
    computed_hmac = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload_bytes,
        digestmod=hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(computed_hmac, expected_signature)


async def _record_malformed_delivery(
    session: AsyncSession, delivery_id: str | None, payload_bytes: bytes
) -> None:
    """Persist only the digest of an authenticated malformed workflow delivery."""
    if not delivery_id or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", delivery_id):
        raise HTTPException(status_code=400, detail="Missing or invalid GitHub delivery ID")
    try:
        event, _ = await WebhookEventRepository(session).register(
            delivery_id, hashlib.sha256(payload_bytes).hexdigest(), "workflow_run"
        )
        if event.status in ("received", "retryable_error"):
            await session.execute(
                update(WebhookEventModel)
                .where(
                    WebhookEventModel.delivery_id == delivery_id,
                    WebhookEventModel.status.in_(("received", "retryable_error")),
                )
                .values(
                    status="rejected", failure_code="malformed_json", updated_at=datetime.now(UTC)
                )
            )
        await session.commit()
    except DeliveryConflictError:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="Delivery conflicts with prior payload"
        ) from None


EXHAUSTED_DETAIL = "Delivery verification retries exhausted"
IN_PROGRESS_RETRY_AFTER_SECONDS = 30
_TRANSIENT_DETAIL = "Workflow provenance could not be verified"


def _retry_schedule(
    headers: dict[str, str], attempt: int, settings_obj: Any, now: datetime
) -> RetrySchedule:
    return classify_retry(
        headers,
        attempt,
        now=now,
        base_delay=settings_obj.webhook_retry_base_delay_seconds,
        max_delay=settings_obj.webhook_retry_max_delay_seconds,
        provider_ceiling=settings_obj.webhook_retry_provider_delay_ceiling_seconds,
    )


def _seconds_until(at: datetime, now: datetime) -> int:
    due = at if at.tzinfo is not None else at.replace(tzinfo=UTC)
    return max(1, math.ceil((due - now).total_seconds()))


def _attempt_failure_response(
    outcome: str | None, retry_at: datetime, now: datetime, transient_detail: str
) -> HTTPException:
    """Map the persisted outcome of a failed attempt to the response for the sender."""
    if outcome in ("retryable_error", "provider_hold"):
        return HTTPException(
            status_code=503,
            detail=transient_detail,
            headers={"Retry-After": str(_seconds_until(retry_at, now))},
        )
    if outcome == "exhausted":
        return HTTPException(status_code=422, detail=EXHAUSTED_DETAIL)
    if outcome == "rejected":
        return HTTPException(status_code=422, detail=_TRANSIENT_DETAIL)
    # The attempt was superseded by another verifier; the holder of that attempt decides.
    return HTTPException(
        status_code=503,
        detail="Delivery verification already in progress",
        headers={"Retry-After": str(IN_PROGRESS_RETRY_AFTER_SECONDS)},
    )


def _unclaimed_response(row: Any, now: datetime) -> HTTPException:
    """Explain a refused redelivery; a scheduled retry tells the sender when to try again."""
    if (
        row is not None
        and row.status in ("retryable_error", "provider_hold")
        and row.next_retry_at is not None
    ):
        return HTTPException(
            status_code=503,
            detail="Delivery verification is scheduled for retry; redeliver after Retry-After",
            headers={"Retry-After": str(_seconds_until(row.next_retry_at, now))},
        )
    return HTTPException(
        status_code=503,
        detail="Delivery verification already in progress",
        headers={"Retry-After": str(IN_PROGRESS_RETRY_AFTER_SECONDS)},
    )


@router.post(
    "/v1/webhooks/github",
    response_model=IngestionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest GitHub webhook events and trigger remediation asynchronously",
)
async def handle_github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str | None = Header(default=None, alias="X-GitHub-Event"),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    x_github_delivery: str | None = Header(default=None, alias="X-GitHub-Delivery"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> IngestionResponse:
    """Receives and verifies webhook events, launching orchestration asynchronously."""
    payload_bytes = await request.body()

    # 1. Security Check: HMAC Signature Verification
    is_valid = verify_github_signature(
        payload_bytes=payload_bytes,
        signature_header=x_hub_signature_256,
        secret=settings.github_webhook_secret,
    )
    if not is_valid:
        logger.warning("Rejected webhook due to invalid HMAC signature")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing webhook signature (X-Hub-Signature-256)",
        )

    # 2. Validate Event Type
    if x_github_event != "workflow_run":
        logger.info("Ignored non-workflow_run event", event_type=x_github_event)
        return IngestionResponse(
            status="ignored",
            message=f"Event '{x_github_event}' is not supported in V1",
        )

    # 3. Parse JSON Payload
    try:
        payload: dict[str, Any] = await request.json()
    except (ValueError, UnicodeDecodeError):
        await _record_malformed_delivery(session, x_github_delivery, payload_bytes)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed JSON payload",
        ) from None

    # 4. Validate and Normalize workflow_run Payload
    if not isinstance(payload, dict):
        await _record_malformed_delivery(session, x_github_delivery, payload_bytes)
        raise HTTPException(status_code=400, detail="Malformed JSON payload")
    workflow_run_data = payload.get("workflow_run")
    repo_data = payload.get("repository")
    action = payload.get("action", "")

    if not isinstance(workflow_run_data, dict) or not isinstance(repo_data, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Payload missing required 'workflow_run' or 'repository' objects",
        )

    conclusion_str = workflow_run_data.get("conclusion") or "unknown"
    conclusion = (
        WorkflowRunConclusion(conclusion_str)
        if conclusion_str in WorkflowRunConclusion._value2member_map_
        else WorkflowRunConclusion.UNKNOWN
    )

    event = WorkflowRunEvent(
        event_type="workflow_run",
        action=action,
        repository_owner=repo_data.get("owner", {}).get("login", "unknown"),
        repository_name=repo_data.get("name", "unknown"),
        run_id=workflow_run_data.get("id", 0),
        workflow_name=workflow_run_data.get("name", "Unnamed Workflow"),
        head_branch=workflow_run_data.get("head_branch") or "",
        head_sha=workflow_run_data.get("head_sha") or "",
        run_url=workflow_run_data.get("html_url", ""),
        conclusion=conclusion,
        sender=payload.get("sender", {}).get("login"),
    )

    # 5. Check if actionable failure
    if event.conclusion != WorkflowRunConclusion.FAILURE:
        logger.info(
            "Ignored non-failure workflow run",
            repo=f"{event.repository_owner}/{event.repository_name}",
            run_id=event.run_id,
            conclusion=event.conclusion,
        )
        return IngestionResponse(
            status="ignored",
            message=f"Workflow run conclusion '{event.conclusion}' is not a failure",
        )

    if action != "completed":
        return IngestionResponse(status="ignored", message="Workflow run is not completed")
    if not x_github_delivery or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", x_github_delivery):
        raise HTTPException(status_code=400, detail="Missing or invalid GitHub delivery ID")
    deliveries = WebhookEventRepository(session)
    try:
        delivery, _ = await deliveries.register(
            x_github_delivery, hashlib.sha256(payload_bytes).hexdigest(), "workflow_run"
        )
        if delivery.status in ("verified", "duplicate"):
            await session.commit()
            return IngestionResponse(status="accepted", message="Delivery already processed")
        if delivery.status == "rejected":
            await session.commit()
            raise HTTPException(status_code=422, detail="Delivery was rejected")
        if delivery.status == "exhausted":
            # Terminal: no claim, no GitHub call, and no state change for repeat deliveries.
            await session.commit()
            raise HTTPException(status_code=422, detail=EXHAUSTED_DETAIL)
        max_attempts = request.app.state.settings.webhook_retry_max_attempts
        claimed = await deliveries.claim(x_github_delivery, max_attempts)
        if not claimed:
            if await deliveries.exhaust(x_github_delivery, max_attempts):
                await session.commit()
                raise HTTPException(status_code=422, detail=EXHAUSTED_DETAIL)
            row = await deliveries.state(x_github_delivery)
            await session.commit()
            raise _unclaimed_response(row, datetime.now(UTC))
        source = workflow_run_data.get("head_repository") or {}
        installation = payload.get("installation") or {}

        def safe_int(value: Any) -> int | None:
            return value if type(value) is int and 0 < value < 2**63 else None

        def safe_str(value: Any, limit: int) -> str | None:
            return value if isinstance(value, str) and 0 < len(value) <= limit else None

        await session.execute(
            update(WebhookEventModel)
            .where(WebhookEventModel.delivery_id == x_github_delivery)
            .values(
                action="completed",
                installation_id=safe_int(installation.get("id"))
                if isinstance(installation, dict)
                else None,
                event_repository_id=safe_int(repo_data.get("id")),
                event_repository_name=safe_str(repo_data.get("full_name"), 255),
                run_id=safe_int(workflow_run_data.get("id")),
                run_attempt=(
                    value
                    if (value := safe_int(workflow_run_data.get("run_attempt"))) is not None
                    and value < 2**31
                    else None
                ),
                source_repository_id=safe_int(source.get("id"))
                if isinstance(source, dict)
                else None,
                source_repository_name=safe_str(source.get("full_name"), 255)
                if isinstance(source, dict)
                else None,
                source_sha=safe_str(workflow_run_data.get("head_sha"), 40),
                source_branch=safe_str(workflow_run_data.get("head_branch"), 255),
                workflow_name=safe_str(workflow_run_data.get("name"), 255),
                run_url=safe_str(workflow_run_data.get("html_url"), 512),
                conclusion=conclusion.value,
            )
        )
        attempt_number = (
            await session.execute(
                select(WebhookEventModel.attempt_count).where(
                    WebhookEventModel.delivery_id == x_github_delivery
                )
            )
        ).scalar_one()
        await session.commit()
    except DeliveryConflictError:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="Delivery conflicts with prior payload"
        ) from None

    # Verification is deliberately separate from the unconditional remediation hold.
    active_settings = request.app.state.settings
    if not active_settings.github_app_id or not active_settings.github_app_private_key:
        now = datetime.now(UTC)
        schedule = _retry_schedule({}, attempt_number, active_settings, now)
        outcome = await deliveries.record_attempt_failure(
            x_github_delivery,
            attempt_number,
            retryable=True,
            failure_code="app_not_configured",
            next_retry_at=schedule.next_retry_at,
            max_attempts=active_settings.webhook_retry_max_attempts,
            retry_status=schedule.status,
        )
        await session.commit()
        raise _attempt_failure_response(
            outcome, schedule.next_retry_at, now, "GitHub App provenance verification unavailable"
        )
    try:
        # One deadline bounds every GitHub request in this verification (see ADR-0010).
        async with asyncio.timeout(active_settings.webhook_verification_deadline_seconds):
            async with httpx.AsyncClient(base_url="https://api.github.com", timeout=30.0) as api:
                app_auth = GitHubAppAuth(
                    active_settings.github_app_id,
                    active_settings.github_app_private_key,
                    client=api,
                )
                provenance = await WorkflowRunProvenanceVerifier(app_auth, api).verify(
                    payload, x_github_delivery
                )
    except TimeoutError:
        now = datetime.now(UTC)
        schedule = _retry_schedule({}, attempt_number, active_settings, now)
        outcome = await deliveries.record_attempt_failure(
            x_github_delivery,
            attempt_number,
            retryable=True,
            failure_code="verification_deadline_exceeded",
            next_retry_at=schedule.next_retry_at,
            max_attempts=active_settings.webhook_retry_max_attempts,
            retry_status=schedule.status,
        )
        await session.commit()
        raise _attempt_failure_response(
            outcome, schedule.next_retry_at, now, _TRANSIENT_DETAIL
        ) from None
    except (ProvenanceVerificationError, ValueError) as error:
        retryable = isinstance(error, TransientProvenanceError)
        now = datetime.now(UTC)
        retry_headers = error.headers if isinstance(error, TransientProvenanceError) else {}
        schedule = _retry_schedule(retry_headers, attempt_number, active_settings, now)
        outcome = await deliveries.record_attempt_failure(
            x_github_delivery,
            attempt_number,
            retryable=retryable,
            failure_code=error.code
            if isinstance(error, ProvenanceVerificationError)
            else "provenance_mismatch",
            next_retry_at=schedule.next_retry_at,
            max_attempts=active_settings.webhook_retry_max_attempts,
            retry_status=schedule.status,
        )
        await session.commit()
        raise _attempt_failure_response(
            outcome, schedule.next_retry_at, now, _TRANSIENT_DETAIL
        ) from None

    admission = await admit_verified_delivery(
        session,
        delivery_id=x_github_delivery,
        attempt_number=attempt_number,
        provenance=provenance,
        workflow_name=event.workflow_name,
        run_url=event.run_url,
        conclusion=event.conclusion,
    )
    if admission.result == "superseded":
        raise HTTPException(status_code=503, detail="Verification claim was superseded")
    if admission.result == "conflict":
        raise HTTPException(
            status_code=409, detail="Workflow identity conflicts with prior delivery"
        )
    if admission.result == "duplicate":
        return IngestionResponse(status="accepted", message="Workflow attempt already processed")
    return IngestionResponse(
        status="accepted",
        incident_id=admission.incident_id,
        message="Provenance verified; remediation remains disabled",
    )


@router.post(
    "/v1/slack/interactions",
    status_code=status.HTTP_200_OK,
    summary="Handle Slack interactive component callbacks and resume orchestration",
)
async def handle_slack_interaction(
    request: Request,
    background_tasks: BackgroundTasks,
    x_slack_signature: str | None = Header(default=None, alias="X-Slack-Signature"),
    x_slack_request_timestamp: str | None = Header(default=None, alias="X-Slack-Request-Timestamp"),
) -> dict[str, Any]:
    """Receives and verifies Slack button interactions, resuming orchestration on approval."""
    raw_body = await request.body()

    is_valid = verify_slack_signature(
        raw_body=raw_body,
        timestamp=x_slack_request_timestamp,
        signature=x_slack_signature,
        signing_secret=settings.slack_signing_secret,
    )
    if not is_valid:
        logger.warning("Rejected Slack interaction due to invalid HMAC signature")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Slack signature",
        )

    try:
        body_str = raw_body.decode("utf-8")
        parsed_form = parse_qs(body_str)
        payload_raw = parsed_form.get("payload", [""])[0]
        if not payload_raw:
            raise ValueError("Missing 'payload' parameter in form data")
        payload_data = json.loads(payload_raw)
    except Exception as err:
        logger.error("Failed to parse Slack interaction payload", error=str(err))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Malformed Slack interaction payload: {err}",
        ) from err

    actions = payload_data.get("actions", [])
    if not actions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No actions found in payload",
        )

    action = actions[0]
    action_id = action.get("action_id", "")
    approval_id = action.get("value", "")
    user = payload_data.get("user", {})
    user_id = user.get("id", "")
    workspace_id = (payload_data.get("team") or {}).get("id", "")
    channel_id = (payload_data.get("channel") or {}).get("id", "")
    response_url = payload_data.get("response_url")

    if not approval_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing approval ID in action value",
        )

    decisions = {"approve_fix": "approve", "reject_fix": "reject"}
    if action_id not in decisions:
        raise HTTPException(status_code=400, detail="Unsupported approval action")
    policy = request.app.state.settings
    if (
        not user_id
        or user_id not in policy.slack_approver_user_ids
        or not policy.slack_workspace_id
        or workspace_id != policy.slack_workspace_id
        or not policy.slack_channel_id
        or channel_id != policy.slack_channel_id
    ):
        raise HTTPException(status_code=403, detail="Slack approver is not authorized")
    decision = decisions[action_id]

    try:
        # 1. Authoritative decision recorded in PostgreSQL
        record, is_duplicate = await approval_service.record_decision(
            approval_id=approval_id,
            decision=decision,
            decided_by=user_id,
            response_url=response_url,
        )

        # 2. Resume pipeline in background task if approved
        if not is_duplicate and record.status.value == "approved":
            background_tasks.add_task(
                _safe_resume_approval,
                approval_id=record.approval_id,
            )

        return {
            "status": "ok",
            "approval_id": record.approval_id,
            "decision": record.status,
            "is_duplicate": is_duplicate,
        }
    except ApprovalNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        ) from err
    except IllegalStateTransitionError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(err),
        ) from err


@router.get(
    "/v1/incidents",
    response_model=list[IncidentSummary],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_read_token)],
    summary="List incident summaries with pagination and deterministic ordering",
)
async def list_incidents(
    limit: int = Query(default=20, ge=1, le=100, description="Max items to return (1-100)"),
    offset: int = Query(default=0, ge=0, description="Pagination offset (>=0)"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[IncidentSummary]:
    """Returns a list of lightweight incident summaries without diff_content."""
    repo = IncidentRepository(session)
    return await repo.list_incidents(limit=limit, offset=offset)


@router.get(
    "/v1/incidents/{incident_id}",
    response_model=IncidentDetail,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_read_token)],
    summary="Get full incident detail by ID including diff_content and execution context",
)
async def get_incident(
    incident_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> IncidentDetail:
    """Returns full incident detail including patch diff, proposals, and logs."""
    repo = IncidentRepository(session)
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found",
        )
    return incident


@router.get(
    "/v1/pipelines",
    response_model=list[PipelineSummary],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_read_token)],
    summary="List recent pipeline summaries with pagination and deterministic ordering",
)
async def list_pipelines(
    limit: int = Query(default=20, ge=1, le=100, description="Max items to return (1-100)"),
    offset: int = Query(default=0, ge=0, description="Pagination offset (>=0)"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[PipelineSummary]:
    """Returns recent pipeline execution summaries from PostgreSQL."""
    repo = PipelineRepository(session)
    return await repo.list_recent_pipelines(limit=limit, offset=offset)
