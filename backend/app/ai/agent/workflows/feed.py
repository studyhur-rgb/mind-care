"""Caregiver/user-scoped 연구 뉴스 Feed 골격. 미확정 계약/연결은 dependency로 남긴다."""
from typing import Callable, Literal, Protocol

from ..agent_loop_runner import AgentLoopRunner
from ..registry import ToolName
from ..schemas import AgentContext


FeedStage = Literal[
    "context_load", "pre_guardrail", "retrieval_planning", "agent_loop",
    "output_validation", "post_guardrail", "source_resolution", "persistence",
]


class FeedContextLoader(Protocol):
    def __call__(self, context: AgentContext, *, recent_days: int) -> object:
        """권한 검증된 caregiver/user-scoped 개인화 Context를 제공한다.

        간병인 맥락, 관리 환자들의 프로필, 최근 돌봄 맥락, optional user-scoped
        장기 개인화 요약을 위한 opaque 경계이며 실제 반환 계약은 후속이다.
        """
        ...


# object는 opaque 내부 값이다. Backend DTO / V2 / 최종 API 계약이 아니다.
FeedAgentStage = Callable[[AgentLoopRunner, AgentContext, object, object], object]


class FeedWorkflowError(RuntimeError):
    """실패한 단계만 전달한다. 원본 환자 데이터/exception text는 노출하지 않는다."""

    def __init__(self, stage: FeedStage):
        self.stage = stage
        super().__init__(f"Feed workflow stopped at {stage}")


def run_feed_agent_loop(runner: AgentLoopRunner, context: AgentContext,
                        personalization: object, plan: object) -> object:
    """후속 FeedAnswerV2 structured 실행 연결 지점. 현재 V1을 V2로 대체하지 않는다."""
    raise NotImplementedError("FeedAnswerV2 execution is not connected")


def _validate_feed_runner(runner: AgentLoopRunner) -> None:
    names = [definition["function"]["name"] for definition in runner.registry.definitions()]
    if names != [ToolName.SEARCH_EVIDENCE.value]:
        raise ValueError("Feed runner must expose search_evidence only")
    if runner.max_tool_rounds != 3 or runner.max_total_tool_calls != 3:
        raise ValueError("Feed runner requires round and total call limits of 3")


class FeedWorkflow:
    """간병인 맞춤 Feed의 단계 순서/실패 중단을 소유한다. Runner/Tool Loop/SQL은 소유하지 않는다.

    Feed ownership은 caregiver/user이며 managed patient 데이터는 돌봄 배경 Context다.
    모든 dependency는 실패/거부 시 예외를 발생시켜야 한다. opaque 반환값에 대해
    null/empty/item filtering 등 미확정 production 정책을 이 골격이 추측하지 않는다.
    """

    def __init__(self, *, agent_loop_runner: AgentLoopRunner,
                 context_loader: FeedContextLoader,
                 pre_guardrail: Callable[[AgentContext, object], None],
                 retrieval_planner: Callable[[AgentContext, object], object],
                 output_validator: Callable[[object], object],
                 post_guardrail: Callable[[object, object], object],
                 source_resolver: Callable[[AgentContext, object, object], object],
                 feed_repository: Callable[[AgentContext, object], object],
                 agent_stage: FeedAgentStage = run_feed_agent_loop):
        _validate_feed_runner(agent_loop_runner)
        self.agent_loop_runner = agent_loop_runner
        self.context_loader = context_loader
        self.pre_guardrail = pre_guardrail
        self.retrieval_planner = retrieval_planner
        self.output_validator = output_validator
        self.post_guardrail = post_guardrail
        self.source_resolver = source_resolver
        self.feed_repository = feed_repository
        self.agent_stage = agent_stage

    @staticmethod
    def _execute(stage: FeedStage, operation: Callable[[], object]) -> object:
        try:
            return operation()
        except Exception:
            raise FeedWorkflowError(stage) from None

    def run(self, context: AgentContext) -> object:
        """Persistence 성공 이후 그 dependency의 receipt만 반환한다 (API 계약 미확정)."""
        personalization = self._execute("context_load", lambda:
            self.context_loader(context, recent_days=30))
        self._execute("pre_guardrail", lambda: self.pre_guardrail(context, personalization))
        plan = self._execute("retrieval_planning", lambda:
            self.retrieval_planner(context, personalization))
        execution = self._execute("agent_loop", lambda:
            self._run_agent(context, personalization, plan))
        validated = self._execute("output_validation", lambda: self.output_validator(execution))
        # execution은 향후 선택 Evidence 내부 projection도 전달할 수 있는 opaque 경계다.
        approved = self._execute("post_guardrail", lambda: self.post_guardrail(validated, execution))
        resolved = self._execute("source_resolution", lambda:
            self.source_resolver(context, approved, execution))
        return self._execute("persistence", lambda: self.feed_repository(context, resolved))

    def _run_agent(self, context: AgentContext, personalization: object, plan: object) -> object:
        # 외부 조립 후 mutable Runner의 configuration이 바뀐 경우에도 실행하지 않는다.
        _validate_feed_runner(self.agent_loop_runner)
        return self.agent_stage(self.agent_loop_runner, context, personalization, plan)
