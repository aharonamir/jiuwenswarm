from __future__ import annotations

from datetime import UTC, datetime

from jiuwenswarm.workflow_capture.ir.models import (
    Binding,
    BindingConfidence,
    BindingKind,
    Component,
    ComponentType,
    Connection,
    Determinism,
    Effect,
    OutputField,
    Parameter,
    SavedWorkflow,
)
from jiuwenswarm.workflow_capture.ir.validate import (
    WorkflowValidationError,
    assert_valid,
    validate_workflow,
)

from .conftest import make_capture_report, make_control_component


def _workflow(components: list[Component], connections: list[Connection], parameters: list[Parameter] | None = None) -> SavedWorkflow:
    return SavedWorkflow(
        workflow_id="wf",
        workflow_name="wf",
        version=1,
        components=components,
        connections=connections,
        parameters=parameters or [],
        source_session_id="s1",
        captured_at=datetime.now(UTC),
        capture_report=make_capture_report(),
    )


def _search_component() -> Component:
    return Component(
        id="node_search",
        name="search",
        type=ComponentType.API,
        determinism=Determinism.TRANSFORM,
        effect=Effect.READ,
        outputs=[OutputField(name="url", type="str")],
    )


def test_valid_minimal_workflow_has_no_issues():
    start = make_control_component("node_start", ComponentType.START)
    end = make_control_component("node_end", ComponentType.END)
    wf = _workflow([start, end], [Connection(source="node_start", target="node_end")])
    assert validate_workflow(wf) == []
    assert_valid(wf)  # does not raise


def test_missing_start_and_end_reported():
    only_end = make_control_component("node_end", ComponentType.END)
    wf = _workflow([only_end], [])
    issues = validate_workflow(wf)
    assert any("jiuwen.start" in issue for issue in issues)


def test_missing_end_reported():
    only_start = make_control_component("node_start", ComponentType.START)
    wf = _workflow([only_start], [])
    issues = validate_workflow(wf)
    assert any("jiuwen.end" in issue for issue in issues)


def test_dangling_connection_reference():
    start = make_control_component("node_start", ComponentType.START)
    end = make_control_component("node_end", ComponentType.END)
    wf = _workflow([start, end], [Connection(source="node_start", target="node_missing")])
    issues = validate_workflow(wf)
    assert any("node_missing" in issue for issue in issues)


def test_dangling_binding_reference():
    start = make_control_component("node_start", ComponentType.START)
    search = _search_component()
    fetch = Component(
        id="node_fetch",
        name="fetch",
        type=ComponentType.API,
        determinism=Determinism.TRANSFORM,
        effect=Effect.READ,
        inputs=[Binding(name="url", kind=BindingKind.REFERENCE, value="${node_ghost.url}", confidence=BindingConfidence.EXACT)],
    )
    end = make_control_component("node_end", ComponentType.END)
    wf = _workflow(
        [start, search, fetch, end],
        [
            Connection(source="node_start", target="node_search"),
            Connection(source="node_search", target="node_fetch"),
            Connection(source="node_fetch", target="node_end"),
        ],
    )
    issues = validate_workflow(wf)
    assert any("dangling reference" in issue and "node_ghost" in issue for issue in issues)


def test_reference_to_undeclared_output_field():
    start = make_control_component("node_start", ComponentType.START)
    search = _search_component()  # declares only "url"
    fetch = Component(
        id="node_fetch",
        name="fetch",
        type=ComponentType.API,
        determinism=Determinism.TRANSFORM,
        effect=Effect.READ,
        inputs=[Binding(name="q", kind=BindingKind.REFERENCE, value="${node_search.query}", confidence=BindingConfidence.EXACT)],
    )
    end = make_control_component("node_end", ComponentType.END)
    wf = _workflow(
        [start, search, fetch, end],
        [
            Connection(source="node_start", target="node_search"),
            Connection(source="node_search", target="node_fetch"),
            Connection(source="node_fetch", target="node_end"),
        ],
    )
    issues = validate_workflow(wf)
    assert any("declared output" in issue for issue in issues)


def test_forward_reference_reported():
    start = make_control_component("node_start", ComponentType.START)
    # node_a (position 1) references node_b (position 2) — a forward reference.
    node_a = Component(
        id="node_a",
        name="a",
        type=ComponentType.API,
        determinism=Determinism.TRANSFORM,
        effect=Effect.READ,
        inputs=[Binding(name="x", kind=BindingKind.REFERENCE, value="${node_b.out}", confidence=BindingConfidence.EXACT)],
    )
    node_b = Component(
        id="node_b",
        name="b",
        type=ComponentType.API,
        determinism=Determinism.TRANSFORM,
        effect=Effect.READ,
        outputs=[OutputField(name="out", type="str")],
    )
    end = make_control_component("node_end", ComponentType.END)
    wf = _workflow(
        [start, node_a, node_b, end],
        [
            Connection(source="node_start", target="node_a"),
            Connection(source="node_a", target="node_b"),
            Connection(source="node_b", target="node_end"),
        ],
    )
    issues = validate_workflow(wf)
    assert any("forward reference" in issue for issue in issues)


