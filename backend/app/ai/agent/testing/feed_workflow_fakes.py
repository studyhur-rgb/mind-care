"""Test-only Feed 골격 fixture. DB/API/실제 RAG/의료 판단/Feed V2 생성 없음."""
from typing import Callable

from ..agent_loop_runner import AgentLoopRunner
from ..registry import TOOL_CONTRACTS, ToolName, ToolRegistry, ToolSpec
from ..schemas import AgentContext, ModelTurn
from ..tools import contracts as c
from ..workflows.feed import FeedWorkflow
from .fake_llm import FakeLLMClient
from .fake_tools import fake_get_recent_care_logs, fake_search_evidence


STAGES = (
    "context_load", "pre_guardrail", "retrieval_planning", "agent_loop",
    "output_validation", "post_guardrail", "source_resolution", "persistence",
)


def build_feed_test_registry(*, query_validator: Callable[[AgentContext, c.EvidenceSearchInput], None],
                             handler=fake_search_evidence) -> ToolRegistry:
    """매 검색 실행 직전 validation seam. 실제 PII detector는 구현하지 않는다."""
    def guarded_search(context, args):
        query_validator(context, args)
        return handler(context, args)

    return ToolRegistry((ToolSpec(TOOL_CONTRACTS[ToolName.SEARCH_EVIDENCE],
                                 guarded_search, test_only=True),), mode="test")


class FakeFeedDependencies:
    """간병인 맞춤 Feed의 합성 fixture. Opaque dict/tuple은 Backend/V2 DTO가 아니다."""

    def __init__(self, context: AgentContext, *, cold_start=False, empty_logs=False,
                 fail_at=None, turns=None, drop_items=()):
        self.events = []
        self.received = {}
        self.fail_at = fail_at
        self.drop_items = drop_items
        self.search_queries = []
        self.saved = []
        self.saved_owners = []  # Test-only caregiver ownership 기록. 실제 저장/권한 계약이 아니다.
        self.receipt = object()
        # 기존 patient_care Fake helper는 최근 돌봄 맥락의 합성 입력으로만 재사용한다.
        # 여러 managed patient의 실제 조회/aggregation 방식은 확정하지 않는다.
        recent = fake_get_recent_care_logs(context, c.RecentCareLogsInput(days=30, limit=30))
        if empty_logs:
            recent = c.RecentCareLogsOutput(period=recent.period, logs=[], total_count=0)
        self.personalization = {
            "caregiver_profile": "[FAKE] 간병인의 돌봄 관심 맥락",
            # 복수 환자의 저장된 배경 데이터이며 Feed의 소유 대상/환자별 partition이 아니다.
            "managed_patient_profiles": (c.PatientProfileOutput(), c.PatientProfileOutput()),
            "recent_care_context": recent,
            "long_term_summary": None if cold_start else "[FAKE] 간병인의 장기 돌봄 관심 요약",
        }
        self.plan = {"queries": ("synthetic care",)}
        self.agent_result = None
        client_turns = turns if turns is not None else [ModelTurn(text="[FAKE] engine smoke only")]
        self.client = FakeLLMClient(client_turns)
        self.runner = AgentLoopRunner(self.client,
            build_feed_test_registry(query_validator=self.validate_query),
            max_tool_rounds=3, max_total_tool_calls=3)

    def _enter(self, stage, *received):
        self.events.append(stage)
        self.received[stage] = received
        if self.fail_at == stage:
            raise RuntimeError("PRIVATE synthetic failure detail")

    def validate_query(self, context, args):
        # 이 marker는 test hook 거부를 검증할 뿐, 개인정보 탐지 보장이 아니다.
        if args.query == "[FAKE BLOCKED]":
            raise ValueError("PRIVATE rejected fixture query")
        self.search_queries.append(args.query)

    def load_context(self, context, *, recent_days):
        self._enter("context_load", context, recent_days)
        return self.personalization

    def pre_guardrail(self, context, personalization):
        self._enter("pre_guardrail", context, personalization)
        if not isinstance(personalization["caregiver_profile"], str):
            raise ValueError("Missing required synthetic caregiver context")
        recent = personalization["recent_care_context"]
        if (recent.period.end_at - recent.period.start_at).total_seconds() != 30 * 24 * 3600:
            raise ValueError("Wrong fixture lookback window")

    def plan_retrieval(self, context, personalization):
        # 간병인에게 유용한 연구 주제 planning의 호출 경계만 검증한다. 환자 상태 병합/추론 없음.
        self._enter("retrieval_planning", context, personalization)
        return self.plan

    def run_agent(self, runner, context, personalization, plan):
        self._enter("agent_loop", runner, context, personalization, plan)
        # 공용 엔진의 실행/오류/상한을 검증한다. legacy text를 Feed 콘텐츠로 사용하지 않는다.
        execution = runner.run("[FAKE] engine smoke only; not Feed generation", context)
        # 검색 오류를 엄격히 중단하는 것은 이 Fake의 정책이다. 운영 정책 확정이 아니다.
        if execution.status != "completed" or execution.errors:
            raise RuntimeError("Synthetic agent stage failed")
        self.agent_result = {
            "execution": execution, "items": ("[FAKE] item A", "[FAKE] item B"),
            "evidence_context": object(),  # 선택 Evidence projection의 opaque 전달만 검증
        }
        return self.agent_result

    def validate_output(self, execution):
        self._enter("output_validation", execution)
        return execution["items"]

    def post_guardrail(self, validated, execution):
        self._enter("post_guardrail", validated, execution)
        approved = tuple(item for item in validated if item not in self.drop_items)
        if not approved:
            raise ValueError("All synthetic items excluded")
        return approved

    def resolve_sources(self, context, approved, execution):
        self._enter("source_resolution", context, approved, execution)
        # 실제 source metadata / paper identity 매핑을 만들어 내지 않는다.
        return approved

    def persist(self, context, resolved):
        self._enter("persistence", context, resolved)
        self.saved.append(resolved)
        self.saved_owners.append(context.user_id)
        return self.receipt

    def workflow(self, *, connect_fake_agent=True):
        # Runner는 Workflow 바깥에서 조립/주입된다. 이 wiring은 test-only다.
        kwargs = {"agent_stage": self.run_agent} if connect_fake_agent else {}
        return FeedWorkflow(agent_loop_runner=self.runner, context_loader=self.load_context,
            pre_guardrail=self.pre_guardrail, retrieval_planner=self.plan_retrieval,
            output_validator=self.validate_output, post_guardrail=self.post_guardrail,
            source_resolver=self.resolve_sources, feed_repository=self.persist, **kwargs)
