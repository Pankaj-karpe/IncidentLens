"""
User management from the command line. This is the ONLY way to create accounts
(no public sign-up endpoint = nothing for an attacker to abuse).

  python -m app.cli create-user alice --role sre
  python -m app.cli list-users
  python -m app.cli set-role alice devops
  python -m app.cli set-password alice
  python -m app.cli deactivate alice        (and: activate alice)

Passwords are typed interactively (getpass), never passed as arguments:
arguments end up in shell history and process lists.
In Docker:      docker compose exec api python -m app.cli create-user alice --role sre
In Kubernetes:  kubectl exec -it deploy/incidentlens-api -n incidentlens-dev -- python -m app.cli ...
"""
import argparse
import getpass
import re
import sys

from sqlalchemy import select

from . import models, security
from .database import SessionLocal

_USERNAME = re.compile(r"^[a-z0-9._-]{3,64}$")
MIN_PASSWORD = 12


def _ask_password() -> str:
    while True:
        password = getpass.getpass(f"Password (min {MIN_PASSWORD} characters): ")
        if len(password) < MIN_PASSWORD:
            print(f"Too short: use atleast {MIN_PASSWORD} characters.")
            continue
        if password != getpass.getpass("Repeat password: "):
            print("Passwords do not match.")
            continue
        return password


def _get_user(db, username: str) -> models.User:
    user = db.scalar(select(models.User).where(models.User.username == username.lower()))
    if user is None:
        sys.exit(f"No such user: {username}")
    return user


def cmd_create(args) -> None:
    username = args.username.lower()
    if not _USERNAME.match(username):
        sys.exit("Username must be 3-64 characters: lowercase letters, digits, . _ -")
    with SessionLocal() as db:
        if db.scalar(select(models.User.id).where(models.User.username == username)):
            sys.exit(f"User already exists: {username}")
        db.add(models.User(username=username, password_hash=security.hash_password(_ask_password()),
                           role=args.role))
        db.commit()
    print(f"created {username} with role {args.role}")


def cmd_list(_args) -> None:
    with SessionLocal() as db:
        users = db.scalars(select(models.User).order_by(models.User.id)).all()
    print(f"{'id':<5}{'username':<24}{'role':<12}active")
    for u in users:
        print(f"{u.id:<5}{u.username:<24}{u.role:<12}{u.is_active}")


def cmd_set_role(args) -> None:
    with SessionLocal() as db:
        _get_user(db, args.username).role = args.role
        db.commit()
    print(f"{args.username} is now {args.role}")


def cmd_set_password(args) -> None:
    with SessionLocal() as db:
        user = _get_user(db, args.username)
        user.password_hash = security.hash_password(_ask_password())
        db.commit()
    print(f"password changed for {args.username} (existing tokens stay valid until they expire)")


def _set_active(username: str, active: bool) -> None:
    with SessionLocal() as db:
        _get_user(db, username).is_active = active
        db.commit()
    print(f"{username} {'activated' if active else 'deactivated (takes effect immediately)'}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-user")
    p.add_argument("username")
    p.add_argument("--role", choices=security.ROLES, default="viewer")
    p.set_defaults(func=cmd_create)

    sub.add_parser("list-users").set_defaults(func=cmd_list)

    p = sub.add_parser("set-role")
    p.add_argument("username")
    p.add_argument("role", choices=security.ROLES)
    p.set_defaults(func=cmd_set_role)

    p = sub.add_parser("set-password")
    p.add_argument("username")
    p.set_defaults(func=cmd_set_password)

    for name, active in (("activate", True), ("deactivate", False)):
        p = sub.add_parser(name)
        p.add_argument("username")
        p.set_defaults(func=lambda a, active=active: _set_active(a.username, active))

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()