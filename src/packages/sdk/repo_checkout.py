import os
import re
from pathlib import Path
from typing import Protocol

import git

from src.packages.shared.config import settings
from src.packages.shared.logging import get_logger

logger = get_logger("akesis.repo_checkout")

COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")
REPO_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]+$")


def _sanitize_git_message(msg: str) -> str:
    """Sanitizes raw git/URL error messages to prevent credential leakage."""
    if not msg:
        return ""
    sanitized = msg
    if settings.github_token:
        sanitized = sanitized.replace(settings.github_token, "***")
    sanitized = re.sub(r"https://[^@\s]+@", "https://***@", sanitized)
    return sanitized


class RepositoryCheckoutError(Exception):
    """Base exception for repository checkout errors."""

    pass


class InvalidCommitError(RepositoryCheckoutError):
    """Raised when commit SHA format is invalid or cannot be resolved."""

    pass


class RepositoryNotFoundError(RepositoryCheckoutError):
    """Raised when repository cannot be cloned or opened."""

    pass


class RepoCheckoutProtocol(Protocol):
    """Interface for checking out repository source at an exact commit."""

    def checkout_commit(
        self,
        repo_owner: str,
        repo_name: str,
        commit_sha: str,
        clone_url: str | None = None,
    ) -> Path:
        """Checks out exact commit SHA into a local working directory and returns its path."""
        ...


class GitRepositoryCheckoutManager:
    """Manages local git checkouts ensuring exact commit alignment and read-only isolation."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base_dir = Path(base_dir or settings.repo_checkout_base_dir)

    def checkout_commit(
        self,
        repo_owner: str,
        repo_name: str,
        commit_sha: str,
        clone_url: str | None = None,
    ) -> Path:
        """Checks out exact commit SHA and returns root directory."""
        if not repo_owner or not REPO_IDENTIFIER_PATTERN.match(repo_owner):
            raise RepositoryCheckoutError(f"Invalid repository owner: {repo_owner!r}")

        if not repo_name or not REPO_IDENTIFIER_PATTERN.match(repo_name):
            raise RepositoryCheckoutError(f"Invalid repository name: {repo_name!r}")

        if not commit_sha or not COMMIT_SHA_PATTERN.match(commit_sha):
            raise InvalidCommitError(
                f"Invalid commit SHA format: {commit_sha!r}. Must be 7-40 hexadecimal characters."
            )

        repo_dir = self.base_dir / f"{repo_owner}_{repo_name}"

        logger.info(
            "repository_checkout_started",
            repo=f"{repo_owner}/{repo_name}",
            commit_sha=commit_sha,
            target_dir=str(repo_dir),
        )

        try:
            if not repo_dir.exists():
                if not clone_url:
                    token_part = f"{settings.github_token}@" if settings.github_token else ""
                    clone_url = f"https://{token_part}github.com/{repo_owner}/{repo_name}.git"

                os.makedirs(repo_dir.parent, exist_ok=True)
                logger.info("cloning_repository", repo=f"{repo_owner}/{repo_name}")
                repo = git.Repo.clone_from(clone_url, repo_dir)
            else:
                repo = git.Repo(repo_dir)

            # Fetch and checkout exact SHA in detached HEAD
            logger.info("checking_out_exact_commit", commit_sha=commit_sha)
            repo.git.checkout(commit_sha, force=True)

            resolved_head = str(repo.head.commit.hexsha)
            if not resolved_head.lower().startswith(commit_sha.lower()):
                raise InvalidCommitError(
                    f"Resolved HEAD {resolved_head} does not match requested commit {commit_sha}"
                )

            logger.info(
                "repository_checkout_completed",
                repo=f"{repo_owner}/{repo_name}",
                commit_sha=commit_sha,
                resolved_head=resolved_head,
            )
            return repo_dir

        except InvalidCommitError:
            raise
        except git.GitCommandError as err:
            safe_err = _sanitize_git_message(str(err))
            logger.error(
                "repository_checkout_failed",
                repo=f"{repo_owner}/{repo_name}",
                commit_sha=commit_sha,
                error=safe_err,
            )
            raise RepositoryCheckoutError(
                f"Failed to checkout commit {commit_sha} in {repo_owner}/{repo_name}: {safe_err}"
            ) from None
        except Exception as err:
            safe_err = _sanitize_git_message(str(err))
            logger.error("repository_error", error=safe_err)
            raise RepositoryCheckoutError(
                f"Unexpected repository checkout error: {safe_err}"
            ) from None
