"""Offline Core/Safety, V1 policy, metamorphic and full-validator fixtures."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from itertools import permutations
import traceback
import unittest
from unittest.mock import patch
from uuid import UUID

from pydantic import ValidationError

from app.user_schemas import APP_TIMEZONE
from ..workflows.feed_profile_planning import FeedProfilePlanningInputV1, FeedUserProfileSourceMappingV1
from ..workflows import feed_retrieval_planning as p
from .test_feed_profile_planning import (
    CONTEXT, NOW, P1, P2, USER, backend_caregiver, backend_patient, backend_profile,
)


def patient(identifier=P1, *, interests=(), symptoms=(), stage=None, safety=(), assessments=()):
    row = backend_patient(identifier)
    row.patient.interests = list(interests)
    row.patient.symptoms = list(symptoms)
    row.patient.dementia_stage = stage
    assessment = row.latest_assessments[0]
    row.latest_assessments = [assessment.model_copy(update={
        "id": UUID(int=assessment.id.int + index), "assessment_type": name,
    }) for index, name in enumerate(assessments)]
    event = row.recent_safety_events[0]
    row.recent_safety_events = [event.model_copy(update={
        "has_fall": "has_fall" in safety, "has_wandering": "has_wandering" in safety,
        "has_missing": "has_missing" in safety,
    })] if safety else []
    return row


def caregiver(*, relationship=None, tags=()):
    row = backend_caregiver()
    row.relationship = relationship
    row.lifestyle_tags = list(tags)
    return row


def planning(rows=(), *, care=None, reference=NOW):
    # Exercise real trusted construction with timezone context; no DB call.
    return FeedUserProfileSourceMappingV1(service_timezone=APP_TIMEZONE)(
        CONTEXT, backend_profile(list(rows), caregiver=care), reference_time=reference,
    )


def topics(plan):
    return [task.topic_key for task in plan.tasks]


def refs(task):
    return [(r.scope, r.patient_ref, r.field, r.signal_key) for r in task.signal_refs]


class CoreSafetyTests(unittest.TestCase):
    def setUp(self):
        self.planner = p.FeedRetrievalPlannerV1()

    def test_private_text_is_not_interpolated_or_matched_as_substrings(self):
        private = (str(USER), str(P1), "patient_1", "PRIVATE_PATIENT_NAME", "PRIVATE_CAREGIVER_NAME",
                   "email@example.invalid", "010-1234-5678", "PRIVATE_ADDRESS", "PRIVATE_HOSPITAL",
                   "PRIVATE_ASSESSOR", "visit_content", "PRIVATE_EVENT_DETAIL")
        row = patient(interests=["music therapy", *private], symptoms=[
            "Sleep problem at PRIVATE_ADDRESS", "수면장애 때문에 PRIVATE_PATIENT_NAME이 방문함", *private,
        ])
        plan = self.planner(planning([row], care=caregiver(tags=private)))
        self.assertEqual(topics(plan), ["music_therapy"])
        for task in plan.tasks:
            for value in private:
                self.assertNotIn(value, task.query)
                self.assertNotIn(value, task.topic_key)
        self.assertEqual(plan.tasks[0].query, "Music therapy research for people with dementia")

    def test_unsupported_scores_medications_visits_notes_do_not_create_topics(self):
        row = patient()
        row.current_medications[0].drug_name = "music therapy"
        row.current_medications[0].note = "fall prevention"
        row.recent_visits[0].visit_content = "sleep problem"
        care = caregiver()
        care.burden_score, care.mood_score = 88, 27
        plan = self.planner(planning([row], care=care))
        self.assertEqual(plan.tasks, [])

    def test_assessment_type_only_not_score_result_or_current_stage_inference(self):
        row = patient(assessments=["K-MMSE"])
        row.latest_assessments[0].score = 0.0
        row.latest_assessments[0].result_detail = "severe dementia sleep problem"
        plan = self.planner(planning([row]))
        self.assertEqual(topics(plan), ["cognitive_assessment"])
        self.assertEqual(refs(plan.tasks[0]), [("patient", "patient_1", "latest_assessments", "k_mmse")])
        row.latest_assessments[0].score = 30.0
        row.latest_assessments[0].result_detail = "normal"
        self.assertEqual(self.planner(planning([row])), plan)

    def test_unknown_legacy_stage_type_and_missing_signals_are_normal_skip(self):
        result = self.planner(planning([patient(stage="legacy-stage", assessments=["legacy-test"])]))
        self.assertEqual(result.tasks, [])
        self.assertEqual(self.planner(planning()).tasks, [])

    def test_patient_signals_are_not_combined_into_composite_query(self):
        result = self.planner(planning([
            patient(P1, symptoms=["sleep problem"]), patient(P2, symptoms=["wandering"]),
        ]))
        self.assertEqual(set(topics(result)), {"patient_sleep", "wandering_support"})
        by_topic = {task.topic_key: task for task in result.tasks}
        self.assertEqual(refs(by_topic["patient_sleep"]), [("patient", "patient_1", "symptoms", "sleep_problem")])
        self.assertEqual(refs(by_topic["wandering_support"]), [("patient", "patient_2", "symptoms", "wandering")])
        self.assertNotIn("wandering", by_topic["patient_sleep"].query.lower())
        self.assertNotIn("sleep", by_topic["wandering_support"].query.lower())

    def test_input_is_unchanged_and_supported_snapshot_is_immutable(self):
        source = planning([patient(interests=["music therapy"])], care=caregiver(relationship="child"))
        before = source.model_dump()
        self.planner(source)
        self.assertEqual(source.model_dump(), before)
        snapshot = p._project_supported_signals_v1(source)
        source.managed_patient_profiles[0].interests[:] = ["exercise"]
        canonical = p._extract_canonical_signals_v1(snapshot, p._CATALOG_V1)
        self.assertIn("music_activity", [r.signal_key for r in canonical])
        self.assertNotIn("physical_activity", [r.signal_key for r in canonical])
        with self.assertRaises(FrozenInstanceError):
            snapshot[0].value = "changed"

    def test_raw_inputs_are_not_public_parser_inputs_and_errors_are_fixed(self):
        for value in (None, {}, '{"private": "PRIVATE_PATIENT_NAME"}', backend_profile([])):
            with self.subTest(kind=type(value).__name__), self.assertRaises(p.FeedRetrievalPlanningError) as caught:
                self.planner(value)
            self.assertEqual(str(caught.exception), "Feed retrieval planning failed")
            self.assertNotIn("PRIVATE_PATIENT_NAME", "".join(traceback.format_exception(caught.exception)))
            self.assertTrue(caught.exception.__suppress_context__)

    def test_corrupted_supported_collections_flags_and_refs_fail_closed(self):
        for target, field, value in (("patient", "interests", "music therapy"),
                                     ("patient", "symptoms", [123]),
                                     ("patient", "patient_ref", " "),
                                     ("caregiver", "lifestyle_tags", None)):
            source = planning([patient()], care=caregiver())
            row = source.managed_patient_profiles[0] if target == "patient" else source.caregiver_profile
            setattr(row, field, value)
            with self.subTest(field=field), self.assertRaises(p.FeedRetrievalPlanningError):
                self.planner(source)
        source = planning([patient(safety=["has_fall"])])
        source.managed_patient_profiles[0].recent_safety_events[0].has_fall = 1
        with self.assertRaises(p.FeedRetrievalPlanningError):
            self.planner(source)
        source = planning([patient(P1), patient(P2)])
        source.managed_patient_profiles[1].patient_ref = "patient_1"
        with self.assertRaises(p.FeedRetrievalPlanningError):
            self.planner(source)

    def test_planner_does_not_revalidate_time_context_or_use_reference_for_query(self):
        first = planning([patient(interests=["music therapy"])])
        second = planning([patient(interests=["music therapy"])], reference=NOW + timedelta(minutes=1))
        with patch.object(type(first), "model_validate", side_effect=AssertionError("No full input reparse")):
            self.assertEqual(self.planner(first), self.planner(second))
        self.assertNotIn("2026", self.planner(first).tasks[0].query)


class V1PolicyTests(unittest.TestCase):
    def setUp(self):
        self.planner = p.FeedRetrievalPlannerV1()

    def test_normalization_contract(self):
        self.assertEqual(p.normalize_v1("　Ｓｌｅｅｐ\t  Ｐｒｏｂｌｅｍ\n"), "sleep problem")
        self.assertEqual(p.normalize_v1("Straße"), "strasse")
        self.assertEqual(p.normalize_v1(" A\u00a0\n B "), "a b")
        self.assertEqual(p.normalize_v1(" Sleep-Problem! "), "sleep-problem!")
        self.assertNotEqual(p.normalize_v1("수면장애"), p.normalize_v1("수면 장애"))

    def test_exact_aliases_use_same_normalizer_without_semantic_matching(self):
        for value in ("sleep problem", "　ＳＬＥＥＰ   ＰＲＯＢＬＥＭ　", "수면장애", "수면 장애"):
            self.assertEqual(topics(self.planner(planning([patient(symptoms=[value])]))), ["patient_sleep"])
        for value in ("sleep-problem", "수면장애가 있음", "insomnia", "음악 치료"):
            self.assertEqual(self.planner(planning([patient(symptoms=[value], interests=[value])])).tasks, [])

    def test_priority_levels_and_merged_strongest_signal(self):
        self.assertEqual(dict(p._PRIORITY), {"interests": 1, "recent_safety_events": 2, "symptoms": 3,
                                           "relationship": 4, "lifestyle_tags": 4,
                                           "dementia_stage": 5, "latest_assessments": 6})
        source = planning([patient(interests=["sleep management"], symptoms=["sleep problem"] )])
        signals = p._extract_canonical_signals_v1(p._project_supported_signals_v1(source), p._CATALOG_V1)
        candidate, = p._merge_topic_candidates_v1(signals, p._CATALOG_V1)
        self.assertEqual(candidate.priority, 1)
        self.assertEqual(refs(self.planner(source).tasks[0]), [
            ("patient", "patient_1", "interests", "sleep_management"),
            ("patient", "patient_1", "symptoms", "sleep_problem"),
        ])

    def test_gain_precedes_signal_priority(self):
        source = planning([
            patient(P1, interests=["music therapy"], symptoms=["sleep problem"]),
            patient(P2, symptoms=["sleep problem"]),
        ], care=caregiver(relationship="child"))
        self.assertEqual(topics(self.planner(source)), ["patient_sleep", "family_caregiving", "music_therapy"])

    def test_new_group_coverage_precedes_filling_high_priority_same_patient(self):
        source = planning([
            patient(P1, interests=["music therapy", "nutrition", "exercise"]),
            patient(P2, symptoms=["wandering"]),
        ], care=caregiver(relationship="child"))
        self.assertEqual(topics(self.planner(source)), ["music_therapy", "wandering_support", "family_caregiving"])

    def test_zero_gain_fill_uses_priority_then_topic_key_and_max_three(self):
        source = planning([patient(interests=["exercise", "nutrition", "music therapy", "sleep management"],
                                   symptoms=["memory loss"], stage="중등도", assessments=["CDR"])])
        plan = self.planner(source)
        self.assertEqual(topics(plan), ["music_therapy", "nutrition_support", "patient_sleep"])
        self.assertEqual([t.task_id for t in plan.tasks], ["task_1", "task_2", "task_3"])
        self.assertTrue(all(t.top_k == 5 for t in plan.tasks))

    def test_canonical_ref_order_is_scope_lexical_ref_fixed_field_then_signal(self):
        values = [
            p.FeedSignalRefV1(scope="patient", patient_ref="patient_2", field="symptoms", signal_key="sleep_problem"),
            p.FeedSignalRefV1(scope="patient", patient_ref="patient_10", field="symptoms", signal_key="sleep_problem"),
            p.FeedSignalRefV1(scope="caregiver", patient_ref=None, field="relationship", signal_key="family_caregiver"),
            p.FeedSignalRefV1(scope="patient", patient_ref="patient_2", field="interests", signal_key="music_activity"),
        ]
        self.assertEqual([r.patient_ref for r in sorted(values, key=p._ref_key)],
                         [None, "patient_10", "patient_2", "patient_2"])
        self.assertEqual(sorted(values, key=p._ref_key)[2].field, "interests")

    def test_greedy_final_tie_break_uses_canonical_provenance(self):
        r1 = p.FeedSignalRefV1(scope="patient", patient_ref="patient_1", field="interests", signal_key="music_activity")
        r2 = r1.model_copy(update={"patient_ref": "patient_2"})
        # Independent helper fixture exercises the otherwise unique-topic tie.
        a = p._TopicCandidateV1("same_key", "unused", (r1,), frozenset({("patient", "patient_1")}), 1)
        b = p._TopicCandidateV1("same_key", "unused", (r2,), frozenset({("patient", "patient_2")}), 1)
        self.assertEqual(p._select_topics_greedy_v1((b, a)), (a, b))

    def test_only_explicit_positive_flags_generate_safety_refs(self):
        result = self.planner(planning([patient(safety=["has_wandering"])]))
        self.assertEqual(topics(result), ["wandering_support"])
        self.assertEqual(refs(result.tasks[0]), [("patient", "patient_1", "recent_safety_events", "wandering")])
        self.assertEqual(self.planner(planning([patient()])).tasks, [])

    def test_registered_stage_and_assessment_keep_distinct_provenance(self):
        result = self.planner(planning([patient(stage="中等度", assessments=["legacy"]) ]))
        self.assertEqual(result.tasks, [])
        result = self.planner(planning([patient(stage="중등도", assessments=["K-MMSE", "CDR"]) ]))
        self.assertEqual(topics(result), ["moderate_dementia", "cognitive_assessment", "functional_assessment"])


class MetamorphicTests(unittest.TestCase):
    def test_patient_and_supported_list_order_permutations_preserve_model_and_array_order(self):
        rows = [patient(P1, interests=["music therapy", "sleep management"], symptoms=["sleep problem"]),
                patient(P2, interests=["sleep management"], symptoms=["wandering", "memory loss"])]
        planner = p.FeedRetrievalPlannerV1()
        source = planning(rows, care=caregiver(tags=["sleep problem", "employed caregiver"]))
        original = source.model_dump()
        original_refs = [row.patient_ref for row in source.managed_patient_profiles]
        self.assertEqual(original_refs, ["patient_1", "patient_2"])
        expected = planner(source).model_dump()
        observed_orders = []
        for order in permutations(original["managed_patient_profiles"]):
            swapped = deepcopy(original)
            swapped["managed_patient_profiles"] = deepcopy(list(order))
            for row in swapped["managed_patient_profiles"]:
                row["interests"].reverse()
                row["symptoms"].reverse()
            swapped["caregiver_profile"]["lifestyle_tags"].reverse()
            # Revalidate the reordered planning DTO directly; Mapper would sort it again.
            reordered = FeedProfilePlanningInputV1.model_validate(
                swapped, context={"service_timezone": APP_TIMEZONE},
            )
            reordered_refs = [row.patient_ref for row in reordered.managed_patient_profiles]
            self.assertEqual(reordered_refs, [row["patient_ref"] for row in order])
            if reordered_refs == list(reversed(original_refs)):
                self.assertNotEqual(reordered_refs, original_refs)
            observed_orders.append(reordered_refs)
            actual = planner(reordered)
            self.assertEqual(actual.model_dump(), expected)
        self.assertEqual(observed_orders, [["patient_1", "patient_2"], ["patient_2", "patient_1"]])
        self.assertEqual(source.model_dump(), original)

    def test_repeated_aliases_and_safety_rows_do_not_change_refs_or_priority(self):
        source = planning([patient(interests=["sleep management"], symptoms=["sleep problem"], safety=["has_fall"])])
        expected = p.FeedRetrievalPlannerV1()(source)
        repeated = deepcopy(source)
        row = repeated.managed_patient_profiles[0]
        row.interests.extend(["SLEEP  MANAGEMENT", "sleep management"])
        row.symptoms.extend(["sleep problem", "수면장애"])
        row.recent_safety_events.extend(deepcopy(row.recent_safety_events) * 4)
        self.assertEqual(p.FeedRetrievalPlannerV1()(repeated), expected)

    def test_empty_selected_plan_is_stable_not_negative_fact_fallback(self):
        source = planning([patient(interests=["unknown narrative"], symptoms=["unknown symptom"])])
        result = p.FeedRetrievalPlannerV1()(source)
        self.assertEqual(result.model_dump(), {
            "schema_version": "feed-retrieval-plan-1", "policy_version": "feed-retrieval-policy-1", "tasks": [],
        })
        self.assertEqual(p.validate_retrieval_plan_v1(result, source), result)


class CatalogQualityTests(unittest.TestCase):
    def test_same_sleep_alias_has_distinct_patient_and_caregiver_subjects(self):
        result = p.FeedRetrievalPlannerV1()(planning([patient(symptoms=["sleep problem"])],
                                                   care=caregiver(tags=["sleep problem"])))
        self.assertEqual(set(topics(result)), {"patient_sleep", "caregiver_sleep"})
        by_topic = {task.topic_key: task for task in result.tasks}
        self.assertNotEqual(by_topic["patient_sleep"].query, by_topic["caregiver_sleep"].query)
        self.assertEqual(refs(by_topic["caregiver_sleep"]), [("caregiver", None, "lifestyle_tags", "sleep_deprivation")])
        self.assertEqual(refs(by_topic["patient_sleep"]), [("patient", "patient_1", "symptoms", "sleep_problem")])

    def test_same_topic_merges_complete_multi_patient_support(self):
        result = p.FeedRetrievalPlannerV1()(planning([
            patient(P2, interests=["music therapy"]), patient(P1, interests=["music activity"]),
        ]))
        self.assertEqual(topics(result), ["music_therapy"])
        self.assertEqual(refs(result.tasks[0]), [
            ("patient", "patient_1", "interests", "music_activity"),
            ("patient", "patient_2", "interests", "music_activity"),
        ])

    def test_catalog_is_deeply_immutable_and_every_signal_has_one_topic(self):
        index = p._validate_catalog_v1(p.TOPIC_CATALOG_V1)
        with self.assertRaises(FrozenInstanceError):
            p.TOPIC_CATALOG_V1[0].query = "changed"
        with self.assertRaises(TypeError):
            index.aliases[("patient", "interests", "private")] = "changed"
        self.assertEqual(len(index.topics), 17)
        for entry in p.TOPIC_CATALOG_V1:
            self.assertIsInstance(entry.sources, tuple)
            for rule in entry.sources:
                self.assertIsInstance(rule.aliases, tuple)
                self.assertEqual(index.signals[(rule.scope, rule.field, rule.signal_key)], entry.topic_key)


class CatalogFailureTests(unittest.TestCase):
    def test_invalid_catalog_shapes_keys_queries_and_rules_fail_configuration(self):
        entry = p.TOPIC_CATALOG_V1[1]
        rule = entry.sources[0]
        cases = [[], (), (entry, entry), (replace(entry, topic_key=" "),),
                 (replace(entry, query=" "),), (replace(entry, query=" padded "),),
                 (replace(entry, query="x" * 2001),), (replace(entry, sources=[]),),
                 (replace(entry, sources=()),),
                 (replace(entry, sources=(replace(rule, scope="caregiver"),)),),
                 (replace(entry, sources=(replace(rule, signal_key=" "),)),),
                 (replace(entry, sources=(replace(rule, aliases=("　",)),)),),
                 (replace(entry, sources=(replace(rule, aliases=(123,)),)),),
                 (replace(entry, sources=(replace(rule, aliases=["music"]),)),),
                 (entry, replace(entry, topic_key="different", sources=(replace(rule, signal_key="other"),)))]
        for catalog in cases:
            with self.subTest(kind=type(catalog).__name__), self.assertRaises(p.FeedRetrievalPlanningError) as caught:
                p._validate_catalog_v1(catalog)
            self.assertEqual(str(caught.exception), "Feed retrieval planning failed")

    def test_nfkc_normalized_alias_collision_is_configuration_failure(self):
        a = p._SourceRuleV1("patient", "interests", "first", ("ＡＢＣ   ＤＥＦ",))
        b = replace(a, signal_key="second", aliases=("abc def",))
        catalog = (p._TopicEntryV1("one", "First query", (a, b)),)
        with self.assertRaises(p.FeedRetrievalPlanningError):
            p._validate_catalog_v1(catalog)
        # Repeated aliases with the same canonical meaning are not ambiguous.
        valid = (p._TopicEntryV1("one", "First query", (replace(a, aliases=("ＡＢＣ   ＤＥＦ", "abc def")),)),)
        self.assertEqual(len(p._validate_catalog_v1(valid).aliases), 1)

    def test_one_canonical_signal_cannot_map_to_multiple_topics(self):
        rule = p._SourceRuleV1("patient", "interests", "music_activity", ("music",))
        catalog = (p._TopicEntryV1("one", "First query", (rule,)),
                   p._TopicEntryV1("two", "Second query", (replace(rule, aliases=("other spelling",)),)))
        with self.assertRaises(p.FeedRetrievalPlanningError):
            p._validate_catalog_v1(catalog)

    def test_corrupted_server_catalog_fails_even_when_input_has_no_signals(self):
        with patch.object(p, "TOPIC_CATALOG_V1", ()):
            with self.assertRaises(p.FeedRetrievalPlanningError):
                p.FeedRetrievalPlannerV1()(planning())
            with self.assertRaises(p.FeedRetrievalPlanningError):
                p.validate_retrieval_plan_v1(p.FeedRetrievalPlanV1(
                    schema_version=p.SCHEMA_VERSION, policy_version=p.POLICY_VERSION, tasks=[]), planning())


class ValidatorCompletenessTests(unittest.TestCase):
    def setUp(self):
        self.source = planning([patient(P1, interests=["music therapy"]), patient(P2, interests=["music therapy"])])
        self.plan = p.FeedRetrievalPlannerV1()(self.source)

    def reject(self, value):
        with self.assertRaises(p.FeedRetrievalPlanningError) as caught:
            p.validate_retrieval_plan_v1(value, self.source)
        self.assertEqual(str(caught.exception), "Feed retrieval planning failed")
        self.assertTrue(caught.exception.__suppress_context__)

    def test_valid_plan_returns_fresh_revalidated_dto(self):
        validated = p.validate_retrieval_plan_v1(self.plan, self.source)
        self.assertEqual(validated, self.plan)
        self.assertIsNot(validated, self.plan)
        self.assertIsNot(validated.tasks[0], self.plan.tasks[0])

    def test_one_missing_valid_supporting_ref_is_not_partial_success(self):
        self.plan.tasks[0].signal_refs.pop()
        # It remains structurally valid; only full semantic completeness rejects.
        p.FeedRetrievalPlanV1.model_validate(self.plan.model_dump())
        self.reject(self.plan)

    def test_missing_task_or_false_empty_plan_is_rejected(self):
        self.plan.tasks.clear()
        self.reject(self.plan)

    def test_existing_patient_with_unsupported_signal_and_unknown_patient_ref_are_rejected(self):
        for ref in ("patient_999", "patient_1"):
            source = self.source if ref == "patient_999" else planning([patient(interests=["nutrition"])])
            wrong = self.plan.model_copy(deep=True)
            wrong.tasks[0].signal_refs = [wrong.tasks[0].signal_refs[0].model_copy(update={"patient_ref": ref})]
            with self.assertRaises(p.FeedRetrievalPlanningError):
                p.validate_retrieval_plan_v1(wrong, source)

    def test_query_top_k_ids_keys_and_versions_cannot_be_tampered(self):
        for field, value in (("query", "PRIVATE_PATIENT_NAME"), ("query", " padded "),
                             ("query", "x" * 2001), ("query", " "), ("top_k", 4),
                             ("top_k", 5.0), ("top_k", "5"), ("task_id", "task_2"),
                             ("task_id", " "), ("topic_key", " MUSIC_THERAPY ")):
            wrong = self.plan.model_copy(deep=True)
            setattr(wrong.tasks[0], field, value)
            self.reject(wrong)
        for field in ("schema_version", "policy_version"):
            wrong = self.plan.model_copy(deep=True)
            setattr(wrong, field, "unsupported-version")
            self.reject(wrong)

    def test_duplicate_and_reordered_refs_are_rejected(self):
        for refs_change in (lambda values: values + [values[0]], lambda values: list(reversed(values))):
            wrong = self.plan.model_copy(deep=True)
            wrong.tasks[0].signal_refs = refs_change(wrong.tasks[0].signal_refs)
            self.reject(wrong)

    def test_task_order_changes_fail_even_with_correct_renumbering(self):
        source = planning([patient(interests=["music therapy", "nutrition", "exercise", "sleep management"])])
        for renumber in (False, True):
            wrong = p.FeedRetrievalPlannerV1()(source)
            wrong.tasks.reverse()
            if renumber:
                for i, task in enumerate(wrong.tasks, start=1):
                    task.task_id = f"task_{i}"
            with self.assertRaises(p.FeedRetrievalPlanningError):
                p.validate_retrieval_plan_v1(wrong, source)

    def test_real_input_candidate_not_selected_by_v1_is_rejected(self):
        source = planning([patient(interests=["music therapy", "nutrition", "exercise", "sleep management"])])
        wrong = p.FeedRetrievalPlannerV1()(source)
        wrong.tasks[-1] = p.FeedRetrievalTaskV1(
            task_id="task_3", topic_key="physical_activity", top_k=5,
            query="Physical activity research for people with dementia",
            signal_refs=[p.FeedSignalRefV1(scope="patient", patient_ref="patient_1", field="interests",
                                         signal_key="physical_activity")],
        )
        with self.assertRaises(p.FeedRetrievalPlanningError):
            p.validate_retrieval_plan_v1(wrong, source)

    def test_constructed_nested_malformed_ref_and_task_are_revalidated(self):
        bad_ref = p.FeedSignalRefV1.model_construct(scope="caregiver", patient_ref="patient_1",
                                                   field="symptoms", signal_key="PRIVATE_PATIENT_NAME")
        bad_task = p.FeedRetrievalTaskV1.model_construct(task_id="task_1", topic_key="music_therapy",
                                                       query=self.plan.tasks[0].query, top_k=5, signal_refs=[bad_ref])
        wrong = p.FeedRetrievalPlanV1.model_construct(schema_version=p.SCHEMA_VERSION,
                                                     policy_version=p.POLICY_VERSION, tasks=[bad_task])
        self.reject(wrong)
        wrong.tasks[0].signal_refs = [self.plan.tasks[0].signal_refs[0]]
        wrong.tasks[0].top_k = True
        self.reject(wrong)

    def test_scope_field_null_blank_and_noncanonical_signal_schema_constraints(self):
        valid = self.plan.tasks[0].signal_refs[0].model_dump()
        for change in ({"scope": "caregiver"}, {"field": "lifestyle_tags"}, {"patient_ref": None},
                       {"patient_ref": " "}, {"signal_key": " "}, {"signal_key": "MUSIC_ACTIVITY"},
                       {"field": "current_medications"}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                p.FeedSignalRefV1.model_validate({**valid, **change})

    def test_duplicate_tasks_limit_extras_and_incorrect_topic_query_relationship(self):
        payload = self.plan.model_dump()
        task = payload["tasks"][0]
        for tasks in ([task, {**task, "task_id": "task_2"}], [task] * 4):
            with self.assertRaises(ValidationError):
                p.FeedRetrievalPlanV1.model_validate({**payload, "tasks": tasks})
        with self.assertRaises(ValidationError):
            p.FeedRetrievalPlanV1.model_validate({**payload, "rationale": "private narrative"})
        with self.assertRaises(ValidationError):
            p.FeedRetrievalTaskV1.model_validate({**task, "query": "Sleep management research for people with dementia"})
        with self.assertRaises(ValidationError):
            p.FeedRetrievalTaskV1.model_validate({**task, "signal_refs": []})
        with self.assertRaises(ValidationError):
            p.FeedRetrievalTaskV1.model_validate({**task, "signal_refs": [
                p.FeedSignalRefV1(scope="patient", patient_ref="patient_1", field="symptoms", signal_key="sleep_problem")
            ]})

    def test_public_validator_rejects_raw_payload_and_preserves_sensitive_error_boundary(self):
        self.reject(self.plan.model_dump())
        self.plan.tasks[0].query = "PRIVATE_PATIENT_NAME " + str(USER)
        with self.assertRaises(p.FeedRetrievalPlanningError) as caught:
            p.validate_retrieval_plan_v1(self.plan, self.source)
        public = "".join(traceback.format_exception(caught.exception))
        self.assertNotIn("PRIVATE_PATIENT_NAME", public)
        self.assertNotIn(str(USER), public)


if __name__ == "__main__":
    unittest.main()
