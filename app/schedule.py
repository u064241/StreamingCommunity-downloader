import json
import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import atomic_write_text, quarantine_corrupt_file

logger = logging.getLogger(__name__)

# How a fired entry ended. "interrupted" is written at startup for one that was
# fired by a previous run and never got an outcome: the process stopped first.
OUTCOMES = ("done", "error", "cancelled", "interrupted")


class ScheduleStore:
    """JSON-backed store for scheduled download entries.

    Each entry:
      {
        "schedule_id": str,
        "type": "film" | "episode" | "anime",
        "scheduled_at": ISO-8601 str,
        "job_id": str | null,   # set once the job is submitted
        "fired": bool,          # handed to the download queue
        "outcome": str | null,  # one of OUTCOMES once the download is over
        "params": { ... }       # type-specific download params
      }

    Entries written before ``outcome`` existed do not carry the key at all,
    which is how a fired one of those is told apart from one this version
    fired and lost to a restart.
    """

    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._entries: list[dict] = []
        self._load()

    # ── persistence ────────────────────────────────────────────────────────────

    def _load(self):
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        try:
            entries = json.loads(text)
            if not isinstance(entries, list):
                raise ValueError(f"expected a JSON list, found {type(entries).__name__}")
        except ValueError as exc:
            # Set aside, never started over: the next save would otherwise
            # write an empty list on top of every pending schedule.
            try:
                moved = quarantine_corrupt_file(self._path)
                logger.error("Schedule file %s is corrupt (%s): moved to %s, starting empty",
                             self._path, exc, moved)
            except OSError as move_exc:
                logger.error("Schedule file %s is corrupt (%s) and could not be set aside: %s",
                             self._path, exc, move_exc)
                raise
            return
        self._entries = entries

    def _save(self):
        """Write the entries atomically. Logs and raises when it cannot: a
        schedule that exists only in memory is gone at the next restart, and
        the caller has to be able to say so."""
        try:
            atomic_write_text(
                self._path, json.dumps(self._entries, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.error("Could not save schedule file %s: %s", self._path, e)
            raise

    def _try_save(self) -> bool:
        """For changes to an entry that already exists: the download they
        describe goes ahead whether or not the file could be updated, so the
        failure is reported (by _save) and returned instead of raised."""
        try:
            self._save()
            return True
        except Exception:
            return False

    # ── public API ─────────────────────────────────────────────────────────────

    def add(self, type_: str, scheduled_at: datetime, params: dict) -> str:
        """Persist a new entry. Raises if it cannot be written: nothing is
        scheduled then, rather than something that would not survive a restart."""
        schedule_id = str(uuid.uuid4())
        entry = {
            "schedule_id": schedule_id,
            "type": type_,
            "scheduled_at": scheduled_at.isoformat(),
            "job_id": None,
            "fired": False,
            "outcome": None,
            "params": params,
        }
        with self._lock:
            self._entries.append(entry)
            try:
                self._save()
            except Exception:
                self._entries.remove(entry)
                raise
        return schedule_id

    def _update(self, schedule_id: str, **fields) -> bool:
        with self._lock:
            for entry in self._entries:
                if entry["schedule_id"] == schedule_id:
                    entry.update(fields)
                    return self._try_save()
        return False

    def set_job_id(self, schedule_id: str, job_id: str) -> bool:
        return self._update(schedule_id, job_id=job_id)

    def mark_fired(self, schedule_id: str) -> bool:
        return self._update(schedule_id, fired=True, outcome=None)

    def set_outcome(self, schedule_id: str, outcome: str) -> bool:
        """Record how a fired entry ended (see OUTCOMES)."""
        return self._update(schedule_id, fired=True, outcome=outcome)

    def has_pending(self) -> bool:
        """Whether any entry is still waiting to be handed to the queue."""
        with self._lock:
            return any(not e.get("fired") for e in self._entries)

    def remove_by_schedule_id(self, schedule_id: str) -> bool:
        with self._lock:
            before = len(self._entries)
            self._entries = [e for e in self._entries if e["schedule_id"] != schedule_id]
            if len(self._entries) < before:
                self._try_save()
                return True
        return False

    def remove_by_job_id(self, job_id: str) -> bool:
        """Called when a completed/cancelled job is dismissed from the UI."""
        with self._lock:
            before = len(self._entries)
            self._entries = [e for e in self._entries if e.get("job_id") != job_id]
            if len(self._entries) < before:
                self._try_save()
                return True
        return False

    def list_all(self) -> list[dict]:
        with self._lock:
            return list(self._entries)

    def due(self) -> list[dict]:
        """Return entries whose scheduled_at is in the past and have no job_id yet."""
        from datetime import timezone
        now = datetime.now(timezone.utc)
        with self._lock:
            result = []
            for e in self._entries:
                if e["job_id"] is not None:
                    continue
                sa = datetime.fromisoformat(e["scheduled_at"])
                if sa.tzinfo is None:
                    sa = sa.replace(tzinfo=timezone.utc)
                if sa <= now:
                    result.append(e)
            return result

    def get_by_schedule_id(self, schedule_id: str) -> Optional[dict]:
        with self._lock:
            return next(
                (e for e in self._entries if e["schedule_id"] == schedule_id), None
            )
