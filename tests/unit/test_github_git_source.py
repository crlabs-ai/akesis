"""GitHub Git Data acquisition: identity, SHA, completeness, integrity and entry policy (ADR-0011).

Each adversarial response is served by tests/support/fake_github.py through httpx.MockTransport.
No real GitHub, Slack or database is contacted.
"""

import base64

import pytest

from src.packages.sdk.github_git_source import (
    GitHubGitSource,
    SourceAcquisitionError,
    tree_object_sha,
)
from tests.support.fake_github import FakeGitHub, blob_id

REPO_ID = 1001
FULL = "crlabs-ai/akesis"


def _source(fake: FakeGitHub, **limits: int) -> GitHubGitSource:
    options = {"max_files": 100, "max_file_bytes": 10_000, "max_total_bytes": 100_000, **limits}
    return GitHubGitSource(fake.client(), "ghs_synthetic", **options)


async def _acquire(fake: FakeGitHub, **limits: int) -> list[str]:
    source = _source(fake, **limits)
    commit = await source.resolve_commit(REPO_ID, FULL, fake.commit_sha)
    files = await source.walk(REPO_ID, commit.tree_sha)
    for entry in files:
        await source.read_blob(REPO_ID, entry)
    return [entry.path for entry in files]


def _base(**overrides: object) -> FakeGitHub:
    files = {
        "src/app.py": ("100644", b"print('ok')\n"),
        ".github/workflows/ci.yml": ("100644", b"name: CI\n"),
        "scripts/run.sh": ("100755", b"#!/bin/sh\necho ok\n"),
    }
    options: dict[str, object] = {"repo_id": REPO_ID, "full_name": FULL, "files": files}
    options.update(overrides)
    return FakeGitHub(**options)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_valid_snapshot_acquires_every_file_and_keeps_github_directories() -> None:
    paths = await _acquire(_base())
    assert paths == [".github/workflows/ci.yml", "scripts/run.sh", "src/app.py"]


@pytest.mark.asyncio
async def test_executable_mode_is_reported_only_for_100755() -> None:
    fake = _base()
    source = _source(fake)
    commit = await source.resolve_commit(REPO_ID, FULL, fake.commit_sha)
    modes = {entry.path: entry.mode for entry in await source.walk(REPO_ID, commit.tree_sha)}
    assert modes["scripts/run.sh"] == "100755"
    assert modes["src/app.py"] == "100644"


@pytest.mark.asyncio
async def test_repository_id_must_match_expected_name() -> None:
    with pytest.raises(SourceAcquisitionError) as error:
        await _source(_base(repo_id_mismatch=True)).resolve_commit(REPO_ID, FULL, "a" * 40)
    assert error.value.code == "identity"


@pytest.mark.asyncio
async def test_commit_belonging_to_another_repository_is_rejected() -> None:
    with pytest.raises(SourceAcquisitionError) as error:
        await _source(_base(commit_repo_mismatch=True)).resolve_commit(REPO_ID, FULL, "a" * 40)
    assert error.value.code == "identity"


@pytest.mark.asyncio
async def test_wrong_commit_sha_is_rejected() -> None:
    with pytest.raises(SourceAcquisitionError) as error:
        await _source(_base(wrong_commit_sha=True)).resolve_commit(REPO_ID, FULL, "a" * 40)
    assert error.value.code == "sha"


@pytest.mark.asyncio
async def test_missing_source_commit_fails_closed() -> None:
    with pytest.raises(SourceAcquisitionError) as error:
        await _source(_base(missing_commit=True)).resolve_commit(REPO_ID, FULL, "a" * 40)
    assert error.value.code == "source_missing"


@pytest.mark.asyncio
async def test_non_full_sha_is_rejected_before_any_request() -> None:
    fake = _base()
    with pytest.raises(SourceAcquisitionError) as error:
        await _source(fake).resolve_commit(REPO_ID, FULL, "abc123")
    assert error.value.code == "invalid_sha"
    assert fake.requests == []


@pytest.mark.asyncio
async def test_truncated_tree_is_never_treated_as_complete() -> None:
    fake = _base(truncate_tree=True)
    with pytest.raises(SourceAcquisitionError) as error:
        await _acquire(fake)
    assert error.value.code == "truncated"


@pytest.mark.asyncio
async def test_tampered_tree_listing_fails_object_id_verification() -> None:
    with pytest.raises(SourceAcquisitionError) as error:
        await _acquire(_base(tamper_tree_listing=True))
    assert error.value.code == "integrity"


