"""T037a — characterization tests pinning agent-core's actual behavior.

`PLAN.md` assumptions 3-5 (loop-body edge placement, branch default
ordering, deferred branch registration, three inherited "agent-core
patches") were sourced to agent-studio's `ir_converter.py`, which is not
present in this checkout. Review Round 3 downgraded them to
`[UNVERIFIABLE IN THIS CHECKOUT]` and converted them into this task: don't
inherit a converter's constraints blind — exercise agent-core's real,
installed workflow-construction API directly and pin what it actually does.
`ir_to_workflow.py` (T038) depends on what these tests prove, not on
`ir_converter.py`.

These tests exercise `openjiuwen` (agent-core), not `jiuwenswarm`. They are
read-only characterization of a dependency's behavior — nothing here tests
`jiuwenswarm.workflow_capture` itself, and none of it lives under
`jiuwenswarm/workflow_capture/`, so the import-boundary rule (T004) does not
apply to this file.

**Assumption 5 finding, recorded here rather than as a test because there is
nothing to test against:** a repo-wide `grep` for `loop_body_session_cleanup`
and `parallel_branch_grouping` (the other two of the three named patches)
found zero occurrences anywhere in this agent-core checkout — not in source,
not in tests, not in comments. There is no code to characterize and no
description of what either patch would touch, so no test is written for
them; inventing one would fabricate verification of something unobserved.
`nested_branch_barrier` is different: it maps directly to native,
already-tested functionality — `Graph._resolve_barrier_groups` in
`openjiuwen/core/graph/graph.py`, exercised by agent-core's own
`test_nested_branch_barrier_cnf_merge` in
`tests/unit_tests/core/workflow/test_workflow.py`. `test_branch_targets_*`
below exercises the same mechanism (`Graph.branch_targets`, the input to
`_resolve_barrier_groups`) directly. This is evidence AGAINST assumption 5's
premise that a patch is needed for nested-branch barrier resolution — the
current agent-core appears to have this natively, un-patched.
"""

from __future__ import annotations


from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.workflow import (
    BranchComponent,
    End,
    Input,
    LoopComponent,
    LoopGroup,
    Output,
    Start,
    Workflow,
    WorkflowComponent,
    create_workflow_session,
)
from openjiuwen.core.workflow.components import Session

# NOTE: no module-level `pytestmark = pytest.mark.asyncio` here — this file
# mixes sync structural-inspection tests with async execution tests, and
# pytest.ini's `--asyncio-mode=auto` already async-marks only the `async def`
# ones. Applying the mark file-wide (agent-core's own test convention, which
# only mixes async tests) breaks the sync tests under this pytest-asyncio
# version.


class _PassThrough(WorkflowComponent):
    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        return inputs


class _AddTen(WorkflowComponent):
    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        return {"result": inputs["source"] + 10}


class _Marker(WorkflowComponent):
    """Records that it ran, into a list supplied at construction — used to
    observe which of several mutually-exclusive branch targets actually
    executed, without depending on how the `End` component's output schema
    resolves an unreached predecessor.
    """

    def __init__(self, name: str, sink: list[str]) -> None:
        super().__init__()
        self._name = name
        self._sink = sink

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        self._sink.append(self._name)
        return {"marker": self._name}


def _endpoints(edges) -> set[str]:
    eps: set[str] = set()
    for src, tgt in edges:
        eps.update(src if isinstance(src, list) else [src])
        eps.add(tgt)
    return eps


# -- assumption 3: loop body-to-body edges live on the LoopGroup, not root --


def _build_looped_workflow() -> tuple[Workflow, LoopGroup]:
    flow = Workflow()
    flow.set_start_comp("start", Start(), inputs_schema={"input_num": "${num}"})
    flow.set_end_comp("end", End(), inputs_schema={"end_out": "${loop}"})

    loop_group = LoopGroup()
    loop_group.add_workflow_comp("loop_1", _AddTen(), inputs_schema={"source": "${loop.index}"})
    loop_group.add_workflow_comp("loop_2", _AddTen(), inputs_schema={"source": "${loop_1.result}"})
    loop_group.start_nodes(["loop_1"])
    loop_group.end_nodes(["loop_2"])
    loop_group.add_connection("loop_1", "loop_2")

    loop_component = LoopComponent(loop_group, output_schema={"l_out": "${loop_2.result}"})
    flow.add_workflow_comp("loop", loop_component, inputs_schema={"loop_type": "number", "loop_number": 3})

    flow.add_connection("start", "loop")
    flow.add_connection("loop", "end")
    return flow, loop_group


