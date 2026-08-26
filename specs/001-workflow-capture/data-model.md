# Data Model: Workflow Capture

Phase 1 output. Entities, fields, validation rules, and state transitions.

---

## SavedWorkflow

The persisted artifact.

| Field | Type | Notes |
|---|---|---|
| `workflow_id` | str | Slug, unique per store. User-supplied name, normalised. |
| `workflow_name` | str | Display name |
| `description` | str | Model-written at capture |
| `version` | int | Increments on overwrite; prior versions retained |
| ~~`status`~~ | — | **Removed in review Round 4.** Version directories are immutable, so promotion had nowhere to write. Verification state is authoritative in the `runs.jsonl` promotion record and cached in the mutable `<slug>/state.json` (`PLAN.md` § Store contract) |
| `components` | `list[Component]` | The graph nodes |
| `connections` | `list[Connection]` | The graph edges |
| `parameters` | `list[Parameter]` | Lifted inputs |
| `source_session_id` | str | Provenance |
| `captured_at` | datetime | |
| `capture_report` | `CaptureReport` | What extraction found and skipped |
| `runs` | `list[RunRecord]` | Execution history |

**Validation**
- `workflow_id` MUST be unique within the store
- MUST contain exactly one `jiuwen.start` and at least one `jiuwen.end` component
- Every `Connection.source` and `.target` MUST reference an existing component id
- The graph MUST be acyclic outside declared loop bodies
- Every `${node.field}` reference MUST resolve to an existing component and a declared output of it

---

## Component

One step in the workflow.

| Field | Type | Notes |
|---|---|---|
| `id` | str | e.g. `node_search` |
| `name` | str | Display |
| `type` | ComponentType | See below |
| `determinism` | `frozen` \| `transform` \| `agent` | Step class |
| `effect` | `read` \| `write_idempotent` \| `write_effectful` | Gating class |
| `inputs` | `list[Binding]` | |
| `outputs` | `list[OutputField]` | Declared shape |
| `configs` | dict | Type-specific: tool identity, prompt template, budgets, loop settings |
| `tool_fingerprint` | str \| None | Set for frozen tool steps; compatibility check at run |
| `source_step_ids` | `list[str]` | `tool_call_id`s this component came from |

**ComponentType** — `jiuwen.start`, `jiuwen.end`, `jiuwen.mcp`, `jiuwen.api`, `jiuwen.code`, `jiuwen.llm`, `jiuwen.LLMReAct`, `jiuwen.agent`, `jiuwen.loop`, `jiuwen.branch`, `jiuwen.subWorkflow`

**Validation**
- `determinism == frozen` REQUIRES every `Binding` resolved and `tool_fingerprint` set
- `determinism == agent` REQUIRES `configs.tool_allowlist` and `configs.max_iterations`
- `effect` derives from the tool card: explicit `properties["effect"]` wins; else `idempotent == True` → `write_idempotent`; else `write_effectful`. **`read` is never inferred** — agent-core's `idempotent` means "no *additional* side effects", which an idempotent write satisfies (`PLAN.md` § Effect model)
- `type == jiuwen.loop` REQUIRES `configs.loop_body` (non-empty component id list) and a `Binding` for `arr_loop_var`

---

## Binding

How one component input gets its value. **The central entity** — most extraction ambiguity lives here.

| Field | Type | Notes |
|---|---|---|
| `name` | str | Input argument name |
| `kind` | `parameter` \| `reference` \| `constant` \| `unresolved` | |
| `value` | str \| Any | `${node_start.topic}` / `${node_x.field}` / literal / None |
| `captured_value` | Any | What this argument was at capture time — always retained |
| `confidence` | `exact` \| `normalised` \| `path_aware` \| `structural` \| `containment` | Match tier, per `PLAN.md` § Provenance algorithm |
| `candidates` | `list[BindingCandidate]` | Populated whenever `kind == unresolved`; never silently collapsed to one |
| `derived_unknown` | bool | Value computed from more than one origin — not bindable, blocks `frozen` |

**BindingCandidate** — `origin_id` (prior component or `node_start`), `json_pointer`, `tier`, and `matched_span` (set only for `containment`).

**Validation**
- `kind == parameter` → `value` MUST be `${node_start.<param>}` and `<param>` MUST exist in `parameters`
- `kind == reference` → `value` MUST be `${<component_id>.<field>}`, target MUST precede this component, and `<field>` MUST be a declared output
- `kind == unresolved` → MUST be surfaced by `/workflow show` and reported at save, and `candidates` MUST be non-empty
- More than one candidate → `kind` MUST be `unresolved`; MUST be reported, MUST NOT be silently resolved
- `confidence == containment` → `kind` MUST be `unresolved`; a fragment of an origin value can never bind to the whole leaf (`PLAN.md` § Provenance algorithm)
- `derived_unknown == True` → the owning component MUST NOT be `frozen`

---

## Parameter

| Field | Type | Notes |
|---|---|---|
| `name` | str | |
| `type` | str | Inferred from captured value |
| `required` | bool | True unless a default was captured |
| `default` | Any \| None | |
| `captured_value` | Any | The value from the original session — shown as an example |
| `description` | str | Model-written |

---

## CaptureReport

What extraction found. Drives the save-time message and `/workflow show`.

