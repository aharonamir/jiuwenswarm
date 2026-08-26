# Plan Review Log: Workflow Capture

Entered at Phase 2 (`/codex-review`). Phases 0–1 completed outside the skill:

- **Recon** — direct source reading of `openJiuwen-ai/jiuwenswarm`, `openJiuwen-ai/agent-core`, and `openJiuwen-ai/agent-studio` (reference only). Findings are recorded as sourced entries in `PLAN.md` `## Assumptions` (13 entries, each with a file path or user confirmation as its source) and in full in `specs/001-workflow-capture/research.md`.
- **Interrogation** — conducted conversationally rather than via the structured decision map. Decisions the user settled: exclude agent-studio as a dependency; exclude swarm-scan / RSPL / SEPL from scope; defer A2UI editing to Phase 2 with Mermaid as the Phase 1 render; in-tree package rather than extension; token reduction named as an explicit outcome.

**Not covered by the prior interrogation** — four decisions remain genuinely open and are listed as risks 1–4 in `PLAN.md` `## Risks / open questions`. They have no assigned owners. If the review can force them closed, that is a better outcome than deferring them to implementation.

MAX_ROUNDS=5. Codex model: gpt-5.5 (reasoning effort medium), read-only sandbox every round.

---

## Reviewer orientation

`PLAN.md` asserts thirteen claims about repository source. Those are checkable — the repos are the strongest attack surface in this plan, and the assumptions were written to be falsifiable rather than persuasive.

Author's own assessment of where the plan is weakest, offered so review effort goes somewhere useful rather than confirming what is already known to be soft: risks 5–12, and among those particularly **5** (provenance under-specified, and everything downstream depends on it), **6** (backward slice may remove implicitly load-bearing effectful steps), and **8** (result-consistency comparison drives promotion but has no operational definition).

Supporting artifacts, if the reviewer wants detail beyond `PLAN.md`:
- `specs/001-workflow-capture/spec.md` — functional requirements FR-001…FR-023, acceptance scenarios, edge cases
- `specs/001-workflow-capture/research.md` — the source-verification record behind the assumptions
- `specs/001-workflow-capture/data-model.md` — entities, validation rules, state transitions
- `specs/001-workflow-capture/contracts/commands.md` — the command surface contract
- `specs/001-workflow-capture/tasks.md` — 63 tasks with dependency ordering

---

## Round 1 — Codex

**Findings**

- `PLAN.md` assumption 8 is stale: this checkout does persist reasoning in some paths. `interface.py` buffers `chat.reasoning` and either attaches `reasoning_content` to `chat.tool_call`/`chat.final` or writes a standalone `chat.reasoning`; see [interface.py](/home/amir/workspace/jiuwenswarm/jiuwenswarm/server/runtime/agent_adapter/interface.py:2708) and [interface.py](/home/amir/workspace/jiuwenswarm/jiuwenswarm/server/runtime/agent_adapter/interface.py:2717). Fix: make classification consume persisted reasoning when present, and only fall back to the heuristic when absent.

- The adapter boundary is undercounted. Evolution slash is dispatched from [team_helpers.py](/home/amir/workspace/jiuwenswarm/jiuwenswarm/server/runtime/agent_adapter/team_helpers.py:1525) and regular agent mode via [interface_deep.py](/home/amir/workspace/jiuwenswarm/jiuwenswarm/server/runtime/agent_adapter/interface_deep.py:10461), so “one dispatch line in `team_helpers.py`” will miss non-team `/workflow` use. Fix: explicitly scope Phase 1 to team-only or add the regular adapter dispatch and update the “only two files” claim.

- The extension claim is overstated. `ExtensionRegistry` has arbitrary callback `register()`/`trigger()` methods beyond the three DI slots; see [registry.py](/home/amir/workspace/jiuwenswarm/jiuwenswarm/extensions/registry.py:87), and the repo also has a separate 17-event user hook surface with pre/post tool and model events in [hooks_config.py](/home/amir/workspace/jiuwenswarm/jiuwenswarm/common/hooks_config.py:20). Fix: rewrite the argument to say the current extension/hook surfaces still cannot own slash dispatch and persisted-session extraction, not that only three registrations exist.

- `history.jsonl` sufficiency is not proven. Writes are asynchronous via `_WRITE_QUEUE` ([session_history.py](/home/amir/workspace/jiuwenswarm/jiuwenswarm/server/runtime/session/session_history.py:636)), and capture may read before the previous request’s terminal events flush. Fix: the adapter must wait for the request-completion receipt or drain/join history writes before extraction.

- Team tool results are truncated before forwarding and likely before history append in the team path; `_truncate_team_tool_result_event()` caps `result` and `raw_output` to 512 chars ([team_helpers.py](/home/amir/workspace/jiuwenswarm/jiuwenswarm/server/runtime/agent_adapter/team_helpers.py:1338)). That breaks provenance and result consistency for large artifacts. Fix: capture from the durable untruncated source or persist a capture-grade raw payload/hash before UI truncation.

- Subagent work is stored under separate subagent histories, not just the parent history; see [interface_deep.py](/home/amir/workspace/jiuwenswarm/jiuwenswarm/server/runtime/agent_adapter/interface_deep.py:12765) and `resolve_subagent_history_path` in [session_history.py](/home/amir/workspace/jiuwenswarm/jiuwenswarm/server/runtime/session/session_history.py:79). Fix: define whether `/workflow save` includes subagent traces and implement deterministic merge ordering if yes.

- Provenance is underspecified enough to invalidate FR-003/FR-009. “Exact then structural” lacks canonicalization rules for JSON strings vs objects, list item identity, substrings, duplicated values, coercions, derived values, and many-to-one transformations. Fix: define a deterministic provenance algorithm with candidate sets, canonicalization, confidence thresholds, and explicit “derived-but-not-bindable” output.

- The data model cannot represent ambiguous provenance cleanly. `Binding.kind` is singular while `confidence == ambiguous` says multiple origins exist, so an implementation will either pick one or lose the candidates. Fix: add `candidates: list[BindingCandidate]` and require `kind = unresolved` for ambiguity unless the user confirms one.

- Backward slicing will delete effectful prerequisites whose outputs are not referenced. Auth, cache warmup, permission grants, cwd/file setup, and tool-discovery calls can be load-bearing without producing downstream values. Fix: preserve all effectful/idempotent-write steps in the task span unless a tool declares itself skippable or the captured trace proves no state dependency.

- Dry-run semantics are unsafe and vague. “Dry run reports effectful steps” is fine, but if the executor calls a generic `ToolComponent` it will execute the tool; agent-core tool cards only expose `idempotent`, not a preview/dry-run interface ([base.py](/home/amir/workspace/agent-core/openjiuwen/core/foundation/tool/base.py:53)). Fix: dry-run must statically skip effectful components and render planned inputs; only tools with an explicit preview capability may be invoked.

- Tool effect classification is not grounded in the available schema. Tool cards have `idempotent`, `parallel_safe`, `stateless`, and `properties`, but no `read/write/write_effectful` declaration ([base.py](/home/amir/workspace/agent-core/openjiuwen/core/foundation/tool/base.py:22)). Fix: define a mapping from existing fields/properties and require explicit metadata for read-vs-write before treating anything as read.

- Tool schema fingerprinting is a hard blocker, not an implementation detail. The plan does not define normalization for Pydantic vs JSON schema, defaults, descriptions, nullable/anyOf ordering, or whether output schema participates. Fix: specify a canonical JSON-schema fingerprint over tool id, input contract, required fields, defaults, and effect metadata, with a compatibility policy.

