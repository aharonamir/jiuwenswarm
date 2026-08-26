# Plan: Workflow Capture — save a successful agent session as a replayable workflow
_Round 6 revision — locked via claudex-loop (entered at Phase 2; recon and interrogation completed prior; see `## Assumptions`) — by Claude + Amir_

Supporting artifacts (detail, not substitutes): `specs/001-workflow-capture/{spec,plan,research,data-model,tasks}.md` and `contracts/commands.md`.

---

## Goal

A JiuwenSwarm agent re-plans its execution path on every run, so a multi-step task the user was satisfied with cannot be reproduced — different tools, different ordering, different iteration counts, different cost. Workflow Capture turns one successful session into a named, versioned, executable workflow by deterministic analysis of that session's own persisted event stream, replayed through agent-core's existing workflow component layer. The user types `/workflow save [name]` on a run they liked and `/workflow run [name]` thereafter. Structural determinism is the guarantee: the same steps in the same order, with values that varied per-run lifted into explicit parameters. Steps that genuinely need judgment stay agentic but bounded.

---

## Approach

1. **Package skeleton + import-boundary test.** `jiuwenswarm/workflow_capture/` with an AST-walking test that fails the build if any module imports `jiuwenswarm.server.runtime.*`. The test lands before implementation.
2. **Capture read path (durable-only, drained, guarded).** Capture reads `history.jsonl` and never the forwarded event stream. Because history writes are asynchronous (`_WRITE_QUEUE` + daemon writer thread, `session_history.py:618-653`), the adapter drains the queue via the existing `_WRITE_QUEUE.join()` (`:841`) before extraction begins, then segments **through the most recent persisted `chat.request_completed` belonging to a non-`/workflow` request**. Round 1 said "wait on the write receipt for the capturing request", which was circular — `/workflow save` is itself a request, and its own completion is neither available nor the boundary wanted. Two guards: capture refuses if any `chat.tool_result` in the span carries `truncated: True`, and refuses if no qualifying `chat.request_completed` is found.
3. **Fixtures from real sessions.** Eight recorded event streams: linear, fan-out, dead-steps, react, ambiguous-provenance, no-tools, multi-task, plus **team-mode** — which is a scope gate, not a detail: it must prove that the parent session's `read_session_history_records()` / `read_team_history_records()` actually contains the leader and teammate `chat.tool_call` / `chat.tool_result` events extraction needs, and whether they arrive truncated (see Assumption 15). If it does not, team mode leaves Phase 1.
4. **IR models + validation.** `SavedWorkflow`, `Component`, `Binding`, `BindingCandidate`, `Parameter`, `CaptureReport`, `RunRecord`. Validation covers dangling `${node.field}` refs, forward references, cycles outside loop bodies, loop-body integrity.
5. **Extraction pipeline**, one module per stage, each a pure function over the prior stage's output:
   `pair` (join `chat.tool_call`↔`chat.tool_result` on `tool_call_id`) → `segment` (task span) → `provenance` (§ Provenance algorithm) → `slice` (backward slice, effect-preserving — § Slicing rule) → `params` (lift request-derived values) → `fanout` (collapse sibling calls into `jiuwen.loop` under § Fan-out criteria) → `classify` (frozen / transform / agent) → `pipeline` (emit IR + report, score viability).
6. **Advisory model call.** Names the workflow, writes descriptions, proposes classifications for the report. Barred from altering graph structure or bindings.
7. **Conversion.** `ir_to_workflow.py` builds an agent-core `Workflow` from IR. Batch only. Ships with characterization tests (§ Assumptions 3–5) that pin agent-core's actual registration constraints instead of inheriting them from a repo not in this checkout.
8. **Storage.** Filesystem store, immutable version directories, atomic pointer, per-workflow lock — § Store contract.
9. **Execution.** Order: exists → tool-fingerprint compatibility (§ Fingerprint contract) → required params → unresolved bindings → execute. Dry-run default while provisional and **statically effect-skipping** (§ Dry-run contract); `--live` is the explicit opt-in for external effects.
10. **Surface.** Mermaid render via `Workflow.draw()` plus a bindings table; **six** subcommand handlers (`save`, `run`, `list`, `show`, `delete`, `promote`) in a runtime-agnostic `commands.py`.
11. **Adapter + dispatch.** `server/runtime/agent_adapter/workflow_slash.py` following `evolution_slash.py`'s shape, plus a dispatch line in **both** `team_helpers.py` (`:1525` precedent) and `interface_deep.py` (`:10461` precedent). These three files are the only changes outside the package.
12. **Integration + token baseline.** End-to-end capture→dry→live→promotion; fan-out adaptation to a different-length array; three representative tasks measured fresh-vs-replayed for token cost.

Full task decomposition with dependency ordering: `specs/001-workflow-capture/tasks.md`.

