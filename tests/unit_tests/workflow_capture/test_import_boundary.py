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
                if alias.name == "*":
                    # A wildcard import exposes whatever the target module
                    # currently binds — which can include names a module
                    # `__getattr__` lazily imports from somewhere else
                    # entirely. Verified concretely against this repo:
                    # jiuwenswarm/server/__init__.py declares
                    # `__all__ = ["JiuWenSwarm", "SkillManager"]` and its
                    # `__getattr__` imports both from
                    # `jiuwenswarm.server.runtime.*` on first access — so
                    # `from jiuwenswarm.server import *` reaches the
                    # forbidden namespace with no literal "runtime" string
                    # anywhere in this package's own source (an independent
                    # Codex code review of this file found this). This
                    # cannot be resolved by reading only this package's
                    # files, so any wildcard import from FORBIDDEN or an
                    # ancestor of it fails closed unconditionally.
                    is_forbidden_or_ancestor = resolved_base == FORBIDDEN or FORBIDDEN.startswith(
                        f"{resolved_base}."
                    )
                    if is_forbidden_or_ancestor:
                        violations.append(
                            f"{label}: wildcard import from '{resolved_base}' can expose {FORBIDDEN} "
                            f"through that module's own __getattr__/__all__ — cannot statically verify"
                        )
                    continue
                full = f"{resolved_base}.{alias.name}" if resolved_base else alias.name
                if _touches_forbidden(full):
                    violations.append(f"{label}: from {resolved_base} import {alias.name}")

        elif isinstance(node, ast.Call):
            func = node.func
            # Matches `importlib.import_module(...)`, `il.import_module(...)`
            # (any base — a renamed import), `from importlib import
            # import_module; import_module(...)`, bare `__import__(...)`,
            # and `builtins.__import__(...)` / `x.__import__(...)`. Matching
            # by attribute/name alone, not restricting the base object, is
            # deliberately broad: a false positive just means an unrelated
            # function happens to share one of these names, which is cheap
            # to acknowledge; a false negative defeats the check.
            is_import_module = (isinstance(func, ast.Attribute) and func.attr == "import_module") or (
                isinstance(func, ast.Name) and func.id == "import_module"
            )
            is_dunder_import = (isinstance(func, ast.Attribute) and func.attr == "__import__") or (
                isinstance(func, ast.Name) and func.id == "__import__"
            )
            if is_import_module or is_dunder_import:
                violations.append(
                    _check_dynamic_import_call(node, label, is_dunder_import=is_dunder_import)
                )

    return [v for v in violations if v is not None]


# Positional argument order for each dynamic-import form, so a keyword and
# a positional value for the same parameter resolve to the same slot.
_IMPORT_MODULE_PARAMS = ("name", "package")
_DUNDER_IMPORT_PARAMS = ("name", "globals", "locals", "fromlist", "level")


def _get_call_arg(node: ast.Call, params: tuple[str, ...], param: str) -> ast.expr | None:
    if param in params:
        index = params.index(param)
        if index < len(node.args):
            return node.args[index]
    for kw in node.keywords:
        if kw.arg == param:
            return kw.value
    return None


def _literal_string_list_items(expr: ast.expr) -> list[str] | None:
    """String literals in a literal list/tuple, or None if `expr` isn't
    statically enumerable as one (a variable, a call result, ...)."""
    if not isinstance(expr, (ast.Tuple, ast.List)):
        return None
    items: list[str] = []
    for elt in expr.elts:
        if not (isinstance(elt, ast.Constant) and isinstance(elt.value, str)):
            return None
        items.append(elt.value)
    return items


