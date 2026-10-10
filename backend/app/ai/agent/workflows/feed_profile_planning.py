"""PatientProfileSourceMappingV1 and profile-only demo planning boundary.

Consumes actual Backend PatientContext DTOs after a trusted authorized read.
No DB calls, counts, Source Snapshot, CareLog/Summary placeholders or full V1
claims. Backend selected lists are preserved separately, not promoted to history.
"""
from datetime import date, datetime, timezone, tzinfo
from decimal import Decimal
from typing import Literal, Sequence
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, StrictStr, ValidationInfo, model_validator

from app.user_schemas import MedicalVisitOut, PatientContext

from ..schemas import AgentContext, AgentModel
from .feed_context import (
    ClinicalAssessmentContext, MedicationContext, MedicalVisitContext, SafetyEventContext,
)


# Trusted server configuration for this mapper; never serialized as patient data.
PROFILE_PLANNING_AVAILABLE_SOURCES = frozenset({
    "stored_patient_profile", "latest_assessments", "current_medications",
    "recent_safety_events", "recent_visits", "upcoming_visits",
})


class FeedPatientPlanningProfile(AgentModel):
    """Stored background and selected signals, without history/count claims."""

    model_config = ConfigDict(revalidate_instances="always")

    patient_ref: StrictStr = Field(min_length=1)
    dementia_stage: StrictStr | None
    diagnosis_date: date | None
    symptoms: list[StrictStr]
    interests: list[StrictStr]
    updated_at: AwareDatetime
    latest_assessments: list[ClinicalAssessmentContext]
    current_medications: list[MedicationContext]
    recent_safety_events: list[SafetyEventContext]
    recent_visits: list[MedicalVisitContext]
    upcoming_visits: list[MedicalVisitContext]


class FeedProfilePlanningInputV1(AgentModel):
    """Separate demo input, neither full Feed Context V1 nor a Lite/V2 version.

    Validate with server-only service_timezone. reference_time is a validation
    anchor; it does not certify Backend helper clocks, auth, counts or snapshot.
    No CareLog/Summary fields: unavailable sources are not successful absence.
    """

    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["feed-profile-planning-1"]
    reference_time: AwareDatetime
    managed_patient_profiles: list[FeedPatientPlanningProfile]

    @model_validator(mode="after")
    def validate_signals(self, info: ValidationInfo):
        service_timezone = (info.context.get("service_timezone")
                            if isinstance(info.context, dict) else None)
        if not isinstance(service_timezone, tzinfo):
            raise ValueError("Planning validation requires service_timezone")
        reference_date = self.reference_time.astimezone(service_timezone).date()
        patients = self.managed_patient_profiles
        if len({patient.patient_ref for patient in patients}) != len(patients):
            raise ValueError("Duplicate planning patient_ref")
        for patient in patients:
            if patient.diagnosis_date is not None and patient.diagnosis_date > reference_date:
                raise ValueError("Future diagnosis_date")
            types = [row.assessment_type for row in patient.latest_assessments]
            if len(set(types)) != len(types):
                raise ValueError("Duplicate latest assessment type")
            if any(row.assessed_at > reference_date for row in patient.latest_assessments):
                raise ValueError("Future assessed_at")
            if any(not row.is_taking for row in patient.current_medications):
                raise ValueError("Current medication lacks stored is_taking flag")
            if any(row.event_date > reference_date or not (
                    row.has_fall or row.has_wandering or row.has_missing)
                   for row in patient.recent_safety_events):
                raise ValueError("Invalid selected safety event")
            if any(not row.is_visited or row.visit_date > reference_date for row in patient.recent_visits):
                raise ValueError("Invalid selected completed visit")
            if any(row.is_visited or row.visit_date < reference_date for row in patient.upcoming_visits):
                raise ValueError("Invalid selected upcoming visit")
        return self


class FeedProfileMappingError(RuntimeError):
    """Fixed failure only; no Backend data or validation input in public text."""

    def __init__(self):
        super().__init__("Feed profile mapping failed")


