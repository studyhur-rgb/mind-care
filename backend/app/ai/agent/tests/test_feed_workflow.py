"""Feed skeleton의 순서/실패 경계와 공용 Runner 조립을 오프라인으로 검증한다."""
import ast
import inspect
import unittest
from unittest.mock import patch
from uuid import UUID

from .. import agent_loop_runner
from ..agent_loop_runner import AgentLoopRunner
from ..registry import ToolName, production_registry
from ..schemas import AgentContext, ModelTurn, ToolCall
from ..testing.fake_tools import fake_registry
from ..testing.feed_workflow_fakes import FakeFeedDependencies, STAGES, build_feed_test_registry
from ..tools.contracts import EvidencePackage, EvidenceSearchInput
from ..workflows import feed
from ..workflows.feed import FeedWorkflow, FeedWorkflowError


CONTEXT = AgentContext(request_id="fake-feed-workflow", user_id=UUID(int=1), patient_id=UUID(int=2))


def search_call(identifier="search_1", query="synthetic care"):
    return ToolCall(id=identifier, name="search_evidence", arguments={"query": query})


class FeedWorkflowTests(unittest.TestCase):
    def test_stage_order_and_opaque_values_are_forwarded(self):
        deps = FakeFeedDependencies(CONTEXT)
        workflow = deps.workflow()
        self.assertIs(workflow.agent_loop_runner, deps.runner)
        with patch.object(deps.runner, "run", wraps=deps.runner.run) as run:
            self.assertIs(workflow.run(CONTEXT), deps.receipt)
        run.assert_called_once()
        self.assertEqual(deps.events, list(STAGES))
        self.assertEqual(deps.received["context_load"], (CONTEXT, 30))
        self.assertIs(deps.received["retrieval_planning"][1], deps.personalization)
        self.assertIs(deps.received["agent_loop"][0], deps.runner)
        self.assertIs(deps.received["agent_loop"][1], CONTEXT)
        self.assertIs(deps.received["agent_loop"][2], deps.personalization)
        self.assertIs(deps.received["agent_loop"][3], deps.plan)
        self.assertIs(deps.received["output_validation"][0], deps.agent_result)
        self.assertIs(deps.received["post_guardrail"][1], deps.agent_result)
        self.assertIs(deps.received["source_resolution"][2], deps.agent_result)
        self.assertIs(deps.received["persistence"][0], CONTEXT)
        self.assertEqual(deps.saved, [deps.agent_result["items"]])

    def test_each_stage_failure_stops_later_dependencies(self):
        for position, stage in enumerate(STAGES):
            with self.subTest(stage=stage):
                deps = FakeFeedDependencies(CONTEXT, fail_at=stage)
                with self.assertRaises(FeedWorkflowError) as caught:
                    deps.workflow().run(CONTEXT)
                self.assertEqual(caught.exception.stage, stage)
                self.assertNotIn("PRIVATE", str(caught.exception))
                self.assertTrue(caught.exception.__suppress_context__)
                self.assertEqual(deps.events, list(STAGES[:position + 1]))
                self.assertEqual(deps.saved, [])
                if position < STAGES.index("agent_loop"):
                    self.assertEqual(deps.client.requests, [])

    def test_cold_start_is_successful(self):
        deps = FakeFeedDependencies(CONTEXT, cold_start=True)
        self.assertIsNone(deps.personalization["memory"])
        self.assertIs(deps.workflow().run(CONTEXT), deps.receipt)

    def test_zero_care_logs_is_successful(self):
        deps = FakeFeedDependencies(CONTEXT, empty_logs=True)
        self.assertEqual(deps.personalization["recent"].total_count, 0)
        self.assertEqual(deps.personalization["recent"].logs, [])
        self.assertIs(deps.workflow().run(CONTEXT), deps.receipt)

    def test_sparse_profile_is_successful(self):
        deps = FakeFeedDependencies(CONTEXT)
        self.assertIsNone(deps.personalization["profile"].name)
        self.assertIs(deps.workflow().run(CONTEXT), deps.receipt)

    def test_missing_required_profile_stops_before_llm(self):
        deps = FakeFeedDependencies(CONTEXT)
        deps.personalization["profile"] = None
        with self.assertRaises(FeedWorkflowError) as caught:
            deps.workflow().run(CONTEXT)
        self.assertEqual(caught.exception.stage, "pre_guardrail")
        self.assertEqual(deps.events, list(STAGES[:2]))
        self.assertEqual(deps.client.requests, [])

    def test_wrong_recent_window_stops_before_llm(self):
        deps = FakeFeedDependencies(CONTEXT)
        recent = deps.personalization["recent"]
        from datetime import timedelta
        recent.period.start_at += timedelta(days=1)
        with self.assertRaises(FeedWorkflowError) as caught:
            deps.workflow().run(CONTEXT)
        self.assertEqual(caught.exception.stage, "pre_guardrail")
        self.assertEqual(deps.client.requests, [])

    def test_default_agent_seam_is_explicitly_unconnected(self):
        deps = FakeFeedDependencies(CONTEXT)
        with self.assertRaises(FeedWorkflowError) as caught:
            deps.workflow(connect_fake_agent=False).run(CONTEXT)
        self.assertEqual(caught.exception.stage, "agent_loop")
        self.assertEqual(deps.client.requests, [])
        self.assertEqual(deps.saved, [])
        with self.assertRaises(NotImplementedError):
            feed.run_feed_agent_loop(deps.runner, CONTEXT, deps.personalization, deps.plan)

    def test_provider_failure_stops_after_agent_stage(self):
        deps = FakeFeedDependencies(CONTEXT, turns=[RuntimeError("PRIVATE provider")])
        with self.assertRaises(FeedWorkflowError) as caught:
            deps.workflow().run(CONTEXT)
        self.assertEqual(caught.exception.stage, "agent_loop")
        self.assertEqual(deps.events, list(STAGES[:4]))

    def test_search_failure_stops_before_output_and_persistence(self):
        deps = FakeFeedDependencies(CONTEXT, turns=[
            ModelTurn(tool_calls=[search_call(query="[FAKE BLOCKED]")]), ModelTurn(text="[FAKE] done")])
        with self.assertRaises(FeedWorkflowError) as caught:
            deps.workflow().run(CONTEXT)
        self.assertEqual(caught.exception.stage, "agent_loop")
        self.assertEqual(deps.search_queries, [])
        self.assertEqual(deps.saved, [])
        self.assertEqual(deps.events, list(STAGES[:4]))

    def test_empty_evidence_is_not_a_tool_failure(self):
        deps = FakeFeedDependencies(CONTEXT, turns=[ModelTurn(tool_calls=[search_call()]),
                                                    ModelTurn(text="[FAKE] done")])
        deps.runner = AgentLoopRunner(deps.client, build_feed_test_registry(
            query_validator=deps.validate_query,
            handler=lambda context, args: EvidencePackage(query=args.query, evidence=[])),
            max_tool_rounds=3, max_total_tool_calls=3)
        self.assertIs(deps.workflow().run(CONTEXT), deps.receipt)
        self.assertEqual(deps.search_queries, ["synthetic care"])
        self.assertEqual(deps.agent_result["execution"].errors, [])

    def test_three_searches_then_fourth_is_blocked(self):
        turns = [ModelTurn(tool_calls=[search_call(f"search_{i}")]) for i in range(4)]
        deps = FakeFeedDependencies(CONTEXT, turns=turns)
        with self.assertRaises(FeedWorkflowError) as caught:
            deps.workflow().run(CONTEXT)
        self.assertEqual(caught.exception.stage, "agent_loop")
        self.assertEqual(len(deps.search_queries), 3)
        self.assertEqual(deps.saved, [])

    def test_four_calls_in_one_turn_are_blocked_before_execution(self):
        deps = FakeFeedDependencies(CONTEXT, turns=[ModelTurn(
            tool_calls=[search_call(f"search_{i}") for i in range(4)])])
        with self.assertRaises(FeedWorkflowError):
            deps.workflow().run(CONTEXT)
        self.assertEqual(deps.search_queries, [])

    def test_three_calls_in_one_turn_use_three_searches(self):
        deps = FakeFeedDependencies(CONTEXT, turns=[ModelTurn(
            tool_calls=[search_call(f"search_{i}") for i in range(3)]), ModelTurn(text="[FAKE] done")])
        self.assertIs(deps.workflow().run(CONTEXT), deps.receipt)
        self.assertEqual(len(deps.search_queries), 3)
        self.assertEqual(deps.agent_result["execution"].tool_rounds, 1)
        self.assertEqual(deps.agent_result["execution"].total_tool_calls, 3)

    def test_profile_tool_is_not_exposed_or_executed(self):
        deps = FakeFeedDependencies(CONTEXT, turns=[ModelTurn(tool_calls=[
            ToolCall(id="profile", name="get_patient_profile", arguments={})]), ModelTurn(text="[FAKE] done")])
        with self.assertRaises(FeedWorkflowError):
            deps.workflow().run(CONTEXT)
        self.assertIsNone(deps.runner.registry.get("get_patient_profile"))
        self.assertEqual(deps.search_queries, [])

    def test_fake_partial_item_exclusion_reaches_persistence(self):
        deps = FakeFeedDependencies(CONTEXT, drop_items=("[FAKE] item A",))
        self.assertIs(deps.workflow().run(CONTEXT), deps.receipt)
        self.assertEqual(deps.saved, [("[FAKE] item B",)])

    def test_fake_all_items_excluded_does_not_persist(self):
        deps = FakeFeedDependencies(CONTEXT, drop_items=("[FAKE] item A", "[FAKE] item B"))
        with self.assertRaises(FeedWorkflowError) as caught:
            deps.workflow().run(CONTEXT)
        self.assertEqual(caught.exception.stage, "post_guardrail")
        self.assertEqual(deps.events, list(STAGES[:6]))
        self.assertEqual(deps.saved, [])

    def test_invalid_runner_configuration_is_rejected_before_context_load(self):
        for registry, rounds, calls in [(fake_registry(), 3, 3), (production_registry(), 3, 3),
                                        (None, 4, 3), (None, 3, 4)]:
            with self.subTest(rounds=rounds, calls=calls, registry=registry):
                deps = FakeFeedDependencies(CONTEXT)
                deps.runner = AgentLoopRunner(deps.client, registry or deps.runner.registry,
                                              max_tool_rounds=rounds, max_total_tool_calls=calls)
                with self.assertRaises(ValueError):
                    deps.workflow()
                self.assertEqual(deps.events, [])

    def test_mutated_runner_limits_are_rechecked_before_execution(self):
        deps = FakeFeedDependencies(CONTEXT)
        workflow = deps.workflow()
        deps.runner.max_total_tool_calls = 4
        with self.assertRaises(FeedWorkflowError) as caught:
            workflow.run(CONTEXT)
        self.assertEqual(caught.exception.stage, "agent_loop")
        self.assertEqual(deps.client.requests, [])