def _check_dynamic_import_call(node: ast.Call, label: str, *, is_dunder_import: bool) -> str | None:
    """Fail closed: an `importlib.import_module(...)` / `__import__(...)`
    call (however it was named or aliased at the call site — see the
    matching logic above) is only cleared when every argument that could
    reach a forbidden module is a plain, statically-checkable literal.
    Anything else — string concatenation, an f-string, a variable, a
    relative form, a non-literal `fromlist=` or `level=` — could be
    composing a forbidden path in a way this detector cannot statically
    prove safe, so it is flagged rather than silently passed. This is
    deliberately broader than "detect known-bad" (an independent Codex
    code review of this file found three rounds of gaps: string
    concatenation, an f-string, a relative dynamic import,
    `import_module(name=...)` as a keyword, `import_module` called by its
    bare aliased name, and — the sharpest one — `__import__(name,
    fromlist=[...])`, which is genuinely how `from x import y` is
    implemented under the hood, so a `fromlist` item can reach the
    forbidden module even when `name` alone does not) — a false positive
    here just means a legitimate dynamic import needs a `# noqa`-style
    acknowledgement; a false negative defeats the one test standing
    between the package and a coupling mistake becoming permanent.

    `import_module`'s real signature is `(name, package=None)` — no
    fromlist, no level, so only the target itself matters.
    `__import__`'s real signature is `(name, globals=None, locals=None,
    fromlist=(), level=0)` — `fromlist` can reach a submodule of `name`
    exactly as `from name import fromlist_item` would (verified:
    `__import__("email", fromlist=["message"])` makes `email.message`
    importable), and a nonzero `level` makes `name` relative the same way
    `ImportFrom.level` does, so a non-dotted `name` string is not
    necessarily absolute for this form.

    A `**mapping` unpacked into the call (`import_module(**{"name": ...})`)
    is a plain, working Python call form — verified directly — but shows
    up as `ast.keyword(arg=None, value=<the dict>)`, which every
    `arg == "name"` lookup above silently skips, making the call look
    argument-less and therefore "safe" by the zero-arg rule below. Any
    `**`-unpack on a matched call is failed closed unconditionally, before
    any other argument is even inspected, rather than trying to statically
    evaluate the unpacked mapping.
    """
    if any(kw.arg is None for kw in node.keywords):
        return f"{label}: dynamic import call unpacks a mapping (**...) — cannot statically verify its target"

    params = _DUNDER_IMPORT_PARAMS if is_dunder_import else _IMPORT_MODULE_PARAMS
    target = _get_call_arg(node, params, "name")

    if target is None:
        # No positional target and no name= keyword — e.g. a zero-arg call
        # is a TypeError at runtime before anything is ever imported.
        return None

    if not (isinstance(target, ast.Constant) and isinstance(target.value, str)):
        return f"{label}: dynamic import with a non-literal target — cannot statically verify it avoids {FORBIDDEN}"

    name = target.value
    if name.startswith("."):
        return f"{label}: dynamic import with a relative target '{name}' — cannot statically verify"

    if is_dunder_import:
        level_arg = _get_call_arg(node, params, "level")
        if level_arg is not None and not (isinstance(level_arg, ast.Constant) and level_arg.value == 0):
            return f"{label}: dynamic __import__ with a non-zero or non-literal level= — cannot statically verify"

        fromlist_arg = _get_call_arg(node, params, "fromlist")
        is_empty_fromlist = fromlist_arg is None or (
            isinstance(fromlist_arg, (ast.Tuple, ast.List)) and not fromlist_arg.elts
        ) or (isinstance(fromlist_arg, ast.Constant) and fromlist_arg.value in ((), [], None))
        if not is_empty_fromlist:
            items = _literal_string_list_items(fromlist_arg)
            if items is None:
                return f"{label}: dynamic __import__ with a non-literal fromlist= — cannot statically verify"
            for item in items:
                full = f"{name}.{item}"
                if _touches_forbidden(full):
                    return f"{label}: dynamic __import__ of '{name}' with fromlist reaching '{full}'"

    if _touches_forbidden(name):
        return f"{label}: dynamic import of '{name}'"
    return None


