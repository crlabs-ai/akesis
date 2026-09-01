import hashlib
import hmac
import time
from typing import Any, Protocol

import httpx

from src.packages.shared.config import settings
from src.packages.shared.logging import get_logger
from src.packages.shared.models import (
    ApprovalRecord,
    ApprovalStatus,
    FailureContext,
    FixProposal,
    ValidationResult,
)

logger = get_logger("akesis.slack_client")


class SlackError(Exception):
    """Base exception for Slack operations."""

    pass


class SlackSignatureError(SlackError):
    """Raised when incoming Slack interaction signature is invalid or timestamp expired."""

    pass


class SlackClientProtocol(Protocol):
    """Interface for Slack notifications and card dispatch."""

    async def post_approval_card(
        self,
        approval: ApprovalRecord,
        context: FailureContext,
        proposal: FixProposal,
        validation: ValidationResult,
    ) -> dict[str, str]:
        """Posts an interactive approval request card to Slack."""
        ...

    async def update_approval_card(
        self,
        response_url: str,
        approval: ApprovalRecord,
    ) -> bool:
        """Updates an interactive approval card replacing action buttons with status."""
        ...


def verify_slack_signature(
    raw_body: bytes,
    timestamp: str | None,
    signature: str | None,
    signing_secret: str,
    max_age_seconds: int = 300,
) -> bool:
    """Verifies HMAC-SHA256 signature from Slack and checks replay window."""
    if not timestamp or not signature or not signing_secret:
        return False

    try:
        req_time = int(timestamp)
        now = int(time.time())
        if abs(now - req_time) > max_age_seconds:
            logger.warning("slack_timestamp_expired", age=abs(now - req_time))
            return False
    except ValueError:
        return False

    sig_basestring = f"v0:{timestamp}:{raw_body.decode('utf-8', errors='replace')}"
    computed = (
        "v0="
        + hmac.new(
            key=signing_secret.encode("utf-8"),
            msg=sig_basestring.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).hexdigest()
    )

    return hmac.compare_digest(computed, signature)


class SlackClient:
    """Dispatches interactive Block Kit cards and updates to Slack."""

    def __init__(
        self,
        webhook_url: str | None = None,
        signing_secret: str | None = None,
    ) -> None:
        self.webhook_url = webhook_url or settings.slack_webhook_url
        self.signing_secret = signing_secret or settings.slack_signing_secret

    async def post_approval_card(
        self,
        approval: ApprovalRecord,
        context: FailureContext,
        proposal: FixProposal,
        validation: ValidationResult,
    ) -> dict[str, str]:
        """Renders and sends Block Kit approval card to configured webhook URL."""
        if not self.webhook_url:
            logger.warning("slack_webhook_not_configured")
            return {"channel_id": "simulated", "message_ts": str(time.time())}

        blocks = self._build_approval_blocks(approval, context, proposal, validation)
        payload = {"blocks": blocks}

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.post(self.webhook_url, json=payload)
                res.raise_for_status()
                logger.info("slack_approval_card_posted", approval_id=approval.approval_id)
                return {"channel_id": "webhook", "message_ts": str(time.time())}
            except Exception as err:
                logger.error("slack_post_failed", error=str(err))
                raise SlackError(f"Failed to post card to Slack: {err}") from err

    async def update_approval_card(
        self,
        response_url: str,
        approval: ApprovalRecord,
    ) -> bool:
        """Updates Slack message via response_url replacing buttons with decision badge."""
        if not response_url:
            return False

        status_emoji = "🟢" if approval.status == ApprovalStatus.APPROVED else "🔴"
        status_title = (
            "PROPOSAL APPROVED"
            if approval.status == ApprovalStatus.APPROVED
            else "PROPOSAL REJECTED"
        )
        decided_by_text = approval.decided_by or "reviewer"
        decided_at_text = approval.decided_at.isoformat() if approval.decided_at else "just now"

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{status_emoji} AKESIS — {status_title}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Status:* `{approval.status.value.upper()}`\n"
                        f"*Decided by:* `{decided_by_text}` at `{decided_at_text}`\n"
                        f"*Approval ID:* `{approval.approval_id}`"
                    ),
                },
            },
        ]
        if approval.decision_reason:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Notes:* {approval.decision_reason}",
                    },
                }
            )

        payload = {
            "replace_original": "true",
            "blocks": blocks,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.post(response_url, json=payload)
                res.raise_for_status()
                logger.info("slack_card_updated", approval_id=approval.approval_id)
                return True
            except Exception as err:
                logger.error("slack_update_failed", error=str(err))
                return False

    def _build_approval_blocks(
        self,
        approval: ApprovalRecord,
        context: FailureContext,
        proposal: FixProposal,
        validation: ValidationResult,
    ) -> list[dict[str, Any]]:
        """Constructs rich Slack Block Kit layout."""
        diff_snippet = proposal.unified_diff
        if len(diff_snippet) > 1500:
            diff_snippet = diff_snippet[:1500] + "\n\n[... diff truncated ...]"

        return [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🔴 AKESIS — CI REMEDIATION APPROVAL REQUEST",
                },
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Repo:* `{context.repository_owner}/{context.repository_name}`",
                    },
                    {"type": "mrkdwn", "text": f"*Run ID:* `#{context.run_id}`"},
                    {"type": "mrkdwn", "text": f"*Category:* `{context.signal.category}`"},
                    {"type": "mrkdwn", "text": f"*Commit:* `{context.commit_sha[:8]}`"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*🛡️ Sandbox Validation:* `{validation.status.value.upper()}` "
                        f"(exit code `{validation.exit_code}`)\n"
                        f"*Confidence:* `{proposal.confidence_score:.2f}` | "
                        f"*Risk:* `{proposal.risk_level.upper()}`"
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Rationale:* {proposal.rationale}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"```\n{diff_snippet}\n```",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Approve & Open PR"},
                        "style": "primary",
                        "action_id": "approve_fix",
                        "value": approval.approval_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "❌ Reject Fix"},
                        "style": "danger",
                        "action_id": "reject_fix",
                        "value": approval.approval_id,
                    },
                ],
            },
        ]
