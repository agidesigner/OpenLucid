"""OpenLucid management CLI — direct-DB ops commands.

Run via: ``docker compose exec app python -m app.cli <subcommand>``

Use case: ops actions that should bypass the HTTP API and email layer.
The most common one is reset-password — the email-driven flow at
``/api/v1/auth/forgot-password`` is unusable when MAIL_FROM /
RESEND_API_KEY / SMTP_HOST are all empty (the URL just lands in
docker logs).

This is intentionally separate from ``tools/openlucid`` (the HTTP CLI):
that one talks to the running app over REST and needs login state.
For "auth is broken, recover the admin password" we want a path that
sidesteps HTTP / email / login entirely — direct DB access via the
async session factory the app already uses.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from app.application import auth_service
from app.database import async_session_factory


async def _reset_password(email: str, new_password: str) -> int:
    async with async_session_factory() as session:
        user = await auth_service.get_user_by_email(session, email)
        if not user:
            print(f"error: no user found with email {email}", file=sys.stderr)
            return 2
        try:
            await auth_service.update_password(session, user, new_password)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 3
        print(f"ok: password updated for {user.email}")
        return 0


async def _reset_email(email: str, new_email: str, email_confirm: str) -> int:
    if new_email != email_confirm:
        print("error: --new-email and --email-confirm do not match", file=sys.stderr)
        return 1
    async with async_session_factory() as session:
        user = await auth_service.get_user_by_email(session, email)
        if not user:
            print(f"error: no user found with email {email}", file=sys.stderr)
            return 2
        # Unique-constraint pre-check — clearer error than the raw IntegrityError
        existing = await auth_service.get_user_by_email(session, new_email)
        if existing and existing.id != user.id:
            print(f"error: email already in use: {new_email}", file=sys.stderr)
            return 3
        try:
            await auth_service.update_email(session, user, new_email)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 4
        print(f"ok: email changed from {email} to {user.email}")
        return 0


async def _create_admin(email: str, password: str) -> int:
    async with async_session_factory() as session:
        existing = await auth_service.get_user_by_email(session, email)
        if existing:
            print(f"error: user already exists: {email}", file=sys.stderr)
            return 2
        try:
            user = await auth_service.create_admin(session, email, password)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 3
        print(f"ok: admin created: {user.email}")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="OpenLucid management CLI (direct-DB ops commands).",
        epilog=(
            "Routine data operations (list offers, write a script, query KB) "
            "go through the host-side 'openlucid' CLI over HTTP — install it "
            "with 'bash tools/install.sh' and run 'openlucid --help' to see "
            "those commands. This in-container CLI is only for recovery cases "
            "where the HTTP path can't be used (auth broken, email not "
            "configured, no admin user yet). See SELF_HOSTING.md."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    rp = sub.add_parser(
        "reset-password",
        help="Set a user's password directly. Bypasses email + HTTP auth.",
        description=(
            "Reset a user's password directly in the database. "
            "Use when email is not configured (no Resend / SMTP) so the "
            "/forgot-password flow has no way to deliver the reset link."
        ),
    )
    rp.add_argument("--email", required=True)
    rp.add_argument(
        "--new-password",
        required=True,
        help="Plaintext password. Must be 8+ chars with at least one letter and one digit.",
    )

    re_ = sub.add_parser(
        "reset-email",
        help="Change a user's email address.",
        description=(
            "Change the email on an existing account. Useful when the "
            "original email is no longer accessible and the account "
            "owner needs to reroute future password-reset / login mail."
        ),
    )
    re_.add_argument("--email", required=True, help="Current email on the account.")
    re_.add_argument("--new-email", required=True)
    re_.add_argument(
        "--email-confirm",
        required=True,
        help="Must match --new-email. Typo guard — same shape as Dify's reset-email.",
    )

    ca = sub.add_parser(
        "create-admin",
        help="Create an account directly. Headless alternative to /install.html.",
        description=(
            "Create a new user account from the command line. The same path "
            "the install wizard uses; suitable for IaC / CI provisioning "
            "where the web installer can't be reached."
        ),
    )
    ca.add_argument("--email", required=True)
    ca.add_argument(
        "--password",
        required=True,
        help="Plaintext password. Must be 8+ chars with at least one letter and one digit.",
    )

    args = parser.parse_args()
    if args.cmd == "reset-password":
        return asyncio.run(_reset_password(args.email, args.new_password))
    if args.cmd == "reset-email":
        return asyncio.run(_reset_email(args.email, args.new_email, args.email_confirm))
    if args.cmd == "create-admin":
        return asyncio.run(_create_admin(args.email, args.password))
    return 1


if __name__ == "__main__":
    sys.exit(main())