def _violations_in_file(path: Path, src_root: Path) -> list[str]:
    dotted, is_package = _module_dotted_name(path.relative_to(src_root))
    return _violations_in_source(path.read_text(encoding="utf-8"), dotted, is_package, label=str(path))


# -- the real guardrail -------------------------------------------------


def _iter_python_files(root: Path):
    """Like `root.rglob('*.py')`, but also descends into symlinked
    directories — `Path.rglob` does not by default (verified: it uses
    `os.scandir` without symlink-following), so a symlinked subpackage
    under `jiuwenswarm/workflow_capture/` could hide a forbidden import
    from this guard entirely (an independent Codex code review of this
    file found this gap). Cycle-protected via each directory's resolved
    real path, so a self-referential symlink can't loop forever.
    """
    visited_real_dirs: set[Path] = set()

    def walk(dir_path: Path):
        real = dir_path.resolve()
        if real in visited_real_dirs:
            return
        visited_real_dirs.add(real)
        for entry in sorted(dir_path.iterdir()):
            if entry.is_dir():
                yield from walk(entry)
            elif entry.suffix == ".py":
                yield entry

    yield from walk(root)


def test_no_module_imports_server_runtime():
    package_root = Path(workflow_capture.__file__).resolve().parent
    src_root = package_root.parent.parent  # the directory containing jiuwenswarm/

    violations: list[str] = []
    for path in _iter_python_files(package_root):
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


def test_detects_dynamic_import_built_from_string_concatenation():
    # A plain-literal-only check would miss this: node.args[0] is an
    # ast.BinOp, not an ast.Constant, so the forbidden path never appears
    # as a literal anywhere in the source.
    source = 'importlib.import_module("jiuwenswarm.server." + "runtime")\n'
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False)


def test_detects_dynamic_import_via_fstring():
    source = 'importlib.import_module(f"jiuwenswarm.server.runtime")\n'
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False)


def test_detects_dynamic_import_with_relative_target():
    # A relative dynamic import needs `package=` to resolve; the target
    # cannot be checked without replicating that resolution, so it fails
    # closed rather than being silently cleared.
    source = 'importlib.import_module("..server.runtime", package="jiuwenswarm.workflow_capture.store")\n'
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False)


def test_does_not_flag_dynamic_import_of_an_unrelated_absolute_literal():
    # Fail-closed on non-literal targets must not become fail-closed on
    # every dynamic import — an ordinary, unrelated, plain-literal dynamic
    # import is still cleared.
    source = 'importlib.import_module("pydantic")\n'
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False) == []


def test_detects_dynamic_import_with_name_as_keyword_argument():
    # import_module's real signature is (name, package=None) — name= is a
    # legitimate keyword form, not just node.args[0].
    source = 'importlib.import_module(name="jiuwenswarm.server.runtime")\n'
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False)


def test_detects_aliased_import_module_called_by_bare_name():
    # `from importlib import import_module` then calling it unqualified —
    # the call site is `ast.Name(id="import_module")`, not an Attribute on
    # a module object, so a check scoped to `.import_module` alone misses it.
    source = 'from importlib import import_module\nimport_module("jiuwenswarm.server.runtime")\n'
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False)


def test_detects_builtins_dunder_import_attribute_form():
    source = 'import builtins\nbuiltins.__import__("jiuwenswarm.server.runtime")\n'
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False)


def test_zero_arg_dynamic_import_call_is_not_flagged():
    # import_module() with neither a positional nor a name= keyword is a
    # TypeError before anything is ever imported — nothing to flag.
    source = "importlib.import_module()\n"
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False) == []


def test_detects_kwargs_unpacking_dynamic_import():
    # import_module(**{"name": ...}) is a real, working call — verified
    # directly — but shows up as ast.keyword(arg=None, value=<dict>), which
    # every `kw.arg == "name"` lookup silently skips, making the call look
    # argument-less and falsely "safe" by the zero-arg rule.
    source = 'importlib.import_module(**{"name": "jiuwenswarm.server.runtime"})\n'
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False)


