from pathlib import Path

import git
import pytest

from src.packages.sdk.repo_checkout import (
    GitRepositoryCheckoutManager,
    InvalidCommitError,
    RepositoryCheckoutError,
    _sanitize_git_message,
)
from src.packages.shared.config import settings


def test_invalid_commit_sha_rejected() -> None:
    manager = GitRepositoryCheckoutManager(base_dir="/tmp/test_repos")
    with pytest.raises(InvalidCommitError) as exc:
        manager.checkout_commit("crlabs-ai", "akesis", "invalid; rm -rf /")
    assert "Invalid commit SHA format" in str(exc.value)

    with pytest.raises(InvalidCommitError):
        manager.checkout_commit("crlabs-ai", "akesis", "")

    with pytest.raises(InvalidCommitError):
        manager.checkout_commit("crlabs-ai", "akesis", "12345")  # Too short


def test_invalid_repo_owner_or_name_rejected() -> None:
    manager = GitRepositoryCheckoutManager(base_dir="/tmp/test_repos")
    with pytest.raises(RepositoryCheckoutError) as exc:
        manager.checkout_commit("../traversal", "repo", "a" * 40)
    assert "Invalid repository owner" in str(exc.value)

    with pytest.raises(RepositoryCheckoutError) as exc:
        manager.checkout_commit("owner", "repo;rm", "a" * 40)
    assert "Invalid repository name" in str(exc.value)


def test_sanitize_git_message() -> None:
    secret_token = "ghp_SECRET_TOKEN_12345"
    orig_token = settings.github_token
    try:
        settings.github_token = secret_token
        msg = f"fatal: clone failed https://{secret_token}@github.com/crlabs-ai/repo.git"
        sanitized = _sanitize_git_message(msg)
        assert secret_token not in sanitized
        assert "https://***@github.com" in sanitized
    finally:
        settings.github_token = orig_token


def test_repo_checkout_exact_commit_detached_head(tmp_path: Path) -> None:
    # 1. Setup local bare/remote repository
    remote_dir = tmp_path / "remote_repo"
    remote_dir.mkdir()
    r_repo = git.Repo.init(remote_dir)

    test_file = remote_dir / "app.py"
    test_file.write_text("print('v1')\n", encoding="utf-8")
    r_repo.index.add(["app.py"])
    c1 = r_repo.index.commit("Initial commit")
    sha1 = c1.hexsha

    test_file.write_text("print('v2')\n", encoding="utf-8")
    r_repo.index.add(["app.py"])
    c2 = r_repo.index.commit("Second commit")
    sha2 = c2.hexsha

    # 2. Checkout c1 via manager
    base_checkout_dir = tmp_path / "checkouts"
    manager = GitRepositoryCheckoutManager(base_dir=base_checkout_dir)
    checked_out = manager.checkout_commit(
        repo_owner="test_owner",
        repo_name="test_repo",
        commit_sha=sha1,
        clone_url=str(remote_dir),
    )

    assert checked_out.exists()
    repo_obj = git.Repo(checked_out)
    assert repo_obj.head.is_detached
    assert repo_obj.head.commit.hexsha == sha1
    assert (checked_out / "app.py").read_text() == "print('v1')\n"

    # 3. Checkout c2 into existing clone
    checked_out_2 = manager.checkout_commit(
        repo_owner="test_owner",
        repo_name="test_repo",
        commit_sha=sha2,
        clone_url=str(remote_dir),
    )
    assert repo_obj.head.commit.hexsha == sha2
    assert (checked_out_2 / "app.py").read_text() == "print('v2')\n"


def test_repo_checkout_missing_repo_error(tmp_path: Path) -> None:
    manager = GitRepositoryCheckoutManager(base_dir=tmp_path / "checkouts")
    with pytest.raises(RepositoryCheckoutError) as exc:
        manager.checkout_commit(
            repo_owner="nonexistent",
            repo_name="nonexistent",
            commit_sha="a" * 40,
            clone_url="/path/to/nonexistent/repo.git",
        )
    assert "Failed to checkout" in str(exc.value) or "Unexpected repository" in str(exc.value)


def test_repo_checkout_fetches_missing_remote_commit_successfully(tmp_path: Path) -> None:
    # 1. Setup remote repo with C1
    remote_dir = tmp_path / "remote_repo"
    remote_dir.mkdir()
    r_repo = git.Repo.init(remote_dir)

    test_file = remote_dir / "app.py"
    test_file.write_text("print('v1')\n", encoding="utf-8")
    r_repo.index.add(["app.py"])
    c1 = r_repo.index.commit("Commit 1")
    sha1 = c1.hexsha

    # 2. Clone initially with C1
    base_checkout_dir = tmp_path / "checkouts"
    manager = GitRepositoryCheckoutManager(base_dir=base_checkout_dir)
    checked_out = manager.checkout_commit(
        repo_owner="test_owner",
        repo_name="test_repo",
        commit_sha=sha1,
        clone_url=str(remote_dir),
    )
    assert (checked_out / "app.py").read_text() == "print('v1')\n"

    # 3. Add C2 directly on remote repo (local clone doesn't know about C2 yet)
    test_file.write_text("print('v2')\n", encoding="utf-8")
    r_repo.index.add(["app.py"])
    c2 = r_repo.index.commit("Commit 2 (new)")
    sha2 = c2.hexsha

    # Verify local clone does not have C2 yet in its object database
    local_repo = git.Repo(checked_out)
    from src.packages.sdk.repo_checkout import _has_commit

    assert _has_commit(local_repo, sha2) is False

    # 4. Checkout C2 via manager - should fetch from remote and succeed
    checked_out_c2 = manager.checkout_commit(
        repo_owner="test_owner",
        repo_name="test_repo",
        commit_sha=sha2,
        clone_url=str(remote_dir),
    )
    assert checked_out_c2 == checked_out
    assert local_repo.head.is_detached is True
    assert local_repo.head.commit.hexsha == sha2
    assert (checked_out / "app.py").read_text() == "print('v2')\n"


def test_repo_checkout_unresolvable_sha_fails_closed(tmp_path: Path) -> None:
    remote_dir = tmp_path / "remote_repo"
    remote_dir.mkdir()
    r_repo = git.Repo.init(remote_dir)

    test_file = remote_dir / "app.py"
    test_file.write_text("print('v1')\n", encoding="utf-8")
    r_repo.index.add(["app.py"])
    r_repo.index.commit("Commit 1")

    base_checkout_dir = tmp_path / "checkouts"
    manager = GitRepositoryCheckoutManager(base_dir=base_checkout_dir)

    # Non-existent SHA
    fake_sha = "f" * 40
    with pytest.raises(InvalidCommitError) as exc:
        manager.checkout_commit(
            repo_owner="test_owner",
            repo_name="test_repo",
            commit_sha=fake_sha,
            clone_url=str(remote_dir),
        )
    assert "does not exist in repository" in str(exc.value) or "Failed to checkout" in str(
        exc.value
    )