@pytest.mark.asyncio
async def test_tampered_blob_content_fails_object_id_verification() -> None:
    fake = _base()
    target = blob_id(b"print('ok')\n")
    fake.tamper_blob_content[target] = b"print('evil')\n"
    with pytest.raises(SourceAcquisitionError) as error:
        await _acquire(fake)
    assert error.value.code == "integrity"


@pytest.mark.asyncio
async def test_oversized_blob_is_rejected() -> None:
    with pytest.raises(SourceAcquisitionError) as error:
        await _acquire(_base(), max_file_bytes=4)
    assert error.value.code == "limit"


@pytest.mark.asyncio
async def test_excessive_file_count_is_rejected_during_walk() -> None:
    with pytest.raises(SourceAcquisitionError) as error:
        await _acquire(_base(), max_files=2)
    assert error.value.code == "limit"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "files",
    [
        {"README.md": ("120000", b"/etc/passwd")},
        {"docs/link": ("120000", b"/etc/hostname")},
        {"a/b/c/link": ("120000", b"../../../../../etc/passwd")},
        {"dangling": ("120000", b"does-not-exist")},
        {"loop": ("120000", b"loop")},
    ],
    ids=["external-file", "external-directory", "nested", "dangling", "loop"],
)
async def test_symlinks_are_rejected_and_their_targets_never_fetched(
    files: dict[str, tuple[str, bytes]],
) -> None:
    fake = _base(files={**_base().files, **files})
    with pytest.raises(SourceAcquisitionError) as error:
        await _acquire(fake)
    assert error.value.code == "symlink"
    # The link is refused at tree level: its target content is never requested or read.
    assert not any("/git/blobs/" in path for path in fake.requests)


@pytest.mark.asyncio
async def test_submodule_gitlink_is_rejected() -> None:
    fake = _base(files={**_base().files, "vendor/lib": ("160000", b"")})
    with pytest.raises(SourceAcquisitionError) as error:
        await _acquire(fake)
    assert error.value.code == "gitlink"


@pytest.mark.asyncio
async def test_unsupported_mode_is_rejected() -> None:
    fake = _base(files={**_base().files, "weird.txt": ("100664", b"x")})
    with pytest.raises(SourceAcquisitionError) as error:
        await _acquire(fake)
    assert error.value.code == "mode"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [".git", ".GIT", "../escape.py", "..", "/absolute", "back\\slash"],
)
async def test_unsafe_path_components_are_rejected(name: str) -> None:
    if name.startswith("/"):
        # An absolute path is a nested empty component in the tree model.
        fake = _base(files={**_base().files, "": ("100644", b"x")})
    else:
        fake = _base(files={**_base().files, name: ("100644", b"x")})
    with pytest.raises(SourceAcquisitionError) as error:
        await _acquire(fake)
    assert error.value.code in {"invalid_path", "git_metadata"}


@pytest.mark.asyncio
async def test_case_colliding_paths_are_rejected() -> None:
    fake = _base(files={**_base().files, "SRC/APP.PY": ("100644", b"other\n")})
    with pytest.raises(SourceAcquisitionError) as error:
        await _acquire(fake)
    assert error.value.code == "collision"


@pytest.mark.asyncio
async def test_lfs_pointer_is_refused_rather_than_validated() -> None:
    pointer = b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 12\n"
    fake = _base(files={**_base().files, "assets/model.bin": ("100644", pointer)})
    with pytest.raises(SourceAcquisitionError) as error:
        await _acquire(fake)
    assert error.value.code == "lfs"


@pytest.mark.asyncio
async def test_request_budget_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.packages.sdk.github_git_source as module

    monkeypatch.setattr(module, "_MAX_API_REQUESTS", 2)
    with pytest.raises(SourceAcquisitionError) as error:
        await _acquire(_base())
    assert error.value.code == "budget"


def test_tree_object_id_matches_git_reference_for_a_known_tree() -> None:
    """Empty tree has the well-known Git object ID; the recomputation must reproduce it."""
    assert tree_object_sha([]) == "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def test_blob_object_id_matches_git_reference() -> None:
    """`git hash-object` of 'hello\\n' is ce013625030ba8dba906f756967f9e9ca394464a."""
    assert blob_id(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_base64_fixture_round_trip_is_stable() -> None:
    assert base64.b64decode(base64.b64encode(b"x")) == b"x"