- Promotion is internally inconsistent. `RunRecord.result_consistent` is `None` for dry runs, but command text says dry run can “Match the original result”; live promotion depends on an undefined comparison. Fix: define result consistency per terminal artifact type: exact/hash for files and structured data, schema+required-fields for JSON, deterministic evaluator contract for generative text, and never claim consistency from skipped effectful dry runs.

- Store concurrency and path safety are missing. A symlink `current`, immutable version dirs, and append-only `runs.jsonl` are race-prone under two saves/runs/deletes, and user names are only “normalised to a slug.” Fix: add strict slug validation, no symlink traversal, per-workflow file locks, atomic temp-dir rename, atomic `current` replacement, and append locking.

- Agent-core patch assumptions 3-5 cannot be verified from this checkout: no `../agent-studio` files were present/readable in my search, so `ir_converter.py` constraints and the three patch names are not checkable here. Fix: vendor the specific reference file or replace the assumptions with tests against local `agent-core` behavior.

- Fan-out detection will overfit coincidence. “Sibling calls whose args all derive from one prior result” confuses batching with intentional loops, especially when calls share a search result plus distinct constants or side effects. Fix: require same tool, same argument template except item binding, contiguous same-turn or same-planning cluster, no cross-call dependencies, compatible effects, and report low-confidence loops instead of collapsing.

- The plan lacks observability for replay failures. `RunRecord` only records failed component and token cost; it omits resolved inputs, tool fingerprint observed, skipped dry-run effects, output hashes, consistency rationale, and capture algorithm version. Fix: add an audit record per run/component sufficient to explain promotion, incompatibility, and drift.

- “No new dependencies” conflicts with Pydantic if the package relies on it but does not pin/verify the installed major version. The repo already uses Pydantic, but model behavior differs materially between v1/v2. Fix: state “no new third-party dependencies; use the repo’s existing Pydantic version” and test serialization against that version.

VERDICT: REVISE
### Claude's response

I verified every checkable claim against the repo before acting on it. Fifteen findings accepted, two corrected as overstated, one accepted with a narrower scope.

**Accepted and verified against source:**

- **Reasoning is persisted — assumption 8 was wrong.** Confirmed: `interface.py:2708-2760` buffers durable reasoning and attaches `reasoning_content` to the following `chat.tool_call`/`chat.final`, or writes a standalone `chat.reasoning`; `session_history.py:121-122` persists any assistant record carrying it. This closes open risk 1 outright — no new emit-path event is needed. Classification now consumes reasoning when present and falls back to the default-toward-Agent heuristic when the model emitted none, recording which path it used.
- **Two dispatch sites, not one.** Confirmed: `handle_evolution_slash_command` is called from `team_helpers.py:1525` *and* `interface_deep.py:10461`. The "one dispatch line / only two files" claim was wrong. The plan now takes both dispatch points; three files outside the package.
- **Async history writes.** Confirmed: `_WRITE_QUEUE` + daemon writer thread (`session_history.py:618-653`), with a receipt mechanism and `_WRITE_QUEUE.join()` at `:841`. Capture now waits on the receipt or drains before extraction, and refuses if the terminal `chat.request_completed` has not landed. New assumption 14.
- **No effect class on `ToolCard`.** Confirmed: `base.py:22-60` has `exposure`, `input_params`, `properties`, `parallel_safe`, `stateless`, `idempotent` — and no read/write field, and no preview interface. This is the most useful finding in the round: it replaced an invented schema field with a real one. Effect now derives from `idempotent` (which already defaults `False` secure-by-default), with a `properties["effect"]` override. New assumption 16.
- **Dry run was unsafe.** Since there is no preview interface, a dry run that reaches a generic `ToolComponent` executes the tool. Dry run is now statically effect-skipping: it executes only `read` components, resolves skipped outputs to typed placeholders, marks dependents "would run", and always reports `result_consistent = None`.
- **Subagent histories are separate.** Confirmed: `resolve_subagent_history_path`, `session_history.py:79`. Phase 1 now explicitly captures the parent span only and marks subagent-containing captures `marginal`, rather than leaving the question open. New assumption 17.
- **Provenance, fingerprinting, store, fan-out, result-consistency, run audit, `Binding` ambiguity.** All accepted and written as specified contracts in the new `## Contracts closed in this revision` section. `Binding` gains `candidates: list[BindingCandidate]` and ambiguity now forces `kind = unresolved` instead of a silent pick. Open risks 2, 3 and 4 are closed there.
- **`T056` was internally inconsistent.** Correct, though cited to the wrong file — it is `tasks.md:112` ("run dry, assert consistency"), which contradicts `data-model.md:127` (`None` for dry runs). The dry-run leg now asserts `result_consistent is None` and that no effectful step executed.
- **Pydantic.** Accepted as a one-line clarification: existing pydantic v2, serialisation tested against it.

**Corrected as overstated:**

- **Extension claim.** The critique is right that assumption 9 was factually wrong — `ExtensionRegistry` does have generic `register()`/`trigger()` (`registry.py:87-106`), and `hooks_config.py` defines 17 events including `PreToolUse`/`PostToolUse`/`BeforeModelCall`/`AfterModelCall`, not nine lifecycle-only ones. But it does not change the decision, and the reviewer agrees: `HookType` is `COMMAND | PROMPT` — out-of-process shell and prompt hooks, which cannot own slash dispatch or build an in-process IR. Argument rewritten, conclusion unchanged.
- **Team truncation "likely before history append" — not confirmed.** `_truncate_team_tool_result_event` is applied at `team_helpers.py:2424`, inside `_consume_stream_with_query`, which is the client-forwarding path. History persistence runs through a different function, `_persist_team_history_event` at `:3011`. The truncated object does not demonstrably flow to disk. The mechanism is real and the risk is real, so I did not dismiss it: it is recorded as partially-verified assumption 15, a `team-mode-truncated` fixture settles it, and a `truncated: True` capture guard fails safe either way. It is also now risk 12 — the finding most likely to shrink Phase 1 scope — but the plan does not assert a breakage it could not prove.

**Accepted with narrower scope:**

- **agent-core patch assumptions 3–5 unverifiable here.** Correct — `agent-studio` is not in this checkout, so those three constraints cannot be checked. Rather than vendoring a reference file, all three are downgraded to `[UNVERIFIABLE IN THIS CHECKOUT]` and converted to characterization tests against local agent-core, with each patch applied only if a test demonstrates the failure it fixes against our node vocabulary. That also absorbs the reviewer's separate point about inheriting the patches blind. Cost of the conversion is recorded as new risk 13.

Net: four open questions closed, three assumptions corrected against source, four new sourced assumptions added, seven contracts specified that were previously prose. Remaining risks were rewritten to attack the specified design rather than the absence of one — the thresholds are now stated and unmeasured rather than undefined.

## Round 2 — Codex

**Findings**

- Supporting docs are now inconsistent with `PLAN.md`. The top-level plan says `BindingCandidate`, `team-mode-truncated`, atomic text `current`, rewritten `T049`/`T056`, and two dispatch sites exist in the task/spec contract, but [tasks.md](/home/amir/workspace/jiuwenswarm/specs/001-workflow-capture/tasks.md:25), [tasks.md](/home/amir/workspace/jiuwenswarm/specs/001-workflow-capture/tasks.md:112), [data-model.md](/home/amir/workspace/jiuwenswarm/specs/001-workflow-capture/data-model.md:66), [data-model.md](/home/amir/workspace/jiuwenswarm/specs/001-workflow-capture/data-model.md:176), and [commands.md](/home/amir/workspace/jiuwenswarm/specs/001-workflow-capture/contracts/commands.md:75) are still Round 0. Fix: update the supporting artifacts or make `PLAN.md` the only implementation source and explicitly obsolete the stale files.

