"""LLM-driven Feed research over trusted demo input; no static search plan.

Only minimize provider fields and delegate to the shared structured Tool loop.
No DB/RAG wiring, query admission, source resolver or persistence implementation.
"""
import json

from ..agent_loop_runner import AgentLoopRunner
from ..outputs import FeedAnswerV1, StructuredAgentResult
from ..schemas import AgentContext
from .feed import validate_feed_runner
from .feed_profile_planning import FeedProfilePlanningInputV1


class FeedRetrievalPlanningError(RuntimeError):
    """Fixed failure only; never expose profile or underlying exception data."""

    def __init__(self):
        super().__init__("Feed retrieval planning failed")


def _build_provider_research_input(planning: FeedProfilePlanningInputV1) -> str:
    """Select disclosure fields only; caller owns auth/scope/time validation.

    Preserve request-local refs and free text. This is not a PII sanitizer.
    Do not dump/reparse the whole DTO or regenerate refs.
    """
    if not isinstance(planning, FeedProfilePlanningInputV1):
        raise TypeError("Trusted planning input required")
    caregiver = planning.caregiver_profile
    payload = {
        "caregiver": None if caregiver is None else {
            "relationship": caregiver.relationship,
            "lifestyle_tags": list(caregiver.lifestyle_tags),
        },
        "patients": [{
            "patient_ref": patient.patient_ref,
            "dementia_stage": patient.dementia_stage,
            "symptoms": list(patient.symptoms),
            "interests": list(patient.interests),
            "safety_events": [
                label for flag, label in (
                    ("has_fall", "fall"), ("has_wandering", "wandering"), ("has_missing", "missing")
                ) if any(getattr(row, flag) for row in patient.recent_safety_events)
            ],
            "assessment_types": [row.assessment_type for row in patient.latest_assessments],
        } for patient in planning.managed_patient_profiles],
    }
    return json.dumps(payload, ensure_ascii=False, allow_nan=False)


class FeedRetrievalPlannerV1:
    """Project trusted input, run iterative research, return the Runner result.

    Uses a caller-assembled Feed runner: FeedPromptBuilder, search_evidence only,
    3 Tool rounds and 3 total calls. No extra loop/counter or output parser.
    Only completed + FeedAnswerV1 is eligible for future Backend handoff;
    Workflow integration owns enforcement and persistence.
    """

    def __init__(self, runner: AgentLoopRunner):
        try:
            validate_feed_runner(runner)
        except Exception:
            raise FeedRetrievalPlanningError() from None
        self.runner = runner

    def __call__(self, context: AgentContext,
                 planning: FeedProfilePlanningInputV1) -> StructuredAgentResult:
        try:
            # Match Workflow's protection against mutable Runner configuration.
            validate_feed_runner(self.runner)
            provider_input = _build_provider_research_input(planning)
            return self.runner.run_structured(provider_input, context, output_model=FeedAnswerV1)
        except Exception:
            raise FeedRetrievalPlanningError() from None
