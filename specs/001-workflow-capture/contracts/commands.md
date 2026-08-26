# Contract: Slash Commands

Phase 1 output. The command surface is the feature's only user-facing contract.

Handlers live in `workflow_capture/commands.py` (runtime-agnostic, returns structured results). The adapter `server/runtime/agent_adapter/workflow_slash.py` translates to the runtime's slash result shape, following `evolution_slash.py`.

---

## Dispatch

Prefix: `/workflow`. Subcommands: `save`, `run`, `list`, `show`, `delete`, `promote`.

Anything else under `/workflow` returns usage text. A non-`/workflow` message returns `None` and falls through to normal handling.

---

## `/workflow save [name]`

Extract a workflow from the current session.

**Arguments**
| Arg | Required | Notes |
|---|---|---|
| `name` | yes | Normalised to a slug, validated against `^[a-z0-9][a-z0-9-]{0,63}$` |
| `--overwrite` | no | Required if `name` exists |
| `--accept-bindings` | no | Accept low-confidence bindings without interactive confirmation |

**Binding confirmation.** Bindings resolved at `exact`, `normalised` or `path_aware` confidence are applied silently and printed in the summary. Any binding at `structural` or `containment` confidence, or carrying more than one candidate, requires confirmation (`PLAN.md` § Parameter-lift confirmation). Interactively, save prompts. Non-interactively, save **refuses** unless `--accept-bindings` is passed, which records `bindings_accepted_unreviewed: true` plus the accepting user and the affected binding list in the `CaptureReport`. `--accept-bindings` never accepts a `containment` match as a `reference` — those stay `unresolved` regardless, because a fragment cannot bind to a whole leaf.

**Returns** — `CaptureReport` summary.

**Success**
```
Captured 7 steps from this session · 3 skipped (no effect on result)
4 frozen, 1 loop, 1 agent
Parameter found: topic  ←  "Q3 revenue Anthropic"
Saved as research-digest · provisional · all bindings resolved
```

**Pending confirmation** — nothing is written yet; the workflow is saved only once the bindings are accepted:
```
Captured 7 steps from this session · 3 skipped (no effect on result)
1 value needs your confirmation before this can be saved:

  fetch_page.url  ←  fragment of search_web.results[0].snippet  (containment)
      captured: "https://example.com/q3-report"

Confirm to save, or re-run with --accept-bindings to accept unreviewed.
Nothing has been written.
```

**Refusal** (FR-008) — exit non-zero, nothing written:
```
Not saved. Only 1 of 6 captured steps is repeatable; the rest need
judgment at run time. This would not be more reliable than re-running
the prompt.
```

**Errors**
| Condition | Message |
|---|---|
| No tool calls in session | `Nothing to capture — this session made no tool calls.` |
| Name exists, no `--overwrite` | `research-digest already exists (v2, verified). Use --overwrite to save a new version.` |
| Extraction failure | `Capture failed: <reason>. Your session is unaffected.` |

**Guarantees** — MUST NOT modify the session (FR-010). MUST report unresolved and ambiguous bindings (FR-009). Refusal MUST write nothing.

---

## `/workflow run [name]`

Execute a saved workflow.

**Arguments**
| Arg | Required | Notes |
|---|---|---|
| `name` | yes | |
| `--<param> <value>` | per workflow | One per declared parameter |
| `--live` | no | Opt in to external effects |
| `--version <n>` | no | Defaults to `current` |

**Order of checks — all before any step executes**
1. Workflow exists
2. Tool compatibility (FR-019) — every referenced tool available and fingerprint-compatible
3. Required parameters supplied (FR-018)
4. Unresolved bindings — refuse if any remain

**Dry run** (default while provisional):
```
Dry run — plan only. 1 step would publish. Nothing sent.
Executed 2 read steps · skipped 1 for effect · 1 not reached.
A dry run does not verify the result. Run again with --live to publish.
```

