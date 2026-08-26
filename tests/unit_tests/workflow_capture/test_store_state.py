from __future__ import annotations

import json

import pytest

from jiuwenswarm.workflow_capture.ir.models import PromotionBasis, RunMode, RunOutcome
from jiuwenswarm.workflow_capture.store.filesystem import UnknownVersionError, WorkflowStore

from .conftest import make_capture_report, make_run_record, make_saved_workflow


def test_fresh_save_is_provisional(store: WorkflowStore):
    store.save(make_saved_workflow(), make_capture_report())
    state = store.get_state("research-digest")
    assert state.status == "provisional"
    assert state.workflow_version == 1


def test_live_success_with_auto_promotion_basis_promotes(store: WorkflowStore):
    store.save(make_saved_workflow(), make_capture_report())
    store.append_run(
        "research-digest",
        make_run_record(mode=RunMode.LIVE, outcome=RunOutcome.SUCCESS, result_consistent=True, promotion_basis=PromotionBasis.AUTO),
    )
    assert store.get_state("research-digest").status == "verified"


def test_live_success_with_manual_ack_promotes(store: WorkflowStore):
    # The exact case review Round 5 fixed: result_consistent is None (a
    # generative terminal), but manual_ack must still promote.
    store.save(make_saved_workflow(), make_capture_report())
    store.append_run(
        "research-digest",
        make_run_record(
            mode=RunMode.LIVE, outcome=RunOutcome.SUCCESS, result_consistent=None,
            promotion_basis=PromotionBasis.MANUAL_ACK, acting_user="amir", source_run_id="run-0",
        ),
    )
    assert store.get_state("research-digest").status == "verified"


def test_live_success_with_evaluator_promotes(store: WorkflowStore):
    store.save(make_saved_workflow(), make_capture_report())
    store.append_run(
        "research-digest",
        make_run_record(mode=RunMode.LIVE, outcome=RunOutcome.SUCCESS, result_consistent=None, promotion_basis=PromotionBasis.EVALUATOR),
    )
    assert store.get_state("research-digest").status == "verified"


def test_dry_run_never_promotes(store: WorkflowStore):
    store.save(make_saved_workflow(), make_capture_report())
    store.append_run("research-digest", make_run_record(mode=RunMode.DRY, outcome=RunOutcome.SUCCESS, result_consistent=None))
    assert store.get_state("research-digest").status == "provisional"


def test_live_run_without_promotion_basis_does_not_promote(store: WorkflowStore):
    store.save(make_saved_workflow(), make_capture_report())
    store.append_run(
        "research-digest",
        make_run_record(mode=RunMode.LIVE, outcome=RunOutcome.SUCCESS, result_consistent=None, promotion_basis=None),
    )
    assert store.get_state("research-digest").status == "provisional"


def test_failed_live_run_does_not_promote(store: WorkflowStore):
    store.save(make_saved_workflow(), make_capture_report())
    store.append_run(
        "research-digest",
        make_run_record(mode=RunMode.LIVE, outcome=RunOutcome.FAILED, result_consistent=None, promotion_basis=None),
    )
    assert store.get_state("research-digest").status == "provisional"


# -- T042a: state.json survives deletion, reconstructed from runs.jsonl -----


def test_state_survives_deletion_and_reconstructs_identically(store: WorkflowStore):
    store.save(make_saved_workflow(), make_capture_report())
    store.append_run(
        "research-digest",
        make_run_record(mode=RunMode.LIVE, outcome=RunOutcome.SUCCESS, result_consistent=True, promotion_basis=PromotionBasis.AUTO),
    )
    expected = store.get_state("research-digest")
    assert expected.status == "verified"

    state_path = store.root / "research-digest" / "state.json"
    assert state_path.exists()
    state_path.unlink()
    assert not state_path.exists()

    reconstructed = store.get_state("research-digest")
    assert reconstructed == expected
    assert state_path.exists()  # get_state rebuilds and re-caches


def test_corrupt_state_json_is_ignored_and_rebuilt(store: WorkflowStore):
    store.save(make_saved_workflow(), make_capture_report())
    store.append_run(
        "research-digest",
        make_run_record(mode=RunMode.LIVE, outcome=RunOutcome.SUCCESS, result_consistent=True, promotion_basis=PromotionBasis.AUTO),
    )
    state_path = store.root / "research-digest" / "state.json"
    state_path.write_text("{not valid json", encoding="utf-8")

    state = store.get_state("research-digest")
    assert state.status == "verified"