| Field | Type | Notes |
|---|---|---|
| `steps_captured` | int | |
| `steps_skipped` | int | Removed by backward slice |
| `skipped_detail` | `list[SkippedStep]` | id, tool, reason |
| `class_breakdown` | dict | `{frozen: n, transform: n, agent: n}` |
| `loops_detected` | int | |
| `parameters_lifted` | `list[str]` | |
| `unresolved_bindings` | `list[BindingRef]` | component id + input name |
| `ambiguous_bindings` | `list[BindingRef]` | |
| `viability` | `viable` \| `marginal` \| `not_viable` | |
| `viability_reason` | str | Required when not `viable` |

**Viability rule** — `not_viable` when the workflow would offer no meaningful repeatability. Triggers: fewer than two captured steps; or `frozen == 0`; or `agent / total > 0.8`. `/workflow save` MUST refuse on `not_viable` and state the reason (FR-008).

---

## RunRecord

| Field | Type | Notes |
|---|---|---|
| `run_id` | str | |
| `workflow_version` | int | **Required.** The version this run executed — `/workflow run --version <n>` means it is not always `current`. Lifecycle rebuild considers only records matching the version `current` points at (review Round 6) |
| `started_at` / `finished_at` | datetime | |
| `parameters` | dict | Supplied values |
| `mode` | `dry` \| `live` | |
| `outcome` | `success` \| `failed` \| `incompatible` | |
| `failed_component_id` | str \| None | |
| `failure_reason` | str \| None | |
| `result_consistent` | bool \| None | Per `PLAN.md` § Result-consistency; always `None` for dry runs and for generative terminals |
| `consistency_method` | str \| None | Which comparison was used, and why |
| `promotion_basis` | `auto` \| `manual_ack` \| `evaluator` \| None | Recorded with acting user and source `run_id` for `manual_ack`; promotes only the `workflow_version` on this record |
| `components` | `list[ComponentRunAudit]` | Per step: resolved inputs (hashed when large), observed `tool_fingerprint`, `skipped_for_effect`, output hash |
| `capture_algorithm_version` / `ir_schema_version` | str | Required for post-hoc explanation |
| `token_cost` | int \| None | For baseline measurement |

---

## State transitions

```
                    /workflow save
                          │
                          ▼
                    ┌───────────┐
                    │provisional│──── dry run ────┐
                    └───────────┘                 │
                          │                       │ (no transition)
                          │ live run              │
                          │ AND outcome==success  │
                          │ AND promotion_basis   ▼
                          ▼
                    ┌───────────┐
                    │ verified  │
                    └───────────┘
                          │
                          │ overwrite (/workflow save same name)
                          ▼
                 new version, provisional
```

**Rules**
- A workflow enters `provisional` on save (FR-012)
- Only a **live** run can promote (FR-013) — a dry run proves nothing about effects
- Promotion requires `outcome == success` AND a `promotion_basis` — one of `result_consistent == True` (auto), `manual_ack` (`/workflow promote --acknowledge`, owner or admin), or `evaluator` (a registered deterministic evaluator). *Corrected in review Round 5*: stating this as `result_consistent == True` alone made manual promotion impossible for generative and mixed terminals, where `result_consistent` is always `None` by construction (`PLAN.md` § Result-consistency contract)
- `provisional` → `run` defaults to `dry` (FR-016); `--live` is the explicit opt-in (FR-017)
- `verified` → `run` still requires `--live` for effectful steps
- Overwrite creates a new version starting `provisional`; the prior version is retained (FR-014). A promotion record for an older version never verifies a newer one — lifecycle state is derived per version, from records whose `workflow_version` matches (review Round 6)
- Compatibility check runs before any step in every mode (FR-019); failure yields `outcome == incompatible` with no partial execution

---

## Store layout

*Corrected in review Round 5 to match `PLAN.md` § Store contract — the previous layout predated it.*

```
<store_root>/workflows/
├── research-digest/
│   ├── versions/1/workflow.json   # SavedWorkflow, serialised — no `status` field
│   ├── versions/1/report.json     # CaptureReport
│   ├── versions/1/ir_schema        # IR schema version stamp, for migration
│   ├── versions/2/…
│   ├── runs.jsonl                 # RunRecord append log — authoritative for promotion
│   ├── state.json                 # derived cache: current lifecycle state + the version it applies to
│   ├── current                    # plain text file holding the version number — NOT a symlink
│   ├── .lock                      # flock target guarding version allocation, runs.jsonl appends, state.json
│   └── .tmp-<uuid>/               # in-progress version build; same directory, therefore same filesystem
└── <other-workflow>/
```

**Rules**
- `versions/<n>/` is immutable once written. Only `runs.jsonl`, `state.json` and `current` mutate
- Version numbers are allocated under `.lock` and never reused; a save whose target `versions/<n>/` already exists **fails** rather than overwriting
- A version is built in `.tmp-<uuid>/`, `fsync`ed (files and containing directory), then `os.replace`d into `versions/<n>/` before `current` is swapped — a crash between the two leaves an unreferenced complete version, never a referenced partial one
- `state.json` is a **cache**. `runs.jsonl` is authoritative; if `state.json` is missing or disagrees, it is rebuilt from the promotion records in `runs.jsonl`, **scoped to the version `current` points at** — records for other versions are ignored rather than inherited
- Slugs must match `^[a-z0-9][a-z0-9-]{0,63}$` and the resolved path must remain under the store root
