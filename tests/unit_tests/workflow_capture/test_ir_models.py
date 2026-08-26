from __future__ import annotations

import pytest
from pydantic import ValidationError

from jiuwenswarm.workflow_capture.ir.models import (
    Binding,
    BindingCandidate,
    BindingConfidence,
    BindingKind,
    CaptureReport,
    Component,
    ComponentType,
    Determinism,
    Effect,
    PromotionBasis,
    RunMode,
    Viability,
)

from .conftest import make_run_record, make_saved_workflow


# -- construction -----------------------------------------------------------


def test_saved_workflow_constructs_and_has_no_status_field():
    workflow = make_saved_workflow()
    assert not hasattr(workflow, "status")
    assert "status" not in workflow.model_dump()


def test_saved_workflow_has_no_runs_field():
    # data-model.md's own field table lists `runs: list[RunRecord]` on
    # SavedWorkflow, but that contradicts immutability the same way `status`
    # did (see ir/models.py docstring) — dropped as a build-time deviation.
    workflow = make_saved_workflow()
    assert not hasattr(workflow, "runs")
    assert "runs" not in workflow.model_dump()


# -- serialisation round-trip ------------------------------------------------


def test_saved_workflow_round_trips_through_json():
    workflow = make_saved_workflow()
    restored = type(workflow).model_validate_json(workflow.model_dump_json())
    assert restored == workflow


def test_run_record_round_trips_through_json():
    record = make_run_record(
        promotion_basis=PromotionBasis.MANUAL_ACK, mode=RunMode.LIVE,
        acting_user="amir", source_run_id="run-0",
    )
    restored = type(record).model_validate_json(record.model_dump_json())
    assert restored == record


# -- Binding validation -------------------------------------------------------


def test_binding_parameter_requires_node_start_format():
    Binding(name="topic", kind=BindingKind.PARAMETER, value="${node_start.topic}")
    with pytest.raises(ValidationError):
        Binding(name="topic", kind=BindingKind.PARAMETER, value="topic")


def test_binding_reference_requires_dotted_format():
    Binding(name="url", kind=BindingKind.REFERENCE, value="${node_search.url}", confidence=BindingConfidence.EXACT)
    with pytest.raises(ValidationError):
        Binding(name="url", kind=BindingKind.REFERENCE, value="not-a-reference")


def test_binding_containment_forces_unresolved():
    with pytest.raises(ValidationError, match="containment"):
        Binding(
            name="url",
            kind=BindingKind.REFERENCE,
            value="${node_search.url}",
            confidence=BindingConfidence.CONTAINMENT,
        )

    # unresolved + containment is fine, and is the only legal combination
    Binding(
        name="url",
        kind=BindingKind.UNRESOLVED,
        confidence=BindingConfidence.CONTAINMENT,
        candidates=[
            BindingCandidate(
                origin_id="node_search",
                json_pointer="/results/0/snippet",
                tier=BindingConfidence.CONTAINMENT,
                matched_span="https://example.com/q3-report",
            )
        ],
    )


def test_binding_candidate_matched_span_required_for_containment_tier():
    with pytest.raises(ValidationError, match="matched_span"):
        BindingCandidate(origin_id="node_search", json_pointer="/x", tier=BindingConfidence.CONTAINMENT)


def test_binding_candidate_matched_span_forbidden_for_non_containment_tier():
    with pytest.raises(ValidationError, match="matched_span"):
        BindingCandidate(origin_id="node_search", json_pointer="/x", tier=BindingConfidence.EXACT, matched_span="fragment")


def test_binding_containment_tier_candidate_forces_unresolved_even_if_confidence_field_disagrees():
    # The confidence FIELD says exact, but the one candidate is at
    # containment tier — the bypass an independent Codex code review found:
    # checking only `self.confidence` let this combination slip through.
    candidate = BindingCandidate(
        origin_id="node_search", json_pointer="/x", tier=BindingConfidence.CONTAINMENT, matched_span="frag"
    )
    with pytest.raises(ValidationError, match="containment"):
        Binding(
            name="url",
            kind=BindingKind.REFERENCE,
            value="${node_search.x}",
            confidence=BindingConfidence.EXACT,
            candidates=[candidate],
        )