- The provenance algorithm has a logic bug: the candidate index includes session-input fields, then “exactly one candidate → `kind = reference`,” so a user-derived value with one session-input match becomes a reference instead of a parameter; the later “no candidate, but appears in session input → parameter” branch is unreachable for indexed values. Fix: separate prior-step candidates from session-input candidates and resolve session-only matches as `parameter`.

- Numeric canonicalization erases type and value distinctions that matter: `1`, `1.0`, `"1"`, and likely `"001"` can collide, creating invalid bindings into string IDs, issue numbers, versions, ports, or schema-constrained strings. Fix: only coerce numeric strings when the source and target schemas are numeric, and retain original scalar type in the provenance hash.

- “Do not index scalars shorter than 4 / small integers / booleans / null” prevents common load-bearing values from binding: booleans, status enums, `US`, `Q3`, small counts, and flags often drive tools. Fix: exclude those values only from global value matching, but allow schema/path-aware matching and exact session-argument equality for known fields.

- Containment matches are not replayable as specified. If an argument is a substring of a prior result or user message, binding to the whole origin leaf does not reproduce the extracted substring. Fix: containment must produce an unresolved candidate requiring user confirmation plus an explicit extraction transform, or be treated as a new parameter/constant.

- The effect model is still unsafe: `idempotent == True → read` is false. Idempotent writes exist, including “write the same file,” “upsert,” “mark as read,” and cache/auth setup; agent-core’s `idempotent` only says repeated calls have no additional side effects, not no side effects. Fix: default `idempotent == True` to `write_idempotent`, require explicit `properties["effect"] == "read"` for read, and keep idempotent writes out of dry runs unless explicitly allowed.

- The revised slicing rule says preserve `write_effectful`, but the data model still has `write_idempotent` and the new effect derivation no longer emits it. This creates a gap where idempotent write prerequisites can be sliced or dry-run executed as reads. Fix: restore `write_idempotent` to the effect contract and retain/skip it conservatively.

- The dry-run placeholder propagation changes the step sequence. The guarantee is “same steps in same order,” but dry run now turns downstream dependent steps into “would run” instead of executing them, while read-only descendants independent of skipped outputs may still execute. Fix: define dry-run as a plan/audit mode with an explicit executed/skipped/would-run DAG, and stop claiming it validates result consistency or exact sequence execution.

- Auto-promotion for generative terminal output is not a consistency check. “Non-empty + output contract + frozen step integrity” can promote a workflow that produced wrong prose, wrong facts, or a publish confirmation for the wrong content. The plan names the weakness but still uses it to satisfy FR-013. Fix: do not auto-promote free-text/generative terminal workflows; require manual promotion or a deterministic domain-specific evaluator.

- `PLAN.md` introduces `/workflow promote <name> --acknowledge`, but the command contract still lists only `save`, `run`, `list`, `show`, `delete` ([commands.md](/home/amir/workspace/jiuwenswarm/specs/001-workflow-capture/contracts/commands.md:10)). Fix: add the promote command contract, permissions/audit behavior, and tests, or remove manual promotion from Phase 1.

- The fingerprint contract excludes output schema, but output drift can break downstream bindings before execution completes. “Caught by binding validation” is not enough if compatibility preflight cannot know the current output shape. Fix: include declared output schema/shape in compatibility when available, or require a preflight capability contract that validates all referenced output paths before running.

- The history-drain rule is ambiguous: “wait on the write receipt for the capturing request” cannot work before `/workflow save` has completed, and the terminal event needed is the previous non-workflow task, not the slash command itself. Fix: drain the existing queue before capture and segment only through the latest persisted `chat.request_completed` for a non-workflow request.

- Your pushback on truncation is correct as far as source proves: `_truncate_team_tool_result_event()` is applied in the forwarding loop ([team_helpers.py](/home/amir/workspace/jiuwenswarm/jiuwenswarm/server/runtime/agent_adapter/team_helpers.py:2424)), while `_persist_team_history_event()` only persists `team.member`/`team.task` ([team_helpers.py](/home/amir/workspace/jiuwenswarm/jiuwenswarm/server/runtime/agent_adapter/team_helpers.py:3011)). But this reveals a different problem: parent team history may not persist teammate `chat.tool_call`/`chat.tool_result` at all, so Phase 1 “parent span only” may capture little or none of a team workflow. Fix: add a fixture that proves parent `read_session_history_records()` contains the leader/teammate tool events needed for extraction, not just whether they are truncated.

- Your extension conclusion is acceptable after revision. The generic callback registry exists, but its `trigger()` path has no return/dispatch contract, and the out-of-process hook config cannot build an in-process IR; keeping the feature in-tree is justified. Fix: no change needed beyond preserving the narrower argument.

- Characterization-test replacement for agent-studio assumptions is the right direction, but `PLAN.md` references placeholder `T0xx`, while [tasks.md](/home/amir/workspace/jiuwenswarm/specs/001-workflow-capture/tasks.md:74) still says to honor inherited constraints and apply all three patches. Fix: add concrete task IDs before conversion and change T039 to “apply only patches justified by failing characterization tests.”

VERDICT: REVISE
### Claude's response

Fifteen findings, fourteen accepted outright, one accepted with a correction to the reviewer's inference. This round found two genuine correctness bugs in contracts I had written the round before, which is the outcome that justifies the loop.

**The two that were actually wrong:**

- **`idempotent == True → read` is false.** The strongest finding of the round. agent-core's docstring reads "repeated invocations with the same inputs have no **additional** side effects" — an idempotent write satisfies that. Round 1 read the field as read-only and would have executed upserts, "mark as read", file rewrites and auth warms during a dry run, which is the precise failure the dry run exists to prevent. § Effect model now maps `idempotent == True → write_idempotent` and **never infers `read`**; only an explicit `properties["effect"] == "read"` executes in a dry run. Assumption 16 records the correction.
- **The dropped `write_idempotent` class.** Follows from the above, and worse than it looks: `data-model.md:45` already declared the three-value enum, and my Round 1 effect derivation silently reduced it to two. The slicing rule then said "retain `write_effectful`", which would have sliced idempotent-write prerequisites away as if they were reads. Slicing now retains anything that is not explicitly `read`.

**Accepted — provenance had a logic bug and three unsafe rules:**

- **Merged candidate index made the `parameter` branch unreachable.** Correct. Origin and session-input candidates are now two separate indices with an explicit five-case resolution order, including "matches both" → `unresolved`.
- **Type-erasing numeric canonicalisation.** Correct, and my Round 1 wording ("collide deliberately") made it worse. `1`, `1.0`, `"1"`, `"001"` no longer collide; scalar type is retained in the hash; numeric-string coercion happens only when both sides are numeric in their declared schemas. Issue numbers, ports, version strings and zero-padded IDs were the concrete break.
- **Excluding small scalars outright.** Correct — booleans, `US`, `Q3`, flags and small counts routinely drive tools. The floor now applies to *global* value matching only; a new `path_aware` tier keeps them bindable by argument name or JSON pointer.
- **Containment is not replayable.** The best catch in the group. If an argument is a *fragment* of an origin value, binding it to the whole leaf replays the wrong value, and the extraction that produced the fragment is captured nowhere. Containment can no longer resolve to a `reference` at all — it emits `unresolved` with the matched span and requires confirmation with an explicit extraction transform, or demotion to `parameter`/`constant`.

**Accepted — the rest:**

