#!/usr/bin/env python
"""Create workspaces and users.

There is deliberately no self-service signup endpoint: this build has no email
verification, so an open registration route would let anyone create a tenant.
Accounts are provisioned deliberately.

    python scripts/manage_users.py create-workspace "Acme Agency"
    python scripts/manage_users.py create-user ayush@example.com "Ayush" <workspace-id> workspace_admin
    python scripts/manage_users.py list
"""

from __future__ import annotations

import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlalchemy import select  # noqa: E402

from adproof.db import init_db, session_scope  # noqa: E402
from adproof.models import Membership, User, Workspace  # noqa: E402
from adproof.security import hash_password  # noqa: E402
from adproof.states import Role  # noqa: E402


def create_workspace(name: str) -> None:
    with session_scope() as session:
        workspace = Workspace(name=name)
        session.add(workspace)
        session.flush()
        print(f"workspace {workspace.id}  {workspace.name}")


def create_user(email: str, display_name: str, workspace_id: str, role: str) -> None:
    email = email.strip().lower()
    try:
        role_enum = Role(role)
    except ValueError:
        print(f"Unknown role {role!r}. Valid: {[r.value for r in Role]}")
        raise SystemExit(2) from None

    with session_scope() as session:
        if session.get(Workspace, workspace_id) is None:
            print(f"No workspace {workspace_id!r}.")
            raise SystemExit(2)
        if session.scalar(select(User).where(User.email == email)):
            print(f"User {email} already exists.")
            raise SystemExit(2)

        password = getpass.getpass("Password (min 12 chars): ")
        if password != getpass.getpass("Confirm: "):
            print("Passwords do not match.")
            raise SystemExit(2)
        try:
            password_hash = hash_password(password)
        except ValueError as exc:
            print(exc)
            raise SystemExit(2) from None

        user = User(
            email=email, display_name=display_name, password_hash=password_hash
        )
        session.add(user)
        session.flush()
        session.add(
            Membership(user_id=user.id, workspace_id=workspace_id, role=role_enum)
        )
        print(f"user {user.id}  {email}  {role_enum.value} in {workspace_id}")


def list_all() -> None:
    with session_scope() as session:
        print("WORKSPACES")
        for workspace in session.scalars(select(Workspace)):
            print(f"  {workspace.id}  {workspace.name}")
        print("USERS")
        for user in session.scalars(select(User)):
            roles = session.scalars(
                select(Membership).where(Membership.user_id == user.id)
            )
            detail = ", ".join(f"{m.role.value}@{m.workspace_id[:8]}" for m in roles)
            print(f"  {user.email:<32} {detail}")


def main() -> None:
    init_db()
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    command = sys.argv[1]
    if command == "create-workspace" and len(sys.argv) == 3:
        create_workspace(sys.argv[2])
    elif command == "create-user" and len(sys.argv) == 6:
        create_user(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    elif command == "list":
        list_all()
    else:
        print(__doc__)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
