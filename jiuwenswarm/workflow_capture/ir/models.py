"""IR data models for Workflow Capture.

Implements `data-model.md`'s entity tables. Field names, types, and cross-field
validation rules follow that document and `PLAN.md`'s corrections to it
verbatim; see the review log (`PLAN-REVIEW-LOG.md`) for why each rule below is
shaped the way it is.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

IR_SCHEMA_VERSION = "1"

PARAMETER_REF_PATTERN = re.compile(r"^\$\{node_start\.([A-Za-z_][A-Za-z0-9_]*)\}$")
REFERENCE_PATTERN = re.compile(
    r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\}$"
)


class ComponentType(str, Enum):
    START = "jiuwen.start"
    END = "jiuwen.end"
    MCP = "jiuwen.mcp"
    API = "jiuwen.api"
    CODE = "jiuwen.code"
    LLM = "jiuwen.llm"
    LLM_REACT = "jiuwen.LLMReAct"
    AGENT = "jiuwen.agent"
    LOOP = "jiuwen.loop"
    BRANCH = "jiuwen.branch"
    SUB_WORKFLOW = "jiuwen.subWorkflow"


class Determinism(str, Enum):
    FROZEN = "frozen"
    TRANSFORM = "transform"
    AGENT = "agent"


class Effect(str, Enum):
    """Gating class for a component's tool.

    `read` is NEVER inferred from `idempotent` — an idempotent tool may still
    write (upsert, "mark as read", cache/auth warm). This was a real bug
    caught in review Round 2 and is the single invariant this enum exists to
    make impossible to get wrong at the type level: there is no default
    member, and any caller assigning `Effect.READ` must justify it against
    an explicit, trusted declaration (`PLAN.md` § Effect model). Deriving a
    value from a tool card belongs to the classification stage (T028), which
    is out of scope for this package.
    """

    READ = "read"
    WRITE_IDEMPOTENT = "write_idempotent"
    WRITE_EFFECTFUL = "write_effectful"


class BindingKind(str, Enum):
    PARAMETER = "parameter"
    REFERENCE = "reference"
    CONSTANT = "constant"
    UNRESOLVED = "unresolved"


class BindingConfidence(str, Enum):
    EXACT = "exact"
    NORMALISED = "normalised"
    PATH_AWARE = "path_aware"
    STRUCTURAL = "structural"
    CONTAINMENT = "containment"


class RunMode(str, Enum):
    DRY = "dry"
    LIVE = "live"


class RunOutcome(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    INCOMPATIBLE = "incompatible"


class PromotionBasis(str, Enum):
    AUTO = "auto"
    MANUAL_ACK = "manual_ack"
    EVALUATOR = "evaluator"


class Viability(str, Enum):
    VIABLE = "viable"
    MARGINAL = "marginal"
    NOT_VIABLE = "not_viable"


class BindingCandidate(BaseModel):
    origin_id: str
    json_pointer: str
    tier: BindingConfidence
    matched_span: str | None = None

    @model_validator(mode="after")
    def _check_matched_span_only_for_containment(self) -> "BindingCandidate":
        # data-model.md: "matched_span (set only for containment)".
        if self.tier == BindingConfidence.CONTAINMENT and self.matched_span is None:
            raise ValueError("tier == containment requires matched_span to be set")
        if self.tier != BindingConfidence.CONTAINMENT and self.matched_span is not None:
            raise ValueError("matched_span is only set for tier == containment")
        return self


class Binding(BaseModel):
    """How one component input gets its value — the central IR entity."""

    name: str
    kind: BindingKind
    value: Any = None
    captured_value: Any = None
    confidence: BindingConfidence | None = None
    candidates: list[BindingCandidate] = Field(default_factory=list)
    derived_unknown: bool = False

    @model_validator(mode="after")
    def _check_kind_confidence_consistency(self) -> "Binding":
        # Both the scalar `confidence` field AND any candidate's own `tier`
        # are checked — checking only `confidence` let a binding declare
        # confidence=exact while smuggling a containment-tier candidate
        # through unresolved-forcing (caught by an independent Codex code
        # review of this diff): a fragment must never bind regardless of
        # which field the caller thought to set.
        has_containment_candidate = any(
            c.tier == BindingConfidence.CONTAINMENT for c in self.candidates
        )
        if (
            self.confidence == BindingConfidence.CONTAINMENT or has_containment_candidate
        ) and self.kind != BindingKind.UNRESOLVED:
            raise ValueError(
                "confidence == containment (or any candidate at containment tier) "
                "requires kind == unresolved — a fragment of an origin value can "
                "never bind to the whole leaf"
            )
        if len(self.candidates) > 1 and self.kind != BindingKind.UNRESOLVED:
            raise ValueError(
                "more than one candidate requires kind == unresolved — the "
                "extractor must never silently pick one"
            )
        if self.kind == BindingKind.UNRESOLVED and not self.candidates:
            raise ValueError("kind == unresolved requires a non-empty candidates list")
        if self.kind == BindingKind.PARAMETER:
            if not isinstance(self.value, str) or not PARAMETER_REF_PATTERN.match(self.value):
                raise ValueError(
                    "kind == parameter requires value to match ${node_start.<param>}"
                )
        if self.kind == BindingKind.REFERENCE:
            if not isinstance(self.value, str) or not REFERENCE_PATTERN.match(self.value):
                raise ValueError(
                    "kind == reference requires value to match ${<component_id>.<field>}"
                )
        return self


class OutputField(BaseModel):
    name: str
    type: str = "any"


class Connection(BaseModel):
    source: str
    target: str


class Component(BaseModel):
    id: str
    name: str
    type: ComponentType
    determinism: Determinism
    effect: Effect
    inputs: list[Binding] = Field(default_factory=list)
    outputs: list[OutputField] = Field(default_factory=list)
    configs: dict[str, Any] = Field(default_factory=dict)
    tool_fingerprint: str | None = None
    source_step_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_conditional_rules(self) -> "Component":
        if self.determinism == Determinism.FROZEN:
            unresolved_or_derived = [
                b for b in self.inputs
                if b.kind == BindingKind.UNRESOLVED or b.derived_unknown
            ]
            if unresolved_or_derived:
                names = ", ".join(b.name for b in unresolved_or_derived)
                raise ValueError(
                    f"determinism == frozen requires every binding resolved and "
                    f"not derived_unknown; offending inputs: {names}"
                )
            if not self.tool_fingerprint:
                raise ValueError("determinism == frozen requires tool_fingerprint to be set")

        if self.determinism == Determinism.AGENT:
            # Truthiness alone let "tool_allowlist": "search" (a bare string,
            # not a list) and "max_iterations": -1 pass — neither is a usable
            # bound (caught by an independent Codex code review of this diff).
            allowlist = self.configs.get("tool_allowlist")
            if not (
                isinstance(allowlist, list)
                and allowlist
                and all(isinstance(item, str) and item for item in allowlist)
            ):
                raise ValueError(
                    "determinism == agent requires configs.tool_allowlist to be a "
                    "non-empty list of non-empty tool-id strings"
                )
            max_iterations = self.configs.get("max_iterations")
            if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or max_iterations <= 0:
                raise ValueError(
                    "determinism == agent requires configs.max_iterations to be a "
                    "positive integer"
                )

        if self.type == ComponentType.LOOP:
            loop_body = self.configs.get("loop_body")
            if not loop_body:
                raise ValueError("type == jiuwen.loop requires a non-empty configs.loop_body")
            if not any(b.name == "arr_loop_var" for b in self.inputs):
                raise ValueError(
                    "type == jiuwen.loop requires a Binding named arr_loop_var"
                )

        return self


class Parameter(BaseModel):
    name: str
    type: str
    required: bool = True
    default: Any = None
    captured_value: Any = None
    description: str = ""


class SkippedStep(BaseModel):
    id: str
    tool: str
    reason: str


class BindingRef(BaseModel):
    component_id: str
    input_name: str


class CaptureReport(BaseModel):
    steps_captured: int
    steps_skipped: int
    skipped_detail: list[SkippedStep] = Field(default_factory=list)
    class_breakdown: dict[str, int] = Field(default_factory=dict)
    loops_detected: int = 0
    parameters_lifted: list[str] = Field(default_factory=list)
    unresolved_bindings: list[BindingRef] = Field(default_factory=list)
    ambiguous_bindings: list[BindingRef] = Field(default_factory=list)
    viability: Viability
    viability_reason: str = ""

    @model_validator(mode="after")
    def _check_viability_reason(self) -> "CaptureReport":
        if self.viability != Viability.VIABLE and not self.viability_reason:
            raise ValueError("viability_reason is required when viability != viable")
        return self


class ComponentRunAudit(BaseModel):
    component_id: str
    resolved_inputs: dict[str, Any] = Field(default_factory=dict)
    tool_fingerprint: str | None = None
    skipped_for_effect: bool = False
    output_hash: str | None = None


class RunRecord(BaseModel):
    run_id: str
    workflow_version: int
    started_at: datetime
    finished_at: datetime | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    mode: RunMode
    outcome: RunOutcome
    failed_component_id: str | None = None
    failure_reason: str | None = None
    result_consistent: bool | None = None
    consistency_method: str | None = None
    promotion_basis: PromotionBasis | None = None
    # Promotion audit trail for the manual path. PLAN.md § Result-consistency
    # contract: "/workflow promote ... records the override, the acting
    # user, and the run_id it is based on in the RunRecord" — data-model.md's
    # own RunRecord field table never listed these, an omission an
    # independent Codex code review of this diff caught as an unacknowledged
    # deviation from that prose. `source_run_id` is the run being promoted;
    # for a manual-promotion record it differs from this record's own
    # `run_id`, which identifies the promotion action itself.
    acting_user: str | None = None
    source_run_id: str | None = None
    components: list[ComponentRunAudit] = Field(default_factory=list)
    capture_algorithm_version: str
    ir_schema_version: str
    token_cost: int | None = None

    @model_validator(mode="after")
    def _check_promotion_and_dry_run_rules(self) -> "RunRecord":
        if self.mode == RunMode.DRY and self.result_consistent is not None:
            raise ValueError(
                "result_consistent must be None for a dry run — a dry run "
                "statically skips non-read components and cannot demonstrate "
                "the captured result"
            )
        if self.promotion_basis == PromotionBasis.AUTO and self.result_consistent is not True:
            raise ValueError(
                "promotion_basis == auto requires result_consistent == True"
            )
        if self.promotion_basis == PromotionBasis.MANUAL_ACK and not (
            self.acting_user and self.source_run_id
        ):
            raise ValueError(
                "promotion_basis == manual_ack requires acting_user and "
                "source_run_id to be set"
            )
        return self


class SavedWorkflow(BaseModel):
    """The persisted artifact. Deliberately carries NO `status` and NO `runs`
    field.

    `data-model.md`'s field table lists both, but each contradicts the same
    architectural fact: version directories are immutable once written
    (`PLAN.md` § Store contract), and neither a lifecycle status nor an
    accumulating execution history can live inside an immutable artifact.
    The review caught and removed `status` in Round 4 for exactly this
    reason (see the removed-field note this class's docstring used to carry
    inline). `runs` is the same bug, unreviewed: it was never revised
    alongside `status`, but the same architecture applies to it — execution
    history is authoritative in `runs.jsonl`, not embedded in `workflow.json`.
    Dropped here as a build-time deviation; flagged for the spec to catch up.
    """

    workflow_id: str
    workflow_name: str
    description: str = ""
    version: int
    components: list[Component]
    connections: list[Connection] = Field(default_factory=list)
    parameters: list[Parameter] = Field(default_factory=list)
    source_session_id: str
    captured_at: datetime
    capture_report: CaptureReport