**Artifact precedence.** Where `PLAN.md` and a `specs/001-workflow-capture/` document disagree, `PLAN.md` wins — it carries the Round 1 and Round 2 corrections. The specific contradictions the review found have been reconciled in place rather than left to precedence: `data-model.md` (`Binding.candidates`, `confidence` tier names, `RunRecord` audit fields), `tasks.md` (T009a/T009b fixtures, T037a characterization tests, T039 patch justification, T046 three-class gating, T049 consistency table, T056 dry-run assertion, T060a promote), and `contracts/commands.md` (the `promote` subcommand, and removal of the "Matches the original result" dry-run line). `specs/.../plan.md` carried the same three errors `PLAN.md` assumptions 9 and 10 corrected and has been fixed in place with a staleness banner. `spec.md` FR-013 is reinterpreted by § Result-consistency rather than edited.

---

## Contracts closed in this revision

These were open questions or under-specified risks in Round 0. They are now specified, because each one gates code that would otherwise be written on a guess.

### Provenance algorithm (was risk 5)

Deterministic, staged, no model involvement. **Revised in Round 2** — the Round 1 version had a resolution bug and three unsafe matching rules.

**Two separate indices, not one.** Prior-step outputs are indexed into the *origin index*; session-input fields are indexed into the *input index*. Round 1 merged them, which made "exactly one candidate → `reference`" swallow session-derived values and left the `parameter` branch unreachable. They are resolved in different orders and must stay distinct.

**Canonicalisation preserves type.** Strings that parse as JSON are parsed; object keys sorted; lists keep order. **Scalar type is retained in the hash** — `1`, `1.0`, `"1"` and `"001"` do **not** collide. Round 1 made them collide deliberately, which was wrong: issue numbers, ports, version strings, zero-padded IDs and schema-constrained strings all break when a string binds to a number. Numeric-string coercion is attempted only when the origin field and the target argument are *both* numeric in their declared schemas.

**Value-matching floor applies to global matching only.** Booleans, `null`, scalars under 4 characters and small integers are excluded from *global* value matching, where they collide by chance far more than by derivation. They remain eligible for **schema/path-aware matching** — same argument name, or same JSON pointer within a same-shaped origin — and for exact equality against a known session-input field. Round 1 excluded them outright, which would have failed to bind status enums, country codes, `Q3`, flags and small counts, all of which routinely drive tools.

**Match tiers**, evaluated in order against the origin index; the first tier producing a non-empty candidate set wins and its name becomes the binding's `confidence`:
- `exact` — canonical hash equality (type-preserving) on a leaf or subtree.
- `normalised` — string equality after whitespace collapse and case-fold.
- `path_aware` — same argument name or same JSON pointer in a same-shaped origin, used for values below the global matching floor.
- `structural` — same JSON shape (identical key set and leaf types) with ≥80% of leaves matching at `exact`.
- `containment` — the argument is a substring of an origin leaf, or vice versa, minimum 12 characters. **Never resolves to a `reference`** — see below.

**Resolution order.** Both indices are queried **before** anything resolves — Round 2 put the origin-only case first, which meant a value present in both a prior output and the session input resolved as a `reference` before the both-indices case could fire, reintroducing the same bug in a new shape.
1. Matches **both** the origin index and the input index → `kind = unresolved`, both candidate classes retained. This is the genuine ambiguity case (`fixtures/session_ambiguous.jsonl` is built from exactly it) and the user resolves it.
2. Origin index only, exactly one candidate at `exact`, `normalised`, `path_aware` or `structural` → `kind = reference`.
3. Origin index only, more than one candidate → `kind = unresolved`, all candidates retained in `candidates: list[BindingCandidate]`. The extractor never silently picks one.
4. Input index only → `kind = parameter`.
5. Neither → `kind = constant`.

**Containment never binds.** A `containment` match means the argument is a *fragment* of an origin value, so binding it to the whole origin leaf would replay the wrong value — the extraction that produced the fragment is not captured anywhere. Containment therefore emits `kind = unresolved` with the candidate and the matched span recorded, and requires either user confirmation with an explicit extraction transform, or demotion to `parameter`/`constant`. Round 1 treated it as a resolvable tier, which would have produced silently wrong replays.

**Explicit non-goal.** A value computed from two or more origins (concatenation, arithmetic, summarisation) is **not bindable**. It is emitted as `constant` with `derived_unknown: true`, surfaced in the report, and a step carrying any `derived_unknown` input can never be classified `frozen`. Inferring transformations is out of scope for Phase 1 and named as such rather than silently attempted.

### Slicing rule (was risk 6)

A step is retained if **either** it is in the backward slice from the terminal artifact **or** its effect class is anything other than `read` (§ Effect model). Only steps that are declared read-only *and* whose outputs are never referenced downstream are removed. **Revised in Round 2**: Round 1 said "effectful (`write_effectful`)", which after the effect-model correction would have let `write_idempotent` steps — upserts, "mark as read", cache and auth setup — be sliced away as if they were reads. This is deliberately conservative: an auth call, a cache warm, or a permission grant is load-bearing without producing a bound value, and the failure mode of dropping one is a workflow that silently fails at replay. Steps retained for effect only are listed in the `CaptureReport` so the user can see what the slice kept and why.