def _visit_payload(row: MedicalVisitOut) -> dict[str, object]:
    # Direct legacy/free memo only; never concat or parse structured 005 fields.
    return {"visit_date": row.visit_date, "is_visited": row.is_visited,
            "department": row.department, "visit_content": row.visit_content}


class PatientProfileSourceMappingV1:
    """Map authorized Backend patient DTOs to minimized demo planning data.

    Structural ownership checks do not implement DB authorization. Caller must
    fail missing/denied/partial reads before calling, and supply every selected
    collection explicitly (Backend DTO defaults are not read-success evidence).
    Future complete Source uses the same field allowlist, with real E/I counts.
    No eligibility/history default or fresh now()/today() is invented here.
    """

    def __init__(self, *, service_timezone: tzinfo):
        self.service_timezone = service_timezone

    def __call__(self, context: AgentContext, profiles: Sequence[PatientContext], *,
                 reference_time: datetime) -> FeedProfilePlanningInputV1:
        try:
            if (not isinstance(reference_time, datetime) or reference_time.tzinfo is None
                    or reference_time.utcoffset() is None):
                raise ValueError("Aware reference_time required")
            if not isinstance(self.service_timezone, tzinfo):
                raise ValueError("Service timezone required")
            reference = reference_time.astimezone(timezone.utc)
            # Reject bad timezone before reading/projecting supplied rows.
            reference.astimezone(self.service_timezone).date()
            ids = set()
            selected = {"latest_assessments", "current_medications", "recent_safety_events",
                        "recent_visits", "upcoming_visits"}
            for item in profiles:
                if not isinstance(item, PatientContext) or not {"patient", *selected} <= item.model_fields_set:
                    raise ValueError("Explicit Backend patient read required")
                patient = item.patient
                if (not isinstance(patient.id, UUID) or patient.id in ids
                        or patient.caregiver_id != context.user_id):
                    raise ValueError("Invalid patient ownership/identity")
                ids.add(patient.id)
                visit_ids = set()
                for field in selected:
                    row_ids = set()
                    for row in getattr(item, field):
                        if row.patient_id != patient.id or not isinstance(row.id, UUID) or row.id in row_ids:
                            raise ValueError("Invalid child ownership/identity")
                        row_ids.add(row.id)
                        if field in ("recent_visits", "upcoming_visits"):
                            if row.id in visit_ids:
                                raise ValueError("Duplicate visit across selected lists")
                            visit_ids.add(row.id)
            payload = []
            for index, item in enumerate(sorted(profiles, key=lambda row: row.patient.id.bytes), start=1):
                patient = item.patient
                payload.append({
                    "patient_ref": f"patient_{index}", "dementia_stage": patient.dementia_stage,
                    "diagnosis_date": patient.diagnosis_date, "symptoms": patient.symptoms,
                    "interests": patient.interests, "updated_at": patient.updated_at,
                    "latest_assessments": [{
                        "assessment_type": row.assessment_type,
                        "score": None if row.score is None else Decimal(str(row.score)),
                        "result_detail": row.result_detail, "assessed_at": row.assessed_at,
                    } for row in item.latest_assessments],
                    "current_medications": [{
                        "drug_name": row.drug_name, "is_taking": row.is_taking,
                        "start_date": row.start_date, "end_date": row.end_date, "note": row.note,
                    } for row in item.current_medications],
                    "recent_safety_events": [{
                        "event_date": row.event_date, "has_fall": row.has_fall,
                        "has_wandering": row.has_wandering, "has_missing": row.has_missing, "note": row.note,
                    } for row in item.recent_safety_events],
                    "recent_visits": [_visit_payload(row) for row in item.recent_visits],
                    "upcoming_visits": [_visit_payload(row) for row in item.upcoming_visits],
                })
            return FeedProfilePlanningInputV1.model_validate({
                "schema_version": "feed-profile-planning-1", "reference_time": reference,
                "managed_patient_profiles": payload,
            }, context={"service_timezone": self.service_timezone})
        except Exception:
            raise FeedProfileMappingError() from None
