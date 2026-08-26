# Implementation Plan: Workflow Capture

> **Status:** this document predates the adversarial plan review. `PLAN.md` at the repo root is authoritative for the effect model, provenance algorithm, dry-run semantics, result-consistency, fingerprinting, and the store contract — all of which were specified or corrected after this file was written. Sections corrected in place are marked inline.


**Branch**: `001-workflow-capture` | **Date**: 2026-08-26 | **Spec**: `./spec.md`

---

## Summary

Extract a replayable workflow from a completed JiuwenSwarm session by deterministic analysis of the session's persisted event stream, serialise it as a versioned IR, and execute it via agent-core's existing workflow component layer.

The feature ships **in-tree** as a self-contained package with a single thin adapter to the session runtime. See "Placement decision" below.

---

## Technical Context

| | |
|---|---|
| **Language** | Python (JiuwenSwarm / agent-core layer) |
| **Primary dependencies** | agent-core (`openjiuwen.core.workflow`, `openjiuwen.core.session`) — existing |
| **New third-party dependencies** | None |
| **Storage** | Filesystem, alongside the skill library |
| **Testing** | pytest, mirroring `tests/unit_tests/` layout |
| **Target** | JiuwenSwarm server runtime |
| **Project type** | Single package + adapter |
| **Performance goals** | Capture is offline analysis; MUST NOT affect session latency. Replay SHOULD reduce token cost vs. a fresh agent run. |
| **Constraints** | Batch execution only. No agent-studio dependency. Read-only visualisation. |
| **Scale** | Hundreds of saved workflows per deployment; sessions up to ~10k events |

---

## Placement decision: in-tree, single seam

**Decision: implement in-tree as `jiuwenswarm/workflow_capture/`.**

### Why not an extension

> **Corrected in review Round 1.** This section originally claimed three registration slots and nine lifecycle-only hook events. Both were wrong. See `PLAN.md` assumption 9 — this text is the corrected version; `PLAN.md` remains authoritative.

JiuwenSwarm has two extension surfaces and neither can host this feature. Verified surface:

- **Registration slots** (`registry.py`): `register_agent_server_client`, `register_crypto_utility`, `register_third_agent` — **plus** generic `register(event, handler)` / `unregister()` / `trigger(event)` callbacks over an in-process callback framework (`registry.py:87-106`).
- **User hook events** (`common/hooks_config.py`): **17** events, including `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `BeforeModelCall`, `AfterModelCall`, `SessionStart`, `SessionEnd` and `SubagentStart`/`SubagentStop` — not the nine lifecycle-only events originally listed.

The blocking facts are narrower than "the surface is missing". No surface owns slash-command dispatch, so an extension cannot claim `/workflow save`. And `HookType` is `COMMAND | PROMPT` — hooks are out-of-process shell commands and prompt injection, so a hook cannot build an IR in-process at capture time. The conclusion is unchanged; the argument is not.

Beyond the missing surface, three properties make in-tree correct on merit:

1. Capture reads the session's own event record. Out-of-tree, it would be coupled to that schema with no protection when the schema moves.
2. `/workflow run` is a second execution mode that needs the same tool registry and permission engine as a normal session.
3. `evolution_slash.py` is the in-tree precedent for a session-derived, command-driven, artifact-persisting feature.

### How optionality is preserved

Extractability comes from module boundaries, not packaging:

- `jiuwenswarm/workflow_capture/` MUST NOT import from `jiuwenswarm.server.runtime.*`. It receives an event list and a tool resolver; it does not go looking for them.
- **Three files outside the package change** (corrected in review Round 1): a new adapter `server/runtime/agent_adapter/workflow_slash.py`, and a dispatch line in **both** `interface_deep.py` (regular agent mode, `:10461`) and `team_helpers.py` (team mode, `:1525`). `evolution_slash.py` is dispatched from both, and the original "one dispatch line" claim would have left `/workflow` dead in regular agent mode. The `team_helpers.py` line is contingent on the team-capture scope gate (`PLAN.md` risk 12, `tasks.md` T009a).
- All session-runtime knowledge lives in the adapter. If a real plugin API appears, the adapter is what gets rewritten; the package is untouched.

**Enforced by**: an import-boundary test (T004) that fails the build if the package reaches into the runtime.

---

## Constitution Check

| Gate | Status | Notes |
|---|---|---|
| No new third-party dependencies | ✅ | agent-core only, already a dependency |
| No agent-studio dependency | ✅ | IR schema adopted as design reference; nothing imported |
| Feature is additive | ✅ | Normal session path unchanged when feature is not invoked |
| Failure isolation | ✅ | Extraction failure returns a command error; session unaffected |
| Deterministic core | ✅ | Steps 1–7 of the pipeline are pure analysis; the model only names and proposes |
| Effects gated | ✅ | Dry-run default while provisional; explicit opt-in for external effects |
| Testable without a live model | ✅ | Extraction and conversion tested against fixture event streams |

**Re-check after Phase 1 design**: ✅ no new violations.

---

## Project Structure

```
jiuwenswarm/workflow_capture/          # self-contained; no runtime imports
├── __init__.py                        # public API: capture(), convert(), Store
├── ir/
│   ├── models.py                      # WorkflowIr, Component, Connection, Binding
│   └── validate.py                    # dangling refs, loop-body integrity, cycles
├── extract/
│   ├── events.py                      # event-stream reader interface (injected)
│   ├── segment.py                     # 1. task span
│   ├── pair.py                        # 2. call ↔ result join on tool_call_id
│   ├── provenance.py                  # 3. dataflow analysis
│   ├── slice.py                       # 4. backward slice
│   ├── params.py                      # 5. parameter lift
│   ├── fanout.py                      # 6. loop detection
│   ├── classify.py                    # 7. step classification
│   └── pipeline.py                    # orchestrates 1–8, emits IR
├── convert/
│   ├── ir_to_workflow.py              # IR → agent-core Workflow
│   └── bindings.py                    # ${node.field} → inputs_schema
├── store/
│   └── filesystem.py                  # save / load / list / version
├── run/
│   ├── executor.py                    # invoke, dry-run, parameter validation
│   ├── effects.py                     # effect classification and gating
│   └── compat.py                      # tool availability + schema fingerprint check
├── render/
│   └── mermaid.py                     # draw() + bindings table
└── commands.py                        # command logic, runtime-agnostic

