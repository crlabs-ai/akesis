"""Regression: the mutation workspace copy must never dereference a shared-checkout link.

Before Phase 1.6, `shutil.copytree` followed a planted symlink and the host file's content would
have been committed into a pull request.
"""

import os
from pathlib import Path

import pytest

from src.packages.shared.mutation_service import (
    WorkingTreeMismatchError,
    copy_checkout_without_links,
)


def test_host_symlink_is_refused_and_no_host_content_is_copied(tmp_path: Path) -> None:
    host_file = tmp_path / "host-secret.txt"
    host_file.write_text("HOST-KEY-MARKER")
    checkout = tmp_path / "checkout"
    (checkout / "src").mkdir(parents=True)
    (checkout / "src" / "ok.py").write_text("x = 1\n")
    os.symlink(host_file, checkout / "src" / "leak.txt")
    destination = tmp_path / "workspace"
    destination.mkdir()

    with pytest.raises(WorkingTreeMismatchError):
        copy_checkout_without_links(checkout, destination)
    for item in destination.rglob("*"):
        if item.is_file() and not item.is_symlink():
            assert b"HOST-KEY-MARKER" not in item.read_bytes()


def test_directory_symlink_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "key.txt").write_text("HOST-KEY-MARKER")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    os.symlink(outside, checkout / "linked-dir")
    destination = tmp_path / "workspace"
    destination.mkdir()

    with pytest.raises(WorkingTreeMismatchError):
        copy_checkout_without_links(checkout, destination)
    assert not (destination / "linked-dir" / "key.txt").exists()


def test_ordinary_tree_is_copied_faithfully(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    (checkout / "pkg" / "sub").mkdir(parents=True)
    (checkout / "pkg" / "sub" / "mod.py").write_text("y = 2\n")
    (checkout / "README.md").write_text("hello\n")
    destination = tmp_path / "workspace"
    destination.mkdir()

    copy_checkout_without_links(checkout, destination)
    assert (destination / "pkg" / "sub" / "mod.py").read_text() == "y = 2\n"
    assert (destination / "README.md").read_text() == "hello\n"
