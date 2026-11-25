"""Operator tool: ``python -m src.apps.worker.requeue_held [--all | APPROVAL_ID ...]``.

Phase 1.14c. Remediation jobs that the remediation hold refused are parked as `held`, with their
approval and attempt budget intact, and are never claimed again on their own. When the hold is
formally superseded (condition 6), an operator runs this tool to move them back to `pending`.
While the hold is still active, a requeued job is simply held again on its next claim, without
consuming an attempt. Only approved, held rows are touched.

Audit: every invocation emits one `remediation_requeue` event (structured JSON on stdout, kept by
journald when run under systemd) with the invoking OS identity, a UTC timestamp, what was
requested, the exact approval IDs requeued, their count and resulting state.

Authorization limitation: there is no operator authentication yet. Anyone who can run this with
the worker's database credentials can requeue. The recorded identity is the process's real OS
UID as reported by the kernel, not a name the caller supplies, so it identifies the account that
ran the tool, not the person behind a shared or sudo-assumed account. The hold, not this tool,
is what prevents execution.
"""

import argparse
import asyncio
import os
import pwd
import socket
from datetime import UTC, datetime
from typing import Any

from src.packages.database import approval_jobs
from src.packages.database.session import get_session_factory
from src.packages.shared.config import settings
from src.packages.shared.logging import configure_logging, get_logger

logger = get_logger("akesis.worker.requeue_held")


def invoker() -> dict[str, Any]:
    """The invoking identity as the kernel reports it. Never caller-supplied."""
    uid = os.getuid()
    try:
        user: str | None = pwd.getpwuid(uid).pw_name
    except KeyError:
        user = None
    return {
        "os_uid": uid,
        "os_euid": os.geteuid(),
        "os_user": user,
        "sudo_uid": os.environ.get("SUDO_UID"),  # informational only: set by sudo, forgeable
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "identity_verified": False,  # no operator authentication exists yet
    }


async def requeue(approval_ids: list[str] | None) -> list[str]:
    async with get_session_factory()() as session:
        async with session.begin():
            return await approval_jobs.requeue_held(session, approval_ids)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.apps.worker.requeue_held")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--all", action="store_true", help="requeue every held job")
    target.add_argument("approval_ids", nargs="*", default=[], metavar="APPROVAL_ID")
    args = parser.parse_args(argv)
    if not args.all and not args.approval_ids:
        parser.error("name approval IDs or pass --all")
    configure_logging(settings.log_level)
    requested = None if args.all else sorted(set(args.approval_ids))
    requeued = asyncio.run(requeue(requested))
    logger.warning(
        "remediation_requeue",
        **invoker(),
        invoked_at=datetime.now(UTC).isoformat(),
        requested="all" if requested is None else requested,
        approval_ids=requeued,
        count=len(requeued),
        resulting_state=approval_jobs.PENDING,
        hold_still_enforced=True,  # require_verified_provenance runs on every claim
    )
    print(f"requeued {len(requeued)} held remediation job(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
