"""Filesystem-backed workflow store.

Implements `PLAN.md` § Store contract and `data-model.md`'s "Store layout" /
"State transitions" sections exactly, including the Round 6 correction that
`RunRecord.workflow_version` scopes lifecycle-state rebuild:

    <store_root>/<slug>/
        versions/<n>/workflow.json   # SavedWorkflow, no `status` field
        versions/<n>/report.json     # CaptureReport
        versions/<n>/ir_schema       # IR schema version stamp
        runs.jsonl                   # RunRecord append log — authoritative
        state.json                  # derived cache, rebuildable from runs.jsonl
        current                     # plain text version number — NOT a symlink
        .lock                       # flock target
        .tmp-<uuid>/                # in-progress version build

Version directories are immutable once written. Only `runs.jsonl`,
`state.json`, and `current` mutate.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..ir.models import IR_SCHEMA_VERSION, CaptureReport, RunMode, RunOutcome, RunRecord, SavedWorkflow

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class WorkflowStoreError(Exception):
    """Base for all store errors."""


class InvalidSlugError(WorkflowStoreError):
    pass


class WorkflowNotFoundError(WorkflowStoreError):
    pass


class WorkflowExistsError(WorkflowStoreError):
    pass


class VersionConflictError(WorkflowStoreError):
    """Raised if a version directory we are about to claim already exists.

    Should not happen under the lock during normal operation — version
    numbers are allocated as max(existing) + 1 — but a corrupted or
    manually-tampered store could still trigger it, and the store contract
    requires failing rather than overwriting.
    """


class UnknownVersionError(WorkflowStoreError):
    """Raised by `append_run` when `RunRecord.workflow_version` does not
    name a version directory that actually exists for the workflow.

    Without this check a record for a not-yet-created version could sit in
    `runs.jsonl` and later "verify" that version the moment it is saved —
    a promotion the record was never really about (caught by an
    independent Codex code review of this diff).
    """


def validate_slug(slug: str) -> None:
    if not SLUG_PATTERN.match(slug):
        raise InvalidSlugError(
            f"'{slug}' is not a valid workflow slug — must match {SLUG_PATTERN.pattern}"
        )


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_tree(root: Path) -> None:
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            _fsync_file(Path(dirpath) / name)
        _fsync_dir(Path(dirpath))


def _atomic_write_text(path: Path, content: str) -> None:
    """Write `content` to `path` via a same-directory temp file + os.replace,
    fsyncing the file and its parent directory so the write survives a crash.
    """
    tmp = path.parent / f".{path.name}.tmp-{uuid.uuid4().hex}"
    tmp.write_text(content, encoding="utf-8")
    _fsync_file(tmp)
    os.replace(tmp, path)
    _fsync_dir(path.parent)


class WorkflowLifecycleState:
    """Derived, cacheable lifecycle state for one workflow version."""

    def __init__(self, workflow_version: int, verified: bool) -> None:
        self.workflow_version = workflow_version
        self.verified = verified

    @property
    def status(self) -> str:
        return "verified" if self.verified else "provisional"

    def to_dict(self) -> dict:
        return {"workflow_version": self.workflow_version, "status": self.status}

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowLifecycleState":
        return cls(workflow_version=data["workflow_version"], verified=data["status"] == "verified")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, WorkflowLifecycleState):
            return NotImplemented
        return self.workflow_version == other.workflow_version and self.verified == other.verified

    def __repr__(self) -> str:
        return f"WorkflowLifecycleState(workflow_version={self.workflow_version}, status={self.status!r})"


def _promotes(record: RunRecord) -> bool:
    """The promotion rule (T042): live + success + a promotion_basis of
    result_consistent==True (auto), manual_ack, or evaluator -> verified.
    """
    return (
        record.mode == RunMode.LIVE
        and record.outcome == RunOutcome.SUCCESS
        and record.promotion_basis is not None
    )


def _derive_state(records: list[RunRecord], version: int) -> WorkflowLifecycleState:
    """Version-scoped: only records whose workflow_version matches `version`
    count. A promotion of an older or newer version never verifies this one
    (review Round 6) — this is what keeps an overwrite starting `provisional`
    even though older runs.jsonl entries exist for the prior version.
    """
    verified = any(
        record.workflow_version == version and _promotes(record) for record in records
    )
    return WorkflowLifecycleState(workflow_version=version, verified=verified)


class WorkflowStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # -- path helpers --------------------------------------------------

    def _workflow_dir(self, slug: str) -> Path:
        validate_slug(slug)
        candidate = (self.root / slug).resolve()
        if self.root not in candidate.parents and candidate != self.root:
            raise InvalidSlugError(f"'{slug}' resolves outside the store root")
        return candidate

    def _versions_dir(self, slug: str) -> Path:
        return self._workflow_dir(slug) / "versions"

    def _lock_path(self, slug: str) -> Path:
        return self._workflow_dir(slug) / ".lock"

    def _runs_path(self, slug: str) -> Path:
        return self._workflow_dir(slug) / "runs.jsonl"

    def _state_path(self, slug: str) -> Path:
        return self._workflow_dir(slug) / "state.json"

    def _current_path(self, slug: str) -> Path:
        return self._workflow_dir(slug) / "current"

    @contextmanager
    def _locked(self, slug: str) -> Iterator[None]:
        workflow_dir = self._workflow_dir(slug)
        workflow_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self._lock_path(slug)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    # -- existence / listing --------------------------------------------

    def exists(self, slug: str) -> bool:
        validate_slug(slug)
        return self._current_path(slug).exists()

    def list_workflows(self) -> list[str]:
        if not self.root.exists():
            return []
        names = []
        for entry in sorted(self.root.iterdir()):
            if not entry.is_dir():
                continue
            if not SLUG_PATTERN.match(entry.name):
                continue
            if (entry / "current").exists():
                names.append(entry.name)
        return names

    def _existing_version_numbers(self, slug: str) -> list[int]:
        """Only directories that look like `versions/<int>/` count. A stray
        `.tmp-*` build directory or anything else is ignored — this is what
        lets a crashed save (orphaned temp dir) coexist with normal
        operation without colliding on the next version number.
        """
        versions_dir = self._versions_dir(slug)
        if not versions_dir.exists():
            return []
        numbers = []
        for entry in versions_dir.iterdir():
            if entry.is_dir() and entry.name.isdigit():
                numbers.append(int(entry.name))
        return sorted(numbers)

    def current_version(self, slug: str) -> int:
        current_path = self._current_path(slug)
        if not current_path.exists():
            raise WorkflowNotFoundError(f"workflow '{slug}' does not exist")
        return int(current_path.read_text(encoding="utf-8").strip())

    # -- save / load ------------------------------------------------------

    def save(
        self,
        workflow: SavedWorkflow,
        report: CaptureReport,
        *,
        overwrite: bool = False,
    ) -> int:
        """Write a new immutable version and atomically advance `current`.
        Returns the new version number.

        The store — not the caller — owns version numbers. `workflow.version`
        is overwritten with the allocated number before serialisation, so a
        caller-supplied value can never desync from the directory it ends up
        in (e.g. `versions/2/workflow.json` claiming `"version": 1`) — a gap
        an independent Codex code review of this diff caught: earlier, the
        caller's `version` field was trusted as-is and only test fixtures
        happened to pass the right number.
        """
        slug = workflow.workflow_id
        with self._locked(slug):
            already_exists = self._current_path(slug).exists()
            if already_exists and not overwrite:
                raise WorkflowExistsError(
                    f"'{slug}' already exists (v{self.current_version(slug)}). "
                    f"Use overwrite=True to save a new version."
                )

            existing = self._existing_version_numbers(slug)
            next_version = (max(existing) + 1) if existing else 1
            workflow = workflow.model_copy(update={"version": next_version})

            workflow_dir = self._workflow_dir(slug)
            versions_dir = self._versions_dir(slug)
            versions_dir.mkdir(parents=True, exist_ok=True)

            target = versions_dir / str(next_version)
            if target.exists():
                raise VersionConflictError(
                    f"version directory '{target}' already exists — refusing to overwrite"
                )

            tmp_dir = workflow_dir / f".tmp-{uuid.uuid4().hex}"
            tmp_dir.mkdir()
            try:
                (tmp_dir / "workflow.json").write_text(
                    workflow.model_dump_json(indent=2), encoding="utf-8"
                )
                (tmp_dir / "report.json").write_text(
                    report.model_dump_json(indent=2), encoding="utf-8"
                )
                (tmp_dir / "ir_schema").write_text(IR_SCHEMA_VERSION, encoding="utf-8")

                _fsync_tree(tmp_dir)

                os.replace(tmp_dir, target)
            except BaseException:
                if tmp_dir.exists():
                    for child in tmp_dir.iterdir():
                        child.unlink()
                    tmp_dir.rmdir()
                raise

            _fsync_dir(versions_dir)

            _atomic_write_text(self._current_path(slug), str(next_version))

            self._rebuild_state_locked(slug)

        return next_version

    def load(self, slug: str, version: int | None = None) -> tuple[SavedWorkflow, CaptureReport]:
        target_version = version if version is not None else self.current_version(slug)
        version_dir = self._versions_dir(slug) / str(target_version)
        workflow_path = version_dir / "workflow.json"
        report_path = version_dir / "report.json"
        if not workflow_path.exists():
            raise WorkflowNotFoundError(f"'{slug}' has no version {target_version}")
        workflow = SavedWorkflow.model_validate_json(workflow_path.read_text(encoding="utf-8"))
        report = CaptureReport.model_validate_json(report_path.read_text(encoding="utf-8"))
        return workflow, report

    def list_versions(self, slug: str) -> list[int]:
        return self._existing_version_numbers(slug)

    # -- runs / lifecycle state --------------------------------------------

    def append_run(self, slug: str, record: RunRecord) -> None:
        with self._locked(slug):
            if record.workflow_version not in self._existing_version_numbers(slug):
                raise UnknownVersionError(
                    f"'{slug}' has no version {record.workflow_version} — cannot "
                    f"append a run record for a version that does not exist"
                )

            runs_path = self._runs_path(slug)
            with open(runs_path, "a", encoding="utf-8") as fh:
                fh.write(record.model_dump_json())
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            _fsync_dir(runs_path.parent)

            self._rebuild_state_locked(slug)

    def _read_runs(self, slug: str) -> list[RunRecord]:
        runs_path = self._runs_path(slug)
        if not runs_path.exists():
            return []
        records = []
        for line in runs_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            records.append(RunRecord.model_validate_json(line))
        return records

    def _rebuild_state_locked(self, slug: str) -> WorkflowLifecycleState:
        """Caller must already hold `slug`'s lock."""
        version = self.current_version(slug)
        records = self._read_runs(slug)
        state = _derive_state(records, version)
        _atomic_write_text(self._state_path(slug), json.dumps(state.to_dict()))
        return state

    def rebuild_state(self, slug: str) -> WorkflowLifecycleState:
        """Force a rebuild of `state.json` from `runs.jsonl`, ignoring
        whatever is currently cached. `runs.jsonl` is authoritative
        (T041a) — this is the never-trust-the-cache path.
        """
        with self._locked(slug):
            return self._rebuild_state_locked(slug)

    def get_state(self, slug: str) -> WorkflowLifecycleState:
        """Read lifecycle state. Always derives from `runs.jsonl` — never
        trusts `state.json` for the returned value, even when its cached
        version matches `current`.

        An earlier version of this method returned the cache whenever the
        version matched, without checking whether its *status* agreed with
        the log. That let a stale, hand-edited, or corrupted-but-valid
        `state.json` report `verified` with no promoting run behind it —
        exactly the failure "state.json is a cache, runs.jsonl is
        authoritative" exists to prevent (caught by an independent Codex
        code review of this diff). `state.json` is still written on every
        call as a side-effect cache for external inspection, but nothing
        in this store trusts it back.
        """
        return self.rebuild_state(slug)
