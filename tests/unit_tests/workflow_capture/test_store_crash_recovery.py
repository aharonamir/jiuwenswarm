"""T041b — crash recovery: an orphaned `.tmp-*` build directory, a complete
version directory newer than `current` (a crash between `os.replace` into
`versions/<n>/` and the `current` pointer swap), and a lost `state.json`
must each leave the store fully functional without manual intervention.
"""

from __future__ import annotations

import json

from jiuwenswarm.workflow_capture.ir.models import PromotionBasis, RunMode, RunOutcome
from jiuwenswarm.workflow_capture.store.filesystem import WorkflowStore

from .conftest import make_capture_report, make_run_record, make_saved_workflow


def test_orphaned_tmp_dir_is_ignored_by_list_and_load(store: WorkflowStore):
    store.save(make_saved_workflow(), make_capture_report())
    workflow_dir = store.root / "research-digest"
    orphan = workflow_dir / ".tmp-orphaned-crash"
    orphan.mkdir()
    (orphan / "workflow.json").write_text("garbage, not valid json", encoding="utf-8")

    assert store.list_workflows() == ["research-digest"]
    workflow, _report = store.load("research-digest")
    assert workflow.workflow_id == "research-digest"


def test_orphaned_tmp_dir_does_not_collide_on_next_save(store: WorkflowStore):
    store.save(make_saved_workflow(), make_capture_report())
    workflow_dir = store.root / "research-digest"
    (workflow_dir / ".tmp-orphaned-crash").mkdir()

    v2 = store.save(make_saved_workflow(version=2), make_capture_report(), overwrite=True)
    assert v2 == 2
    assert store.list_versions("research-digest") == [1, 2]


def test_version_dir_ahead_of_current_does_not_break_reads(store: WorkflowStore):
    store.save(make_saved_workflow(version=1), make_capture_report())

    # Simulate a crash between os.replace(tmp_dir, versions/2) and the
    # current-pointer swap: versions/2 exists on disk but `current` still
    # says "1".
    workflow_dir = store.root / "research-digest"
    orphan_version = workflow_dir / "versions" / "2"
    orphan_version.mkdir()
    (orphan_version / "workflow.json").write_text(
        make_saved_workflow(version=2).model_dump_json(), encoding="utf-8"
    )
    (orphan_version / "report.json").write_text(
        make_capture_report().model_dump_json(), encoding="utf-8"
    )
    (orphan_version / "ir_schema").write_text("1", encoding="utf-8")

    assert store.current_version("research-digest") == 1
    workflow, _ = store.load("research-digest")
    assert workflow.version == 1

    # get_state must also key off the (unswapped) current pointer, not the
    # orphaned newer directory.
    state = store.get_state("research-digest")
    assert state.workflow_version == 1


def test_version_dir_ahead_of_current_does_not_collide_on_next_save(store: WorkflowStore):
    store.save(make_saved_workflow(version=1), make_capture_report())
    workflow_dir = store.root / "research-digest"
    orphan_version = workflow_dir / "versions" / "2"
    orphan_version.mkdir()
    (orphan_version / "workflow.json").write_text(
        make_saved_workflow(version=2).model_dump_json(), encoding="utf-8"
    )
    (orphan_version / "report.json").write_text(
        make_capture_report().model_dump_json(), encoding="utf-8"
    )
    (orphan_version / "ir_schema").write_text("1", encoding="utf-8")

    # A subsequent save must not try to reclaim version 2 (already on disk,
    # unreferenced) — it must allocate 3, never colliding or overwriting.
    v3 = store.save(make_saved_workflow(version=3), make_capture_report(), overwrite=True)
    assert v3 == 3
    assert store.list_versions("research-digest") == [1, 2, 3]
    assert store.current_version("research-digest") == 3


def test_lost_state_json_recovers_without_manual_intervention(store: WorkflowStore):
    store.save(make_saved_workflow(), make_capture_report())
    store.append_run(
        "research-digest",
        make_run_record(mode=RunMode.LIVE, outcome=RunOutcome.SUCCESS, result_consistent=True, promotion_basis=PromotionBasis.AUTO),
    )
    state_path = store.root / "research-digest" / "state.json"
    state_path.unlink()

    # No exception, no manual rebuild call required by the caller — a plain
    # read self-heals.
    state = store.get_state("research-digest")
    assert state.status == "verified"
    assert json.loads(state_path.read_text(encoding="utf-8"))["status"] == "verified"
