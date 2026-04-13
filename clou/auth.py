"""Auth status checking and Claude CLI discovery for ``clou auth``."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AuthStatus:
    """Result of checking Claude CLI authentication."""

    cli_found: bool
    cli_path: str | None
    logged_in: bool
    auth_method: str | None
    email: str | None
    subscription_type: str | None


def find_claude_cli() -> str | None:
    """Locate the ``claude`` CLI binary.

    Mirrors the SDK's discovery logic without depending on SDK internals.
    """
    if cli := shutil.which("claude"):
        return cli

    locations = [
        Path.home() / ".npm-global/bin/claude",
        Path("/usr/local/bin/claude"),
        Path.home() / ".local/bin/claude",
        Path.home() / "node_modules/.bin/claude",
        Path.home() / ".yarn/bin/claude",
        Path.home() / ".claude/local/claude",
    ]
    for path in locations:
        if path.is_file():
            return str(path)

    return None


def check_auth_status(cli_path: str) -> AuthStatus:
    """Run ``claude auth status`` and parse the JSON response."""
    try:
        result = subprocess.run(
            [cli_path, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return AuthStatus(
                cli_found=True,
                cli_path=cli_path,
                logged_in=False,
                auth_method=None,
                email=None,
                subscription_type=None,
            )
        status = json.loads(result.stdout)
        return AuthStatus(
            cli_found=True,
            cli_path=cli_path,
            logged_in=bool(status.get("loggedIn", False)),
            auth_method=status.get("authMethod"),
            email=status.get("email"),
            subscription_type=status.get("subscriptionType"),
        )
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return AuthStatus(
            cli_found=True,
            cli_path=cli_path,
            logged_in=False,
            auth_method=None,
            email=None,
            subscription_type=None,
        )


def run_login(cli_path: str) -> bool:
    """Run ``claude auth login`` interactively.

    Returns True if login succeeded.
    """
    try:
        result = subprocess.run([cli_path, "auth", "login"])
        if result.returncode != 0:
            return False
        status = check_auth_status(cli_path)
        return status.logged_in
    except (OSError, subprocess.SubprocessError):
        return False


def ensure_authenticated() -> AuthStatus:
    """Check auth at startup.  Returns status or exits.

    Called before the TUI launches. If not authenticated, offers
    interactive login. Exits with code 1 if auth cannot be established.
    """
    cli_path = find_claude_cli()

    if cli_path is None:
        print("Claude CLI not found.\n")
        print("Install it with:")
        print("  npm install -g @anthropic-ai/claude-code\n")
        sys.exit(1)

    status = check_auth_status(cli_path)
    if status.logged_in:
        return status

    print("Not authenticated.\n")
    try:
        answer = input("Log in now? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)

    if answer in ("", "y", "yes"):
        if run_login(cli_path):
            return check_auth_status(cli_path)
        else:
            print("\nLogin failed.")
            sys.exit(1)
    else:
        print("\nRun `clou auth login` to authenticate.")
        sys.exit(1)


def run_auth_command() -> None:
    """Entry point for ``clou auth`` — status, login, or logout."""
    args = sys.argv[2:]  # after "clou auth"

    cli_path = find_claude_cli()
    if cli_path is None:
        print("Claude CLI not found.\n")
        print("Install it with:")
        print("  npm install -g @anthropic-ai/claude-code\n")
        sys.exit(1)

    if args and args[0] == "login":
        if run_login(cli_path):
            status = check_auth_status(cli_path)
            print(f"\nAuthenticated as {status.email}")
        else:
            print("Login failed.")
            sys.exit(1)
        return

    if args and args[0] == "logout":
        try:
            subprocess.run([cli_path, "auth", "logout"])
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"Logout failed: {exc}")
            sys.exit(1)
        return

    # Default: show status
    status = check_auth_status(cli_path)

    if not status.logged_in:
        print(f"Claude CLI: {cli_path}")
        print("Status: not logged in\n")
        print("Log in with:")
        print("  clou auth login")
        sys.exit(1)

    print(f"Claude CLI: {cli_path}")
    print("Status: authenticated")
    if status.email:
        print(f"Email: {status.email}")
    if status.auth_method:
        print(f"Auth method: {status.auth_method}")
    if status.subscription_type:
        print(f"Subscription: {status.subscription_type}")