### Effect model (grounded — corrected in Round 2)

agent-core's `ToolCard` declares `idempotent`, `parallel_safe`, `stateless`, and `properties` — there is **no** read/write/effectful field (`foundation/tool/base.py`). The plan does not assume one. But Round 1 mapped `idempotent == True → read`, and that is **wrong**: the field's own docstring says "repeated invocations with the same inputs have no *additional* side effects", which is not the same as no side effects. Idempotent writes are common — writing the same file, an upsert, "mark as read", a cache or auth warm. Mapping them to `read` would have let a dry run execute them.

Three classes, matching the enum `data-model.md:45` already declares:

- `properties["effect"]` present → it wins, **except** that a `read` declaration is only honoured from a trusted provider (see below).
- else `idempotent == True` → `write_idempotent`.
- else → `write_effectful`.

**A `read` declaration is only trusted from local built-in tools and allowlisted providers.** Added in Round 3: `read` is the one class that causes a dry run to actually invoke a tool, so a wrong or hostile `properties["effect"] = "read"` on a remote MCP or third-party plugin tool turns the dry run into the writer it exists to prevent. A `read` claim from an untrusted provider is downgraded to `write_effectful` and the downgrade is reported. `write_idempotent` and `write_effectful` declarations are honoured from any provider — they only ever gate harder.

**`read` is never inferred.** It requires an explicit declaration, because nothing on `ToolCard` distinguishes a read from an idempotent write. `idempotent` already defaults to `False` in agent-core for secure-by-default reasons, so the unknown case gates hardest, with no change to agent-core and no invented schema.

### Dry-run contract (reframed in Round 2)

**A dry run is a plan/audit mode, not a validation mode.** Round 1 implied it could confirm a workflow reproduces its result; it cannot, and `commands.md:75` currently prints "Matches the original result" on a dry run, which is removed by this revision.

A dry run never invokes a `write_idempotent` or `write_effectful` component — there is no preview interface on `ToolCard` to call safely, so skipping is static, not delegated. It executes only components explicitly declared `read`, resolves bindings, renders planned inputs, and emits an **executed / skipped / would-run DAG** so the user can see exactly which steps ran, which were skipped for effect, and which were not reached because an ancestor was skipped. Where a skipped step's output is bound downstream, the binding resolves to a typed placeholder and every dependent step is marked *would run*.

Because skipping changes what actually executes, a dry run **does not** demonstrate the "same steps, same order" guarantee and **never** sets `result_consistent`, which stays `None`. A dry run can never promote.

### Result-consistency contract (was risk 8)

Defined per terminal artifact type, because one comparison cannot cover all of them:

- **File artifact** — sha256 over bytes.
- **Structured / JSON terminal output** — key set and leaf types match to depth 3, **and** every field declared in the workflow's `outputs` compares equal.
- **Free text / generative terminal step** — **never auto-promotes.** Revised in Round 2: Round 1 let "non-empty + output contract satisfied + frozen-step integrity" stand in for consistency and satisfy FR-013, but those checks pass equally for wrong prose, wrong facts, or a publish confirmation describing the wrong content. `result_consistent = None` and promotion requires either an explicit manual acknowledgement or a registered deterministic domain evaluator for that workflow. The structural checks are still computed and reported — they are evidence for the human, not a gate.
- **Mixed or unrecognised** — `result_consistent = None`, promotion blocked, same manual path.

**Manual promotion** is `/workflow promote <name> --acknowledge`, which records the override, the acting user, and the `run_id` it is based on in the `RunRecord`. FR-013 is satisfied by "promotion requires evidence a human or a deterministic evaluator accepted", not by an automatic check that cannot see content.

`T049` implements this table. **`T056` is corrected**: it asserted consistency on a dry run, which the data model already forbids — the dry-run leg now asserts `result_consistent is None` and that no effectful step executed.

### Fingerprint contract (was open risk 3)

`tool_fingerprint = sha256(canonical_json({tool_id, input_schema, effect_metadata}))`. **`effect_metadata` added in Round 3**: `properties["effect"]` (if declared), `idempotent`, and the resolved effect class. Without it a tool could flip from `read` to `write_effectful` while keeping its input schema and still pass compatibility — the workflow would then execute a writer that had been captured, gated, and approved as a read. Effect-class drift refuses before dry *or* live execution rather than warning. `input_schema` is normalised: pydantic models rendered via `model_json_schema()`; keys sorted; `title`, `description`, and `examples` stripped; `anyOf`/`oneOf` members sorted by canonical form; `default` retained; `required` sorted. Output schema is **excluded** — output drift is caught by binding validation, and including it would fail workflows on benign additions.

Compatibility policy at run: identical fingerprint → run. Differing fingerprint → compare normalised schemas; if the change only **adds optional** fields, run and warn; otherwise refuse and print the schema diff.