jiuwenswarm/server/runtime/agent_adapter/
└── workflow_slash.py                  # ADAPTER — the only new runtime file

tests/unit_tests/workflow_capture/
├── fixtures/                          # recorded event streams
├── test_import_boundary.py            # enforces the seam
├── test_provenance.py
├── test_slice.py
├── test_fanout.py
├── test_classify.py
├── test_ir_validate.py
├── test_ir_to_workflow.py
├── test_store.py
├── test_executor.py
└── test_commands.py
```

**Structure decision**: single package with a thin adapter, per the placement decision above.

---

## Phase 0: Research

Complete. See `research.md`. Source verification against `openJiuwen-ai/jiuwenswarm`, `openJiuwen-ai/agent-core`, and `openJiuwen-ai/agent-studio` (reference only) resolved the following:

- `${node.field}` binding syntax is **agent-core native**, not a studio invention
- agent-core's component layer covers every step class needed
- Loop-body connections must not be registered at root level
- Branch default must be registered last; branch nodes deferred until routes wired
- Three agent-core patches are load-bearing for loops and nested branches
- No LLM-call-boundary event is persisted → classification heuristic required
- JiuwenSwarm extension system cannot host this feature

---

## Phase 1: Design

Outputs: `data-model.md`, `contracts/`, `quickstart.md`.

**Key design decisions**

1. **IR as the interchange format.** Extraction emits IR; conversion consumes IR. This keeps the analysis testable without agent-core, and makes the saved artifact inspectable.
2. **Deterministic analysis, advisory model.** Steps 1–7 are pure functions over the event list. The model is called only to name the workflow, write its description, and propose step classifications for reporting. An LLM-authored workflow would itself be non-deterministic.
3. **Classification defaults toward open-ended.** With no persisted LLM-call boundary, ambiguity between "fixed-shape generation" and "bounded open-ended" resolves toward the latter — it preserves capability at some cost to determinism, and is the recoverable direction.
4. **Effect classification at capture, gating at run.** Effects are derived from the tool's own declaration where available, defaulting to effecting when unknown.
5. **Provisional by default.** n=1 is not evidence. Promotion requires a matching replay.

---

## Phase 2: Task Generation Approach

Tasks derive from the structure above, ordered by dependency:

1. **Foundation** — IR models and validation; import-boundary test. Nothing depends on the runtime yet.
2. **Extraction** — one module per pipeline stage, each with fixture-driven tests. Stages 3–7 are the substance and are individually testable; several are parallelisable once `pair.py` lands.
3. **Conversion** — IR → agent-core Workflow, respecting the verified loop/branch constraints.
4. **Storage and execution** — persist, load, validate parameters, dry-run, compatibility check.
5. **Surface** — render, command logic, adapter, dispatch.
6. **Integration** — end-to-end against recorded sessions; token-cost baseline measurement.

Estimated: ~46 tasks. See `tasks.md`.

---

## Complexity Tracking

| Item | Why it's needed | Simpler alternative rejected because |
|---|---|---|
| Separate IR layer rather than building `Workflow` directly | Makes analysis testable without agent-core; artifact is inspectable and versionable | Direct construction couples extraction to the engine and makes the saved artifact opaque |
| Three determinism classes rather than freeze-everything | Freezing all steps discards the judgment that made the agent useful | A fully frozen workflow fails on any input variation |
| Provisional/verified lifecycle | A capture from one session is a sample of one | Trusting first capture fills the library with one-offs |
| Tool schema fingerprinting | Workflows pin tool identities; MCP schemas drift | Silent failure or wrong-argument execution at replay |

---

## Progress Tracking

**Phase status**
- [x] Phase 0: Research complete
- [x] Phase 1: Design complete
- [x] Phase 2: Task approach defined
- [ ] Phase 3: Tasks generated → `tasks.md`
- [ ] Phase 4: Implementation
- [ ] Phase 5: Validation

**Gate status**
- [x] Initial Constitution Check: PASS
- [x] Post-Design Constitution Check: PASS
- [x] All clarifications resolved
- [x] Complexity deviations documented