# -- T042b: version-scoped rebuild — a promotion of v1 never verifies v2 ----


def test_overwrite_resets_to_provisional_even_though_prior_version_was_verified(store: WorkflowStore):
    store.save(make_saved_workflow(version=1), make_capture_report())
    store.append_run(
        "research-digest",
        make_run_record(
            workflow_version=1, mode=RunMode.LIVE, outcome=RunOutcome.SUCCESS,
            result_consistent=True, promotion_basis=PromotionBasis.AUTO,
        ),
    )
    assert store.get_state("research-digest").status == "verified"

    store.save(make_saved_workflow(version=2), make_capture_report(), overwrite=True)

    state = store.get_state("research-digest")
    assert state.workflow_version == 2
    assert state.status == "provisional"


def test_version_scoped_rebuild_after_state_json_deleted(store: WorkflowStore):
    store.save(make_saved_workflow(version=1), make_capture_report())
    store.append_run(
        "research-digest",
        make_run_record(
            run_id="run-v1", workflow_version=1, mode=RunMode.LIVE, outcome=RunOutcome.SUCCESS,
            result_consistent=True, promotion_basis=PromotionBasis.AUTO,
        ),
    )
    store.save(make_saved_workflow(version=2), make_capture_report(), overwrite=True)

    state_path = store.root / "research-digest" / "state.json"
    state_path.unlink()

    state = store.rebuild_state("research-digest")
    assert state.workflow_version == 2
    assert state.status == "provisional"


def test_promoting_v2_does_not_retroactively_verify_v1(store: WorkflowStore):
    store.save(make_saved_workflow(version=1), make_capture_report())
    store.save(make_saved_workflow(version=2), make_capture_report(), overwrite=True)
    store.append_run(
        "research-digest",
        make_run_record(
            run_id="run-v2", workflow_version=2, mode=RunMode.LIVE, outcome=RunOutcome.SUCCESS,
            result_consistent=True, promotion_basis=PromotionBasis.AUTO,
        ),
    )
    assert store.get_state("research-digest").status == "verified"

    from jiuwenswarm.workflow_capture.store.filesystem import _derive_state

    records = store._read_runs("research-digest")
    v1_state = _derive_state(records, version=1)
    assert v1_state.status == "provisional"


# -- regression: forged/stale same-version state.json must never be trusted -


def test_forged_verified_state_json_with_no_promoting_run_is_corrected(store: WorkflowStore):
    # get_state previously trusted state.json whenever its cached
    # workflow_version matched current, without checking whether its status
    # agreed with runs.jsonl — a same-version but WRONG cache (hand-edited,
    # forged, or from a bug elsewhere) would report verified forever. Caught
    # by an independent Codex code review of this diff.
    store.save(make_saved_workflow(version=1), make_capture_report())
    assert store.get_state("research-digest").status == "provisional"

    state_path = store.root / "research-digest" / "state.json"
    state_path.write_text(json.dumps({"workflow_version": 1, "status": "verified"}), encoding="utf-8")

    state = store.get_state("research-digest")
    assert state.status == "provisional"
    assert state.workflow_version == 1


# -- regression: version stamping and unknown-version append_run guard -----


def test_save_stamps_the_store_allocated_version_not_the_callers(store: WorkflowStore):
    # The caller-supplied SavedWorkflow.version was previously written
    # as-is; a caller passing the wrong number produced e.g.
    # versions/2/workflow.json claiming "version": 1. The store now owns
    # version numbers and stamps them regardless of caller input.
    store.save(make_saved_workflow(version=1), make_capture_report())
    v2 = store.save(make_saved_workflow(version=999), make_capture_report(), overwrite=True)
    assert v2 == 2

    loaded, _ = store.load("research-digest", version=2)
    assert loaded.version == 2


def test_append_run_rejects_workflow_version_with_no_version_directory(store: WorkflowStore):
    store.save(make_saved_workflow(version=1), make_capture_report())
    with pytest.raises(UnknownVersionError):
        store.append_run(
            "research-digest",
            make_run_record(
                workflow_version=2, mode=RunMode.LIVE, outcome=RunOutcome.SUCCESS,
                result_consistent=True, promotion_basis=PromotionBasis.AUTO,
            ),
        )