def test_loop_body_connection_is_registered_on_loop_group_not_root():
    flow, loop_group = _build_looped_workflow()

    root_edges = flow._internal._graph.edges
    body_edges = loop_group._graph.edges

    assert ("loop_1", "loop_2") in body_edges
    assert ("loop_1", "loop_2") not in root_edges
    # The root graph is not merely missing this one edge — it has no
    # knowledge of the loop body's internal node ids at all.
    assert {"loop_1", "loop_2"}.isdisjoint(_endpoints(root_edges))
    assert {"loop_1", "loop_2"} <= _endpoints(body_edges)


async def test_loop_body_isolation_does_not_break_execution():
    # The structural separation above is only useful if the loop still runs
    # correctly despite it — confirm end-to-end, not just structurally.
    flow, _ = _build_looped_workflow()
    result = await flow.invoke({"num": 0}, session=create_workflow_session())
    # loop_number=3 -> index 0,1,2 -> loop_1 = 10,11,12 -> loop_2 = 20,21,22
    assert result.result["output"]["end_out"]["l_out"] == [20, 21, 22]


# -- assumption 4: register_branch_targets snapshots at add_workflow_comp --


def test_branch_targets_snapshot_excludes_branches_added_after_registration():
    # register_branch_targets fires inside BranchComponent.add_component,
    # called synchronously by workflow.add_workflow_comp — it reads
    # router.all_targets AT THAT MOMENT and stores a plain set, not a live
    # view. A branch wired onto the SAME component afterward is invisible
    # to graph.branch_targets even though the router itself knows about it.
    flow = Workflow()
    flow.set_start_comp("start", Start(), inputs_schema={"input": "${data}"})

    branch = BranchComponent()
    branch.add_branch(condition=lambda: True, target=["a"])
    branch.add_branch(condition=lambda: False, target=["b"])
    # Two branches wired (len(all_targets) == 2 > 1) -> registration fires here.
    flow.add_workflow_comp("branch", branch)

    # A third branch, added AFTER registration — assumption 4's exact mistake.
    branch.add_branch(condition=lambda: False, target=["c"])

    flow.add_workflow_comp("a", _PassThrough(), inputs_schema={"data": "${start.input}"})
    flow.add_workflow_comp("b", _PassThrough(), inputs_schema={"data": "${start.input}"})
    flow.add_workflow_comp("c", _PassThrough(), inputs_schema={"data": "${start.input}"})
    flow.set_end_comp("end", End(), inputs_schema={"end_out": "${a}"})
    flow.add_connection("start", "branch")
    flow.add_connection("a", "end")
    flow.add_connection("b", "end")
    flow.add_connection("c", "end")

    registered = flow._internal._graph.branch_targets.get("branch")
    assert registered == {"a", "b"}
    assert "c" not in registered
    # The router's own live view, asked now, DOES know about "c" — the
    # staleness is specific to the graph's snapshot, not the router.
    assert branch._router.all_targets == {"a", "b", "c"}


def test_branch_targets_includes_all_branches_wired_before_registration():
    flow = Workflow()
    flow.set_start_comp("start", Start(), inputs_schema={"input": "${data}"})

    branch = BranchComponent()
    branch.add_branch(condition=lambda: True, target=["a"])
    branch.add_branch(condition=lambda: False, target=["b"])
    branch.add_branch(condition=lambda: False, target=["c"], branch_id="default")
    # All three wired BEFORE add_workflow_comp — the correct order.
    flow.add_workflow_comp("branch", branch)

    registered = flow._internal._graph.branch_targets.get("branch")
    assert registered == {"a", "b", "c"}


# -- assumption 4 (runtime half): first-match evaluation, order decides ----


async def _run_branch_order(first_target: str, second_target: str) -> list[str]:
    executed: list[str] = []
    flow = Workflow()
    flow.set_start_comp("start", Start(), inputs_schema={"input": "${data}"})

    branch = BranchComponent()
    branch.add_branch(condition=lambda: True, target=[first_target])
    branch.add_branch(condition=lambda: True, target=[second_target])
    flow.add_workflow_comp("branch", branch)

    flow.add_workflow_comp("a", _Marker("a", executed), inputs_schema={"data": "${start.input}"})
    flow.add_workflow_comp("b", _Marker("b", executed), inputs_schema={"data": "${start.input}"})
    flow.set_end_comp("end", End(), inputs_schema={"end_out": "${branch}"})
    flow.add_connection("start", "branch")
    flow.add_connection("a", "end")
    flow.add_connection("b", "end")

    await flow.invoke({"data": "x"}, session=create_workflow_session())
    return executed


async def test_branch_order_determines_which_of_two_always_true_branches_runs():
    # Both branches are unconditionally satisfiable — BranchRouter.__call__
    # evaluates in list order and returns the first match, so whichever
    # add_branch() call came first is the one that actually runs. This is
    # the runtime consequence of "the default branch MUST be registered
    # last": a catch-all added early silently masks everything after it.
    assert await _run_branch_order("a", "b") == ["a"]
    assert await _run_branch_order("b", "a") == ["b"]