- **Dry run reframed as a plan/audit mode.** Placeholder propagation does change what executes, so the "same steps, same order" guarantee is not demonstrated by a dry run. It now emits an executed/skipped/would-run DAG and claims nothing about consistency. Combined with `read` never being inferred, the dry run is weak enough that I added risk 14 questioning whether it should remain the default.
- **Generative terminals no longer auto-promote.** Correct that Round 1 named the weakness and then used it to satisfy FR-013 anyway — "non-empty + contract satisfied + frozen integrity" passes equally for confidently wrong prose. Promotion for generative terminals is now manual acknowledgement or a registered deterministic evaluator; the structural checks are reported as evidence, not used as a gate.
- **`/workflow promote` did not exist in the command contract.** Correct — I invented a command in Round 1 and left `commands.md` listing five subcommands. Full contract added, plus `T049a`.
- **Fingerprint output preflight.** Accepted in the form the reviewer offered as the alternative: output schema stays out of the identity hash so benign additions don't fail a workflow, but a preflight now resolves every `${node.field}` against the current declared output schema and refuses up front. "Caught by binding validation" was too late — effectful steps could already have run.
- **The drain rule was circular.** Correct: `/workflow save` is itself a request, so waiting for "the capturing request's receipt" waits on itself. Now: drain the queue, then segment through the latest persisted `chat.request_completed` belonging to a non-`/workflow` request.
- **Stale supporting artifacts.** Correct and fixed in place rather than by precedence: `data-model.md` (candidates, tier names, effect derivation, `RunRecord` audit fields, `current` as text not symlink), `tasks.md` (T009a/T009b, T037a, T039, T046, T049, T049a, T056), `commands.md` (promote, dry-run output). A precedence note now covers anything missed. Also: the reviewer was right in **Round 1** about `commands.md` claiming a dry run "Matches the original result" — I failed to find it with a case-sensitive grep and wrongly called that finding overreach. It is at `commands.md:75` and is now removed.
- **`T0xx` placeholder and stale T039.** Correct. Concrete IDs assigned; T039 now applies only patches justified by a failing characterization test.

**Accepted with a correction to the inference:**

- **Team-mode capture viability.** The reviewer confirmed my truncation pushback and then drew a sharper conclusion from it: since `_persist_team_history_event` only handles `team.member`/`team.task`, teammate tool events may never reach parent history, which would gut "parent span only". That is the right question and it is now risk 12 — the largest scope risk in the plan. But the inference is not settled, and there is counter-evidence the review did not weigh: `session_history.py:425-449` defines a **read-side** filter, `_is_team_relevant`, that explicitly whitelists `chat.tool_call` and `chat.tool_result` gated on `mode == "team"`, applied by `read_team_history_records` at `:475`. A read filter for records that are never written would be dead code. Static reading cannot settle it either way, so assumption 15 now states both halves and the `team-mode` fixture (`T009a`) is promoted to a **scope gate that must run before the dispatch work is scheduled**, with agent-mode-only as the stated fallback.

Net for the round: two correctness bugs fixed in contracts written last round, provenance rewritten with a corrected resolution order and a tier that can no longer bind wrongly, promotion tightened, four supporting artifacts reconciled, and one scope risk promoted to a gate with an explicit fallback.

## Round 3 — Codex

**Findings**

- Provenance still has the same ambiguity bug in a different shape. In [PLAN.md](/home/amir/workspace/jiuwenswarm/PLAN.md:57), case 4 says “matches both indices → unresolved,” but cases 1-2 run first on origin matches; if a value appears in one prior output and the session input, case 1 resolves it as `reference` before case 4 can fire. Fix: evaluate “matches both origin and input indices” before any origin-only resolution.

- The command contract omits the new save confirmation switch. [PLAN.md](/home/amir/workspace/jiuwenswarm/PLAN.md:123) introduces `/workflow save --accept-bindings`, but [commands.md](/home/amir/workspace/jiuwenswarm/specs/001-workflow-capture/contracts/commands.md:21) only lists `name` and `--overwrite`. Fix: add `--accept-bindings` behavior, errors, audit trail, and tests.

- Effect drift is not fingerprinted. The fingerprint is `{tool_id, input_schema}` only ([PLAN.md](/home/amir/workspace/jiuwenswarm/PLAN.md:107)), but effect class comes from `properties["effect"]` or `idempotent`; a tool can change from `read` to `write_effectful` with the same input schema and still pass compatibility. Fix: include normalized effect metadata in the compatibility fingerprint and refuse/warn on effect-class drift before dry or live execution.

- The output-path preflight still permits unknown output shape: it resolves references against current declared output schema “where one is available” ([PLAN.md](/home/amir/workspace/jiuwenswarm/PLAN.md:111)). If no schema is available, an effectful downstream step can still run before a missing field is discovered. Fix: if any later live/effectful step depends on an output path with no current schema proof, require captured observed-shape validation before effects or refuse.

- The “explicit `properties["effect"] == "read"` wins” rule trusts tool-supplied metadata too much. For remote/MCP/plugin tools, a wrong or malicious read declaration makes dry run invoke a writer despite the whole safety model. Fix: only trust read declarations from allowlisted tool providers or signed/local built-ins; otherwise treat as `write_effectful`.

- `tasks.md` is reconciled in content but still has execution traps. It duplicates no-tools/multi-task fixtures as T009b and T010 ([tasks.md](/home/amir/workspace/jiuwenswarm/specs/001-workflow-capture/tasks.md:30)), T052 still says “all five subcommands” after `promote` was added ([tasks.md](/home/amir/workspace/jiuwenswarm/specs/001-workflow-capture/tasks.md:107)), T055 still wires only `team_helpers.py` despite PLAN requiring `interface_deep.py` too ([tasks.md](/home/amir/workspace/jiuwenswarm/specs/001-workflow-capture/tasks.md:110)), and dependencies do not gate dispatch on T009a. Fix: clean the task graph so it encodes the Round 2 plan, not just mentions it.

- `specs/001-workflow-capture/plan.md` is still Round 0 and contradicts the new source-backed decisions on extensions, dispatch sites, reasoning persistence, and file count; see [plan.md](/home/amir/workspace/jiuwenswarm/specs/001-workflow-capture/plan.md:38) and [plan.md](/home/amir/workspace/jiuwenswarm/specs/001-workflow-capture/plan.md:56). Fix: update or delete that file; “PLAN.md wins” is not enough when the stale artifact is named as supporting implementation detail.

- Manual promotion lacks authorization semantics. [commands.md](/home/amir/workspace/jiuwenswarm/specs/001-workflow-capture/contracts/commands.md:142) lets anyone who can issue the command verify a workflow after one live success, but the plan only says “acting user” is recorded. Fix: require the same permission model as live effect execution, or restrict promotion to workflow owner/admin.

- The store contract says `os.replace` is used for version directories ([PLAN.md](/home/amir/workspace/jiuwenswarm/PLAN.md:115)) but does not specify same-filesystem temp placement, fsync, or behavior if a target version dir unexpectedly exists. Fix: build temp dirs under the same workflow dir, fail if the final version exists, fsync files/directories before pointer swap, and test crash recovery.

- Your team-mode inference is plausible but not proof. `_is_team_relevant` whitelisting `chat.tool_call`/`chat.tool_result` in [session_history.py](/home/amir/workspace/jiuwenswarm/jiuwenswarm/server/runtime/session/session_history.py:425) is evidence those records may exist, but it could also support older writers, other adapters, or future paths; the only direct writer in `team_helpers.py` persists only `team.member`/`team.task` at [team_helpers.py](/home/amir/workspace/jiuwenswarm/jiuwenswarm/server/runtime/agent_adapter/team_helpers.py:3011). Fix: keep T009a as a pre-dispatch scope gate, and make agent-mode-only the default plan unless the fixture passes.

