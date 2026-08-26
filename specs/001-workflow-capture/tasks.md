# Tasks: Workflow Capture

**Branch**: `001-workflow-capture`
**Input**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/commands.md`

`[P]` = parallelisable (different files, no unmet dependency).
Paths are repository-relative. Tests are written before the implementation they cover.

---

## Phase 3.1: Setup

- [ ] **T001** Create package skeleton `jiuwenswarm/workflow_capture/` with `__init__.py` and empty subpackages `ir/`, `extract/`, `convert/`, `store/`, `run/`, `render/`, per `plan.md` structure
- [ ] **T002** Create test package `tests/unit_tests/workflow_capture/` with `__init__.py` and `fixtures/`
- [ ] **T003** [P] Add `workflow_capture` config block to `resources/config.yaml` — `enabled` (default true), `store_root`, `max_session_events`
- [ ] **T004** Write `tests/unit_tests/workflow_capture/test_import_boundary.py` — walks the package AST and **fails if any module imports from `jiuwenswarm.server.runtime.*`**. This enforces the seam from `plan.md`; it must be in place before implementation begins.

---

## Phase 3.2: Fixtures

Extraction is untestable without realistic recorded input. These block everything in 3.4.

- [ ] **T005** Capture a real multi-step session and store its event stream as `fixtures/session_linear.jsonl` — search → fetch → summarise → post, no loops, no dead steps
- [ ] **T006** [P] `fixtures/session_fanout.jsonl` — one search followed by N fetches over its results
- [ ] **T007** [P] `fixtures/session_dead_steps.jsonl` — contains searches returning nothing and files read but unused
- [ ] **T008** [P] `fixtures/session_react.jsonl` — a genuinely open-ended sub-task with variable iteration count
- [ ] **T009** [P] `fixtures/session_ambiguous.jsonl` — a value appearing in both the user request and a tool result
- [ ] **T009a** [P] `fixtures/session_team.jsonl` — **scope gate, must run before any team dispatch work.** Assert the parent session's `read_session_history_records()` / `read_team_history_records()` actually contains leader and teammate `chat.tool_call` / `chat.tool_result`, and whether they arrive with `truncated: True`. If they are absent, team mode leaves Phase 1 (`PLAN.md` risk 12)
- [ ] **T010** [P] `fixtures/session_no_tools.jsonl` and `fixtures/session_multi_task.jsonl` — the refusal and segmentation edge cases from `spec.md`

---

## Phase 3.3: IR foundation

- [ ] **T011** Write `tests/.../test_ir_models.py` — construction, serialisation round-trip, field validation per `data-model.md`
- [ ] **T012** Implement `ir/models.py` — `SavedWorkflow`, `Component`, `Binding`, `Parameter`, `Connection`, `CaptureReport`, `RunRecord`, `ComponentType`
- [ ] **T013** Write `tests/.../test_ir_validate.py` — dangling `${node.field}` refs, forward references, missing start/end, cycles outside loop bodies, loop body integrity
- [ ] **T014** Implement `ir/validate.py` — all rules under "Validation" in `data-model.md`
- [ ] **T015** [P] Implement `ir/models.py` validators for the conditional rules: frozen requires resolved bindings + fingerprint; agent requires allowlist + iteration budget; loop requires `loop_body` + `arr_loop_var`

---

## Phase 3.4: Extraction pipeline

The substance of the feature. Each stage is a pure function over the previous stage's output.

- [ ] **T016** Define `extract/events.py` — the injected event-source interface. Takes an iterable of event dicts; the package never opens a file or reaches into the runtime.
- [ ] **T017** Write `tests/.../test_pair.py` against `session_linear` and `session_fanout`
- [ ] **T018** Implement `extract/pair.py` — join `chat.tool_call` ↔ `chat.tool_result` on `tool_call_id`; emit ordered steps with args and results; flag null-yield results (success with empty output) per `spec.md` edge cases
- [ ] **T019** Write `tests/.../test_segment.py` against `session_multi_task` and `session_no_tools`
- [ ] **T020** Implement `extract/segment.py` — bound the task span; report the boundary used; raise a typed error when no tool calls exist
- [ ] **T021** Write `tests/.../test_provenance.py` against `session_linear` and `session_ambiguous` — must cover exact match, structural match, and the ambiguous case producing `confidence == ambiguous` rather than a silent pick
- [ ] **T022** Implement `extract/provenance.py` — for each argument, search prior results then the session input; emit `Binding` with `kind` and `confidence`. **Deterministic; no model call.**
- [ ] **T023** Write `tests/.../test_slice.py` against `session_dead_steps` — assert dead steps removed and recorded in `skipped_detail` with reasons
- [ ] **T024** Implement `extract/slice.py` — backward slice from the terminal artifact along provenance edges
- [ ] **T025** [P] Write `tests/.../test_params.py` — request-derived values become parameters; `captured_value` retained
- [ ] **T026** [P] Implement `extract/params.py` — lift to `node_start` outputs, infer types, generate parameter names
- [ ] **T027** Write `tests/.../test_fanout.py` against `session_fanout` — N sibling calls collapse to one loop; assert `loop_body` and `arr_loop_var` correctness
- [ ] **T028** Implement `extract/fanout.py` — detect sibling calls whose args all derive from one prior result; emit a `jiuwen.loop` component
- [ ] **T029** Write `tests/.../test_classify.py` against `session_linear` and `session_react` — assert the documented default-toward-Agent behaviour from `research.md` R6
- [ ] **T030** Implement `extract/classify.py` — assign `determinism` by tool-call adjacency and argument interdependence; assign `effect` from tool declaration, defaulting to `write_effectful` when unknown
- [ ] **T031** Write `tests/.../test_viability.py` — assert refusal on `<2` steps, `frozen == 0`, and `agent/total > 0.8`
- [ ] **T032** Implement viability scoring in `extract/pipeline.py` per the `CaptureReport` rule in `data-model.md`
- [ ] **T033** Implement `extract/pipeline.py` — orchestrate stages 1–8, emit `SavedWorkflow` + `CaptureReport`
- [ ] **T034** Implement the advisory model call — names the workflow, writes descriptions, proposes classifications for the report. **MUST NOT alter graph structure or bindings.** Behind an interface so tests run without a live model.

---

## Phase 3.5: Conversion

- [ ] **T035** Write `tests/.../test_bindings.py` — `[{name, value}]` → `inputs_schema` flattening; `${...}` passthrough verified against agent-core's `is_ref_path`
- [ ] **T036** Implement `convert/bindings.py`
- [ ] **T037** Write `tests/.../test_ir_to_workflow.py` — linear graph, loop graph, branch graph; each asserts the constraints in `research.md` R4
- [ ] **T037a** Write characterization tests against **local agent-core** pinning its actual registration constraints — loop body edge placement, branch default ordering, deferred branch registration. These replace `PLAN.md` assumptions 3–5, which cannot be verified in this checkout (agent-studio is not present). T038 depends on what these prove, not on `ir_converter.py`
- [ ] **T038** Implement `convert/ir_to_workflow.py` — frozen/transform/agent/loop/branch/start/end, honouring **whatever T037a demonstrates**
- [ ] **T039** Apply **only** those agent-core patches justified by a failing characterization test against our own node vocabulary. Each applied patch cites the test that fails without it; unneeded patches are dropped rather than inherited (`PLAN.md` assumption 5)

---

## Phase 3.6: Storage

- [ ] **T040** [P] Write `tests/.../test_store.py` — save, load, list, version on overwrite, prior versions retained, `current` pointer, immutable version dirs
- [ ] **T041** Implement `store/filesystem.py` per the store layout in `data-model.md` as corrected in review Round 5 — `versions/<n>/` immutable, slug validation and root containment, version allocation and `runs.jsonl`/`state.json` writes guarded by `flock` on `.lock`, build in a same-directory `.tmp-<uuid>/` with `fsync` of files and directory, `os.replace` into place, fail if the target version exists, then atomic `current` swap
- [ ] **T041a** [P] Implement `state.json` as a derived cache and its **rebuild from `runs.jsonl`** — `runs.jsonl` is authoritative; a missing or disagreeing `state.json` is regenerated, never trusted over the log. Rebuild is **version-scoped**: derive state for the version `current` points at, considering only records whose `workflow_version` matches
- [ ] **T041b** [P] Test crash recovery — orphaned `.tmp-*` directories, a complete version newer than `current`, and a lost `state.json` each recover without manual intervention
- [ ] **T042** [P] Implement `RunRecord` append to `runs.jsonl` and the promotion rule — live + success + a `promotion_basis` of `result_consistent == True`, `manual_ack`, or `evaluator` → `verified`. Note `workflow.json` carries **no** `status` field (review Round 4)
- [ ] **T042a** [P] Test that promotion state survives a `state.json` delete and is reconstructed identically from `runs.jsonl` alone
- [ ] **T042b** [P] Test version-scoped rebuild — promote v1, overwrite to create v2, delete `state.json`, assert the rebuild reports v2 `provisional` and does **not** inherit v1's promotion (review Round 6)

---

## Phase 3.7: Execution

- [ ] **T043** Write `tests/.../test_compat.py` — missing tool and changed fingerprint both produce `outcome == incompatible` with zero steps executed
- [ ] **T044** Implement `run/compat.py` — tool availability and schema fingerprint check
- [ ] **T045** [P] Write `tests/.../test_effects.py` — dry run reports effectful steps without performing them
- [ ] **T046** [P] Implement `run/effects.py` — three-class gating per `PLAN.md` § Effect model. Both `write_effectful` and `write_idempotent` are blocked unless `--live`; only an explicitly declared `read` executes in a dry run
- [ ] **T047** Write `tests/.../test_executor.py` — check order per `contracts/commands.md`; missing parameter; step failure reporting
- [ ] **T048** Implement `run/executor.py` — validate → compat → params → unresolved → execute; capture `RunRecord` including `token_cost`
- [ ] **T049** Implement the result-consistency table in `PLAN.md` § Result-consistency — sha256 for file artifacts; key-set/type match to depth 3 plus declared-output equality for structured output; **no auto-promotion for generative terminals**; `None` for mixed, unrecognised, and all dry runs
- [ ] **T049a** Implement `/workflow promote <name> --acknowledge` — source run is the most recent live+success record matching the current version, printed before the state changes; — manual promotion recording `promotion_basis`, acting user, and source `run_id`; the only promotion path for a generative terminal absent a registered evaluator

---

## Phase 3.8: Surface

- [ ] **T050** [P] Write `tests/.../test_mermaid.py` — graph renders; bindings table lists unresolved first and marks them
- [ ] **T051** [P] Implement `render/mermaid.py` — `Workflow.draw()` passthrough plus the bindings table from `contracts/commands.md`
- [ ] **T052** Write `tests/.../test_commands.py` — all **six** subcommands (`save`, `run`, `list`, `show`, `delete`, `promote`), `save --accept-bindings`, every error case in `contracts/commands.md`, usage text on unknown subcommand
- [ ] **T053** Implement `commands.py` — runtime-agnostic handlers returning structured results
- [ ] **T054** Implement adapter `server/runtime/agent_adapter/workflow_slash.py` following `evolution_slash.py`'s shape — `_COMMANDS` tuple, prefix matcher, context object carrying session id and event access, `None` fallthrough
- [ ] **T055** Wire dispatch in `interface_deep.py` (regular agent mode, `:10461` precedent) alongside the existing evolution slash dispatch. **Depends on T054 only.**
- [ ] **T055a** Wire dispatch in `team_helpers.py` (team mode, `:1525` precedent). **Blocked on T009a** — do not schedule until the team-capture scope gate passes; if it fails, this task is cut and Phase 1 ships agent-mode only (`PLAN.md` risk 12). T054, T055 and T055a are the only changes outside the package.

---

## Phase 3.9: Integration & validation

- [ ] **T056** End-to-end: capture from `session_linear`, save, run dry — assert `result_consistent is None` and that **no** non-`read` step executed — then run live and assert promotion to `verified`. (Round 0 asserted consistency on the dry run, which `data-model.md` forbids.)
- [ ] **T057** End-to-end: capture from `session_fanout`, run with a different-length input array, assert the loop adapts
- [ ] **T058** [P] Assert capture does not modify the session (FR-010) — checksum the history file before and after
- [ ] **T059** [P] Assert extraction failure returns a command error and leaves the session usable
- [ ] **T060** **Token baseline measurement** — run 3 representative tasks as fresh agent sessions and as replayed workflows; record token cost for each. Produces the figure deliberately left unstated in the approval document.
- [ ] **T061** [P] Plain-text rendering check on a non-Web channel — no command depends on A2UI
- [ ] **T062** [P] Write `quickstart.md` from the delivered behaviour
- [ ] **T063** Update `docs/` with the command reference

---

## Dependencies

```
T001-T004  ──▶  everything
T004       ──▶  enforced continuously (CI)
T005-T010  ──▶  T017-T034            (no fixtures, no extraction tests)
T011-T015  ──▶  T016-T034, T035-T039 (IR is the interchange type)
T018       ──▶  T020, T022           (pairing precedes segmentation and provenance)
T022       ──▶  T024, T026, T028     (provenance precedes slice, params, fanout)
T024,T026,T028,T030 ──▶ T033         (pipeline orchestrates the stages)
T033       ──▶  T037-T039, T056-T057
T038       ──▶  T048                 (cannot execute what cannot be built)
T041       ──▶  T042, T042a, T048
T044,T046  ──▶  T048
T048       ──▶  T049, T053
T053       ──▶  T054  ──▶  T055
T054       ──▶  T055a                (team dispatch needs the registered adapter surface)
T009a      ──▶  T055a                (team dispatch is also gated on the team-capture scope gate)
T037a      ──▶  T038, T039           (characterization tests precede conversion and patching)
T049       ──▶  T049a
T056-T059  ──▶  T060
```

---

## Parallel execution examples

**Fixtures (after T002)** — T006, T007, T008, T009, T010 together.

**Extraction stages (after T022)** — the slice, params, and fanout tracks are independent:
```
Track A: T023 → T024
Track B: T025 → T026
Track C: T027 → T028
```

**Post-conversion (after T038)** — storage (T040–T042), compat (T043–T044), and effects (T045–T046) are independent.

**Validation (after T055)** — T058, T059, T061, T062.

---

## Notes

- **T004 is not optional.** It is the mechanism that keeps the feature extractable; without it the seam erodes silently.
- **T034 is deliberately constrained.** The model names things. If it starts influencing structure, the workflow becomes a non-deterministically produced artifact, which is the problem the feature exists to solve.
- **T060 is the only place a token figure gets asserted.** Until it runs, no percentage belongs in any external document.
- **T005 requires a real session.** Do not hand-write it; synthetic traces omit exactly the noise the slice stage exists to remove.
- Open items from the approval document that surface here: store versioning (T041), schema fingerprinting (T044), parameter-lift confirmation (T026/T053). Each is implemented with the documented default and flagged for revisit.

---

## Validation checklist

- [x] Every contract in `contracts/commands.md` has a test task (T052)
- [x] Every entity in `data-model.md` has a model task (T012)
- [x] Every test task precedes its implementation task
- [x] `[P]` tasks touch distinct files
- [x] Every task names a specific file path
- [x] Each functional requirement in `spec.md` maps to at least one task
