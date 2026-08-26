# Research: Workflow Capture

> **Status:** this document predates the adversarial plan review and contains two source claims (the extension surface, and reasoning persistence) that the review proved wrong. Both are corrected inline. `PLAN.md` is authoritative wherever the two disagree.


Phase 0 output. Every decision below was verified against repository source, not inferred.

---

## R1. Where does the feature live — extension or core?

**Decision**: In-tree package with a single adapter.

**Rationale** *(corrected in review Round 1 — the original claim was wrong)*: `registry.py` exposes three registration slots **plus** generic `register(event, handler)` / `trigger(event)` callbacks (`:87-106`), and the user-hook surface in `common/hooks_config.py` defines **17** events including `PreToolUse`, `PostToolUse`, `BeforeModelCall` and `AfterModelCall` — not nine lifecycle-only events. The conclusion survives on narrower grounds: no surface owns slash-command dispatch, and `HookType` is `COMMAND | PROMPT`, i.e. out-of-process, so a hook cannot build an IR in-process at capture time. See `PLAN.md` assumption 9.

**Alternatives considered**:
- *Out-of-tree extension* — not possible with the current surface.
- *Extend the extension API first* — adds an unrelated platform change to this feature's critical path.
- *In-tree, freely coupled* — rejected; forfeits future extractability for no gain.

---

## R2. Binding syntax

**Decision**: Use `${node_id.field}` and store bindings directly in agent-core `inputs_schema` form.

**Rationale**: This is agent-core native. `openjiuwen/core/session/utils.py`:
- `is_ref_path(path)` — true when a string starts `${` and ends `}`
- `extract_origin_key(key)` — `"${start123.p2}"` → `"start123.p2"`
- `get_by_schema(...)` — recursive resolver over an `inputs_schema` dict, resolving reference leaves against runtime state

The provenance edges produced by dataflow analysis therefore serialise with **no translation layer**.

**Implication**: converting IR's `[{name, value}]` input lists to `inputs_schema` is a flattening operation, not a mapping.

---

## R3. IR schema

**Decision**: Adopt agent-studio's `WorkflowIr` shape; implement our own models. Import nothing.

**Rationale**: `agent-runtime/jiuwen/orchestration/flow/model/workflow_ir_validation.py` defines a ~25-line pydantic schema — `workflowId`, `workflowName`, `description`, `workflowVersion`, `configs`, `components[]`, `connections[]` — that is a proven fit for this exact graph shape and already carries the `${...}` convention. Re-deriving a format would produce something similar with less evidence behind it.

**Alternatives considered**:
- *Import from agent-runtime* — rejected by requirement; also drags in SpiffWorkflow, a second orchestration engine, and a large transitive tree.
- *Invent a format* — no benefit over an existing proven one.
- *Serialise agent-core `WorkflowSpec` directly* — insufficient. `WorkflowSpec` holds `edges`, `stream_edges`, `comp_configs`, `start_nodes` but **not** component class or construction parameters, and there is no `from_dict` anywhere under `core/workflow/`. It does not round-trip.

---

## R4. Execution engine constraints

**Decision**: Build on agent-core's workflow component layer, batch mode only.

**Verified constraints** (from `agent-studio/agent-runtime/.../ir_converter.py`, which drives the same engine):

| Constraint | Detail |
|---|---|
| Loop body edges | Body-to-body connections MUST NOT be added to the root workflow; they belong to the `LoopGroup`. Adding them at root breaks execution. |
| Loop boundaries | Body start/end derived from virtual `{node_id}_input` / `{node_id}_output` connections; fall back to first/last body node. |
| Loop break | `end_node → BranchComponent → LoopBreakComponent`, placed after the end node rather than replacing it. Flag set in one superstep, checked the next. |
| Branch default | The default branch MUST be registered last. |
| Branch registration | Branch nodes MUST be deferred until routes are wired — `add_workflow_comp` → `register_branch_targets` requires the full router at registration. |
| Patches | `loop_body_session_cleanup` applied unconditionally at import; `parallel_branch_grouping` and `nested_branch_barrier` applied before construction. |

