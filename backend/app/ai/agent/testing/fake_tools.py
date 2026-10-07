"""합성 고정 fixture만 사용하는 6개 Fake. 저장/의료 판단/DB/API 호출 없음."""
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, NAMESPACE_URL, uuid5

from ..registry import TOOL_CONTRACTS, ToolName, ToolRegistry, ToolSpec
from ..schemas import AgentContext
from ..tools import contracts as c

FAKE_LOG_ID = UUID("00000000-0000-0000-0000-000000000003")
FIXTURE_DATE = date(2026, 10, 6)


def fake_get_patient_profile(context: AgentContext, args: c.PatientProfileInput) -> c.PatientProfileOutput:
    return c.PatientProfileOutput(
        patient_id=context.patient_id, name="합성 테스트 환자", dementia_stage="경도",
        diagnosis_date=date(2025, 1, 1), symptoms=["수면 변화"], interests=["수면"],
        conditions=["합성 테스트 조건"] if args.include_conditions else None,
        care_environment=c.CareEnvironment(type="test_home") if args.include_care_environment else None,
    )


def fake_get_recent_care_logs(context: AgentContext, args: c.RecentCareLogsInput) -> c.RecentCareLogsOutput:
    start = FIXTURE_DATE - timedelta(days=args.days - 1)
    log = c.CareLog(log_id=FAKE_LOG_ID,
                    logged_at=datetime(2026, 10, 5, 2, 10, tzinfo=timezone(timedelta(hours=9))),
                    log_type="patient_care", content="[FAKE] 새벽에 여러 차례 잠에서 깸")
    logs = [log] if log.log_type in args.log_types and start <= log.logged_at.date() <= FIXTURE_DATE else []
    return c.RecentCareLogsOutput(period=c.DatePeriod(from_date=start, to_date=FIXTURE_DATE),
                                 logs=logs[:args.limit], total_count=len(logs))


def fake_get_patient_history(context: AgentContext, args: c.PatientHistoryInput) -> c.PatientHistoryOutput:
    # 이 fixture만 지원한다. 다른 지표/집계는 데이터 없음; 실제 분석처럼 꾸미지 않는다.
    data = []
    if args.metric == "night_awakening" and args.aggregation == "weekly":
        start = FIXTURE_DATE - timedelta(days=args.period_days - 1)
        data = [c.HistoryPoint(period_start=d, count=n) for d, n in
                [(date(2026, 9, 21), 3), (date(2026, 9, 28), 6)] if d >= start]
    latest = data[-1].count if data else 0
    previous = data[-2].count if len(data) >= 2 else 0
    return c.PatientHistoryOutput(metric=args.metric, aggregation=args.aggregation, data=data,
                                  summary=c.HistorySummary(latest_value=latest, previous_value=previous,
                                                           change=latest - previous))


def fake_search_evidence(context: AgentContext, args: c.EvidenceSearchInput) -> c.EvidencePackage:
    return c.EvidencePackage(query=args.query, evidence=[c.EvidenceItem(
        evidence_type="new_research", pmid="FAKE-PMID", title="[FAKE] Dementia sleep orchestration fixture",
        journal="Fake Journal", doi="FAKE-DOI", publication_year=2025, full_text_available=False,
        study_type="systematic_review", relevance_score=0.94,
        ai_summary="[FAKE] 수면 변화 관련 합성 근거", abstract="Fake abstract; not clinical evidence",
    )][:args.top_k])


def fake_save_ai_annotation(context: AgentContext, args: c.AIAnnotationInput) -> c.AIAnnotationOutput:
    if args.log_id != FAKE_LOG_ID:
        raise ValueError("Log is outside the synthetic fixture")
    # 같은 요청/입력은 항상 같은 합성 UUID. 실제 저장은 하지 않는다.
    identifier = uuid5(NAMESPACE_URL, str(context.patient_id) + ":" + args.model_dump_json())
    return c.AIAnnotationOutput(success=True, annotation_id=identifier)


def fake_check_safety_flags(context: AgentContext, args: c.SafetyFlagsInput) -> c.SafetyFlagsOutput:
    # 임상 기준과 무관한 sentinel만 검사한다.
    matched = ["FAKE_SENTINEL_RULE"] if any(
        item.type == "TEST_ONLY_SENTINEL" and item.present for item in args.observations
    ) else []
    return c.SafetyFlagsOutput(flagged=bool(matched), level="test_only" if matched else "none",
                               matched_rules=matched)


def fake_registry() -> ToolRegistry:
    handlers = {
        ToolName.PATIENT_PROFILE: fake_get_patient_profile,
        ToolName.RECENT_CARE_LOGS: fake_get_recent_care_logs,
        ToolName.PATIENT_HISTORY: fake_get_patient_history,
        ToolName.SEARCH_EVIDENCE: fake_search_evidence,
        ToolName.SAVE_AI_ANNOTATION: fake_save_ai_annotation,
        ToolName.SAFETY_FLAGS: fake_check_safety_flags,
    }
    return ToolRegistry(tuple(ToolSpec(TOOL_CONTRACTS[name], handler, test_only=True)
                              for name, handler in handlers.items()), mode="test")