class FeedArchitectureTests(unittest.TestCase):
    def test_workflow_has_no_engine_inheritance_creation_or_tool_loop(self):
        self.assertFalse(issubclass(FeedWorkflow, AgentLoopRunner))
        tree = ast.parse(inspect.getsource(feed))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                self.assertNotEqual(getattr(node.func, "id", None), "AgentLoopRunner")
                self.assertNotIn(getattr(node.func, "attr", None), ("generate", "run_structured", "execute"))
            self.assertNotIsInstance(node, (ast.For, ast.While))

    def test_shared_engine_has_no_workflow_specific_branch(self):
        tree = ast.parse(inspect.getsource(agent_loop_runner))
        self.assertEqual([node.name for node in tree.body if isinstance(node, ast.ClassDef)],
                         ["AgentLoopRunner"])
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                self.assertNotEqual(node.id, "workflow_type")
            if isinstance(node, ast.Constant):
                self.assertNotEqual(node.value, "feed")

    def test_runtime_and_global_registry_boundaries(self):
        deps = FakeFeedDependencies(CONTEXT)
        self.assertEqual([d["function"]["name"] for d in deps.runner.registry.definitions()],
                         ["search_evidence"])
        self.assertEqual(deps.runner.registry.mode, "test")
        self.assertTrue(deps.runner.registry.get("search_evidence").test_only)
        self.assertEqual({name.value for name in ToolName},
                         {"get_patient_profile", "get_recent_care_logs", "search_evidence"})
        self.assertEqual(production_registry().definitions(), [])

    def test_query_validation_precedes_each_search_and_rejection_skips_handler(self):
        events = []

        def validate(context, args):
            events.append("validate")
            if args.query == "[FAKE BLOCKED]":
                raise ValueError("rejected fixture")

        def search(context, args):
            events.append("search")
            return EvidencePackage(query=args.query, evidence=[])

        registry = build_feed_test_registry(query_validator=validate, handler=search)
        spec = registry.get("search_evidence")
        spec.handler(CONTEXT, EvidenceSearchInput(query="synthetic care"))
        self.assertEqual(events, ["validate", "search"])
        with self.assertRaises(ValueError):
            spec.handler(CONTEXT, EvidenceSearchInput(query="[FAKE BLOCKED]"))
        self.assertEqual(events, ["validate", "search", "validate"])
