"""Workspace roles and the rules for who may do what (ADR-0012). Pure functions, no I/O.

Roles, most to least privileged: owner > admin > maintainer > approver > viewer.

- Every member can read the workspace's incidents, pipelines and deliveries.
- approver and above can approve remediation (in Slack, once an owner/admin maps their Slack ID).
- admin and above manage members, invitations, Slack mappings and GitHub installations.
- Nobody changes their own role. An actor grants or manages only roles strictly below their own;
  owners are the exception and may manage owners. A workspace always keeps at least one owner.
"""

from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MAINTAINER = "maintainer"
    APPROVER = "approver"
    VIEWER = "viewer"


_RANK = {Role.OWNER: 5, Role.ADMIN: 4, Role.MAINTAINER: 3, Role.APPROVER: 2, Role.VIEWER: 1}


def rank(role: Role) -> int:
    return _RANK[role]


def at_least(role: Role, minimum: Role) -> bool:
    return _RANK[role] >= _RANK[minimum]


def can_manage_members(actor: Role) -> bool:
    return at_least(actor, Role.ADMIN)


def can_assign(actor: Role, target_role: Role) -> bool:
    """May `actor` grant `target_role` (in an invitation or a role change)?"""
    if not can_manage_members(actor):
        return False
    return actor == Role.OWNER or _RANK[target_role] < _RANK[actor]


def can_manage(actor: Role, subject: Role) -> bool:
    """May `actor` change or remove a member who currently holds `subject`?"""
    if not can_manage_members(actor):
        return False
    return actor == Role.OWNER or _RANK[subject] < _RANK[actor]


def can_approve(role: Role) -> bool:
    return at_least(role, Role.APPROVER)
