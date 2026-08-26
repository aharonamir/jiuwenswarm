from __future__ import annotations

import pytest

from jiuwenswarm.workflow_capture.ir.models import IR_SCHEMA_VERSION
from jiuwenswarm.workflow_capture.store.filesystem import (
    InvalidSlugError,
    VersionConflictError,
    WorkflowExistsError,
    WorkflowNotFoundError,
    WorkflowStore,
)

from .conftest import make_capture_report, make_saved_workflow


def test_save_then_load_round_trips(store: WorkflowStore):
    workflow = make_saved_workflow()
    report = make_capture_report()

    version = store.save(workflow, report)
    assert version == 1

    loaded_workflow, loaded_report = store.load("research-digest")
    assert loaded_workflow == workflow
    assert loaded_report == report


def test_current_pointer_is_a_plain_text_file_not_a_symlink(store: WorkflowStore):
    store.save(make_saved_workflow(), make_capture_report())
    current_path = store.root / "research-digest" / "current"
    assert current_path.is_file()
    assert not current_path.is_symlink()
    assert current_path.read_text(encoding="utf-8").strip() == "1"


def test_save_without_overwrite_on_existing_name_fails(store: WorkflowStore):
    store.save(make_saved_workflow(), make_capture_report())
    with pytest.raises(WorkflowExistsError):
        store.save(make_saved_workflow(), make_capture_report())


def test_overwrite_creates_new_version_and_retains_prior(store: WorkflowStore):
    store.save(make_saved_workflow(), make_capture_report())
    v2 = store.save(make_saved_workflow(version=2), make_capture_report(), overwrite=True)
    assert v2 == 2

    assert store.list_versions("research-digest") == [1, 2]
    assert store.current_version("research-digest") == 2

    v1_workflow, _ = store.load("research-digest", version=1)
    assert v1_workflow.version == 1
    v2_workflow, _ = store.load("research-digest", version=2)
    assert v2_workflow.version == 2


def test_version_directories_are_immutable_files_on_disk(store: WorkflowStore):
    store.save(make_saved_workflow(), make_capture_report())
    version_dir = store.root / "research-digest" / "versions" / "1"
    workflow_json = version_dir / "workflow.json"
    report_json = version_dir / "report.json"
    ir_schema = version_dir / "ir_schema"

    assert workflow_json.exists()
    assert report_json.exists()
    assert ir_schema.read_text(encoding="utf-8") == IR_SCHEMA_VERSION

    # Nothing in the store code path mutates a version dir after creation —
    # assert its mtime is untouched by a second, unrelated save.
    before = workflow_json.stat().st_mtime_ns
    store.save(make_saved_workflow(workflow_id="other-workflow"), make_capture_report())
    after = workflow_json.stat().st_mtime_ns
    assert before == after


def test_list_workflows(store: WorkflowStore):
    assert store.list_workflows() == []
    store.save(make_saved_workflow(workflow_id="alpha"), make_capture_report())
    store.save(make_saved_workflow(workflow_id="beta"), make_capture_report())
    assert store.list_workflows() == ["alpha", "beta"]


def test_load_missing_workflow_raises(store: WorkflowStore):
    with pytest.raises(WorkflowNotFoundError):
        store.load("does-not-exist")


def test_load_missing_version_raises(store: WorkflowStore):
    store.save(make_saved_workflow(), make_capture_report())
    with pytest.raises(WorkflowNotFoundError):
        store.load("research-digest", version=99)


@pytest.mark.parametrize("bad_slug", ["Research-Digest", "-leading-dash", "has spaces", "has/slash", "..", "a" * 65])
def test_invalid_slugs_are_rejected(store: WorkflowStore, bad_slug: str):
    with pytest.raises(InvalidSlugError):
        store.save(make_saved_workflow(workflow_id=bad_slug), make_capture_report())


def test_slug_path_traversal_is_rejected(store: WorkflowStore):
    with pytest.raises(InvalidSlugError):
        store._workflow_dir("../escaped")


def test_save_fails_if_target_version_dir_already_exists(store: WorkflowStore, monkeypatch):
    store.save(make_saved_workflow(), make_capture_report())
    # Version allocation is max(existing)+1, so a stray directory never
    # actually collides on its own — that is the point of allocating that
    # way (see the crash-recovery tests). To exercise the defensive
    # fail-rather-than-overwrite path, force the allocator to recompute a
    # version number that is already on disk.
    monkeypatch.setattr(store, "_existing_version_numbers", lambda slug: [])
    with pytest.raises(VersionConflictError):
        store.save(make_saved_workflow(version=1), make_capture_report(), overwrite=True)
