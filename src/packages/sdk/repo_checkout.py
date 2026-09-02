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


def _has_commit(repo: git.Repo, commit_sha: str) -> bool:
    """Checks whether the commit object exists locally in the Git object database."""
    try:
        repo.commit(commit_sha)
        return True
    except (git.BadName, git.GitCommandError, ValueError):
        return False


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
            token_part = f"{settings.github_token}@" if settings.github_token else ""
            default_remote_url = f"https://{token_part}github.com/{repo_owner}/{repo_name}.git"
            effective_remote_url = clone_url or default_remote_url

            if not repo_dir.exists():
                os.makedirs(repo_dir.parent, exist_ok=True)
                logger.info("cloning_repository", repo=f"{repo_owner}/{repo_name}")
                repo = git.Repo.clone_from(effective_remote_url, repo_dir)
            else:
                repo = git.Repo(repo_dir)

            # Check if requested commit is available locally
            if not _has_commit(repo, commit_sha):
                logger.info(
                    "fetching_missing_commit_from_remote",
                    repo=f"{repo_owner}/{repo_name}",
                    commit_sha=commit_sha,
                )
                try:
                    # Attempt fetching the specific commit first
                    repo.git.fetch(effective_remote_url, commit_sha)
                except git.GitCommandError:
                    # Fallback to fetching remote refs
                    repo.git.fetch(effective_remote_url)

            # If commit is still missing after fetch, fail closed
            if not _has_commit(repo, commit_sha):
                raise InvalidCommitError(
                    f"Commit {commit_sha} does not exist in repository {repo_owner}/{repo_name}."
                )

            # Checkout exact SHA in detached HEAD
            logger.info("checking_out_exact_commit", commit_sha=commit_sha)
            repo.git.checkout(commit_sha, force=True)

            resolved_head = str(repo.head.commit.hexsha)
            if not resolved_head.lower().startswith(commit_sha.lower()):
                raise InvalidCommitError(
                    f"Resolved HEAD {resolved_head} does not match requested commit {commit_sha}"
                )

            if not repo.head.is_detached:
                raise RepositoryCheckoutError(
                    f"Repository HEAD is not detached after checking out {commit_sha}"
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
