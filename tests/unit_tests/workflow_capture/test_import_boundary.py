"""T004 — import-boundary enforcement.

`jiuwenswarm/workflow_capture/` must never import from
`jiuwenswarm.server.runtime` or anything under it — the package receives an
event list and a tool resolver from its caller; it does not go looking for
them itself (`PLAN.md` § Key decisions, "How optionality is preserved":
"the package imports nothing from the session runtime, and one adapter
file carries all runtime knowledge"). This is the seam that keeps the
package extractable if a real plugin API ever appears, and it is meant to
land before implementation, not be discovered missing after the fact.

Detection is AST-based, not a plain substring/grep check, because a
substring check is easy to evade by accident: a deep enough relative import
(`from ... import x` with enough dots to escape the package) resolves to an
absolute dotted path that a naive check on the literal source text would
never see, and `from jiuwenswarm.server import runtime` reaches the
forbidden submodule through the *imported name*, not through
`node.module`, which only says `jiuwenswarm.server`. Both forms — plus
`importlib.import_module(...)` / `__import__(...)` called with a matching
string literal — are resolved to an absolute dotted path and checked
against the same prefix.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import jiuwenswarm.workflow_capture as workflow_capture

FORBIDDEN = "jiuwenswarm.server.runtime"


def _touches_forbidden(dotted: str) -> bool:
    return dotted == FORBIDDEN or dotted.startswith(FORBIDDEN + ".")


def _module_dotted_name(rel_path: Path) -> tuple[str, bool]:
    """`rel_path` is a .py path relative to the directory containing the
    top-level `jiuwenswarm` package (e.g. `jiuwenswarm/workflow_capture/ir/models.py`).
    Returns (dotted module name, is_package).
    """
    parts = list(rel_path.with_suffix("").parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts = parts[:-1]
    return ".".join(parts), is_package


def _resolve_relative(module_dotted: str, is_package: bool, level: int, target: str | None) -> str:
    """Resolve a `from <level dots><target> import ...` relative to the
    importing module's own dotted name, per Python's relative-import rules:
    a package (`__init__.py`) resolves level=1 to itself; a plain module
    resolves level=1 to its parent package; each extra dot goes up one more.
    """
    parts = module_dotted.split(".")
    base_parts = parts if is_package else parts[:-1]
    up = level - 1
    if up > 0:
        base_parts = base_parts[: max(0, len(base_parts) - up)]
    base = ".".join(p for p in base_parts if p)
    if target:
        return f"{base}.{target}" if base else target
    return base


def _violations_in_source(source: str, module_dotted: str, is_package: bool, label: str = "<source>") -> list[str]:
    tree = ast.parse(source, filename=label)
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _touches_forbidden(alias.name):
                    violations.append(f"{label}: import {alias.name}")

        elif isinstance(node, ast.ImportFrom):
            resolved_base = (node.module or "") if node.level == 0 else _resolve_relative(
                module_dotted, is_package, node.level, node.module
            )
            if _touches_forbidden(resolved_base):
                violations.append(f"{label}: from {'.' * node.level}{node.module or ''} import ...")
                continue
            # `from package import submodule` reaches a submodule through
            # the imported NAME, not through `node.module` alone.
            for alias in node.names:
                full = f"{resolved_base}.{alias.name}" if resolved_base else alias.name
                if _touches_forbidden(full):
                    violations.append(f"{label}: from {resolved_base} import {alias.name}")

        elif isinstance(node, ast.Call):
            func = node.func
            is_dynamic_import = (isinstance(func, ast.Attribute) and func.attr == "import_module") or (
                isinstance(func, ast.Name) and func.id == "__import__"
            )
            if is_dynamic_import and node.args and isinstance(node.args[0], ast.Constant):
                value = node.args[0].value
                if isinstance(value, str) and _touches_forbidden(value):
                    violations.append(f"{label}: dynamic import of '{value}'")

    return violations


def _violations_in_file(path: Path, src_root: Path) -> list[str]:
    dotted, is_package = _module_dotted_name(path.relative_to(src_root))
    return _violations_in_source(path.read_text(encoding="utf-8"), dotted, is_package, label=str(path))


# -- the real guardrail -------------------------------------------------


def test_no_module_imports_server_runtime():
    package_root = Path(workflow_capture.__file__).resolve().parent
    src_root = package_root.parent.parent  # the directory containing jiuwenswarm/

    violations: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        violations.extend(_violations_in_file(path, src_root))

    assert not violations, (
        "jiuwenswarm/workflow_capture must not import jiuwenswarm.server.runtime "
        "(or anything under it) — the import boundary is load-bearing "
        "architecture (PLAN.md § Key decisions):\n" + "\n".join(violations)
    )


# -- the detector must actually detect, not just pass vacuously ----------


def test_detects_plain_absolute_import():
    source = "import jiuwenswarm.server.runtime\n"
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False)


def test_detects_absolute_import_of_a_submodule():
    source = "from jiuwenswarm.server.runtime.session import session_history\n"
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False)


def test_detects_submodule_reached_via_from_package_import_name():
    # `from jiuwenswarm.server import runtime` — the forbidden module is
    # reached through the imported NAME, not through node.module, which is
    # only "jiuwenswarm.server" here.
    source = "from jiuwenswarm.server import runtime\n"
    violations = _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False)
    assert violations
    assert "runtime" in violations[0]


def test_detects_deep_relative_import_escaping_the_package():
    # From jiuwenswarm.workflow_capture.store.filesystem, three dots walks
    # up past `jiuwenswarm.workflow_capture` to `jiuwenswarm` itself, then
    # imports `server.runtime` from there.
    source = "from ...server import runtime\n"
    violations = _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False)
    assert violations


def test_detects_dynamic_importlib_import_module():
    source = 'import importlib\nimportlib.import_module("jiuwenswarm.server.runtime.session_history")\n'
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False)


def test_detects_dynamic_dunder_import():
    source = '__import__("jiuwenswarm.server.runtime")\n'
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False)


def test_does_not_flag_legitimate_intra_package_relative_imports():
    # These are the real import shapes used inside the package today.
    source = textwrap.dedent(
        """
        from .models import CaptureReport, SavedWorkflow
        from ..ir.models import RunRecord
        import os
        import re
        from pydantic import BaseModel
        """
    )
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False) == []


def test_does_not_flag_unrelated_absolute_imports():
    source = "import jiuwenswarm.server\nfrom jiuwenswarm.server import config\n"
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False) == []
