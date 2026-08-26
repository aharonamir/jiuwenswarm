"""Whole-graph validation for `SavedWorkflow`.

Per-component and per-binding rules that only need the object itself are
pydantic validators in `ir/models.py`. Rules that need the rest of the graph —
dangling references, precedence, cycles, loop-body membership — live here.
"""

from __future__ import annotations

from .models import (
    BindingKind,
    ComponentType,
    SavedWorkflow,
    PARAMETER_REF_PATTERN,
    REFERENCE_PATTERN,
)


class WorkflowValidationError(ValueError):
    """Raised by `assert_valid`; carries every issue found, not just the first."""

    def __init__(self, issues: list[str]) -> None:
        self.issues = issues
        super().__init__("; ".join(issues))


def _loop_body_ids(workflow: SavedWorkflow) -> dict[str, str]:
    """Map component id -> id of the jiuwen.loop component whose body contains it."""
    membership: dict[str, str] = {}
    for component in workflow.components:
        if component.type != ComponentType.LOOP:
            continue
        for body_id in component.configs.get("loop_body", []):
            membership[body_id] = component.id
    return membership


def validate_workflow(workflow: SavedWorkflow) -> list[str]:
    """Return every validation issue found. Empty list means valid."""
    issues: list[str] = []
    ids = [c.id for c in workflow.components]
    id_set = set(ids)

    if len(id_set) != len(ids):
        seen: set[str] = set()
        dupes: set[str] = set()
        for cid in ids:
            if cid in seen:
                dupes.add(cid)
            seen.add(cid)
        issues.append(f"duplicate component ids: {sorted(dupes)}")

    starts = [c for c in workflow.components if c.type == ComponentType.START]
    ends = [c for c in workflow.components if c.type == ComponentType.END]
    if len(starts) != 1:
        issues.append(f"expected exactly one jiuwen.start component, found {len(starts)}")
    if len(ends) < 1:
        issues.append("expected at least one jiuwen.end component")

    for conn in workflow.connections:
        if conn.source not in id_set:
            issues.append(f"connection source '{conn.source}' references an unknown component")
        if conn.target not in id_set:
            issues.append(f"connection target '{conn.target}' references an unknown component")

    components_by_id = {c.id: c for c in workflow.components}
    index_of = {c.id: i for i, c in enumerate(workflow.components)}
    loop_membership = _loop_body_ids(workflow)
    parameter_names = {p.name for p in workflow.parameters}

    # Loop body integrity: every referenced body id must exist in the graph.
    for component in workflow.components:
        if component.type != ComponentType.LOOP:
            continue
        for body_id in component.configs.get("loop_body", []):
            if body_id not in id_set:
                issues.append(
                    f"loop '{component.id}' body references unknown component '{body_id}'"
                )

    for component in workflow.components:
        for binding in component.inputs:
            if binding.kind == BindingKind.PARAMETER:
                match = PARAMETER_REF_PATTERN.match(binding.value) if isinstance(binding.value, str) else None
                if match and match.group(1) not in parameter_names:
                    issues.append(
                        f"{component.id}.{binding.name}: parameter "
                        f"'{match.group(1)}' is not declared in workflow parameters"
                    )
                continue

            if binding.kind != BindingKind.REFERENCE:
                continue

            match = REFERENCE_PATTERN.match(binding.value) if isinstance(binding.value, str) else None
            if not match:
                # Format is already enforced by the Binding model validator;
                # unreachable in practice, but fail loudly rather than KeyError below.
                issues.append(f"{component.id}.{binding.name}: malformed reference '{binding.value}'")
                continue

            target_id, field = match.group(1), match.group(2)

            if target_id not in id_set:
                issues.append(
                    f"{component.id}.{binding.name}: dangling reference to unknown component '{target_id}'"
                )
                continue

            target = components_by_id[target_id]
            declared_outputs = {o.name for o in target.outputs}
            if field not in declared_outputs:
                issues.append(
                    f"{component.id}.{binding.name}: reference field '{field}' is not "
                    f"a declared output of '{target_id}'"
                )

            same_loop_body = (
                loop_membership.get(target_id) is not None
                and loop_membership.get(target_id) == loop_membership.get(component.id)
            )
            if not same_loop_body and index_of[target_id] >= index_of[component.id]:
                issues.append(
                    f"{component.id}.{binding.name}: forward reference to '{target_id}' "
                    f"(target must precede the referencing component)"
                )

    issues.extend(_find_cycles_outside_loop_bodies(workflow, loop_membership))

    return issues


def _find_cycles_outside_loop_bodies(
    workflow: SavedWorkflow, loop_membership: dict[str, str]
) -> list[str]:
    """DFS cycle detection over `connections`, excluding edges where both
    endpoints belong to the same loop body — a loop body is expected to
    contain a back-edge (it is, structurally, a cycle) and that is by
    design, not the defect this check exists to catch.
    """
    adjacency: dict[str, list[str]] = {}
    for conn in workflow.connections:
        if loop_membership.get(conn.source) is not None and (
            loop_membership.get(conn.source) == loop_membership.get(conn.target)
        ):
            continue
        adjacency.setdefault(conn.source, []).append(conn.target)

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {c.id: WHITE for c in workflow.components}
    issues: list[str] = []

    def dfs(node: str, path: list[str]) -> bool:
        color[node] = GRAY
        for neighbour in adjacency.get(node, []):
            if neighbour not in color:
                continue
            if color[neighbour] == GRAY:
                cycle = path[path.index(neighbour):] + [neighbour] if neighbour in path else [node, neighbour]
                issues.append(f"cycle outside loop bodies: {' -> '.join(cycle)}")
                return True
            if color[neighbour] == WHITE and dfs(neighbour, path + [neighbour]):
                return True
        color[node] = BLACK
        return False

    for component in workflow.components:
        if color[component.id] == WHITE:
            dfs(component.id, [component.id])

    return issues


def assert_valid(workflow: SavedWorkflow) -> None:
    issues = validate_workflow(workflow)
    if issues:
        raise WorkflowValidationError(issues)