def test_parameter_reference_must_be_declared():
    start = make_control_component("node_start", ComponentType.START)
    node_a = Component(
        id="node_a",
        name="a",
        type=ComponentType.API,
        determinism=Determinism.TRANSFORM,
        effect=Effect.READ,
        inputs=[Binding(name="topic", kind=BindingKind.PARAMETER, value="${node_start.topic}")],
    )
    end = make_control_component("node_end", ComponentType.END)
    wf = _workflow(
        [start, node_a, end],
        [Connection(source="node_start", target="node_a"), Connection(source="node_a", target="node_end")],
        parameters=[],  # "topic" never declared
    )
    issues = validate_workflow(wf)
    assert any("parameter" in issue and "topic" in issue for issue in issues)

    wf_ok = _workflow(
        [start, node_a, end],
        [Connection(source="node_start", target="node_a"), Connection(source="node_a", target="node_end")],
        parameters=[Parameter(name="topic", type="str", captured_value="Q3 revenue")],
    )
    assert validate_workflow(wf_ok) == []


def test_loop_body_integrity_unknown_member():
    start = make_control_component("node_start", ComponentType.START)
    loop_var = Binding(name="arr_loop_var", kind=BindingKind.REFERENCE, value="${node_search.results}", confidence=BindingConfidence.EXACT)
    search = Component(
        id="node_search", name="search", type=ComponentType.API,
        determinism=Determinism.TRANSFORM, effect=Effect.READ,
        outputs=[OutputField(name="results", type="list")],
    )
    loop = Component(
        id="node_loop", name="loop", type=ComponentType.LOOP,
        determinism=Determinism.TRANSFORM, effect=Effect.READ,
        inputs=[loop_var], configs={"loop_body": ["node_missing_body"]},
    )
    end = make_control_component("node_end", ComponentType.END)
    wf = _workflow(
        [start, search, loop, end],
        [
            Connection(source="node_start", target="node_search"),
            Connection(source="node_search", target="node_loop"),
            Connection(source="node_loop", target="node_end"),
        ],
    )
    issues = validate_workflow(wf)
    assert any("loop" in issue and "node_missing_body" in issue for issue in issues)


def test_cycle_outside_loop_body_reported():
    start = make_control_component("node_start", ComponentType.START)
    node_a = Component(id="node_a", name="a", type=ComponentType.API, determinism=Determinism.TRANSFORM, effect=Effect.READ)
    node_b = Component(id="node_b", name="b", type=ComponentType.API, determinism=Determinism.TRANSFORM, effect=Effect.READ)
    end = make_control_component("node_end", ComponentType.END)
    wf = _workflow(
        [start, node_a, node_b, end],
        [
            Connection(source="node_start", target="node_a"),
            Connection(source="node_a", target="node_b"),
            Connection(source="node_b", target="node_a"),  # back-edge, not inside any loop body
            Connection(source="node_b", target="node_end"),
        ],
    )
    issues = validate_workflow(wf)
    assert any("cycle" in issue for issue in issues)


def test_loop_body_back_edge_is_not_a_reported_cycle():
    start = make_control_component("node_start", ComponentType.START)
    loop_var = Binding(name="arr_loop_var", kind=BindingKind.REFERENCE, value="${node_search.results}", confidence=BindingConfidence.EXACT)
    search = Component(
        id="node_search", name="search", type=ComponentType.API,
        determinism=Determinism.TRANSFORM, effect=Effect.READ,
        outputs=[OutputField(name="results", type="list")],
    )
    body_a = Component(id="node_body_a", name="body_a", type=ComponentType.API, determinism=Determinism.TRANSFORM, effect=Effect.READ)
    body_b = Component(id="node_body_b", name="body_b", type=ComponentType.API, determinism=Determinism.TRANSFORM, effect=Effect.READ)
    loop = Component(
        id="node_loop", name="loop", type=ComponentType.LOOP,
        determinism=Determinism.TRANSFORM, effect=Effect.READ,
        inputs=[loop_var], configs={"loop_body": ["node_body_a", "node_body_b"]},
    )
    end = make_control_component("node_end", ComponentType.END)
    wf = _workflow(
        [start, search, loop, body_a, body_b, end],
        [
            Connection(source="node_start", target="node_search"),
            Connection(source="node_search", target="node_loop"),
            Connection(source="node_loop", target="node_body_a"),
            Connection(source="node_body_a", target="node_body_b"),
            Connection(source="node_body_b", target="node_body_a"),  # loop-internal back-edge — allowed
            Connection(source="node_loop", target="node_end"),
        ],
    )
    issues = validate_workflow(wf)
    assert not any("cycle" in issue for issue in issues)


def test_assert_valid_raises_with_all_issues():
    only_end = make_control_component("node_end", ComponentType.END)
    wf = _workflow([only_end], [Connection(source="node_start", target="node_missing")])
    try:
        assert_valid(wf)
        assert False, "expected WorkflowValidationError"
    except WorkflowValidationError as exc:
        # both the missing-start issue and the dangling-connection issues should be present
        assert len(exc.issues) >= 2
