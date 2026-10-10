"""AI-side server-only Source DTOs; not public Backend/DB or LLM contracts.

Production Source owns authorization, eligibility, snapshot/count consistency
and stable record ordering. Free text remains untrusted data.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Generic, Protocol, TypeVar
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

from ..schemas import AgentModel


class FeedSourceModel(AgentModel):
    model_config = ConfigDict(revalidate_instances="always")


T = TypeVar("T")


class SourceCollection(FeedSourceModel, Generic[T]):
    """Eligible count after authorization/base/time filters, before return limit."""

    items: list[T]
    total_count: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def validate_count(self):
        if len(self.items) > self.total_count:
            raise ValueError("Source total_count is smaller than items")
        return self


class SourceCaregiverProfile(FeedSourceModel):
    user_id: UUID
    relationship: StrictStr | None
    burden_score: StrictInt | None
    mood_score: StrictInt | None
    lifestyle_tags: list[StrictStr]
    updated_at: AwareDatetime


class SourceClinicalAssessment(FeedSourceModel):
    patient_id: UUID
    assessment_type: StrictStr
    score: Decimal | None
    result_detail: StrictStr | None
    assessed_at: date


class SourceSafetyEvent(FeedSourceModel):
    patient_id: UUID
    event_date: date
    has_fall: StrictBool
    has_wandering: StrictBool
    has_missing: StrictBool
    note: StrictStr | None


class SourceMedication(FeedSourceModel):
    patient_id: UUID
    drug_name: StrictStr
    is_taking: StrictBool
    start_date: date | None
    end_date: date | None
    note: StrictStr | None


class SourceMedicalVisit(FeedSourceModel):
    patient_id: UUID
    visit_date: date
    is_visited: StrictBool
    department: StrictStr | None
    visit_content: StrictStr | None


class SourceManagedPatient(FeedSourceModel):
    patient_id: UUID
    caregiver_id: UUID
    dementia_stage: StrictStr | None
    diagnosis_date: date | None
    symptoms: list[StrictStr]
    interests: list[StrictStr]
    updated_at: AwareDatetime
    clinical_assessments: SourceCollection[SourceClinicalAssessment]
    safety_events: SourceCollection[SourceSafetyEvent]
    medications: SourceCollection[SourceMedication]
    medical_visits: SourceCollection[SourceMedicalVisit]


class SourceCareLog(FeedSourceModel):
    user_id: UUID
    patient_id: UUID | None
    log_type: StrictStr
    content: StrictStr | None
    mood_tag: StrictStr | None
    logged_at: AwareDatetime


class SourceLongTermSummary(FeedSourceModel):
    user_id: UUID
    period_start: date
    period_end: date
    summary: StrictStr | None
    updated_tags: list[StrictStr]
    generated_at: AwareDatetime


class FeedContextSourceSnapshot(FeedSourceModel):
    caregiver_profile: SourceCaregiverProfile | None
    managed_patients: SourceCollection[SourceManagedPatient]
    recent_care_logs: SourceCollection[SourceCareLog]
    long_term_summary: SourceLongTermSummary | None


class FeedContextSource(Protocol):
    def load_snapshot(self, *, user_id: UUID, reference_time: datetime,
                      recent_period_start: datetime,
                      recent_period_end: datetime) -> FeedContextSourceSnapshot:
        """Return authorized/eligible user data with stable ordering and counts.

        Use exactly the supplied reference/window for time-based selection;
        never create a separate now()/today(). CareLogs use [start, end).
        Clinical/safety/visit/log ordering follows the existing Context README;
        medication ordering and DB tie-breaks remain Production Source policy.
        No service timezone/local-date selection contract is supplied here.
        """
        ...
