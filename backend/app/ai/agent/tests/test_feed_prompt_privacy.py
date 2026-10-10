"""Capture every offline provider turn and search admission; no PII detector claim."""
import json
import unittest
from uuid import UUID

from ..agent_loop_runner import AgentLoopRunner
from ..outputs import FeedAnswerV1
from ..prompts import DefaultPromptBuilder
from ..schemas import AgentContext, ModelTurn, ToolCall
from ..testing.fake_llm import FakeLLMClient
from ..testing.feed_workflow_fakes import FakeFeedDependencies, build_feed_test_registry
from ..tools.contracts import EvidenceItem, EvidencePackage, EvidenceSearchInput
from ..workflows.feed import FeedWorkflowError
from ..workflows.feed_prompts import FeedPromptBuilder, FeedResearchQueryPolicy
from .test_feed_profile_planning import NOW, P2, backend_caregiver, backend_patient, backend_profile
from ..workflows.feed_profile_planning import FeedUserProfileSourceMappingV1
from app.user_schemas import APP_TIMEZONE


CONTEXT = AgentContext(request_id="PRIVATE_REQUEST_ID", user_id=UUID(int=10), patient_id=UUID(int=99))


class FeedPromptPrivacyTests(unittest.TestCase):
    def test_provider_messages_across_tool_rounds_exclude_server_identity(self):
        payload = FeedUserProfileSourceMappingV1(service_timezone=APP_TIMEZONE)(
            CONTEXT, backend_profile([backend_patient(), backend_patient(P2)],
                                     caregiver=backend_caregiver()), reference_time=NOW)
        for structured in (False, True):
            with self.subTest(structured=structured):
                queries = []
                def search(context, args):
                    self.assertIs(context, CONTEXT)  # identity still reaches server handler
                    queries.append(args.query)
                    return EvidencePackage(query=args.query, evidence=[EvidenceItem(
                        evidence_type="new_research", pmid="12345", title="Synthetic public research",
                        relevance_score=0.8, abstract="Synthetic public evidence",
                    )])
                client = FakeLLMClient([
                    ModelTurn(tool_calls=[ToolCall(id="search_1", name="search_evidence", arguments={"query": "dementia sleep research"})]),
                    ModelTurn(text='{"schema_version": "1", "response_type": "feed", "items": []}'
                              if structured else "synthetic final"),
                ])
                runner = AgentLoopRunner(client, build_feed_test_registry(
                    query_validator=FeedResearchQueryPolicy(frozenset({"dementia sleep research"})), handler=search),
                    prompt_builder=FeedPromptBuilder(), max_tool_rounds=3, max_total_tool_calls=3)
                result = (runner.run_structured(payload.model_dump_json(), CONTEXT, output_model=FeedAnswerV1)
                          if structured else runner.run(payload.model_dump_json(), CONTEXT))
                self.assertEqual(result.execution.status if structured else result.status, "completed")
                self.assertEqual(len(client.requests), 2)
                followup = client.requests[1][0]
                self.assertTrue(any(message.kind == "tool_result" for message in followup))
                self.assertEqual(any(message.kind == "evidence" for message in followup), not structured)
                self.assertIn("Synthetic public research", json.dumps([
                    message.model_dump(mode="json") for message in followup]))
                for messages, tools in client.requests:
                    serialized = json.dumps([message.model_dump(mode="json") for message in messages])
                    for value in (CONTEXT.request_id, str(CONTEXT.user_id), str(CONTEXT.patient_id),
                                  str(UUID(int=1)), str(P2), *(str(UUID(int=i)) for i in range(101, 106)),
                                  "request_id", "user_id", "patient_id", "caregiver_id", "PRIVATE_CAREGIVER_NAME",
                                  "PRIVATE_PATIENT_NAME", "PRIVATE_005_DETAIL", "PRIVATE_ASSESSOR",
                                  "PRIVATE_HOSPITAL", "PRIVATE_DOSAGE", "PRIVATE_FREQUENCY"):
                        self.assertNotIn(value, serialized)
                    self.assertFalse(any(message.kind == "agent_context" for message in messages))
                self.assertEqual(queries, ["dementia sleep research"])
                for narrative in ("stored relationship", "stored caregiver tag", "stored symptom",
                                  "stored interest", "stored assessment", "legacy whole visit or free memo"):
                    self.assertNotIn(narrative, queries[0])

    def test_workflow_rejects_default_or_mutated_prompt_before_provider(self):
        deps = FakeFeedDependencies(CONTEXT)
        deps.runner.prompt_builder = DefaultPromptBuilder()
        with self.assertRaises(ValueError):
            deps.workflow()
        self.assertEqual(deps.events, [])
        deps.runner.prompt_builder = FeedPromptBuilder()
        workflow = deps.workflow()
        deps.runner.prompt_builder = DefaultPromptBuilder()
        with self.assertRaises(FeedWorkflowError) as caught:
            workflow.run(CONTEXT)
        self.assertEqual(caught.exception.stage, "agent_loop")
        self.assertEqual(deps.client.requests, [])

    def test_chat_default_context_serialization_is_unchanged(self):
        messages = DefaultPromptBuilder().build_initial_messages("legacy input", CONTEXT)
        self.assertEqual(json.loads(messages[1].content)["trusted_agent_context"], CONTEXT.model_dump(mode="json"))

    def test_query_policy_rejects_raw_identity_ref_or_narrative_before_search(self):
        seen = []
        policy = FeedResearchQueryPolicy(frozenset({"dementia sleep research"}))
        registry = build_feed_test_registry(query_validator=policy,
            handler=lambda context, args: seen.append(args.query))
        handler = registry.get("search_evidence").handler
        for query in (str(CONTEXT.user_id), str(CONTEXT.patient_id), CONTEXT.request_id,
                      "patient_1", "PRIVATE_PATIENT_NAME", "stored symptom", "legacy whole visit or free memo",
                      "dementia sleep research " + str(CONTEXT.user_id)):
            with self.subTest(query=query), self.assertRaises(ValueError) as caught:
                handler(CONTEXT, EvidenceSearchInput(query=query))
            self.assertEqual(str(caught.exception), "Feed research query is not approved")
        self.assertEqual(seen, [])
        handler(CONTEXT, EvidenceSearchInput(query="dementia sleep research"))
        self.assertEqual(seen, ["dementia sleep research"])

    def test_query_configuration_has_no_mutable_or_empty_fallback(self):
        for values in (set({"research"}), frozenset(), frozenset({""}), frozenset({123})):
            with self.assertRaises(ValueError):
                FeedResearchQueryPolicy(values)