def test_binding_multiple_candidates_forces_unresolved():
    candidates = [
        BindingCandidate(origin_id="node_a", json_pointer="/x", tier=BindingConfidence.EXACT),
        BindingCandidate(origin_id="node_b", json_pointer="/y", tier=BindingConfidence.EXACT),
    ]
    with pytest.raises(ValidationError, match="more than one candidate"):
        Binding(name="v", kind=BindingKind.REFERENCE, value="${node_a.x}", candidates=candidates)

    Binding(name="v", kind=BindingKind.UNRESOLVED, candidates=candidates)


def test_binding_unresolved_requires_nonempty_candidates():
    with pytest.raises(ValidationError, match="candidates"):
        Binding(name="v", kind=BindingKind.UNRESOLVED, candidates=[])


# -- Component conditional rules (T015) --------------------------------------


def _resolved_binding() -> Binding:
    return Binding(name="url", kind=BindingKind.CONSTANT, value="https://example.com")


def test_frozen_requires_resolved_bindings_and_fingerprint():
    Component(
        id="c1",
        name="c1",
        type=ComponentType.API,
        determinism=Determinism.FROZEN,
        effect=Effect.READ,
        inputs=[_resolved_binding()],
        tool_fingerprint="abc123",
    )

    with pytest.raises(ValidationError, match="tool_fingerprint"):
        Component(
            id="c1",
            name="c1",
            type=ComponentType.API,
            determinism=Determinism.FROZEN,
            effect=Effect.READ,
            inputs=[_resolved_binding()],
        )

    unresolved = Binding(
        name="url",
        kind=BindingKind.UNRESOLVED,
        candidates=[BindingCandidate(origin_id="x", json_pointer="/y", tier=BindingConfidence.EXACT)],
    )
    with pytest.raises(ValidationError, match="frozen"):
        Component(
            id="c1",
            name="c1",
            type=ComponentType.API,
            determinism=Determinism.FROZEN,
            effect=Effect.READ,
            inputs=[unresolved],
            tool_fingerprint="abc123",
        )


def test_frozen_forbids_derived_unknown_binding():
    derived = Binding(name="url", kind=BindingKind.CONSTANT, value="x", derived_unknown=True)
    with pytest.raises(ValidationError, match="derived_unknown"):
        Component(
            id="c1",
            name="c1",
            type=ComponentType.API,
            determinism=Determinism.FROZEN,
            effect=Effect.READ,
            inputs=[derived],
            tool_fingerprint="abc123",
        )


def test_agent_requires_allowlist_and_iteration_budget():
    Component(
        id="c1",
        name="c1",
        type=ComponentType.AGENT,
        determinism=Determinism.AGENT,
        effect=Effect.WRITE_EFFECTFUL,
        configs={"tool_allowlist": ["search"], "max_iterations": 5},
    )

    with pytest.raises(ValidationError, match="tool_allowlist"):
        Component(
            id="c1",
            name="c1",
            type=ComponentType.AGENT,
            determinism=Determinism.AGENT,
            effect=Effect.WRITE_EFFECTFUL,
            configs={"max_iterations": 5},
        )

    with pytest.raises(ValidationError, match="max_iterations"):
        Component(
            id="c1",
            name="c1",
            type=ComponentType.AGENT,
            determinism=Determinism.AGENT,
            effect=Effect.WRITE_EFFECTFUL,
            configs={"tool_allowlist": ["search"]},
        )


@pytest.mark.parametrize(
    "bad_allowlist", ["search", [], ["search", ""], ["search", 1], None]
)
def test_agent_tool_allowlist_must_be_nonempty_list_of_strings(bad_allowlist):
    with pytest.raises(ValidationError, match="tool_allowlist"):
        Component(
            id="c1", name="c1", type=ComponentType.AGENT, determinism=Determinism.AGENT,
            effect=Effect.WRITE_EFFECTFUL,
            configs={"tool_allowlist": bad_allowlist, "max_iterations": 5},
        )