**Errors**
| Condition | Message |
|---|---|
| Missing parameter | `Missing required parameter: topic. Example from capture: "Q3 revenue Anthropic"` |
| Incompatible tool | `Cannot run: web_search interface has changed since capture. No steps executed.` |
| Unresolved binding | `Cannot run: node_search.query was never resolved. See /workflow show research-digest.` |
| Step failure | `Failed at step 3 (node_fetch): <reason>. Steps 1-2 completed.` |

**Guarantees** — same step sequence every run (FR-015). No external effect without `--live` (FR-017). Compatibility failure produces zero partial execution (FR-019).

---

## `/workflow list`

**Returns**
```
NAME              STATUS       STEPS  VERSION  LAST RUN
research-digest   verified     7      v2       2026-08-24 (live, ok)
weekly-report     provisional  4      v1       never
competitor-scan   provisional  12     v1       2026-08-19 (dry)
```

Empty store returns `No saved workflows. Run /workflow save [name] after a session that went well.`

---

## `/workflow show [name]`

**Returns** two blocks.

**1. Graph** — mermaid from `Workflow.draw(output_format="mermaid")`.

**2. Bindings table** (FR-023) — every input in the workflow:
```
STEP          INPUT      SOURCE                        VALUE
node_search   query      parameter: topic              "Q3 revenue Anthropic"
node_fetch    url        ← node_search.results[item]   —
node_post     channel    fixed                         "#research"
node_post     title      UNRESOLVED                    "Q3 digest"      ⚠
```

Unresolved rows MUST be visually distinguished and listed first.

**Also reports** — status, version, capture date, source session, skipped steps with reasons, and effect class per step.

---

## `/workflow delete [name]`

Requires `--confirm`. Without it, reports what would be deleted.
```
Would delete research-digest (2 versions, 4 runs). Re-run with --confirm.
```

---

## Non-Web channels

Every command MUST produce usable plain text on any channel. Mermaid is emitted as a fenced block; channels that cannot render it show the source, which remains readable. No command may depend on A2UI in Phase 1.

---

## `/workflow promote <name> --acknowledge`

Manually promote a `provisional` workflow to `verified` when automatic result-consistency cannot apply — a generative terminal step, or a mixed/unrecognised terminal artifact (`PLAN.md` § Result-consistency contract).

**Authorization** — promotion requires the same permission as executing that workflow's effectful steps with `--live`, because a `verified` workflow is trusted more and gated less thereafter. Restricted to the workflow's owner or an admin; a user who may run a workflow but not approve its effects may not promote it.

**Source run selection** — the **most recent** `RunRecord` with `mode == live`, `outcome == success`, and `workflow_version` equal to the version `current` points at. Ties cannot occur (`run_id` is ordered by append). A `--run-id <id>` override is a Phase 2 addition; Phase 1 has no way to promote on the basis of an older run, and the selected `run_id` is always printed before the state changes.

**Preconditions**
1. The caller is authorized (above)
2. The workflow exists and is `provisional`
3. At least one `RunRecord` exists with `mode == live`, `outcome == success`, and a matching `workflow_version`
4. `--acknowledge` is present — promotion is never implicit

**Effect** — sets state to `verified` **for the version the source run executed** (`RunRecord.workflow_version`), and writes `promotion_basis = manual_ack`, the acting user, and the source `run_id`. Promoting a version that `current` no longer points at is recorded against that version and does not verify the current one. Without `--acknowledge`, prints what would be promoted and on what evidence, and exits without changing state.

**Errors**
| Condition | Message |
|---|---|
| No live successful run | `Nothing to promote — <name> has no successful live run for v<n>.` |
| Already verified | `<name> is already verified.` |
| Missing `--acknowledge` | `Refusing to promote without --acknowledge. Basis: run <run_id>, consistency not automatically checkable (<reason>).` |
| Not authorized | `Not permitted to promote <name> — promotion requires the same permission as running it with --live.` |
