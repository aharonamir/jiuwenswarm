from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from jiuwenswarm.workflow_capture.ir.models import (
    CaptureReport,
    Component,
    ComponentType,
    Connection,
    Determinism,
    Effect,
    RunMode,
    RunOutcome,
    RunRecord,
    SavedWorkflow,
    Viability,
)
from jiuwenswarm.workflow_capture.store.filesystem import WorkflowStore


def make_control_component(component_id: str, component_type: ComponentType) -> Component:
    return Component(
        id=component_id,
        name=component_id,
        type=component_type,
        determinism=Determinism.TRANSFORM,
        effect=Effect.READ,
    )


def make_capture_report(*, viability: Viability = Viability.VIABLE, viability_reason: str = "") -> CaptureReport:
    return CaptureReport(
        steps_captured=2,
        steps_skipped=0,
        viability=viability,
        viability_reason=viability_reason or ("n/a" if viability == Viability.VIABLE else "not viable"),
    )


def make_saved_workflow(
    workflow_id: str = "research-digest",
    *,
    version: int = 1,
    extra_components: list[Component] | None = None,
    extra_connections: list[Connection] | None = None,
    parameters: list | None = None,
) -> SavedWorkflow:
    start = make_control_component("node_start", ComponentType.START)
    end = make_control_component("node_end", ComponentType.END)
    components = [start, *(extra_components or []), end]
    connections = [
        Connection(source="node_start", target=(extra_components[0].id if extra_components else "node_end")),
        *(extra_connections or []),
    ]
    if extra_components:
        connections.append(Connection(source=extra_components[-1].id, target="node_end"))

    return SavedWorkflow(
        workflow_id=workflow_id,
        workflow_name=workflow_id.replace("-", " ").title(),
        description="test fixture",
        version=version,
        components=components,
        connections=connections,
        parameters=parameters or [],
        source_session_id="session-123",
        captured_at=datetime.now(UTC),
        capture_report=make_capture_report(),
    )


def make_run_record(
    *,
    run_id: str = "run-1",
    workflow_version: int = 1,
    mode: RunMode = RunMode.LIVE,
    outcome: RunOutcome = RunOutcome.SUCCESS,
    result_consistent: bool | None = None,
    promotion_basis=None,
    acting_user: str | None = None,
    source_run_id: str | None = None,
) -> RunRecord:
    # manual_ack requires an audit trail (acting_user + source_run_id, see
    # RunRecord's validator) — deliberately no default-filling here, so a
    # caller testing the "missing audit field" failure can pass None and
    # have it actually mean None.
    return RunRecord(
        run_id=run_id,
        workflow_version=workflow_version,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        mode=mode,
        outcome=outcome,
        result_consistent=result_consistent,
        promotion_basis=promotion_basis,
        acting_user=acting_user,
        source_run_id=source_run_id,
        capture_algorithm_version="1",
        ir_schema_version="1",
    )


@pytest.fixture
def store(tmp_path: Path) -> WorkflowStore:
    return WorkflowStore(tmp_path / "workflows")
