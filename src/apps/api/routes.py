import hashlib
import hmac
import json
import uuid
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Header, HTTPException, Request, status

from src.packages.sdk.github_client import GitHubClient, GitHubResourceNotFoundError
from src.packages.sdk.slack_client import verify_slack_signature
from src.packages.shared.approval_service import (
    ApprovalNotFoundError,
    IllegalStateTransitionError,
    approval_service,
)
from src.packages.shared.config import settings
from src.packages.shared.log_parser import filter_and_extract_signal
from src.packages.shared.logging import get_logger
from src.packages.shared.models import (
    FailureContext,
    IngestionResponse,
    WorkflowRunConclusion,
    WorkflowRunEvent,
)
from src.packages.shared.mutation_service import (
    GitMutationService,
)

logger = get_logger("akesis.api.routes")
router = APIRouter()
mutation_service = GitMutationService()


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


@router.post(
    "/v1/webhooks/github",
    response_model=IngestionResponse,
    status_code=status.HTTP_200_OK,
    summary="Ingest GitHub webhook events",
)
async def handle_github_webhook(
    request: Request,
    x_github_event: str | None = Header(default=None, alias="X-GitHub-Event"),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> IngestionResponse:
    """Receives, verifies, and normalizes GitHub webhook events for CI failure remediation."""
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
    except Exception as err:
        logger.error("Failed to parse webhook JSON body", error=str(err))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed JSON payload",
        ) from err

    # 4. Validate and Normalize workflow_run Payload
    workflow_run_data = payload.get("workflow_run")
    repo_data = payload.get("repository")
    action = payload.get("action", "")

    if not workflow_run_data or not repo_data:
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
        head_branch=workflow_run_data.get("head_branch", "unknown"),
        head_sha=workflow_run_data.get("head_sha", "unknown"),
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

    incident_id = f"inc_{uuid.uuid4().hex[:12]}"
    logger.info(
        "Ingested workflow failure event",
        incident_id=incident_id,
        repo=f"{event.repository_owner}/{event.repository_name}",
        run_id=event.run_id,
        sha=event.head_sha,
        branch=event.head_branch,
    )

    # 6. Fetch Logs & Extract Signal
    client = GitHubClient()
    raw_logs = ""
    try:
        raw_logs = await client.get_workflow_run_logs(
            owner=event.repository_owner,
            repo=event.repository_name,
            run_id=event.run_id,
        )
    except GitHubResourceNotFoundError:
        logger.warning("Logs not found on GitHub for run", run_id=event.run_id)
        raw_logs = "Log output unavailable from GitHub API"
    except Exception as err:
        logger.error("Failed to retrieve logs from GitHub API", error=str(err))
        raw_logs = f"Log retrieval error: {err}"

    # 7. Extract Failure Signal & Construct FailureContext
    signal = filter_and_extract_signal(raw_logs)
    failure_context = FailureContext(
        incident_id=incident_id,
        repository_owner=event.repository_owner,
        repository_name=event.repository_name,
        run_id=event.run_id,
        workflow_name=event.workflow_name,
        commit_sha=event.head_sha,
        branch=event.head_branch,
        run_url=event.run_url,
        conclusion=event.conclusion,
        signal=signal,
        raw_log_excerpt=signal.extracted_snippet,
    )

    logger.info(
        "Constructed failure context",
        incident_id=failure_context.incident_id,
        category=signal.category,
        error_type=signal.error_type,
        target_file=signal.target_file,
    )

    target_display = signal.target_file or "unknown file"
    return IngestionResponse(
        status="accepted",
        incident_id=incident_id,
        category=signal.category,
        message=f"Failure successfully diagnosed: {signal.error_type} in {target_display}",
    )


@router.post(
    "/v1/slack/interactions",
    status_code=status.HTTP_200_OK,
    summary="Handle Slack interactive component callbacks",
)
async def handle_slack_interaction(
    request: Request,
    x_slack_signature: str | None = Header(default=None, alias="X-Slack-Signature"),
    x_slack_request_timestamp: str | None = Header(default=None, alias="X-Slack-Request-Timestamp"),
) -> dict[str, Any]:
    """Receives and verifies Slack button interactions (Approve / Reject)."""
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
    user_name = user.get("username") or user.get("name") or user.get("id", "unknown_user")
    response_url = payload_data.get("response_url")

    if not approval_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing approval ID in action value",
        )

    decision = "approve" if action_id == "approve_fix" else "reject"

    try:
        record, is_duplicate = await approval_service.record_decision(
            approval_id=approval_id,
            decision=decision,
            decided_by=user_name,
            response_url=response_url,
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
