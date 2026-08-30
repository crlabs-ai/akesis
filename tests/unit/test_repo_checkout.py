import pytest

from src.packages.sdk.repo_checkout import (
    GitRepositoryCheckoutManager,
    InvalidCommitError,
)


def test_invalid_commit_sha_rejected() -> None:
    manager = GitRepositoryCheckoutManager(base_dir="/tmp/test_repos")
    with pytest.raises(InvalidCommitError) as exc:
        manager.checkout_commit("crlabs-ai", "akesis", "invalid; rm -rf /")
    assert "Invalid commit SHA format" in str(exc.value)

    with pytest.raises(InvalidCommitError):
        manager.checkout_commit("crlabs-ai", "akesis", "")