VERDICT: REVISE
### Claude's response

Ten findings, all ten accepted, nothing rejected. The findings narrowed sharply this round — one real logic bug, one good security catch, and eight hardening or spec-hygiene items. That shape is what convergence looks like.

**The logic bug:**

- **The resolution order still had the same defect in a new shape.** Correct and my error: Round 2 listed the both-indices case fourth, but cases 1–2 resolved origin matches first, so a value appearing in both a prior output and the session input became a `reference` before the ambiguity case could fire — which is precisely the situation `fixtures/session_ambiguous.jsonl` exists to test. Both indices are now queried before anything resolves, and the both-match case is evaluated first.

**The security catch:**

- **`properties["effect"] == "read"` trusted tool-supplied metadata absolutely.** `read` is the one class that makes a dry run actually invoke a tool, so a wrong or hostile `read` declaration on a remote MCP or third-party plugin tool turns the dry run into the writer it exists to prevent. A `read` claim is now honoured only from local built-ins and allowlisted providers, and downgraded to `write_effectful` with a report otherwise. `write_idempotent` and `write_effectful` are still honoured from anyone — they only gate harder. This introduced a dependency the repo does not have, recorded as new risk 15.

**Hardening, all accepted:**

- **Effect drift was not fingerprinted.** A tool could flip `read` → `write_effectful` with an unchanged input schema and pass compatibility, so a workflow captured and approved as a read would execute a writer. `effect_metadata` is now in the fingerprint and drift refuses before dry *or* live execution. Recorded new risk 16, since this will invalidate pinned workflows the first time the built-in tools declare effects.
- **"Where one is available" left the preflight hole open.** Correct — a tool with no declared output schema skipped the check and an effectful step could run before a missing field surfaced. Now: fall back to the observed shape captured at save, and if that cannot be established either, refuse when any non-`read` step depends on the path. Unproven paths may only precede reads.
- **Store contract was underspecified on the details that matter for atomicity.** Temp directories are now explicitly under the same workflow directory (same filesystem, since `os.replace` is only atomic within one), an existing target version fails rather than overwrites, files and directories are `fsync`ed before the pointer swap so a crash leaves an unreferenced complete version rather than a referenced partial one, and crash recovery is a tested path.
- **Manual promotion had no authorization model.** Correct — Round 2 recorded the acting user without checking them. Promotion now requires the same permission as `--live` execution and is restricted to owner or admin, since a `verified` workflow is gated less thereafter.

**Spec hygiene, all accepted:**

- **`--accept-bindings` was another invented command flag.** Same class of error as `/workflow promote` last round, and I did not check for others when fixing that one. Full argument contract, refusal behaviour, and audit trail added, including that `--accept-bindings` can never accept a `containment` match as a reference.
- **`tasks.md` mentioned the Round 2 plan without encoding it.** Correct on all four counts: T009b duplicated T010 (my own duplicate, now removed), T052 still said "five subcommands", T055 wired only `team_helpers.py`, and no dependency gated dispatch on the T009a scope gate. The task graph now encodes it — T055 is `interface_deep.py`, T055a is `team_helpers.py` and is blocked on T009a, and T037a→T038/T039, T049→T049a were added.
- **`specs/.../plan.md` was still Round 0.** Correct, and a fair hit: I reconciled three supporting artifacts last round and did not check the fourth, which repeated the exact extension and dispatch errors assumptions 9 and 10 had corrected. Fixed in place with a staleness banner. "PLAN.md wins" was indeed not enough.

**On team mode** — the reviewer's position converged with mine: the read-side filter is evidence, not proof, and could equally support older writers or other adapters. I accept that framing and have gone further than the fix asked for: **agent-mode-only is now the default plan**, not the fallback. Team dispatch is a contingent task blocked on the gate. That resolves the planning question in the safe direction without pretending the factual one is settled.

## Round 4 — Codex

**Findings**

1. Lifecycle state conflicts with immutable version storage. `SavedWorkflow.status` is modeled inside the saved workflow, and promotion sets it to `verified`, but the store contract says version directories are immutable and only `current`/runs mutate. That leaves no defined place for promotion state.
Fix: store verification state in mutable metadata or derive it from signed `runs.jsonl` promotion records, not from immutable `workflow.json`.

2. `commands.md` still contradicts binding-confirmation semantics. The `/workflow save` success example says `Saved workflow ... 1 value needs your confirmation`, but `PLAN.md` says structural/containment/multiple candidates block save until confirmation or `--accept-bindings`.
Fix: split this into a pending-confirmation response, or state that no workflow is saved until bindings are accepted.

3. Supporting artifacts are not fully reconciled. `research.md` still contains stale source claims like “No LLM-call-boundary event is persisted” and the old three-patch extension framing, while `PLAN.md` now corrects those assumptions. Since `research.md` is still listed as supporting detail, implementers can follow obsolete constraints.
Fix: add a staleness banner to `research.md`, update it, or remove it from implementation inputs.

4. The command count is still internally inconsistent. `PLAN.md` says five subcommand handlers, but the contract now defines six with `/workflow promote`.
Fix: update the top-level approach text to six subcommands.

5. The implementation task graph misses one gate edge. `T055a` is blocked by `T009a`, but it also depends on `T054` because the dispatch integration needs the registered CLI surface. The task prose implies this; the dependency graph does not.
Fix: add `T054 -> T055a`.

On team mode: I do not push back on the current Round 3 stance. The read-side filter is only circumstantial evidence, but making agent-mode-only the default and gating team dispatch on `T009a` is a sound containment.

VERDICT: REVISE
### Claude's response

Five findings, all five accepted. Four were consistency defects; one was a genuine design conflict I had created two rounds earlier without noticing.

**The design conflict:**

- **Immutable version directories had nowhere to store promotion state.** `SavedWorkflow.status` lives in `workflow.json`, promotion sets it to `verified`, and the store contract I wrote in Round 1 says version directories are immutable with only `current` and `runs.jsonl` mutating. Those cannot both be true. The reviewer's framing is the right one and I took the second half of it: `workflow.json` no longer carries `status` at all; the authoritative record is the promotion entry appended to `runs.jsonl` (which already holds `promotion_basis`, acting user, and source `run_id`), and `<slug>/state.json` is a lock-guarded, rebuildable cache derived from it. Status is a judgement about a version made over time, not a property of the captured artifact — the store now models it that way, and a lost or disagreeing `state.json` is regenerated from the append-only log.

**Consistency defects:**

- **`commands.md` still showed a workflow saved *and* awaiting confirmation.** Correct — the success example printed `Saved as research-digest · provisional · 1 value needs your confirmation`, which contradicts the Round 2 rule that low-confidence bindings block the save. Split into two responses: a success case with all bindings resolved, and a pending-confirmation case that writes nothing and says so.
- **`research.md` was the fifth stale artifact.** Correct, and the third round running where I fixed the artifacts I was told about without sweeping for the rest. It still asserted "no LLM-call-boundary event is persisted" and the three-slots/nine-hooks framing — the two claims Rounds 1 and 2 proved wrong. Both corrected inline, with a staleness banner.
- **"Five subcommand handlers" in the approach text.** Correct, stale since `promote` was added. Now six.
- **Missing `T054 → T055a` edge.** Correct — the prose implied it, the graph did not. Added.

Nothing was rejected this round. The findings are now confined to internal consistency between documents rather than to the design itself, which is a different class of problem than Rounds 1–3 surfaced.

## Round 5 — Codex (final round, MAX_ROUNDS cap)

**Blockers**