**Raw Pregel is not the target.** `core/graph/graph.py`'s `ConditionalRouter` only selects among statically registered targets; `register_branch_targets` requires a known target set. There is no Send-style dynamic instance spawning. Dynamic fan-out is available at the *workflow component* layer via `LoopComponent`, not below it.

---

## R5. Step class → component mapping

**Decision**: Three classes, mapped to existing components.

| Class | Component | IR type |
|---|---|---|
| Frozen | `components/tool/tool_comp.py` | `jiuwen.mcp`, `jiuwen.api`, `jiuwen.code` |
| Transform | `components/llm/llm_comp.py` | `jiuwen.llm` |
| Agent | `components/llm/react/` `ReActAgentComp` | `jiuwen.LLMReAct`, `jiuwen.agent` |
| (repetition) | `components/flow/loop/loop_comp.py` | `jiuwen.loop` |
| (branching) | `components/flow/branch_comp.py` + `condition/` | `jiuwen.branch` |

All exist. Capture selects among them; it does not invent components.

---

## R6. Event stream sufficiency

**Decision**: Use the persisted session history; accept a heuristic for Transform vs. Agent.

**Verified**: `session_history.py` persists `chat.tool_call`, `chat.tool_result`, `chat.file`, `chat.final`, `chat.tracer_agent`. Calls and results are joinable on `tool_call_id`, giving a complete ordered step list with arguments and outputs — sufficient for stages 1–6 of the pipeline with no new capture path.

**Gap** *(corrected in review Round 1 — this finding was wrong)*: reasoning **is** persisted. `interface.py` (~2708-2760) attaches `reasoning_content` to the following `chat.tool_call`/`chat.final`, or writes a standalone `chat.reasoning` as a fallback, and `session_history.py:121-122` persists any assistant record carrying non-empty `reasoning_content`. The real gap is narrower: reasoning is model-dependent and may be absent from an entire session, so classification consumes it when present and falls back to defaulting toward Agent when not. See `PLAN.md` assumption 8.

**Mitigation**: classify by tool-call adjacency and argument interdependence, defaulting toward **Agent**. Misclassifying Transform as Agent preserves capability and costs some determinism; the reverse over-constrains a step that needed judgment. Recoverable direction chosen deliberately.

**Alternative**: add a persisted boundary event. Small change to the emit path; raised as an open item in the approval document rather than assumed here.

---

## R7. Visualisation

**Decision**: `Workflow.draw(output_format="mermaid")` plus a bindings table. Read-only.

**Rationale**: `draw()` exists in agent-core and supports `mermaid`, `png`, `svg`. Mermaid needs no extra dependency.

**A2UI assessed and deferred to Phase 2**: JiuwenSwarm has A2UI (`server/runtime/a2ui/`, `a2ui-agent-sdk==0.2.1`), but its `BasicCatalog` is `Text`, `Card`, `Button`, `List`, `Image`, `Markdown`, `TextField`, `CheckBox`, `MultipleChoice`, `Slider`, `DateTimeInput` — no canvas or node component and no visible extension point. It cannot draw the graph.

It is, however, well suited to the *bindings* correction surface, which is where the real ambiguity sits. Two blockers to confirm before scoping Phase 2:
- A2UI is Web-channel only (`is_a2ui_channel` returns true only for `web`) and disabled by default (`a2ui.enabled: false`) — a text fallback is mandatory.
- A2UI is designed for model-generated UI. Whether a command handler can emit a deterministic `<a2ui-json>` block through the finalizer is **unverified**.

**Rejected**: `@antv/x6` via agent-studio's frontend — reintroduces the excluded dependency.

---

## R8. Workflow as a callable tool

**Finding, not yet a requirement**: `WorkflowCard.tool_info()` returns a `ToolInfo` with `name`, `description`, `parameters=input_params`. A saved workflow is therefore directly exposable to an agent as a tool.

**Decision**: build the capability, do not enable it by default in Phase 1. Agent-invoked workflow execution has effect-gating implications that the dry-run model does not yet cover.