**Output-path preflight** (added in Round 2). Excluding the output schema from the hash keeps benign output additions from failing a workflow, but it cannot catch an output field that *disappeared* — and "caught by binding validation" is too late, because by then effectful steps may already have run. So before execution, the preflight resolves every `${node.field}` reference in the workflow against the tool's currently declared output schema and refuses up front on any reference that no longer resolves.

**No schema, no effects.** Round 2 said "where one is available", which left the hole open: a tool with no declared output schema skipped the check entirely, and an effectful downstream step could run before a missing field was discovered. Now, if any reference resolves against a tool with no current output schema, the preflight falls back to validating the path against the **observed shape captured at save time**; if that also cannot be established and a `write_idempotent` or `write_effectful` step depends on the path, the run refuses. Unproven output paths may only precede `read` steps.

### Store contract (was open risk 2)

`<root>/<slug>/versions/<n>/` immutable; `<root>/<slug>/current` is a **plain text file holding the version number, not a symlink**; `runs.jsonl` append-only alongside. Slug must match `^[a-z0-9][a-z0-9-]{0,63}$` and its resolved path must remain under the store root (`Path.resolve()` + containment check) — user-supplied names never traverse. Version directories are built in `<root>/<slug>/.tmp-<uuid>/` — **under the same workflow directory, therefore the same filesystem**, since `os.replace` is only atomic within one — and moved into `versions/<n>/` with `os.replace`. If `versions/<n>/` already exists the save **fails** rather than overwriting; version numbers are allocated under the lock and never reused. Files are `fsync`ed and their containing directory `fsync`ed before the `current` pointer is swapped, so a crash between the two leaves an unreferenced complete version rather than a referenced partial one. `current` is replaced the same way (`current.tmp` + `os.replace`). Crash recovery — orphaned `.tmp-*` directories and versions newer than `current` — is a tested path, not an assumed one. A per-workflow `flock` on `<slug>/.lock` guards version allocation and `runs.jsonl` appends, so concurrent saves and runs cannot interleave. Every version directory stamps the IR schema version for migration.

**Every run record names the version it ran.** Added in Round 6, closing a gap the previous round opened: making `runs.jsonl` authoritative for lifecycle state only works if the log says *which version* each record belongs to, and `RunRecord` had no such field while `/workflow run --version <n>` already existed. Without it, a successful live run against an old version writes a promotion record indistinguishable from one against the current version, and the `state.json` rebuild silently verifies a version that was never verified. So: `RunRecord` carries `workflow_version`, every promotion record names the version it promotes, and **rebuild derives state for the version `current` points at, considering only records whose `workflow_version` matches it.** A promotion of version 1 does not verify version 2 — which is the enforceable form of the existing rule that an overwrite starts `provisional`.

**Lifecycle state lives outside the immutable version.** Added in Round 4 to resolve a real conflict: `SavedWorkflow.status` was modelled inside `workflow.json`, but version directories are immutable, so promotion had nowhere to write. `workflow.json` therefore carries **no** `status` field. The authoritative record of verification is the promotion entry appended to `runs.jsonl` — which already carries `promotion_basis`, the acting user, and the source `run_id` — and `<slug>/state.json` holds the derived current state (`provisional` | `verified`, plus the version it applies to) as a lock-guarded, rebuildable cache. If `state.json` is lost or disagrees, it is regenerated from `runs.jsonl`, which is append-only and never rewritten. Status is a property of a version *as judged over time*, not a property of the captured artifact, and the store now models it that way.

### Fan-out criteria (was risk 7)

Sibling calls collapse into `jiuwen.loop` only when **all** hold: same tool id; same fingerprint; at least three calls; argument templates identical except exactly one binding position; that position's values all resolve to leaves of a single array-valued origin; no call in the group binds to another call in the group; the calls form a contiguous run in step order with no intervening different tool. Anything short of that is emitted as N sequential steps with a report note that a loop may be present — a false sequence is recoverable, a false loop silently changes semantics.

### Parameter-lift confirmation (was open risk 4)

Decided: lifts at `exact` confidence proceed without confirmation and are printed in the save output. Any binding resolved at `containment` or `structural` confidence, or carrying multiple candidates, blocks the save until the user accepts it — interactively, or with `/workflow save --accept-bindings` in a non-interactive context. Confirmation is required exactly where the extractor is guessing, and nowhere else.

### Run audit record (observability gap)

`RunRecord` carries, per component: resolved inputs (hashed when large), the tool fingerprint actually observed, whether the step was skipped as effectful, and the output hash. At record level it carries the capture algorithm version, the IR schema version, the consistency method used, and the rationale for the promotion decision. Without these, a refused promotion or an incompatibility is unexplainable after the fact.

---

## Key decisions & tradeoffs