1. Manual promotion is still contradicted by the lifecycle rule. `PLAN.md` defines `/workflow promote --acknowledge` for generative/mixed terminals, but the key decision still says promotion requires `result_consistent == True`, and [data-model.md](/home/amir/workspace/jiuwenswarm/specs/001-workflow-capture/data-model.md:168) repeats that. That makes manual promotion impossible on the cases it exists for.
Fix: define promotion as `live success AND (result_consistent == True OR manual_ack OR evaluator)` everywhere lifecycle rules are stated.

2. The store contract fix is not carried into the implementation-driving store artifact. `PLAN.md` now requires `versions/<n>/`, lock-guarded `state.json`, rebuild from `runs.jsonl`, and immutable workflow artifacts, but [data-model.md](/home/amir/workspace/jiuwenswarm/specs/001-workflow-capture/data-model.md:176) still shows `v1/`, `v2/`, no `state.json`, and says only `current`/`runs.jsonl` mutate. [tasks.md](/home/amir/workspace/jiuwenswarm/specs/001-workflow-capture/tasks.md:139) points storage implementation at that data-model layout, so this can produce the wrong store.
Fix: update the data-model store layout and T041/T042 to explicitly implement `versions/<n>/`, `.lock`, `state.json`, and state rebuild from `runs.jsonl`.

VERDICT: REVISE
### Claude's response

Two blockers, both accepted and both fixed. Neither is a disagreement — they are propagation failures from my own Round 4 fix, caught correctly.

- **Manual promotion was contradicted by the lifecycle rule.** The real defect, and an embarrassing one: Round 2 introduced `/workflow promote --acknowledge` precisely because generative and mixed terminals can never set `result_consistent`, and then left the promotion rule stated as `outcome == success AND result_consistent == True` in both `PLAN.md` and `data-model.md:168`. Under that rule the manual path could never fire on the only cases it exists for. Promotion is now `live success AND promotion_basis ∈ {result_consistent, manual_ack, evaluator}` everywhere the lifecycle is stated, including the state diagram.
- **The store fix never reached the artifact that drives the implementation.** Correct and consequential: `PLAN.md` § Store contract was rewritten across Rounds 1, 3 and 4, but `data-model.md:176` still showed the original `v1/`, `v2/` layout with no `state.json` and the stale "only `current` and `runs.jsonl` mutate" rule — and `tasks.md` T041 points storage implementation directly at that layout. Following the specs as written would have built the wrong store. The layout now matches the plan (`versions/<n>/`, `.lock`, `state.json`, `.tmp-<uuid>/`, `ir_schema`), with the atomicity and rebuild rules stated alongside it, and T041 is expanded into T041/T041a/T041b plus T042a to cover locking, fsync ordering, state rebuild from `runs.jsonl`, and crash recovery as tested paths.

**Loop terminated at the MAX_ROUNDS=5 cap on a REVISE verdict.** These two fixes are therefore unreviewed by Codex — they are the only part of the plan that has not been through an adversarial pass. Both are narrow and mechanical, but that is my assessment, not a reviewed one, and it is recorded here rather than presented as convergence.

## Round 6 — Codex (cap extended by the human; scoped to the two unreviewed Round 5 fixes)

**Blocker**

`runs.jsonl` is now authoritative for lifecycle state, but `RunRecord` still does not record the workflow version it executed. That breaks `state.json` rebuild after overwrite or explicit `/workflow run --version <n>`: an old version’s successful promotion record can be replayed without enough data to know whether it applies to the current version. The plan says `state.json` carries the version it applies to, but the authoritative log it rebuilds from does not.

Fix: add `workflow_version` to every `RunRecord` and promotion record, and define rebuild as “derive state for the current pointer’s version only; older-version promotion records do not verify newer versions.”

Promotion wording is otherwise consistent now: `PLAN.md`, `data-model.md`, `commands.md`, and `tasks.md T042/T049a` no longer make manual promotion impossible. The old `v1/`/`v2/` store layout references are gone from the actual store model; remaining `v1`/`v2` strings are display examples, not paths.

VERDICT: REVISE
### Claude's response

Fix 1 (promotion lifecycle) confirmed consistent across `PLAN.md`, `data-model.md`, `commands.md` and `tasks.md`, with the manual and evaluator paths reachable. Fix 2 (store layout) confirmed to have displaced the old `v1`/`v2` layout everywhere it drove implementation — one blocker remained, and it is a real gap I opened in Round 4.

**`runs.jsonl` was made authoritative for lifecycle state without `RunRecord` recording which version it ran.** Accepted in full. The scenario is reachable today, not hypothetical: `commands.md:80` already documents `/workflow run --version <n>`, so a successful live run against an older version writes a promotion record that the rebuild cannot distinguish from one against `current`. A `state.json` rebuild would then silently mark a version `verified` that was never verified — the exact class of failure the append-only log was introduced to prevent, reintroduced by the fix for it.

Applied across four artifacts:
- `RunRecord.workflow_version` is now a **required** field, and every promotion record names the version it promotes.
- Rebuild is **version-scoped**: derive state for the version `current` points at, considering only records whose `workflow_version` matches. Records for other versions are ignored, never inherited.
- `/workflow promote` sets state for the version the source run executed; promoting a version `current` no longer points at does not verify the current one.
- This is also the enforceable form of an existing rule — `data-model.md` already said an overwrite starts `provisional`, but nothing prevented an old promotion record from contradicting it. `T042b` tests exactly that: promote v1, overwrite to v2, delete `state.json`, assert the rebuild reports v2 `provisional`.

The gap is worth naming plainly: Rounds 4, 5 and 6 each fixed a consequence of the same original decision to move lifecycle state out of the immutable version, and each fix opened the next gap. That is the cost of changing a storage model late in a review rather than a sign the review was churning — but it is also the strongest argument for `T041b`/`T042a`/`T042b` being written before `store/filesystem.py`, not after.

## Round 7 — Codex (final)

The `workflow_version` fix closes the Round 6 blocker: `RunRecord` now carries the executed version, rebuild is scoped to `current`, old-version promotions are not inherited, and `T042b` tests the overwrite/delete-cache regression. The store model is now implementable.

One remaining issue is real but I would not block implementation on it: `/workflow promote` still does not say how it selects the source live run when multiple successful runs exist. The implementation should make that deterministic, ideally “latest successful live run for the currently selected version unless a future `--run-id` is added.” That is a contract tightening, not a lifecycle/storage design blocker.

VERDICT: APPROVED
### Claude's response

Converged. The lifecycle and store model is approved as implementable.

The one non-blocking item was accepted and applied rather than carried into implementation, since it was a one-line contract gap: `/workflow promote` did not say how it picks the source run when several successful live runs exist. It now selects the most recent `RunRecord` with `mode == live`, `outcome == success`, and `workflow_version` matching the version `current` points at, prints the selected `run_id` before changing state, and names `--run-id` as a Phase 2 addition rather than leaving the door ambiguous. `T049a` and the error message were updated to match.

---

## Loop closed

7 rounds (cap of 5 extended once by the human to close out two unreviewed fixes). 49 findings surfaced: 47 accepted, 2 corrected as overstated.

**Verdict: APPROVED at Round 7.**

The three findings that would have shipped defects were all in contracts written *during* the review rather than in the original plan — the effect model misreading `idempotent`, containment matches binding to whole leaves, and the promotion rule making its own manual path unreachable. The review's value was less in attacking Round 0 than in attacking each round's repairs.

