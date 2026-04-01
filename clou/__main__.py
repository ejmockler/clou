"""Allow ``python -m clou`` to launch the TUI."""

import sys


def _run_init() -> None:
    """Run clou init from the CLI, before project discovery."""
    import asyncio
    from pathlib import Path

    from clou.tools import clou_init

    project_name = sys.argv[2] if len(sys.argv) > 2 else Path.cwd().name
    project_dir = Path.cwd()
    result = asyncio.run(clou_init(project_dir, project_name, ""))
    print(result)


def _parse_resume_flag() -> str | None:
    """Parse --continue and --resume flags from sys.argv.

    --continue: resume the most recent session.
    --resume SESSION_ID: resume a specific session.

    Returns a session ID or None.
    """
    args = sys.argv[1:]
    if "--continue" in args:
        from clou.project import resolve_project_dir_or_exit
        from clou.session import latest_session_id

        project_dir = resolve_project_dir_or_exit()
        sid = latest_session_id(project_dir)
        if sid is None:
            print("No previous session found.")
            sys.exit(1)
        return sid

    for i, arg in enumerate(args):
        if arg == "--resume" and i + 1 < len(args):
            return args[i + 1]

    return None


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        _run_init()
        return

    if len(sys.argv) > 1 and sys.argv[1] == "auth":
        from clou.auth import run_auth_command

        run_auth_command()
        return

    from pathlib import Path

    from clou.project import resolve_project_dir_or_exit
    from clou.ui.app import ClouApp

    resume_id = _parse_resume_flag()

    work_dir = Path.cwd()
    project_dir = resolve_project_dir_or_exit()
    app = ClouApp(
        project_dir=project_dir,
        work_dir=work_dir,
        resume_session_id=resume_id,
    )
    app.run()


if __name__ == "__main__":
    main()
