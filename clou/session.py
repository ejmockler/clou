"""Session persistence — append-only JSONL transcript per session.

Each invocation of Clou creates a session identified by a UUID. Every
conversation turn is appended as a single JSON line to the session file.
On resume, the transcript is read back and used to reconstruct context.

Design principles (from research-foundations §4b):
  1. JSONL transcript is the source of truth.
  2. Separate transcript from resumable context.
  3. Persist decisions and results, not intermediate computation.
  4. Auto-persist every turn — don't wait for /exit.
  5. Session identity is per-project, per-invocation.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class SessionEntry:
    """One line in the JSONL transcript."""

    role: str  # "user", "assistant", "tool", "system"
    content: str
    timestamp: float = field(default_factory=time.time)
    meta: dict[str, object] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, line: str) -> SessionEntry:
        d = json.loads(line)
        return cls(
            role=d["role"],
            content=d["content"],
            timestamp=d.get("timestamp", 0.0),
            meta=d.get("meta", {}),
        )


@dataclass
class SessionInfo:
    """Metadata stored in the first line of a session JSONL (role='system')."""

    session_id: str
    project_dir: str
    started_at: float
    model: str = "opus"

    def to_entry(self) -> SessionEntry:
        return SessionEntry(
            role="system",
            content="session_start",
            timestamp=self.started_at,
            meta={
                "session_id": self.session_id,
                "project_dir": self.project_dir,
                "model": self.model,
            },
        )

    @classmethod
    def from_entry(cls, entry: SessionEntry) -> SessionInfo:
        return cls(
            session_id=str(entry.meta.get("session_id", "")),
            project_dir=str(entry.meta.get("project_dir", "")),
            started_at=entry.timestamp,
            model=str(entry.meta.get("model", "opus")),
        )


def sessions_dir(project_dir: Path) -> Path:
    """Return the sessions directory, creating it if needed."""
    d = project_dir / ".clou" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def session_path(project_dir: Path, session_id: str) -> Path:
    """Return the JSONL file path for a session."""
    return sessions_dir(project_dir) / f"{session_id}.jsonl"


class Session:
    """A live session that appends entries to a JSONL transcript."""

    def __init__(
        self,
        project_dir: Path,
        session_id: str | None = None,
        model: str = "opus",
    ) -> None:
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.project_dir = project_dir
        self.model = model
        self.started_at = time.time()
        self._path = session_path(project_dir, self.session_id)
        self._count = 0

        # Write the session header.
        info = SessionInfo(
            session_id=self.session_id,
            project_dir=str(project_dir),
            started_at=self.started_at,
            model=model,
        )
        self._append(info.to_entry())

    @property
    def path(self) -> Path:
        return self._path

    @property
    def message_count(self) -> int:
        """Number of non-system entries appended."""
        return self._count

    def append(self, role: str, content: str, **meta: object) -> None:
        """Append a turn to the transcript."""
        entry = SessionEntry(role=role, content=content, meta=meta if meta else {})
        self._append(entry)
        self._count += 1

    def _append(self, entry: SessionEntry) -> None:
        with self._path.open("a", encoding="utf-8") as f:
            f.write(entry.to_json() + "\n")


def read_transcript(project_dir: Path, session_id: str) -> list[SessionEntry]:
    """Read all entries from a session transcript."""
    p = session_path(project_dir, session_id)
    if not p.exists():
        return []
    entries: list[SessionEntry] = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(SessionEntry.from_json(line))
    return entries


def list_sessions(project_dir: Path) -> list[SessionInfo]:
    """List all sessions for a project, most recent first."""
    d = sessions_dir(project_dir)
    sessions: list[SessionInfo] = []
    for p in d.glob("*.jsonl"):
        try:
            with p.open(encoding="utf-8") as f:
                first_line = f.readline().strip()
                if first_line:
                    entry = SessionEntry.from_json(first_line)
                    sessions.append(SessionInfo.from_entry(entry))
        except (json.JSONDecodeError, KeyError):
            continue
    sessions.sort(key=lambda s: s.started_at, reverse=True)
    return sessions


def latest_session_id(project_dir: Path) -> str | None:
    """Return the most recent session ID, or None."""
    sessions = list_sessions(project_dir)
    return sessions[0].session_id if sessions else None


def session_summary(project_dir: Path, session_id: str) -> dict[str, object]:
    """Quick summary of a session: message count, duration, roles."""
    entries = read_transcript(project_dir, session_id)
    if not entries:
        return {"session_id": session_id, "message_count": 0}

    info_entry = entries[0]
    info = SessionInfo.from_entry(info_entry)
    messages = [e for e in entries if e.role != "system"]
    last_ts = entries[-1].timestamp if entries else info.started_at
    duration = last_ts - info.started_at

    return {
        "session_id": session_id,
        "model": info.model,
        "started_at": info.started_at,
        "duration_s": duration,
        "message_count": len(messages),
        "user_messages": sum(1 for e in messages if e.role == "user"),
        "assistant_messages": sum(1 for e in messages if e.role == "assistant"),
    }