def test_detects_dunder_import_kwargs_unpacking_too():
    source = '__import__(**{"name": "jiuwenswarm.server.runtime"})\n'
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False)


def test_detects_dunder_import_fromlist_reaching_the_forbidden_submodule():
    # __import__(name, fromlist=[...]) is how `from name import x` is
    # actually implemented — a fromlist item can reach the forbidden
    # module even though the base `name` alone ("jiuwenswarm.server")
    # would not match.
    source = '__import__("jiuwenswarm.server", fromlist=["runtime"])\n'
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False)


def test_detects_dunder_import_with_non_literal_fromlist():
    source = "fromlist = compute_fromlist()\n__import__('jiuwenswarm.server', fromlist=fromlist)\n"
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False)


def test_detects_dunder_import_with_nonzero_level():
    # A nonzero level makes `name` relative, the same way ImportFrom.level
    # does — a plain, non-dot-prefixed name string is not necessarily
    # absolute for this call form.
    source = "__import__('server', level=1)\n"
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False)


def test_does_not_flag_dunder_import_with_empty_fromlist_and_zero_level():
    source = "__import__('pydantic', fromlist=[], level=0)\n"
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False) == []


def test_does_not_flag_import_module_call_shaped_like_fromlist_evasion():
    # import_module has no fromlist/level parameters at all (real signature
    # is (name, package=None)) — a "fromlist" keyword on it is just an
    # unrelated TypeError at runtime, not a forbidden-module reach, and
    # must not be treated as the __import__-specific evasion.
    source = 'importlib.import_module("pydantic", fromlist=["anything"])\n'
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False) == []


def test_symlinked_subpackage_is_not_invisible_to_the_walker(tmp_path):
    # Path.rglob does not descend into symlinked directories by default —
    # verified separately — so a symlinked subpackage containing a real
    # violation would otherwise be silently skipped by the only test
    # standing between the package and this exact mistake.
    real_dir = tmp_path / "real_vendored"
    real_dir.mkdir()
    (real_dir / "leaky.py").write_text("import jiuwenswarm.server.runtime\n", encoding="utf-8")

    package_root = tmp_path / "jiuwenswarm" / "workflow_capture"
    package_root.mkdir(parents=True)
    (package_root / "linked_vendored").symlink_to(real_dir, target_is_directory=True)

    src_root = tmp_path
    found = list(_iter_python_files(package_root))
    assert any(p.name == "leaky.py" for p in found)

    violations: list[str] = []
    for path in found:
        violations.extend(_violations_in_file(path, src_root))
    assert violations


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


def test_detects_wildcard_import_from_direct_ancestor():
    # The exact concrete case: jiuwenswarm/server/__init__.py declares
    # __all__ = ["JiuWenSwarm", "SkillManager"] and lazily imports both
    # from jiuwenswarm.server.runtime.* in __getattr__.
    source = "from jiuwenswarm.server import *\n"
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False)


def test_detects_wildcard_import_from_a_higher_ancestor():
    source = "from jiuwenswarm import *\n"
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False)


def test_detects_wildcard_import_from_forbidden_itself():
    source = "from jiuwenswarm.server.runtime import *\n"
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False)


def test_does_not_flag_wildcard_import_from_an_unrelated_package():
    source = "from pydantic import *\n"
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False) == []


def test_does_not_flag_wildcard_import_from_a_sibling_of_the_forbidden_prefix():
    # "jiuwenswarm.server.sandbox" is neither FORBIDDEN, an ancestor of it,
    # nor a descendant of it — it's a sibling under the same parent, which
    # the ancestor check must not conflate with actually being an ancestor.
    source = "from jiuwenswarm.server.sandbox import *\n"
    assert _violations_in_source(source, "jiuwenswarm.workflow_capture.store.filesystem", False) == []
