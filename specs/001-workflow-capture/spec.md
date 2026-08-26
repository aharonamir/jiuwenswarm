# Feature Specification: Workflow Capture

**Feature Branch**: `001-workflow-capture`
**Created**: 2026-08-26
**Status**: Draft
**Input**: Save a successful agent session as a named, replayable workflow

---

## Execution Flow (main)

```
1. User completes a multi-step task in a JiuwenSwarm session and is satisfied with the result
2. User runs /workflow save [name]
3. System analyses the session's own event record and extracts a workflow
4. System reports what it captured, including anything it could not resolve
5. Later, user runs /workflow run [name] with parameters
6. System executes the captured workflow, dry by default until verified
```

---

## ⚡ Quick Guidelines

- ✅ Focus on WHAT the user needs and WHY
- ❌ No implementation detail in this document (see `plan.md`)
- 👥 Written for review by product and engineering stakeholders

---

## User Scenarios & Testing

### Primary User Story

A user runs a multi-step task — search a topic, read the top sources, summarise, publish. It works well. Today, running it again produces a different execution path and a different-quality result, because the agent re-plans on every run. The user wants to keep the version that worked.

They type `/workflow save research-digest`. The system reads back over the session, works out which steps actually contributed to the result, which values were inputs rather than fixed choices, and where the agent was repeating itself over a list. It saves that as a workflow and tells the user what it found.

Next week they run `/workflow run research-digest --topic "agent security"`. The same steps execute in the same order.

### Acceptance Scenarios

1. **Given** a completed session containing a successful multi-tool task, **When** the user runs `/workflow save [name]`, **Then** the system saves a workflow and reports the number of steps captured, the number skipped, the class breakdown, and every lifted parameter.

2. **Given** a session where the agent searched, then fetched five URLs from the search results, **When** the user saves it, **Then** the five fetches are captured as a single repeating step over the search output, not as five separate steps.

3. **Given** a session containing searches that returned nothing and files opened but unused, **When** the user saves it, **Then** those steps are excluded from the workflow and reported as skipped.

4. **Given** a session where the user's original request contained "Q3 revenue Anthropic" and a search was run with that value, **When** the user saves it, **Then** the value is offered as a workflow parameter rather than frozen into the saved step.

5. **Given** a saved provisional workflow containing a publish step, **When** the user runs it without `--live`, **Then** the publish step reports what it would do and nothing is published.

6. **Given** a provisional workflow that has been run once with a result matching the original, **When** the user views the workflow list, **Then** its status shows as verified.

7. **Given** a session whose extraction yields almost entirely open-ended steps, **When** the user runs `/workflow save`, **Then** the system declines to save and explains that the capture would not be meaningfully more repeatable than re-running the prompt.

8. **Given** a saved workflow, **When** the user runs `/workflow show [name]`, **Then** the system renders the workflow graph and a table of every value showing which are parameters, which are fixed, and which remain unresolved.

9. **Given** a workflow with a required parameter, **When** the user runs it without supplying that parameter, **Then** the system reports which parameter is missing and does not execute.

10. **Given** a saved workflow referencing a tool that is no longer available or whose interface has changed, **When** the user runs it, **Then** the system reports the incompatibility before executing any step.

### Edge Cases

- Session with no tool calls at all → decline with a clear reason
- Session containing several distinct tasks → capture the most recent completed task; report the boundary used
- Tool that returned an empty or "nothing found" result while reporting success → must not be treated as a load-bearing step
- Two workflows saved with the same name → require explicit overwrite, and preserve the prior version
- Extraction failure of any kind → return an error from the command; the session itself must be unaffected
- Session where the same value appears in both the user's request and a tool result → provenance must be reported as ambiguous rather than silently resolved

---

## Requirements

### Functional Requirements

**Capture**

- **FR-001**: System MUST derive a workflow from a completed session without requiring any authoring step from the user.
- **FR-002**: System MUST identify which recorded steps contributed to the final result and exclude those that did not.
- **FR-003**: System MUST determine, for every value passed to a step, whether it originated from the user's request, from an earlier step's output, or from neither.
- **FR-004**: System MUST offer values originating from the user's request as workflow parameters.
- **FR-005**: System MUST represent repeated steps that all read from one earlier output as a single repeating step over that output.
- **FR-006**: System MUST assign each captured step a determinism class: fully fixed, fixed-shape-with-generated-content, or bounded-open-ended.
- **FR-007**: System MUST record, for every captured step, whether it reads, writes idempotently, or produces an external effect.
- **FR-008**: System MUST refuse to save a capture that would offer no meaningful repeatability over re-running the original prompt, and MUST state why.
- **FR-009**: System MUST report every value whose origin it could not determine, rather than defaulting it silently.
- **FR-010**: Capture MUST NOT alter the session it reads from.

**Storage & lifecycle**

- **FR-011**: System MUST persist saved workflows under a user-supplied name, surviving restarts.
- **FR-012**: Saved workflows MUST start in a provisional state.
- **FR-013**: System MUST promote a workflow to verified only after a successful execution whose result is consistent with the originally captured outcome.
- **FR-014**: System MUST preserve prior versions when a workflow is overwritten.

**Execution**

- **FR-015**: System MUST execute a saved workflow with the same step sequence on every run.
- **FR-016**: System MUST default to a non-effecting run while a workflow is provisional, reporting what effecting steps would do.
- **FR-017**: System MUST require explicit opt-in before performing any external effect.
- **FR-018**: System MUST validate that required parameters are supplied before executing any step.
- **FR-019**: System MUST verify that every referenced tool is still available and interface-compatible before executing any step, and report incompatibilities without partial execution.
- **FR-020**: Execution failure MUST report which step failed and why.

**Inspection**

- **FR-021**: System MUST list saved workflows with name, status, size, and last run.
- **FR-022**: System MUST render a saved workflow's structure in a human-readable diagram.
- **FR-023**: System MUST present a table of every value in the workflow, classified as parameter, fixed value, or unresolved.

### Key Entities

- **Captured Workflow** — a named, versioned procedure derived from one session. Has a status (provisional/verified), an ordered set of steps, a parameter list, and provenance back to the session it came from.
- **Step** — one unit of work in the workflow. Has a determinism class, an effect class, a set of input values, and an output shape.
- **Value Binding** — how one step input gets its value: from a parameter, from an earlier step's output, or fixed at capture time. May be unresolved.
- **Parameter** — a value the user supplies at run time, lifted from the original request during capture.
- **Run Record** — the outcome of one execution: which parameters were used, whether it was effecting, and whether the result was consistent with the captured outcome.

---

## Out of Scope (this feature)

- Editing steps or values in place — read-only inspection only
- Composing a workflow from more than one session
- Sharing, exporting, or a workflow marketplace
- Automatically detecting that a task should be captured
- Streaming execution, parallel branch joins, stream-transform steps
- Any dependency on agent-studio
- A security review gate for captured workflows (tracked in the security workstream)

---

## Review & Acceptance Checklist

### Content Quality
- [x] No implementation detail
- [x] Focused on user value and business need
- [x] Written for non-implementation stakeholders
- [x] All mandatory sections completed

### Requirement Completeness
- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

### Deferred decisions
These are recorded in the requirements approval document as open items. None block this specification.
- Whether parameter lift requires confirmation at save time or is sufficient to surface on inspection (FR-004, FR-009)
- Workflow store versioning scheme (FR-011, FR-014)
- What constitutes tool interface compatibility (FR-019)