@pytest.mark.parametrize("bad_iterations", [-1, 0, "5", True, 1.5])
def test_agent_max_iterations_must_be_positive_int(bad_iterations):
    with pytest.raises(ValidationError, match="max_iterations"):
        Component(
            id="c1", name="c1", type=ComponentType.AGENT, determinism=Determinism.AGENT,
            effect=Effect.WRITE_EFFECTFUL,
            configs={"tool_allowlist": ["search"], "max_iterations": bad_iterations},
        )


def test_loop_requires_loop_body_and_arr_loop_var():
    loop_var = Binding(name="arr_loop_var", kind=BindingKind.REFERENCE, value="${node_search.results}", confidence=BindingConfidence.EXACT)

    Component(
        id="loop1",
        name="loop1",
        type=ComponentType.LOOP,
        determinism=Determinism.TRANSFORM,
        effect=Effect.READ,
        inputs=[loop_var],
        configs={"loop_body": ["node_fetch"]},
    )

    with pytest.raises(ValidationError, match="loop_body"):
        Component(
            id="loop1",
            name="loop1",
            type=ComponentType.LOOP,
            determinism=Determinism.TRANSFORM,
            effect=Effect.READ,
            inputs=[loop_var],
            configs={},
        )

    with pytest.raises(ValidationError, match="arr_loop_var"):
        Component(
            id="loop1",
            name="loop1",
            type=ComponentType.LOOP,
            determinism=Determinism.TRANSFORM,
            effect=Effect.READ,
            inputs=[],
            configs={"loop_body": ["node_fetch"]},
        )


# -- CaptureReport viability --------------------------------------------------


def test_capture_report_requires_reason_when_not_viable():
    CaptureReport(steps_captured=1, steps_skipped=0, viability=Viability.VIABLE)

    with pytest.raises(ValidationError, match="viability_reason"):
        CaptureReport(steps_captured=1, steps_skipped=0, viability=Viability.NOT_VIABLE, viability_reason="")

    CaptureReport(
        steps_captured=1,
        steps_skipped=0,
        viability=Viability.NOT_VIABLE,
        viability_reason="fewer than two captured steps",
    )


# -- RunRecord promotion / dry-run rules --------------------------------------


def test_dry_run_forbids_result_consistent():
    make_run_record(mode=RunMode.DRY, result_consistent=None)
    with pytest.raises(ValidationError, match="result_consistent"):
        make_run_record(mode=RunMode.DRY, result_consistent=True)


def test_auto_promotion_basis_requires_result_consistent_true():
    make_run_record(mode=RunMode.LIVE, result_consistent=True, promotion_basis=PromotionBasis.AUTO)
    with pytest.raises(ValidationError, match="promotion_basis"):
        make_run_record(mode=RunMode.LIVE, result_consistent=None, promotion_basis=PromotionBasis.AUTO)


def test_manual_ack_and_evaluator_do_not_require_result_consistent():
    # This is the exact case the review's Round 5 fix exists for: generative
    # terminals always have result_consistent is None, and manual_ack /
    # evaluator must still be reachable promotion bases.
    make_run_record(
        mode=RunMode.LIVE, result_consistent=None, promotion_basis=PromotionBasis.MANUAL_ACK,
        acting_user="amir", source_run_id="run-0",
    )
    make_run_record(mode=RunMode.LIVE, result_consistent=None, promotion_basis=PromotionBasis.EVALUATOR)


def test_manual_ack_requires_acting_user_and_source_run_id():
    # PLAN.md § Result-consistency contract: manual promotion "records the
    # override, the acting user, and the run_id it is based on" — a gap an
    # independent Codex code review of this diff found data-model.md's own
    # RunRecord field table never actually enforced.
    make_run_record(
        mode=RunMode.LIVE, result_consistent=None, promotion_basis=PromotionBasis.MANUAL_ACK,
        acting_user="amir", source_run_id="run-42",
    )
    with pytest.raises(ValidationError, match="acting_user"):
        make_run_record(
            mode=RunMode.LIVE, result_consistent=None, promotion_basis=PromotionBasis.MANUAL_ACK,
            acting_user=None, source_run_id="run-42",
        )
    with pytest.raises(ValidationError, match="acting_user"):
        make_run_record(
            mode=RunMode.LIVE, result_consistent=None, promotion_basis=PromotionBasis.MANUAL_ACK,
            acting_user="amir", source_run_id=None,
        )