**In-tree package, not an extension.** JiuwenSwarm has two extension surfaces and neither can host this feature. `ExtensionRegistry` (`jiuwenswarm/extensions/registry.py`) offers three DI slots plus generic `register(event, handler)` / `trigger(event)` callbacks over an in-process callback framework. Separately, `jiuwenswarm/common/hooks_config.py` defines 17 hook events including `PreToolUse`, `PostToolUse`, `BeforeModelCall`, `AfterModelCall`, and `SessionEnd` — but `HookType` is `COMMAND | PROMPT`, i.e. out-of-process shell commands and prompt injection, not in-process Python handlers. Neither surface can claim a `/workflow` slash command, and neither can build an IR in-process at capture time. Optionality is preserved structurally instead: the package imports nothing from the session runtime, and one adapter file carries all runtime knowledge. *Tradeoff:* the feature ships in the core tree and shares its release cadence. *Rejected:* extending the extension API first (adds an unrelated platform change to the critical path); in-tree with free coupling (forfeits extractability for nothing).

**Three determinism classes, chosen per step, not per workflow.** Frozen (tool + all args pinned), Transform (prompt template + output schema pinned), Agent (goal, tool allowlist, output contract, iteration budget pinned). *Tradeoff:* a workflow is not fully deterministic; only its structure is. *Rejected:* freeze everything — fails on any input variation and discards the judgment that made the agent useful.

**A separate IR layer rather than constructing `Workflow` directly.** Makes extraction testable without agent-core and the saved artifact inspectable and versionable. *Tradeoff:* one more format to maintain and migrate. *Rejected:* direct construction (couples analysis to the engine, opaque artifact); serialising agent-core's `WorkflowSpec` (verified insufficient — it holds `edges`, `stream_edges`, `comp_configs`, `start_nodes` but not component class or construction params, and no `from_dict` exists anywhere under `core/workflow/`; it does not round-trip).

**IR schema borrowed from agent-studio, nothing imported.** `WorkflowIr` is ~25 lines of pydantic proven against this exact graph shape and already carries the `${...}` convention. *Tradeoff:* schema-level coupling to a design we don't control. *Rejected:* importing `agent-runtime` (drags in SpiffWorkflow, a second orchestration engine); inventing a format (no benefit over a proven one).

**Deterministic analysis; the model is advisory only.** Stages 1–7 are pure functions. *Tradeoff:* worse names and descriptions than an LLM-authored plan would produce, and heuristic classification. *Rejected:* LLM-driven extraction — a non-deterministically produced artifact is the exact problem this feature exists to solve.

**Classification uses persisted reasoning when present, heuristic when not.** Revised in Round 1: reasoning **is** persisted, contrary to the Round 0 assumption. `interface.py` buffers durable reasoning and either attaches `reasoning_content` to the `chat.tool_call` / `chat.final` it precedes or falls back to a standalone `chat.reasoning` record; `session_history.py:121` persists any assistant record carrying non-empty `reasoning_content`. So a tool call preceded by substantive reasoning is evidence of deliberation → Agent, and a call with no reasoning and fully-resolved provenance is a Frozen candidate. Reasoning is model-dependent and may be absent from an entire session; where it is, classification falls back to defaulting toward Agent, and the `CaptureReport` records which path was used. *Tradeoff:* classification quality now varies by model. *Rejected:* adding a new persisted LLM-boundary event — no longer necessary, and it would have touched the session runtime the import boundary keeps at arm's length.

**Provisional until a matching replay.** A capture from one session is n=1. Promotion requires a live run with `outcome == success` **and** one of three bases: `result_consistent == True`, an explicit `manual_ack`, or a registered deterministic `evaluator` (§ Result-consistency contract). Round 5 correction: earlier revisions stated the rule as `result_consistent == True` alone, which made the manual path impossible on exactly the generative and mixed terminals it was introduced for — the same cases where `result_consistent` is always `None` by construction. *Tradeoff:* friction before a workflow is trusted. *Rejected:* trusting first capture — fills the library with one-offs.

