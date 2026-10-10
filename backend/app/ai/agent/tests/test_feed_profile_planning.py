"""Actual Backend DTO projection; no DB/live provider or snapshot claims."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import traceback
import unittest
from unittest.mock import patch
from uuid import UUID

from pydantic import ValidationError

from app.user_schemas import APP_TIMEZONE, PatientContext, PROFILE_RECENT_VISITS, PROFILE_SAFETY_DAYS
from app.ai.agent.schemas import AgentContext
from app.ai.agent.workflows.feed_profile_planning import (
    FeedPatientPlanningProfile, FeedProfileMappingError, FeedProfilePlanningInputV1,
    PatientProfileSourceMappingV1, PROFILE_PLANNING_AVAILABLE_SOURCES,
)


NOW = datetime(2026, 10, 9, 16, tzinfo=timezone.utc)
TODAY = date(2026, 10, 10)
USER, P1, P2, OTHER = (UUID(int=value) for value in (10, 1, 2, 99))
CONTEXT = AgentContext(request_id="private-request-marker", user_id=USER, patient_id=OTHER)


def backend_patient(identifier=P1):
    # Build against real Backend DTO; these are synthetic persisted values.
    row = {"patient_id": identifier, "created_at": NOW}
    return PatientContext.model_validate({
        "patient": {"id": identifier, "caregiver_id": USER, "name": "PRIVATE_PATIENT_NAME",
                    "dementia_stage": "legacy-stage", "diagnosis_date": TODAY,
                    "symptoms": ["stored symptom"], "interests": ["stored interest"],
                    "created_at": NOW, "updated_at": NOW},
        "latest_assessments": [{**row, "id": UUID(int=101), "assessment_type": "legacy-test",
                                "score": 12.3, "result_detail": "stored assessment",
                                "assessed_at": date(2020, 1, 1), "assessed_by": "PRIVATE_ASSESSOR"}],
        "current_medications": [{**row, "id": UUID(int=102), "drug_name": "stored drug",
                                 "is_taking": True, "start_date": date(2020, 1, 1),
                                 "end_date": date(2020, 2, 1), "note": "stored medication note",
                                 "dosage": "PRIVATE_DOSAGE", "frequency": "PRIVATE_FREQUENCY",
                                 "updated_at": NOW}],
        "recent_safety_events": [{**row, "id": UUID(int=103), "event_date": TODAY,
                                  "has_fall": True, "has_wandering": False, "has_missing": False,
                                  "note": "stored event note"}],
        "recent_visits": [{**row, "id": UUID(int=104), "visit_date": TODAY, "is_visited": True,
                           "department": "stored department", "hospital_name": "PRIVATE_HOSPITAL",
                           "visit_content": "legacy whole visit or free memo", "updated_at": NOW,
                           **{key: "PRIVATE_005_DETAIL" for key in (
                               "visit_reason", "diagnosis", "treatment_content", "test_summary",
                               "medication_change", "doctor_note", "follow_up_plan")}}],
        "upcoming_visits": [{**row, "id": UUID(int=105), "visit_date": TODAY + timedelta(days=1),
                             "is_visited": False, "department": None, "visit_content": None,
                             "updated_at": NOW}],
    })


class ProfileMappingTests(unittest.TestCase):
    def setUp(self):
        self.mapper = PatientProfileSourceMappingV1(service_timezone=APP_TIMEZONE)

    def map(self, rows=None):
        return self.mapper(CONTEXT, [backend_patient()] if rows is None else rows, reference_time=NOW)

    def test_real_backend_dto_allowlist_and_direct_visit_content(self):
        result = self.map()
        text = result.model_dump_json()
        for forbidden in (str(USER), str(P1), str(OTHER), CONTEXT.request_id,
                          "PRIVATE_PATIENT_NAME", "PRIVATE_ASSESSOR", "PRIVATE_DOSAGE",
                          "PRIVATE_FREQUENCY", "PRIVATE_HOSPITAL", "PRIVATE_005_DETAIL"):
            self.assertNotIn(forbidden, text)
        patient = result.managed_patient_profiles[0]
        self.assertEqual(patient.patient_ref, "patient_1")
        self.assertEqual(patient.latest_assessments[0].score, Decimal("12.3"))
        self.assertEqual(patient.latest_assessments[0].assessed_at, date(2020, 1, 1))
        self.assertEqual(patient.dementia_stage, "legacy-stage")
        self.assertEqual(patient.current_medications[0].end_date, date(2020, 2, 1))
        self.assertTrue(patient.current_medications[0].is_taking)
        self.assertEqual(patient.recent_visits[0].visit_content, "legacy whole visit or free memo")
        self.assertIsNone(patient.upcoming_visits[0].visit_content)

    def test_uuid_bytes_mapping_and_provenance_ignore_context_patient_id(self):
        first, second = backend_patient(P1), backend_patient(P2)
        second.patient.symptoms = ["second patient only"]
        a, b = self.map([second, first]), self.map([first, second])
        self.assertEqual(a, b)
        self.assertEqual([row.patient_ref for row in a.managed_patient_profiles], ["patient_1", "patient_2"])
        self.assertEqual(a.managed_patient_profiles[0].symptoms, ["stored symptom"])
        self.assertEqual(a.managed_patient_profiles[1].symptoms, ["second patient only"])

    def test_unavailable_sources_are_not_empty_or_null_fields(self):
        result = self.map()
        self.assertEqual(set(type(result).model_fields), {"schema_version", "reference_time", "managed_patient_profiles"})
        self.assertNotIn("care_logs", result.model_dump_json())
        self.assertNotIn("long_term_summary", result.model_dump_json())
        self.assertNotIn("coverage", result.model_dump_json())
        self.assertNotIn("available_source_scope", result.model_dump_json())
        self.assertNotIn("care_logs", PROFILE_PLANNING_AVAILABLE_SOURCES)
        self.assertNotIn("long_term_summary", PROFILE_PLANNING_AVAILABLE_SOURCES)

    def test_empty_selected_lists_require_explicit_backend_fields(self):
        sparse = PatientContext(patient=backend_patient().patient)
        with self.assertRaises(FeedProfileMappingError):
            self.map([sparse])
        explicit = PatientContext(patient=sparse.patient, latest_assessments=[], current_medications=[],
                                  recent_safety_events=[], recent_visits=[], upcoming_visits=[])
        self.assertEqual(self.map([explicit]).managed_patient_profiles[0].latest_assessments, [])

    def test_scope_and_invalid_rows_fail_without_partial_output_or_data_leak(self):
        cases = []
        row = backend_patient(); row.patient.caregiver_id = OTHER; cases.append([row])
        cases.append([backend_patient(), backend_patient()])
        for field in ("latest_assessments", "current_medications", "recent_safety_events",
                      "recent_visits", "upcoming_visits"):
            row = backend_patient(); getattr(row, field)[0].patient_id = OTHER; cases.append([row])
            row = backend_patient(); getattr(row, field).append(deepcopy(getattr(row, field)[0])); cases.append([row])
        row = backend_patient(); row.current_medications[0].is_taking = "true"; cases.append([row])
        row = backend_patient(); row.patient.updated_at = NOW.replace(tzinfo=None); cases.append([row])
        cases.append([backend_patient().model_dump()])
        for rows in cases:
            with self.subTest(rows_type=type(rows[0]).__name__), self.assertRaises(FeedProfileMappingError) as caught:
                self.map(rows)
            self.assertEqual(str(caught.exception), "Feed profile mapping failed")
            self.assertNotIn("PRIVATE", "".join(traceback.format_exception(caught.exception)))
            self.assertTrue(caught.exception.__suppress_context__)

    def test_selection_and_temporal_invalid_rows_reject_not_drop(self):
        changes = [
            lambda row: setattr(row.latest_assessments[0], "assessed_at", TODAY + timedelta(days=1)),
            lambda row: setattr(row.current_medications[0], "is_taking", False),
            lambda row: setattr(row.current_medications[0], "start_date", TODAY),
            lambda row: setattr(row.recent_safety_events[0], "has_fall", False),
            lambda row: setattr(row.recent_visits[0], "is_visited", False),
            lambda row: setattr(row.upcoming_visits[0], "visit_date", TODAY - timedelta(days=1)),
            lambda row: setattr(row.upcoming_visits[0], "id", row.recent_visits[0].id),
        ]
        for change in changes:
            row = backend_patient(); change(row)
            with self.assertRaises(FeedProfileMappingError):
                self.map([row])

    def test_strict_extra_forbid_mutated_instance_and_duplicate_ref(self):
        data = self.map().model_dump()
        for field, value in (("user_id", USER), ("care_logs", []), ("long_term_summary", None)):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                FeedProfilePlanningInputV1.model_validate({**data, field: value}, context={"service_timezone": APP_TIMEZONE})
        for field in ("patient_id", "name", "hospital_name"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                FeedPatientPlanningProfile.model_validate({**data["managed_patient_profiles"][0], field: "private"})
        mutated = self.map()
        mutated.managed_patient_profiles[0].current_medications[0].is_taking = "true"
        with self.assertRaises(ValidationError):
            FeedProfilePlanningInputV1.model_validate(mutated, context={"service_timezone": APP_TIMEZONE})
        data["managed_patient_profiles"].append(deepcopy(data["managed_patient_profiles"][0]))
        with self.assertRaises(ValidationError):
            FeedProfilePlanningInputV1.model_validate(data, context={"service_timezone": APP_TIMEZONE})

    def test_aware_utc_reference_and_service_date_without_new_clock(self):
        result = self.mapper(CONTEXT, [backend_patient()], reference_time=NOW.astimezone(APP_TIMEZONE))
        self.assertEqual(result.reference_time, NOW)
        self.assertIs(result.reference_time.tzinfo, timezone.utc)
        for instant in (NOW.replace(tzinfo=None), TODAY, None):
            with self.assertRaises(FeedProfileMappingError):
                self.mapper(CONTEXT, [backend_patient()], reference_time=instant)
        with self.assertRaises(FeedProfileMappingError):
            PatientProfileSourceMappingV1(service_timezone=None)(CONTEXT, [backend_patient()], reference_time=NOW)

    def test_backend_selected_aggregate_rules_with_mocked_reads(self):
        # Execute actual selection code, mocking all DB helpers; no SQL executes.
        from app.db import user_functions as backend
        row = backend_patient()
        positive = row.recent_safety_events[0]
        negative = positive.model_copy(update={"has_fall": False})
        near, far = row.upcoming_visits[0], row.upcoming_visits[0].model_copy(
            update={"id": UUID(int=106), "visit_date": TODAY + timedelta(days=3)})
        with patch.object(backend, "get_latest_assessments", return_value=row.latest_assessments) as assessments, \
             patch.object(backend, "list_medications", return_value=row.current_medications) as medications, \
             patch.object(backend, "list_safety_events", return_value=[positive, negative]) as safety, \
             patch.object(backend, "list_medical_visits", side_effect=[ [far, near], row.recent_visits ]) as visits:
            actual = backend._patient_context(row.patient, TODAY)
        assessments.assert_called_once_with(P1)
        medications.assert_called_once_with(P1, is_taking=True)
        safety.assert_called_once_with(P1, start_date=TODAY - timedelta(days=PROFILE_SAFETY_DAYS - 1), end_date=TODAY)
        self.assertEqual(actual.recent_safety_events, [positive])
        self.assertEqual(actual.upcoming_visits, [near, far])
        self.assertEqual(visits.call_args_list[0].kwargs, {"is_visited": False, "start_date": TODAY})
        self.assertEqual(visits.call_args_list[1].kwargs, {"is_visited": True, "limit": PROFILE_RECENT_VISITS})
        self.assertEqual(self.map([actual]).managed_patient_profiles[0].latest_assessments[0].assessed_at, date(2020, 1, 1))