Known open items carried into implementation, none of them blockers:
- Team-mode capture viability is unproven and gated on `T009a`; agent-mode-only is the default plan (risk 12).
- The trusted-provider registry that `read` declarations depend on does not exist yet (risk 15).
- Effect metadata in the fingerprint will invalidate pinned workflows the first time built-in tools declare effects (risk 16).
- Thresholds throughout — provenance tiers, viability, fan-out — are chosen and unmeasured, with fixtures assigned to measure them.

---

## Act 3 — Build (Claude, direct — Codex `--yolo` blocked by auto-mode classifier)

Scope: T011-T015 (IR models + validation) and T040-T042b (filesystem store). Claude built directly, no Codex write access. See turn transcript for the build itself; this section covers the independent Codex code-review cross-check requested afterward.

### Round 1 — Codex code review (fresh session, read-only)

> You are an adversarial code reviewer. Be skeptical and specific — find what breaks,
> don't be agreeable. You are read-only: do not modify any files.
> 
> Context: `PLAN.md` at the repo root is a frozen spec for a feature called Workflow
> Capture. It survived 7 rounds of adversarial review by a prior Codex session before
> any code was written — `PLAN-REVIEW-LOG.md` has the full transcript if you want the
> reasoning behind a specific rule. Claude then implemented two self-contained slices
> of that spec directly (no Codex involvement in the build) and is now asking you,
> a fresh session, to cross-inspect the diff before anything is committed.
> 
> Scope of the diff — read these files directly, they are new/untracked:
>   jiuwenswarm/workflow_capture/ir/models.py
>   jiuwenswarm/workflow_capture/ir/validate.py
>   jiuwenswarm/workflow_capture/store/filesystem.py
>   tests/unit_tests/workflow_capture/*.py   (all files — conftest.py + 5 test files)
> 
> This is T011-T015 (IR data models + validation) and T040-T042b (filesystem store)
> from `specs/001-workflow-capture/tasks.md`. Cross-check the implementation against:
>   - `PLAN.md` § Store contract, § Effect model, § Result-consistency contract
>   - `specs/001-workflow-capture/data-model.md` — the entity field tables and the
>     "State transitions" / "Store layout" sections
> Nothing else in the spec (extraction pipeline, conversion, command surface,
> execution/dry-run) is in scope — it was not built.
> 
> One known deviation, already flagged in the code (see the SavedWorkflow docstring
> in ir/models.py and the test test_saved_workflow_has_no_runs_field): the build
> found that data-model.md's own SavedWorkflow field table lists
> `runs: list[RunRecord]`, which contradicts the immutable-version-directory
> architecture the same way the `status` field did before the plan review's Round 4
> caught it — the field was dropped rather than implemented. Confirm whether that
> reasoning holds, and whether it's the only such contradiction or there are others
> you can find.
> 
> What to look for, concretely:
>   - Correctness bugs: the store's atomicity chain (flock, temp-dir build, fsync
>     ordering, os.replace, fail-if-target-exists, atomic current-pointer swap),
>     the version-scoped state.json rebuild from runs.jsonl (a promotion of one
>     version must never verify another), the Binding validators (containment
>     forces unresolved, multiple candidates force unresolved, kind/value format
>     rules), the Component conditional rules (frozen/agent/loop requirements),
>     the RunRecord promotion/dry-run rules.
>   - Race conditions or crash-safety gaps the store's own tests might have missed.
>   - Places where the code deviates from PLAN.md/data-model.md without saying so.
>   - Test quality: are the crash-recovery and state-rebuild tests actually proving
>     what their names claim, or could they pass for the wrong reason?
>   - Simpler alternatives, if a chosen approach is needlessly complex for what the
>     spec actually requires.
> 
> For each finding, give a concrete file:line and a one-line fix. End your reply
> with EXACTLY one line: `VERDICT: APPROVED` if the diff is sound enough to keep
> as-is, or `VERDICT: REVISE` if it has material problems.

**Findings** (thread `01a03e4a-341e-7ef0-b68c-eb869eb835a6`):

High: `filesystem.py` trusts `state.json` whenever the cached version matches `current`, without checking whether its status agrees with `runs.jsonl`. This violates the "state.json is a cache; runs.jsonl is authoritative; missing or disagreeing state is rebuilt" contract. A forged/stale same-version `state.json` can report `verified` with no promotion record. Fix: read runs under the workflow lock and compare cached state to `_derive_state(records, current_version)`, or always derive on `get_state`.

High: `filesystem.py` appends a `RunRecord` for any `workflow_version`, including nonexistent future versions. Later, when that version number is created, `_derive_state()` will treat the old record as verifying the new version. Fix: under the lock, reject records whose `workflow_version` is not an existing version directory for that workflow.

High: `filesystem.py` allocates `next_version` but writes the caller-supplied `workflow.version` unchanged. The tests hide this by manually passing `version=2/3`; real callers can save `versions/2/workflow.json` containing `"version": 1`. Fix: copy/update the `SavedWorkflow` to `version=next_version` before serializing, or reject mismatches.

Medium: `models.py` does not enforce `BindingCandidate.matched_span` semantics, and only checks binding-level `confidence == containment`. A binding with one containment candidate but `kind=reference` can pass if `confidence` is omitted or set differently, despite PLAN's "containment never binds." Fix: require any candidate with `tier == containment` to have `matched_span` and force the owning binding to `unresolved`; reject `matched_span` for non-containment candidates.

Medium: `models.py` only checks truthiness for `configs.tool_allowlist` and `configs.max_iterations`. It accepts `tool_allowlist="search"` and `max_iterations=-1`, which breaks the "agentic but bounded" requirement. Fix: validate `tool_allowlist` is a non-empty list of strings and `max_iterations` is a positive integer.

Medium: `models.py` has no fields for the manual-promotion audit data that PLAN/data-model require: acting user and source `run_id`. The `SavedWorkflow.runs` removal is justified by immutable version directories, but this is a separate unacknowledged deviation from the RunRecord table. Fix: add explicit `acting_user` / `source_run_id` fields or a typed promotion audit object, and require them when `promotion_basis == manual_ack`.

Medium test gap: `test_store_state.py` tests corrupt `state.json`, but no test writes a valid same-version wrong state. That lets the highest-risk cache bug pass. Fix: add a test that writes `{"workflow_version": 1, "status": "verified"}` with no promotion record and expects rebuild to `provisional`.

The `SavedWorkflow.runs` deviation holds: embedding append-only execution history in immutable `workflow.json` has the same architectural flaw as the removed `status` field. No other same-class contradiction found. The RunRecord promotion audit omission above is a real spec deviation.

VERDICT: REVISE

### Claude's response

All 7 findings accepted, none rejected — every one held up on independent re-verification against the actual code before I touched anything.

- **get_state cache-trust (High).** Real: `get_state` returned the cache whenever `workflow_version` matched, never comparing its *status* to the log. Simplified to the strongest possible fix rather than a narrower comparison-based one: `get_state()` now always calls `rebuild_state()`, which derives fresh from `runs.jsonl` on every call and writes `state.json` only as a side-effect cache for external inspection. Nothing in the store trusts the file back. `_read_cached_state` became dead code and was removed.
- **append_run accepts unknown workflow_version (High).** Real and reachable — nothing stopped a `RunRecord` for a version that doesn't exist yet from sitting in `runs.jsonl` and later "verifying" that version the moment it was created. Added `UnknownVersionError`, checked under the lock in `append_run`.
- **save() doesn't reconcile workflow.version (High).** Real — my own tests were colluding with the bug by manually passing the right `version=` to the fixture before `overwrite=True`. The store now owns version numbers: `workflow = workflow.model_copy(update={"version": next_version})` before serialization, regardless of what the caller supplied.
- **Containment bypass via confidence/tier mismatch (Medium).** Real defect in the validator: checking only `self.confidence == CONTAINMENT` let a binding declare `confidence=exact` while carrying a single containment-tier candidate, since the multiple-candidates check doesn't fire on exactly one. Now checks `any(c.tier == CONTAINMENT for c in self.candidates)` too. Also added the `matched_span` tier-coupling data-model.md's field note specifies but I never enforced.
- **Agent config truthiness (Medium).** Real — `tool_allowlist="search"` and `max_iterations=-1` both passed. Now a non-empty list of non-empty strings, and a positive int with `bool` explicitly excluded (Python's `bool` is an `int` subclass).
- **Missing promotion audit fields (Medium).** This is the same gap I'd noticed and consciously deferred while building — I'd reasoned it belonged to the out-of-scope `/workflow promote` command (T049a) since data-model.md's own field table doesn't list it, only PLAN.md's prose does. Codex independently found the same gap and called it correctly: PLAN.md is explicit enough ("records the override, the acting user, and the run_id it is based on") that leaving it unenforced at the model layer was the wrong call. Added `acting_user`/`source_run_id` to `RunRecord`, required together when `promotion_basis == manual_ack`.
- **Test gap (Medium).** Added `test_forged_verified_state_json_with_no_promoting_run_is_corrected`, which — after the `get_state` fix — passes trivially, but is kept as a named regression test for the exact vulnerability found.

Added two more regression tests beyond what was asked (`test_append_run_rejects_workflow_version_with_no_version_directory`, `test_save_stamps_the_store_allocated_version_not_the_callers`) plus a fourth test isolating the confidence/tier bypass specifically. Test count: 62 → 79. `ruff check jiuwenswarm/workflow_capture` stayed clean throughout.

### Round 2 — Codex code review (same session, resumed)

VERDICT: APPROVED. "The seven fixes close the gaps I previously called out: `get_state()` now derives from `runs.jsonl`, `append_run()` rejects nonexistent versions, `save()` stamps the allocated version, containment candidates cannot bypass unresolved handling, agent config bounds are real, manual promotion audit fields exist, and the new tests cover the prior false-pass cases. [...] The `SavedWorkflow.runs` removal remains justified. I do not see another same-class immutable-artifact contradiction in the inspected diff." (Codex noted it did not re-run pytest/ruff itself, being read-only; Claude re-ran both after the fixes — 79 passed, ruff clean — before sending this round.)

**Net for Act 3:** 7 findings from an independent Codex code review of Claude's own build, all substantively real (3 High: a cache-trust bug that would have let a forged `state.json` report `verified` with no promoting run, an unvalidated `workflow_version` on `append_run`, and `save()` not reconciling the caller's `version` field with the store-allocated one), all fixed and verified, converged at round 2. No code was committed as part of this review — that remains a separate human decision.

---

## Act 3 continued — Codex code review of T004 (`test_import_boundary.py`)

Fresh Codex session (thread `01a03e81-a128-7352-9e3e-8e73f5530b64`), read-only, reviewing the T004 + T037a commit (`8f36b19fd`) that closed the import-boundary guardrail and added the agent-core characterization tests. T037a was confirmed sound on the first pass — Codex read `.reference-agent-core` directly rather than trusting the test file's own comments, and confirmed the loop/branch/patch-grep claims independently. All six rounds below are against `test_import_boundary.py` alone.

**Round 1** — 2 findings: (1) High, dynamic-import detection only matched a plain `ast.Constant` argument, missing string concatenation, f-strings, and relative dynamic imports (`importlib.import_module("..server.runtime", package=...)`); (2) Medium, `Path.rglob("*.py")` does not follow symlinked directories, so a symlinked subpackage could hide a violation entirely. Both accepted. Fix 1: fail closed on any dynamic-import call whose target isn't a provably-safe absolute string literal. Fix 2: replaced `rglob` with a manual cycle-protected walker following symlinks. 4 + regression tests added (14 total in file).

**Round 2** — 2 findings, both High: `importlib.import_module`'s real signature is `(name, package=None)` — `name=` as a keyword was uncovered, and the call-matcher only recognized `.import_module`/bare `__import__`, missing `from importlib import import_module; import_module(...)` and `builtins.__import__(...)`. Both verified (checked `import_module`'s actual signature) and fixed: target extraction now checks `name=` keyword; matcher now accepts the aliased-name and attribute forms of both `import_module` and `__import__`. 4 regression tests added (18 total).

**Round 3** — 1 finding, High: `__import__(name, fromlist=[...])` is genuinely how `from x import y` is implemented (verified directly: `__import__("email", fromlist=["message"])` makes `email.message` importable), so a fromlist item can reach the forbidden module even when the base `name` doesn't; `level=` was also unchecked, and a non-dot-prefixed `name` isn't necessarily absolute once `level` is nonzero. Accepted — `__import__`'s five-arg signature (`name, globals, locals, fromlist, level`) is genuinely different from `import_module`'s two-arg one, so the fix is `__import__`-specific: nonzero/non-literal `level` fails closed; non-empty `fromlist` is only cleared if it's a literal list/tuple of string constants, each checked as `f"{name}.{item}"`. 5 regression tests added (23 total).

**Round 4** — 1 finding, High: `from jiuwenswarm.server import *` was cleared, because `alias.name == "*"` was checked as the literal string `"jiuwenswarm.server.*"`, which never matches. Verified concretely against this repo: `jiuwenswarm/server/__init__.py` declares `__all__ = ["JiuWenSwarm", "SkillManager"]` with a `__getattr__` that lazily imports both from `jiuwenswarm.server.runtime.*` — so the wildcard import genuinely reaches the forbidden namespace with no literal "runtime" string anywhere in the importing file. Fixed: any `from X import *` where `X == FORBIDDEN` or `X` is an ancestor of `FORBIDDEN` fails closed unconditionally — this can't be resolved by reading only this package's own files, so no attempt is made to trace into the ancestor's `__getattr__`/`__all__` logic. 5 regression tests added, including a sibling-not-ancestor negative case (28 total).

**Round 5** — 1 finding, High: `**{"name": "..."}` unpacking (`import_module(**{"name": ...})` / `__import__(**{"name": ...})`) is a working call form (verified: produces `ast.keyword(arg=None, value=<Dict>)`, and `import_module(**{"name": "email"})` genuinely imports `email`), but every `kw.arg == "name"` lookup silently skips a `**`-unpack keyword, so the call fell through to the "no target, harmless zero-arg call" path. Fixed: any `**`-unpack present on a matched call fails closed unconditionally, before any other argument is inspected — no attempt to evaluate the unpacked mapping. 2 regression tests added (30 total).

**Round 6** — `VERDICT: APPROVED`. No material findings remaining; the guard now covers every realistic evasion class found across five rounds (static/relative/wildcard imports, symlinked packages, dynamic-import literals and non-literals, aliasing, keyword args, `fromlist`/`level`, `**kwargs`-unpacking). Codex named the residual limitation explicitly and correctly: `eval`/`exec`/`getattr(...)(...)`-style obfuscation is out of scope for any AST-based import check, not a gap specific to this one.

**Net:** 5 real findings across 5 rounds — every round found something, none were false positives or scraping. The pattern worth noting: each fix closed one form of dynamic-import evasion, and the review kept finding the next one, because "detect known-bad literal patterns" is fundamentally the wrong frame for a security-adjacent boundary check — "fail closed on anything not provably safe" is what actually converges, and only fully arrived at that framing by round 5. Final state: 30 tests in `test_import_boundary.py` (114 in the full `tests/unit_tests/workflow_capture` suite), `ruff` clean.