**Effects derived from `idempotent`, gated at run, and `read` never inferred.** See § Effect model. Because nothing on `ToolCard` distinguishes a read from an idempotent write, every undeclared tool gates: non-idempotent as `write_effectful`, idempotent as `write_idempotent`, and only an explicit `properties["effect"] == "read"` is treated as safe to execute in a dry run. *Tradeoff:* until tools declare themselves, dry runs execute almost nothing and are close to a pure plan render — which is what § Dry-run contract now honestly claims they are. *Rejected:* inferring `read` from `idempotent` (Round 1's error — it would have executed idempotent writes during a dry run); inventing an effect field agent-core does not have.

**Agent mode is the Phase 1 default; team mode is contingent.** Round 3: team support ships only if the `T009a` scope gate proves that leader and teammate tool events reach parent history (assumption 15, risk 12). Until it passes, the planned surface is `interface_deep.py` dispatch only, and `team_helpers.py` dispatch (`T055a`) is not scheduled. This is the safe default because the gate can only be settled empirically and the review could not settle it from source in either direction. *Tradeoff:* team users may not get `/workflow` in Phase 1.

**Parent session only in Phase 1.** Subagent work is persisted to separate per-subagent history files (`resolve_subagent_history_path`, `session_history.py:79`), not merged into the parent stream. Merging them deterministically is its own ordering problem. Phase 1 captures the parent span; a span containing `chat.subagent_activity` marks the workflow `marginal` and emits the subagent step as an Agent-class step rather than descending into it. *Tradeoff:* a subagent-heavy session captures as a coarse workflow. *Rejected:* silently ignoring subagent events (produces a workflow with an unexplained gap).

**Read-only visualisation in Phase 1.** Mermaid via `Workflow.draw()`. A2UI assessed and deferred: its `BasicCatalog` (`Text`, `Card`, `Button`, `List`, `Image`, `Markdown`, `TextField`, `CheckBox`, `MultipleChoice`, `Slider`, `DateTimeInput`) has no canvas or node component and no visible extension point, so it cannot draw the graph — though it suits the bindings-correction surface, which is where the real ambiguity sits. *Rejected:* `@antv/x6` via agent-studio's frontend (reintroduces the excluded dependency).

---

## Toolchain

Skill inventory scan not yet run — Phase 0 was completed outside the skill's recon step. Run the scan against `~/.claude/skills/` and `~/.agents/skills/` before the build phase and populate this section, or delete it if nothing matches.

Known relevant now:
- **agent-core** (`openjiuwen.core.workflow`, `openjiuwen.core.session`) — existing dependency, the execution engine.
- **No new third-party dependencies.** The IR models use the repo's existing pydantic v2; serialisation is tested against that version rather than assumed portable across v1/v2. Any proposal that adds a dependency contradicts the approved requirement document.

---

## Assumptions

_Confirmed via direct source reading of `openJiuwen-ai/jiuwenswarm`, `openJiuwen-ai/agent-core`, and `openJiuwen-ai/agent-studio` (reference only). Each is checkable — attack them. Entries 8, 9, 10 were corrected in Round 1; 3–5 were downgraded; 14–17 are new._

1. `${node_id.field}` is **agent-core native**, not an agent-studio invention — `openjiuwen/core/session/utils.py` defines `is_ref_path` (line 171), `extract_origin_key` (line 175), and `get_by_schema` recursively resolves reference leaves in an `inputs_schema` against runtime state. *Consequence:* provenance edges serialise with no translation layer. — source: agent-core source
2. Every step class needed already exists as an agent-core component: `components/tool/tool_comp.py`, `components/llm/llm_comp.py`, `components/llm/react/`, `components/flow/loop/loop_comp.py`, `components/flow/branch_comp.py` + `condition/`. — source: agent-core source
3. **[UNVERIFIABLE IN THIS CHECKOUT — converted to a test]** Loop body-to-body connections must not be registered at root level; they belong to the `LoopGroup`, with boundaries derived from virtual `{node_id}_input`/`{node_id}_output` connections. — was sourced to agent-studio `ir_converter.py`, which is not present here. `T0xx` pins this as a characterization test against local agent-core before `ir_to_workflow.py` relies on it.
4. **[UNVERIFIABLE IN THIS CHECKOUT — converted to a test]** The default branch must be registered last and branch nodes deferred until routes are wired, because `add_workflow_comp` → `register_branch_targets` requires the full router at registration time. — same provenance and same treatment as 3.
5. **[UNVERIFIABLE IN THIS CHECKOUT — converted to a test]** Three agent-core patches are load-bearing: `loop_body_session_cleanup`, `parallel_branch_grouping`, `nested_branch_barrier`. Whether all three are needed for *our* node vocabulary was never verified even in Round 0. Each is applied only if a test demonstrates the failure it fixes against our vocabulary; unneeded patches are dropped.
6. Raw Pregel cannot do dynamic fan-out — `core/graph/graph.py`'s `ConditionalRouter` selects only among statically registered targets and `register_branch_targets` requires a known target set. Dynamic fan-out exists at the workflow-component layer via `LoopComponent`, not below it. *Consequence:* the workflow layer is the build target, not Pregel. — source: agent-core source
7. `history.jsonl` persists `chat.tool_call` and `chat.tool_result` joinable on `tool_call_id`, giving a complete ordered step list with arguments and outputs — sufficient for pipeline stages 1–6 with no new capture path, **subject to assumptions 14 and 15**. — source: `session_history.py`
8. **[CORRECTED in Round 1]** Reasoning **is** persisted. `interface.py` (~2708–2760) buffers durable reasoning chunks and either attaches `reasoning_content` to the `chat.tool_call`/`chat.final` that follows, or writes a standalone `chat.reasoning` record as a fallback; `session_history.py:121-122` persists any assistant record whose extra carries non-empty `reasoning_content`. Round 0 asserted the opposite and built a decision on it. *Consequence:* classification consumes reasoning when present (§ Key decisions) — but reasoning is model-dependent and may be absent, so the heuristic remains the fallback, not the primary. — source: jiuwenswarm source
9. **[CORRECTED in Round 1]** Neither extension surface can host this feature — but not for the reason Round 0 gave. `ExtensionRegistry` has three DI slots **plus** generic `register()`/`trigger()` callbacks (`registry.py:87-106`), and `hooks_config.py` defines **17** events including `PreToolUse`/`PostToolUse`/`BeforeModelCall`/`AfterModelCall`, not nine lifecycle-only events. The blocking facts are narrower: no surface owns slash-command dispatch, and `HookType` is `COMMAND | PROMPT` — out-of-process, so a hook cannot build an in-process IR. Conclusion unchanged, argument replaced. — source: `registry.py`, `hooks_config.py`
10. **[CORRECTED in Round 1]** `evolution_slash.py` is a valid structural precedent, and it is dispatched from **two** places, not one: `team_helpers.py:1525` (team mode) and `interface_deep.py:10461` (regular agent mode). Round 0's "one dispatch line / only two files outside the package" was wrong; it is two dispatch lines and three files, or Phase 1 must scope itself to team mode explicitly. The plan takes both. — source: jiuwenswarm source
11. `WorkflowCard.tool_info()` returns a `ToolInfo` with `name`/`description`/`parameters`, so a saved workflow is directly exposable to an agent as a tool. Capability built, **not enabled by default** in Phase 1 — agent-invoked execution has effect-gating implications the dry-run model doesn't yet cover. — source: agent-core source
12. Scope exclusions confirmed with the user: no agent-studio dependency, no swarm-scan / RSPL / SEPL integration, no security review gate (tracked separately), A2UI editing deferred to Phase 2. — source: user interrogation
13. Workload estimate 3.5 person-months for Phase 1, as submitted in the approved requirements document. — source: RAT requirements review document
14. **[NEW]** History writes are asynchronous — `append_history_record` enqueues onto `_WRITE_QUEUE` served by a daemon writer thread (`session_history.py:618-653`), with a receipt mechanism and a `_WRITE_QUEUE.join()` drain at `:841`. *Consequence:* capture that reads immediately after a request can observe a truncated span. Approach step 2 requires the drain. — source: `session_history.py`
15. **[NEW, PARTIALLY VERIFIED — REVISED in Round 2]** Two separate questions about team-mode capture, one settled and one open.
    *Settled:* the 512-character truncation of `chat.tool_result` (`_truncate_team_tool_result_event`, `team_helpers.py:1338`) is applied at `:2424` inside `_consume_stream_with_query`, the **client-forwarding** path. It does not demonstrably reach disk: the only history call in that module is `_persist_team_history_event` (`:3011`), which persists **only** `team.member` and `team.task` and returns early on everything else.
    *Open, and larger:* that same fact means it is unproven that teammate and leader `chat.tool_call` / `chat.tool_result` events reach the **parent** session history at all. If they do not, "parent span only" (§ Key decisions) captures little or nothing of a team session. Counter-evidence exists — `session_history.py:425-449` defines a **read-side** filter `_is_team_relevant` that explicitly whitelists `chat.tool_call` and `chat.tool_result` gated on `mode == "team"`, and `read_team_history_records` (`:475`) applies it, which would be pointless if such records were never written. Neither side is conclusive from static reading. The `team-mode` fixture (approach step 3) settles it by asserting on real recorded output, and the `truncated: True` capture guard fails safe either way. — source: `team_helpers.py`, `session_history.py`
16. **[NEW — CORRECTED in Round 2]** `ToolCard` declares `exposure`, `input_params`, `properties`, `parallel_safe`, `stateless`, `idempotent` — and **no** read/write effect class, and no preview or dry-run interface (`agent-core/openjiuwen/core/foundation/tool/base.py:22-62`). Round 1 concluded from this that `idempotent == True` means `read`. It does not: the field documents "repeated invocations with the same inputs have no **additional** side effects", which an idempotent *write* satisfies. § Effect model now maps `idempotent == True` to `write_idempotent` and never infers `read`. — source: agent-core source
17. **[NEW]** Subagent traces live in per-subagent history files under `<session>/subagents/` (`resolve_subagent_history_path`, `session_history.py:79`), not in the parent stream. *Consequence:* Phase 1 captures the parent span only, and marks subagent-containing captures `marginal`. — source: `session_history.py`

---

## Risks / open questions

**Previously open, now closed by this revision:**

- ~~1. Persisted LLM-boundary event~~ — closed. No new event needed; reasoning is already persisted (Assumption 8), and classification consumes it with the heuristic as fallback.
- ~~2. Workflow store versioning scheme~~ — closed by § Store contract.
- ~~3. Tool schema fingerprinting~~ — closed by § Fingerprint contract.
- ~~4. Parameter-lift confirmation~~ — closed: confirmation required exactly where confidence is below `exact` or candidates are plural.

**Remaining risks, restated against the now-specified design:**

5. **Provenance rules are specified but unmeasured.** § Provenance algorithm is now type-preserving, two-index, and refuses to bind containment matches — but its tier ordering, the 12-character containment floor, the 80% structural threshold, and the global-matching floor are chosen, not derived. The `ambiguous-provenance` fixture measures false-bind and false-unresolved rates; the thresholds are expected to move once measured.
6. **Effect gating over-retains and under-executes.** Because `read` is never inferred and `idempotent` defaults to `False`, nearly every step will gate until tools declare `properties["effect"]`. The slice keeps almost everything and dry runs execute almost nothing. This is the safe direction, but it weakens risk 10 directly and makes the dry run less useful than the command surface implies. Measured by the `dead-steps` fixture. *Mitigation available but not in Phase 1 scope:* declaring `effect` on the built-in tool set.
7. **Fan-out criteria may be too strict.** Seven conjunctive conditions plus a three-call floor will miss real loops — the deliberate direction, since a false loop changes semantics silently while a false sequence does not. The `fan-out` fixture measures the miss rate.
8. **Generative-terminal workflows cannot auto-promote at all.** Resolved in Round 2 in the safe direction, but the cost is real: a workflow whose last step writes prose stays `provisional` indefinitely unless a human acknowledges it or someone registers a deterministic evaluator. If most captured workflows end generatively, the promotion model is mostly manual and the `verified` state carries less weight than the design assumes.
9. **Viability thresholds are unjustified.** `<2` steps, `frozen == 0`, `agent/total > 0.8` are asserted with no evidence. They will produce false refusals or false saves until measured. Risk 6 pushes `frozen` down, so the `frozen == 0` trigger in particular may refuse workflows that are fine.
10. **Token-saving claim is unmeasured, and risks 6 and 7 push against it.** Asserted in the requirements document without a figure. `T060` is the only place it gets tested. If the saving turns out marginal, one of the feature's stated outcomes is wrong.
11. **The import boundary may not survive contact.** `T004` enforces it mechanically, but approach step 2 already requires the adapter to know about queue draining and request-completion semantics — runtime knowledge that must stay on the adapter side of the seam. Worth attacking: is the injected-interface design actually sufficient?
12. **Team-mode capture is contingent, and agent-mode-only is now the default plan.** Assumption 15's open half is the single largest scope risk in the plan, and Round 3 resolved the *planning* question by inverting the default rather than by settling the *factual* one, which no amount of static reading could settle. If teammate tool events do not reach parent history, team capture cannot work from the parent span, and the choices are descending into `subagents/` (excluded from Phase 1), adding a persistence path (touches the runtime the import boundary holds at arm's length), or shipping Phase 1 as **agent-mode only** — the assumed fallback. The `team-mode` fixture must run before the `interface_deep.py` and `team_helpers.py` dispatch work is scheduled, not after.
13. **Characterization tests replace three inherited assumptions.** Assumptions 3–5 are now tests to be written rather than facts. If agent-core's real registration constraints differ from what agent-studio's converter implies, `ir_to_workflow.py` is more work than `tasks.md` budgets.
15. **[NEW] Trusted-provider allowlisting is an unbuilt dependency.** § Effect model now honours a `read` declaration only from local built-ins and allowlisted providers, but this repo has no such provider-trust registry to consult. Phase 1 must either ship a minimal one or treat every non-built-in `read` as `write_effectful` — the latter is the assumed fallback and makes dry runs weaker still, compounding risk 6.
16. **[NEW] Effect metadata in the fingerprint will cause churn.** Folding `idempotent` and `properties["effect"]` into `tool_fingerprint` means any tool author adding effect metadata to an existing tool invalidates every workflow pinned to it — the correct behaviour on a genuine class change, but indistinguishable from a tool simply becoming better documented. Expect a wave of refusals the first time the built-in tool set declares effects, and consider a one-time re-pin path.

14. **[NEW] The dry run is now weak enough to question its default.** It executes only declared reads, cannot set `result_consistent`, and does not prove the sequence. It remains the default while a workflow is `provisional` because rendering the plan before first live execution is still worth something — but if risk 6 holds, "dry run" is a plan preview, and the command surface should say so rather than implying a rehearsal.

## Out of scope

- Editing steps or bindings in place — read-only inspection only in Phase 1
- Composing a workflow from more than one session
- Merging subagent histories into the captured span (Assumption 17)
- Inferring value transformations for provenance — multi-origin derived values are reported, never bound (§ Provenance algorithm)
- Sharing, exporting, or a workflow marketplace
- Automatically detecting that a task should be captured
- Streaming execution, parallel branch joins, stream-transform steps
- Any dependency on agent-studio (schema borrowed as design reference only)
- A security review gate for captured workflows — tracked in the security workstream
- swarm-scan, RSPL, SEPL integration
- A2UI-based graph editing — Phase 2, with two unverified blockers recorded in `specs/001-workflow-capture/research.md` R7
