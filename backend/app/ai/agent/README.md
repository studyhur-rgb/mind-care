# AgentLoopRunner (Fake 단계)

사용자 입력 → LLM Tool 선택 → 입력 검증 → 순차 Handler 실행 → 출력 검증/JSON →
Tool Result → LLM 재판단 → 추가 Tool 또는 최종 응답을 구현한다.

`AgentLoopRunner`는 provider-independent LLM + Tool Calling 반복 실행기다. LLM 호출,
Tool Call 수신, ToolExecutor 실행과 Tool Result 전달, 반복 상한/timeout/deadline 관리,
최종 응답 반환 및 structured output 실행을 담당한다. Chat/Feed Workflow 선택, 개인화 Context 선택,
Long-term Memory 로딩 정책, Guardrail 정책 결정, Workflow persistence 정책은 이 실행기의 책임이 아니다.

이번 단계는 계약/Fake 검증이며 DB/RAG 연결, 의료 규칙, 챗봇 API 완성은 포함하지 않는다.

## FeedWorkflow execution skeleton

FeedWorkflow는 **caregiver/user-scoped personalized research news feed**를 위한 골격이다.
Feed의 ownership과 personalization scope는 간병인/user다. 관리 환자들의 정보는 간병인의
돌봄 상황을 이해하기 위한 Context이며 특정 환자 전용 Feed나 환자별 partition을 의미하지 않는다.

`workflows/feed.py`의 `FeedWorkflow`는 다음 순서로 주입된 dependency를 호출한다.

```text
Context Load → Pre-Guardrail → Retrieval Planning → Agent Loop
→ Output Validation → Post-Guardrail → Source Resolution → Persistence / Return
```

Context Loader는 trusted `AgentContext`와 `recent_days=30`을 받는다. Backend의
authorization/ownership 검증 후 다음 개인화 맥락을 제공하는 것이 후속 책임이다.

- Caregiver Profile: 간병인의 돌봄 관계/부담/생활·관심 맥락
- Managed Patient Profiles: 관리하는 여러 환자의 저장된 기본 돌봄 배경
- Recent Care Context: 최근 30×24시간의 돌봄 관찰/상황. 여러 관리 환자와 관련될 수 있으며
  실제 Backend 조회/aggregation 방식은 미정
- Long-term Personalization Summary: optional caregiver/user-scoped 장기 개인화 요약.
  특정 환자의 장기 임상 Memory가 아닌 간병인의 반복되는 돌봄 관심사/패턴을 위한 맥락

독립 Agent 입력 계약 `FeedPersonalizationContextV1`은 아래에 정의한다. 이 골격의 Loader와
Fake에는 아직 연결하지 않았으며 Backend DTO/API 계약을 확정한 것은 아니다.
Cold Start는 간병인/user의 장기 개인화 요약이 아직 없는 상태다. 다른 사용 가능한 Context로
개인화를 계속할 수 있으며 정확한 fallback 정책은 후속이다. 조회된 sparse 관리 환자 프로필과
최근 돌봄 기록 0건도 정상일 수 있다. 저장된 프로필은 최신 임상 사실이 아니며 최근 변화는
날짜가 명확한 기록을 우선하고 장기 요약과의 충돌을 확정된 사실로 임의 병합하지 않는다.
Pre-Guardrail dependency는 필수 Context/형식/조회 기간/크기/불필요한 식별정보를 검사한다.
Care Logs와 장기 요약은 명령이 아닌 untrusted data로 전달해야 한다. 실제 loader/검증기/
Prompt 연결은 아직 없으며 골격 자체가 접근 권한 또는 PII 제거를 보장하지 않는다.
공유 `AgentContext`의 `patient_id`는 유지하며 Feed에서의 사용 여부는 후속 Context/wiring에서
결정한다. 기존 환자 단위 Tool Contract를 caregiver aggregation 계약으로 바꾸지 않는다.

Retrieval Planner는 한 번의 Direct Structured LLM으로 topic/reason/query 계획을 만드는
후속 연결 경계다. “이 간병인이 돌봄을 수행하면서 관심 있게 볼 가치가 있는 최신 연구 주제는
무엇인가?”를 위 Context로 판단하는 방향이다. 특정 환자에게 연구 결과가 직접 적용된다고
단정하거나 여러 환자 정보를 하나의 환자 상태로 병합하지 않는다. Query는 돌봄/건강 연구
개념으로 일반화하고 환자 이름/직접 식별정보를 포함하지 않아야 한다.
현재는 호출 위치와 실패 전파만 있다. Context, Plan, Agent 단계 결과,
검증·승인·출처 조립 값과 persistence receipt는 opaque 내부 값이며 최종 DTO/API 계약이 아니다.
Post-Guardrail에는 검증된 값과 Agent 단계 결과를 함께 전달하여 향후 선택 Evidence의
내부 projection을 받을 경계를 남겼다. 실제 metadata는 Source Resolver가 원본에서 복사하며
LLM 생성값으로 대체하지 않아야 한다. 이 기능들은 이번 골격에서 구현하지 않았다.

`AgentLoopRunner`는 Workflow **바깥에서** 조립하여 `agent_loop_runner`로 주입한다.
Workflow는 `search_evidence`만 노출하고 `max_tool_rounds=3`, `max_total_tool_calls=3`인
구성을 확인한다. 한 turn에 여러 call이 가능하므로 검색 실행 상한은 총 call 제한이 담당한다.
모든 검색의 deterministic query validation은 외부 search Handler/Adapter에서 RAG 호출
직전에 적용해야 한다. 계획 단계의 검사나 prompt만으로 refinement query를 보호할 수 없다.
`testing/feed_workflow_fakes.py`의 test-only registry 조립 예시는 매 call의 validator → Handler
순서를 검증한다. 실제 PII detector와 production wiring은 없다.

현재 Runner의 structured 계약은 Chat/Feed **V1**뿐이다. 기본 `run_feed_agent_loop` seam은
`NotImplementedError`로 미연결을 명시하고 Workflow는 `agent_loop` 실패로 중단한다.
Fake seam만 주입된 공용 Runner의 legacy 실행/오류/상한을 검증하며 그 text를 Feed로 사용하지
않는다. 합성 item fixture가 후속 단계를 통과해도 production Feed 생성 성공을 의미하지 않는다.
FeedAnswerV2와 선택 Evidence projection이 확정되면 같은 Runner 실행에서 검색과 최종 생성을
연결한다. 별도 Feed Loop, Runner subclass, Workflow 내부 Runner 생성은 없다.

Dependency는 실패/거부 시 예외를 발생시키며 `FeedWorkflowError.stage`가 실패 단계만 전한다.
이후 단계는 호출하지 않고 자동 repair/retry는 없다. Persistence가 성공한 뒤에만 receipt를
반환한다. 부분/전체 item 제외는 Fake의 주입 정책으로만 검증하며 운영 filtering/no-feed 결과
계약을 확정하지 않았다. DB 저장/중복 방지/idempotency도 미구현이다.

후속 계약/연결: `FeedPersonalizationContextV1` Loader integration, `FeedRetrievalPlan`, `FeedAnswerV2`,
`SelectedEvidenceContext`, EvidenceReference → 내부 paper_id, 다중 source persistence.
장기 요약의 입력 projection은 `profile_summaries`에 대응하지만 최신 row 선택/조회 정책과
`chat_messages` 활용 방식은 확정하지 않았다. Real Backend Context Loader,
production Retrieval Planner/PII validation/persistence integration도 미구현이다.
DB/RAG identity나 schema를 임시 값으로 대체하지 않았다.

오프라인 골격 검증 (`backend/`):

```bash
MINDCARE_RUN_PROVIDER_INTEGRATION=0 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest app.ai.agent.tests.test_feed_workflow -v
```

### FeedPersonalizationContext V1

`workflows/feed_context.py`는 caregiver/user-scoped **입력** 계약이다. 공통 실행 `schemas.py`,
Tool 계약 `tools/contracts.py`, 최종 응답 `outputs.py`와 책임을 분리한다. Workflow/Loader/
Prompt/Runner 연결은 없다. 최상위는 `schema_version="1"`, aware `reference_time`과 다음 네 구성이다.

- `caregiver_profile`: 저장된 간병인 프로필, row가 없으면 명시적 `null`
- `managed_patient_profiles`: 여러 관리 환자의 저장 프로필 및 환자별 종속 collection
- `recent_care_context`: 최근 정확히 30×24시간의 관찰 기록
- `long_term_summary`: optional caregiver/user-scoped derived 장기 요약, 없으면 명시적 `null`

모든 선언 필드는 required이며 nullable scalar도 생략하지 않고 `null`, 조회된 빈 list는
`[]`로 표현한다. Extra field를 거부한다. 사용자 ID, raw 환자 UUID, 간병인/환자 이름,
전화번호, 병원명 및 직접 식별정보를 위한 필드는 없다. 자유 텍스트 내부 PII 제거까지
schema가 보장하는 것은 아니며 서버 내부 `AgentContext` 계약도 변경하지 않았다.

| 모델 / DB 출처 | 포함 필드 및 의미 |
|---|---|
| `CaregiverProfileContext` / `caregiver_profiles` | `relationship`, `burden_score`, `mood_score`, `lifestyle_tags`, aware `updated_at`. 관계는 저장된 보조 TEXT이며 가족/전문간병인 분류나 특정 관리 환자와의 관계를 확정하지 않는다. 두 score는 strict int/null 원문이며 척도 버전·범위·방향·임상 기준을 보장하지 않는다. |
| `ManagedPatientContext` / `patient_profiles` | `patient_ref`, `dementia_stage`, `diagnosis_date`, `symptoms`, `interests`, aware `updated_at`과 아래 네 종속 collection. Stage는 저장값이고 `[]`는 미등록이지 무증상/관심 없음의 증거가 아니다. 수정 시각은 임상 평가 시각이 아니다. |
| `ClinicalAssessmentContext` / `clinical_assessments` | `assessment_type`, `score: Decimal/null`, `result_detail`, `assessed_at: date`. Unknown type을 그대로 허용하며 type/검사 버전/범위/판정 기준 없이 score로 단계·중증도·악화를 추론하지 않는다. |
| `SafetyEventContext` / `safety_events` | `event_date: date`, `has_fall`, `has_wandering`, `has_missing`, `note`. False는 해당 row의 flag일 뿐이며 날짜의 row 부재는 사건 없음의 증거가 아니다. |
| `MedicationContext` / `medications` | `drug_name`, `is_taking`, nullable `start_date`, `end_date`, `note`. Dosage/frequency는 제외한다. |
| `MedicalVisitContext` / `medical_visits` | `visit_date: date`, `is_visited`, `department`, `visit_content`. `hospital_name`은 제외한다. |
| `CareLogContext` / `care_logs` | nullable `patient_ref`, `log_type`, `content`, `mood_tag`, aware `logged_at`. Null ref는 이 Context에서 환자와 연결되지 않았다는 뜻이며 caregiver self-care로 단정하지 않는다. |
| `LongTermPersonalizationSummary` / `profile_summaries` | date `period_start`, `period_end`, nullable `summary`, `updated_tags`, aware `generated_at`. 간병인/user의 derived 개인화 맥락이며 특정 환자의 장기 임상 memory가 아니다. |

DB의 같은 이름 컬럼을 위 필드에 대응시키고 DB row ID, `assessed_by`, 불필요한
created/updated metadata는 포함하지 않는다. `patient_ref`만 Loader가 DB patient ID를
Context-local opaque reference(예: `patient_1`)로 변환한다. 영구 identity나 중요도 순번이 아니다.

**Schema validation / Loader boundary.** `ContextCollection[T]`는 `items`와 `coverage`
(`total_count`, `included_count`, `is_truncated`)로 구성한다. 두 count는 strict non-negative int이며
`included_count == len(items) <= total_count`, `is_truncated == (included_count < total_count)`를 강제한다.
Total은 동일 논리적 snapshot에서 authorization과 정의된 eligibility/basic filter를 통과한 수다.
빈 `items`, count 0/0, false는 정상 조회 결과다. DB/Loader 실패를 empty/null로 위장하지 않는다.
`care_logs.coverage`는 **선택된 관리 환자 집합 + eligibility상 허용된 비귀속 기록** 범위의 완전성이다.
False여도 간병인의 모든 환자 기록이 포함되었다는 뜻은 아니다. 조회 limit/선택 알고리즘은 payload에 없다.

Context 안의 `patient_ref`는 unique이고 모든 non-null log ref는 포함된 관리 환자를 가리켜야 한다.
Truncated 환자 collection에도 이 규칙을 적용한다. Loader는 먼저 선택 환자 집합을 확정하고
DB ID ↔ ref의 일대일 대응과 같은 환자 소속의 clinical/safety/medication/visit record 조립을
보장해야 한다. Payload에는 raw ID가 없어 validator가 DB 소속/권한을 증명할 수 없다.
선택되지 않은 환자의 기록을 다른 ref에 붙이거나 원래 ref를 null로 바꿔 비귀속으로 위장하면 안 된다.
`care_logs.patient_id`의 `ON DELETE SET NULL`로 원래 비귀속/삭제 환자 기록을 현재 값만으로
구분하지 못할 수 있다. 삭제 환자 기록 eligibility는 아래 미확정 정책으로 남긴다.

모든 datetime은 aware여야 한다. `reference_date`는 payload가 아니라 서비스 timezone의
`reference_time` 날짜이며 date-only 필드와 비교한다. 프로젝트에 서비스 timezone 명시값이
없으므로 기본 UTC/한국 시간을 선택하지 않는다. 서버는 아래와 같이 validation context에
합의된 `tzinfo`를 공급해야 하며 없으면 validation failure다. 어떤 timezone을 사용할지는 후속 결정이다.

```python
validated = FeedPersonalizationContextV1.model_validate(
    payload, context={"service_timezone": agreed_service_timezone},
)
```

개별 하위 모델의 validation 성공은 전체 V1 Context 계약 충족을 의미하지 않는다.
`reference_time`에 의존하는 temporal rule과 관리 환자/CareLog cross-reference는 최상위
Context에서 검증한다. Context Loader/Assembler는 완성된 payload를 LLM에 전달하기 전에
반드시 위 `FeedPersonalizationContextV1.model_validate()`로 최종 검증하고,
`context={"service_timezone": agreed_service_timezone}`을 명시적으로 전달해야 한다.

Diagnosis/assessment/safety 날짜와 summary 종료일은 `reference_date` 이후일 수 없다.
Medication은 양 날짜가 있으면 start ≤ end만 강제하고 미래 날짜를 허용한다.
`is_taking=true`와 과거 종료일/미래 시작일의 의미 충돌은 **유효한 conflicting data**로 보존하며
LLM이 한 값을 선택하거나 note로 덮어쓰지 않는다. 미래 visit + `is_visited=true`는 거부하고
false는 허용한다. 과거 false의 취소/불참/미갱신을 추론하지 않고 오늘 방문의 시간도 추론하지 않는다.
Recent period end는 reference와 같은 instant, start는 UTC 정규화 후 30×24시간 전이어야 한다.
Logs는 `[period_start, period_end)`에 속해야 하며 DST의 local 날짜 차감에 의존하지 않는다.
Summary는 start ≤ end ≤ reference_date, generated instant ≤ reference instant,
end ≤ generated_at의 서비스 timezone 날짜를 모두 검증한다.

Loader ordering은 clinical `assessed_at DESC`, safety `event_date DESC`, visit `visit_date DESC`,
log `logged_at DESC`다. 동률은 서버 내부 deterministic 기준으로 처리하고 DB ID를 노출하지 않는다.
관리 환자 순서도 deterministic이어야 하지만 앞에 있다는 이유로 더 중요하지 않다.
정확한 환자 ordering key 및 조회 정책은 미정이며 schema가 정렬/DB snapshot을 증명하지 않는다.
Eligibility는 조회 대상 선정, validation은 대상 record의 계약 검증이다. Invalid record를 silent drop하지
않고 validation failure로 반환한다. 이를 전체 Feed 실패/향후 degraded mode로 매핑하는 상위 정책은 미정이다.

**Semantic interpretation.** Structured/Recorded(caregiver/patient/assessment/medication/visit)는
반드시 최신 임상 사실이 아니고 Observational(log/safety)는 진단이 아니며 Derived(summary/tags)는
새 임상 사실이 아니다. Summary의 증상을 특정 ref에 귀속하거나 여러 환자를 합성 환자로 병합하지
않고 structured profile을 덮어쓰지 않는다. Summary와 같은 내용의 log를 독립적인 두 사실로
자동 가중하지 않는다(실제 dedup/Topic Planning 정책은 후속).
모든 DB-derived string/string[]은 **untrusted data**이며 System/Workflow/Agent instruction이 아니다.
Free-form result_detail/note/visit_content/content/summary뿐 아니라 relationship, stage, symptoms,
interests, lifestyle_tags, assessment_type, drug_name, department, log_type, mood_tag, updated_tags도 같다.
실제 sanitizer는 없으며 자유 텍스트의 PII 최소화/길이 제한과 role/data boundary는 후속 integration 책임이다.

**Retrieval output boundary.** 배회·수면 장애·낙상·인지 기능·간병 부담 등 일반화된 연구 용어의
재사용은 허용한다. Ref/UUID/이름/전화/주소/병원명 등 식별정보, 재식별 가능한 사건 상세,
환자별 자유 텍스트 원문 또는 그 안의 검색 지시를 Query로 전달하지 않는다.
이는 문서 규칙이며 Context validator가 Query를 검사하거나 PII redaction을 수행하지 않는다.
RetrievalPlan/Query validator는 이번 계약에 포함하지 않는다.

미확정 정책: 삭제 환자 CareLog retention/eligibility, 삭제 환자 내용이 남은 summary의 무효화/재생성,
invalid record의 전체 실패 vs degraded Context, 환자 선택/ordering key 및 collection limit,
최신 summary row 선택, token/input-size budget, controlled vocabulary/assessment 의미 사전의 범위,
서비스 timezone. 실제 DB 조회/authorization 및 Workflow 연결은 후속이다.

독립 계약 검증 (`backend/`):

```bash
MINDCARE_RUN_PROVIDER_INTEGRATION=0 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest app.ai.agent.tests.test_feed_context -v
```

## 실행

`backend/`에서 기존 `requirements.txt` 의존성을 설치한 Python 환경으로 실행한다.
새 dependency는 없다. `unittest`는 Python 표준 라이브러리다.

```bash
.venv/bin/python -m unittest discover -s app/ai/agent/tests -t . -v
.venv/bin/python -m app.ai.agent.testing.demo
```

Windows 등에서는 활성화한 가상환경의 `python`으로 `.venv/bin/python`을 대체한다.
기본 테스트/데모는 API key, DB, 외부 네트워크가 필요 없다.
Provider 테스트는 설치된 `openai==1.51.0` + `httpx.MockTransport`로 API 형식만 검증한다.

선택적인 실제 Provider 검증은 기존 `app.config.settings.openai_api_key` binding과 명시적인
모델 이름을 준비한 뒤 실행한다. API key를 코드/명령줄에 넣거나 출력하지 않는다.

```bash
MINDCARE_RUN_PROVIDER_INTEGRATION=1 MINDCARE_AGENT_MODEL=<사용할_모델> \
  .venv/bin/python -m unittest app.ai.agent.tests.test_provider_integration -v
```

실제 테스트는 opt-in 없으면 skip이며, opt-in하더라도 key/model이 없으면 skip한다.
외부 연결 테스트를 실행하지 않았으면 실제 Provider 동작 검증으로 보고하지 않는다.

`OpenAIClient(sdk, model=model, timeout_seconds=20.0)`로 네트워크 timeout을 명시한다.
Adapter는 매 호출에 `max_retries=0`을 적용하고 네트워크 timeout을 남은 Agent deadline
이하로 제한한다. 주입한 SDK의 key/model/HTTP client 및 원래 설정은 변경하지 않는다.
HTTP timeout은 네트워크 단계별 제한이며 실행 중인 thread의 강제 취소를 보장하지 않는다.

## 파일별 역할

모든 Agent 코드/문서는 `backend/app/ai/` 내부에 있다. DB/RAG 공용 계약, main/config,
의존성 선언에는 변경이 없다.

| 파일 (agent/ 기준) | 역할 |
|---|---|
| `schemas.py` | Context, ToolCall, ModelTurn, Message, State, Result, 오류/메타데이터 trace |
| `outputs.py` | 최종 Chat/Feed V1, Paper Detail 보조 콘텐츠, 근거 참조 검증, 선택적 결과 wrapper |
| `tools/contracts.py` | 3개 Tool의 입력/출력 Pydantic 모델, EvidencePackage |
| `registry.py` | ToolName/ToolContract 단일 출처, JSON Schema 생성, ToolSpec/Registry, 빈 운영 Registry |
| `executor.py` | 입력/출력 검증, Handler 실행, JSON 직렬화, 안전한 오류/메타데이터 로그 |
| `_execution.py` | 동기 호출 대기 timeout, 요청 deadline 전파, 제한된 daemon worker |
| `prompts.py` | PromptBuilder Protocol, 최소 정책과 DefaultPromptBuilder |
| `agent_loop_runner.py` | 호출 루프, 한 응답의 여러 Tool 순차 실행, 상한, 최종 결과 |
| `clients/base.py` | Provider-independent LLMClient Protocol |
| `clients/openai_client.py` | SDK 주입, 모델 설정 주입, Chat Completions 변환/정규화 |
| `tools/evidence_tools.py` | 미래 RAG 서비스의 EvidenceRetriever Protocol; 실행 구현 없음 |
| `testing/fake_tools.py` | 고정 합성 fixture 기반 3개 Fake Handler/테스트 Registry |
| `testing/fake_llm.py` | 정해진 ModelTurn 순서와 요청 snapshot을 사용하는 FakeLLMClient |
| `testing/demo.py` | 최근 기록 → 근거 조회 → 최종 응답의 Fake 데모 |
| `testing/structured_demo.py` | 같은 Tool Loop로 실행하는 Chat/Feed V1 오프라인 예시 |
| `tests/test_outputs.py` | V1 경계/버전/참조/구조화 실행/legacy 호환성 검증 |
| `tests/test_workflows.py` | A–D 워크플로, 오류, 경계/Builder 교체/Fake 계약 검증 |
| `tests/test_openai_client.py` | 네트워크 없이 실제 SDK 메시지 형식/오류 검증 |
| `tests/test_provider_integration.py` | 명시적 opt-in으로만 실행하는 외부 Provider 테스트 |
| `tests/test_readiness.py` | timeout/deadline, 실패 경계, 정본 등록, 출력 일관성 회귀 검증 |
| `README.md` | 사용법, 계약, 보안 경계, 후속 연결 사항 |

`ai/`, `agent/`, `tools/`, `clients/`, `testing/`, `tests/`의 `__init__.py`는 패키지 표식이다.
자동 Tool 등록이나 DB/API 호출 부작용은 없다.

## Tool Contract

아래 이름/설명/모델은 `registry.TOOL_CONTRACTS`가 단일 출처다.
LLM JSON Schema는 `input_model.model_json_schema()`에서 생성하여 이중 정의하지 않는다.
모든 모델은 알 수 없는 필드를 거부한다. 모든 입력에서 `patient_id`, `user_id`는 제외한다.
UUID는 레포 실제 타입을 따른다. 날짜는 ISO 날짜, 시각은 datetime JSON으로 전달한다.

| Tool / 목적 | Input → Output 모델 | 주요 입력 | 주요 출력 |
|---|---|---|---|
| `get_patient_profile`: 저장된 기본 환자 프로필 snapshot | `PatientProfileInput` → `PatientProfileOutput` | `{}` (추가 argument 금지) | `name?: str`, `dementia_stage?: str`, `diagnosis_date?: date`, `symptoms: list[str]`, `interests: list[str]` |
| `get_recent_care_logs`: 최근 patient_care 기록 | `RecentCareLogsInput` → `RecentCareLogsOutput` | `days: strict int=14 (1..90)`, `limit: strict int=10 (1..30)` | `period: {start_at,end_at}`, `logs: list[CareLogItem]`, `total_count: strict int>=0` |
| `search_evidence`: RAG 근거 | `EvidenceSearchInput` → `EvidencePackage` | `query: str (1..2000자)`, `top_k: int=5 (1..20)` | `query`, `evidence: list[EvidenceItem]` |

`CareLogItem`: `logged_at: aware datetime`, `content: str | None=null`, `mood_tag: str | None=null`.
기간의 `start_at`/`end_at`도 timezone-aware이며 `start_at <= logged_at < end_at`을 검사한다.
`logs`는 실제 시각 기준 최신순(동일 시각 허용)이며 `total_count >= len(logs)`다.
DB ID와 log_type은 결과에 노출하지 않고 동일한 visible data의 기록도 허용한다.

`EvidenceItem`: 필수 `evidence_type: new_research/guideline`, `title: str`,
`relevance_score: float (-1..1, 기존 코사인 점수 범위)`.
선택 필드: `pmid: str | None`, `publication_year: int | None`, `study_type`, `ai_summary`,
`abstract`, `journal`, `doi`, `organization`, `source_url` (기본 null).
`full_text_available: bool`은 기본 false다. `query` + `evidence` 묶음과 각 필드의 타입/기본값은
`app.ai.retrieval.search.EvidencePackage`의 직렬화 형식과 일치한다.
DB 내부 `paper_id`/`external_id`/`published_date`는 이 RAG 경계에서 받지 않는다.
Agent의 extra 금지 및 유한 코사인 점수 범위 검증은 유지한다.
선택 필드는 반환하지 않은 정보를 추측해서 채우지 않는다.

예시의 `age/sex/cognitive_status`는 현재 환자 스키마에 없으므로 추가하지 않았다.
실제 `dementia_stage/diagnosis_date/symptoms/interests`를 우선했다.
`conditions/care_environment`는 `get_patient_profile V1` 범위에서 제외한다.
돌봄 기록은 DB의 `logged_at`을 사용하며 `patient_care` 조건은 서버 내부 의미다.

### get_patient_profile V1 semantics

`get_patient_profile`은 trusted server `AgentContext`가 지정한 환자의 **DB에 저장된 기본
프로필 snapshot**을 읽기 전용으로 조회하는 Tool이다. Chat/Feed 등의 개인화 context가
필요할 때 사용한다. 아래 의미는 V1 계약이며 현재 Handler는 deterministic 합성 Fake다.
Real Handler, Backend Adapter, DB 조회 함수와 production 등록은 후속 작업이다.

대상 환자는 서버의 `AgentContext.patient_id`/`user_id` 및 서버 측 인증/접근권한 경계가
결정한다. LLM-visible input은 `{}`이며 `patient_id`, `user_id`, `caregiver_id`,
`include_conditions`, `include_care_environment` 등 추가 argument를 모두 거부한다.
`PatientProfileOutput`에도 `patient_id`를 포함하지 않는다. 단, 현재 `PromptBuilder`는
trusted `AgentContext` 자체를 system message로 직렬화하므로 `patient_id`/`user_id`가
LLM prompt에 전달된다. 이 계약은 LLM이 UUID를 전혀 볼 수 없음을 보장하지 않는다.
Context ID의 prompt redaction은 별도 보안/Prompt-boundary 후속 과제다.

출력은 다음 5개 필드만 포함하며 저장되지 않은 정보를 추정하거나 생성하지 않는다.

- `name`: 저장된 표시 이름/별칭. 법적 실명을 보장하지 않으며 미등록이면 null이다.
- `dementia_stage`: 저장된 stage 문자열(`str | None`). 최신 임상 평가 결과나 임상적으로
  확정된 현재 단계를 보장하지 않는다. CDR, 임상 평가, 돌봄 기록 등으로 새로 판정하거나
  보정·정규화하지 않는다. 미등록이면 null이며 enum/MCI 분류는 이번 V1에서 결정하지 않는다.
- `diagnosis_date`: 저장된 진단일. 미등록이면 null이며 다른 정보에서 추정하지 않는다.
  Pydantic `date`의 기존 ISO JSON 직렬화를 사용한다.
- `symptoms`: 프로필에 등록된 증상 항목/태그. 임상적으로 확인된 전체 증상 목록이 아니며
  다른 기록에서 증상을 추가하지 않는다. `[]`는 저장된 항목이 없다는 뜻이며 무증상을 뜻하지 않는다.
- `interests`: 프로필에 등록된 관심 치료/관리 분야. 의료적으로 권장된 치료 목록이 아니다.
  `[]`는 저장된 항목이 없다는 뜻이며 실제 관심이나 관리 필요성이 없다는 뜻이 아니다.

`name`, `dementia_stage`, `diagnosis_date`가 모두 null이고 `symptoms`, `interests`가 모두
`[]`인 출력도 **성공적으로 조회된 sparse profile**일 수 있다. 이는 profile row가 존재하고
선택 정보가 미등록이라는 뜻이며, dementia/MCI가 아님·진단받은 적 없음·증상/관심 없음으로
해석하면 안 된다. 저장된 profile data는 독립적으로 검증된 임상 사실 또는 Agent inference와
구분한다. 예를 들어 “프로필에 경도 단계가 저장되어 있다”는 설명은 가능하지만,
이를 “현재 경도 치매가 임상적으로 확진되었다”로 바꾸지 않는다.

대상 profile 자체를 찾지 못함, 접근 권한 문제, DB 조회 실패, timeout, Handler exception,
Tool output contract 위반은 실패다. 이러한 실패를 null/`[]`의 sparse success로 위장하지 않는다.
현재 Executor의 `empty_result`, `invalid_output`, `tool_exception`, `tool_timeout`을 재사용하며,
실제 DB not-found/access-denied 처리와 row identity/권한 검증은 향후 Real Handler의 책임이다.

최근 돌봄 기록, `profile_summaries`, 임상 평가, 복약, 안전/행동 이벤트, 병원 방문 기록을
조회하거나 결합하지 않는다. `conditions`, `care_environment`, caregiver relationship,
`reading_level`, 사용자/간병인 ID는 출력 범위에 없다. 장기 patient-memory semantic retrieval,
embedding 검색/생성/저장, dementia stage 재판정 또는 의료적 진단/추론 Tool이 아니다.
향후 Real Handler에서도 read-only semantics를 유지하며 profile 수정, DB write,
annotation 저장, semantic memory 갱신 등의 side effect를 수행하지 않는다.

### get_recent_care_logs V1 semantics

`get_recent_care_logs`는 trusted server `AgentContext`가 지정한 현재 사용자/환자에 대해,
간병인이 저장한 최근 **patient_care 돌봄 기록**을 제한된 범위에서 읽기 전용으로 조회해
개인화 context를 제공하는 Tool이다. 현재는 합성 Fake만 존재한다. 아래 조회/권한 의미는
V1 계약이며 실제 DB 조회 함수, Backend DTO/Adapter, Real Handler, production 등록은 후속이다.

LLM-visible input은 `days`와 `limit`뿐이다. 두 값은 strict integer이며 문자열 숫자/bool/float를
거부한다. `days`는 기본 14, 1..90이고 `limit`은 기본 10, 1..30의 최대 반환 개수다.
`patient_id`, `user_id`, `caregiver_id`, `log_type`, `log_types`와 임의의 추가 필터는 받지 않는다.
대상 사용자/환자와 종료 시각을 LLM argument로 선택하거나 바꿀 수 없다.
기존 PromptBuilder의 trusted Context system message와 ID 전달은 이번에 변경하지 않는다.

현재 단일 caregiver DB 구조에서 **향후 Real Handler/Backend Adapter가 강제할 backing semantics**:

1. 먼저 `patient_profiles.id == context.patient_id`인 환자의
   `patient_profiles.caregiver_id == context.user_id`인지 서버에서 검증한다.
2. 조회 row는 `care_logs.patient_id == context.patient_id`,
   `care_logs.user_id == context.user_id`, `care_logs.log_type == "patient_care"`를 모두 만족해야 한다.

이는 현재 Fake가 실제 접근 권한을 검증한다는 뜻이 아니다. 권한/identity 실패를 빈 logs로
위장하지 않는다. DB query, 권한 적용, timeout/cancellation 및 production 연결은 후속 책임이다.

`end_at`은 Handler가 요청당 한 번 결정한 조회 종료 시각이고,
UTC 기준으로 `start_at = end_at - timedelta(days=args.days)`다. 달력 날짜 N개가 아닌 최근 **N×24시간
rolling window**이며 DST 등에서도 UTC instant 기준으로 N×24시간을 뺀다.
조회 범위는 `start_at <= logged_at < end_at`(start inclusive / end exclusive)이고
`start_at < end_at`이어야 한다. 모든 시각은 timezone-aware이며 offset이 달라도 실제 instant로
비교한다. `logged_at`은 DB의 기록 시각이며 content가 서술하는 사건/증상의 발생 시각으로 추론하지 않는다.

향후 Real Handler는 종료 시각을 timezone-aware instant로 결정하고, duration 계산 전에 UTC로 정규화한다.

```python
end_utc = end_at.astimezone(timezone.utc)
start_utc = end_utc - timedelta(hours=24 * args.days)
```

DB의 `TIMESTAMPTZ` 조회 경계도 instant 기준의 `start_utc <= logged_at < end_utc`로 적용한다.
local wall-clock timezone datetime에 직접 `timedelta(days=...)`를 적용하는 계산에 의존하지 않는다.
DST spring-forward/fall-back에서도 실제 elapsed duration은 항상 `days × 24시간`이어야 한다.
이는 후속 구현 규칙이며 현재 Real Handler는 없다. 향후 Handler 테스트에서는 두 DST 전환 모두
`days=1`의 UTC elapsed가 정확히 24시간인지 확인한다.

출력은 `period`, `logs`, `total_count`뿐이다. `period`는 `start_at`, `end_at`만,
각 `CareLogItem`은 `logged_at`, `content`, `mood_tag`만 포함한다. 환자/사용자/간병인 ID와
`log_id`/`log_type`은 결과에 노출하지 않는다. 로그는 `logged_at` 최신순(non-increasing)이고
같은 시각이나 완전히 같은 visible data도 허용한다. 임의의 duplicate rejection을 하지 않는다.
향후 DB ordering은 `ORDER BY logged_at DESC, id DESC`다. `id DESC`는 UUID가 더 최신이라는
뜻이 아니라 동일 시각의 내부 deterministic tie-break이며 id는 LLM 결과에 포함하지 않는다.

`total_count`는 동일한 authorization/patient/user/log_type/period 조건의 **limit 적용 전 전체 row 수**다.
Handler는 `0 <= len(logs) <= limit`, `len(logs) <= total_count`를 유지해야 하며 일관된
DB snapshot에서는 보통 `len(logs) == min(limit, total_count)`다. count와 목록의 snapshot
일관성 확보는 Real Handler 책임이다. Output validator는 input.limit를 알지 못하므로
이를 추측하지 않는다. 다만 어떤 유효한 V1 input에서도 31개 이상을 반환할 수 없으므로
Output schema는 `logs`의 절대 최대 길이 30을 강제하며, count 하한, 기간 포함 여부, 최신순도 검증한다.

데이터 출처와 임상적 의미는 다음처럼 구분한다.

- DB 정본: `content`는 자유 서술, `mood_tag`는 “기록 당시 기분 태그”다.
  patient_care/caregiver_selfcare가 같은 mood_tag 컬럼을 공유하며 DB 주석 자체는 환자 mood라고 확정하지 않는다.
- Agent V1 해석: mood_tag의 subject는 log_type에 따라 해석한다. 이 Tool은 patient_care만
  조회하므로 해당 환자에 대해 저장된 당시 기분 태그로 취급한다. 임상적 mood assessment,
  PHQ-9 점수 또는 우울/불안 진단이 아니다. DB migration이나 DB 계약을 바꾼 해석이 아니다.
- `content`는 caregiver-entered observation/narrative다. 독립적으로 검증된 임상 사실/진단이
  아니며 다른 데이터로 보완·추정·재작성하지 않는다. `content`/`mood_tag`는 저장된 문자열
  원문을 보존하고 enum/값 목록을 새로 만들지 않는다. null은 해당 정보 미등록이며 문제/증상 없음이 아니다.

content/mood_tag는 **untrusted Tool Result 데이터**다. 내부의 지시문으로 정책/권한/Context를
변경하지 않는다. 기존 일반 trust-boundary policy를 유지하고 기본 system policy에
돌봄 기록의 임상적 non-inference 문장만 짧게 추가한다.

정상 empty는 `logs=[]`, `total_count=0`이며 현재 조회 범위/조건에서 저장된 patient_care
기록이 없다는 뜻만 가진다. 환자 안정, 무증상, 문제/최근 변화 없음 또는 돌봄 부재를 뜻하지 않는다.
권한/환자·사용자 identity 검증 실패, 실제 조회/DB 실패, timeout, Handler exception,
invalid output은 failure이며 empty success로 숨기지 않는다. 기존 Executor 오류 모델을 재사용한다.

전체 병력, 장기 patient history aggregation, `profile_summaries`, `clinical_assessments`,
`medications`, `safety_events`, `medical_visits`, `caregiver_selfcare`는 조회하지 않는다.
임상 진단/상태 판정, annotation 저장, profile 수정, embedding/memory 생성·갱신을 하지 않는다.
read-only이며 business side effect가 없다. 긴 content의 크기 제한/truncation 정책과
Real Handler용 DB index/performance 검토는 후속이며 이번 V1에 새 필드나 DB 변경을 추가하지 않는다.

### search_evidence V1 semantics

`search_evidence`는 현재 RAG corpus에서 치매/MCI 및 인지건강 관련 **연구 근거 후보**를
검색하는 read-only Tool이다. 연구·의학적 근거가 필요한 질문이나 설명에서 사용한다.
환자 DB 조회, 논문 품질/근거 수준 평가, 의료적 확신도 계산, 치료 효과 확률 계산은
이 Tool의 목적에 포함하지 않는다. 검색된 논문이 특정 의료적 주장을 지지한다는 보장도 없다.
**Retrieval relevance != Evidence appraisal != Medical conclusion**을 유지한다.

**입력:** `query`는 1..2000자이며 사용자 원문 또는 Workflow/Agent가 검색 목적에 맞게
재구성한 질문을 허용한다. 임상/돌봄 맥락은 포함할 수 있지만 검색에 불필요한 직접 식별정보
(patient/user UUID, 이름, 전화번호, 주소 등)는 최소화하여 query에 포함하지 않는다.
이 원칙은 의미 정책이며 개인정보 검출 validator를 구현한 것은 아니다.
Agent input validator는 whitespace-only query를 거부하고, nonblank query를 trim/정규화/재작성하지 않는다.
현재 RAG `search()`는 자체적으로 query를 strip한다. Agent 입력 원문 보존과 RAG 내부 처리는 구분한다.
따라서 현재 RAG가 반환하는 `EvidencePackage.query`는 검색 실행 전에 `(query or "").strip()`으로 정규화된 검색 query를 나타내며, Agent가 제출한 원문과 항상 byte-for-byte 동일하지는 않다.
LLM input은 `query`, `top_k` 두 필드뿐이며 `patient_id`/`user_id`/`exclude_animal`은 노출하지 않는다.
`top_k`는 StrictInt, 기본 5, 범위 1..20인 **최대** 반환 개수다. 정확히 N개를 채우는 보장은 없다.

**검색 책임과 점수:** query embedding은 RAG 영역의 BGE-M3가 담당하고 DB 검색은
pgvector cosine similarity 기반이다. 현재 별도 reranker는 없다. Agent/향후 Handler는
임베딩·검색 알고리즘을 구현하는 대신 RAG를 호출하고 반환 계약을 검증한다.
`relevance_score`는 cosine-similarity 기반 retrieval score이며 Agent는 유한한 [-1, 1] 값을 허용한다.
논문 품질, evidence level, 의료적 확신도, 치료 효과 또는 환자 적합도 점수로 해석하지 않는다.
RAG 모델의 Field description에는 현재 0~1 표현이 있으나 Agent의 [-1, 1] 계약과 계산 방식은 유지한다.

**동물 연구 정책:** 보호자에게 사람 대상 근거를 제공하기 위해 향후 Real Adapter는
server-side RAG policy로 `exclude_animal=True`를 전달해야 한다. LLM이 이 정책을 선택하지 않는다.
제외 대상은 RAG classifier가 동물/세포 전용 연구로 판단한 결과다. 인간 연구 신호가 있는
mixed study를 단순히 동물 관련 표현 때문에 제외한다는 뜻은 아니다.
세부 판별 알고리즘의 정본은 `app.ai.retrieval.evidence`이며 이번 계약에서는 재구현하지 않는다.

**순서와 참조:** `EvidencePackage.evidence`의 순서는 RAG가 반환한 순서가 정본이다.
Agent와 향후 Handler/Adapter는 임의 재정렬하지 않는다. Structured Output의 authoritative reference는
성공한 `search_evidence`의 `tool_call_id`와 해당 호출의 `evidence[]` 배열에 대한 **0-based**
`evidence_index` 쌍이다. 기존 `outputs.py`의 참조 검증/metadata 복사를 그대로 사용한다.

**성공과 실패:** `evidence=[]`, `top_k`보다 적은 결과, 동물 필터링 후 결과 감소,
개별 candidate의 상세 데이터 누락에 따른 결과 감소는 정상 성공이 될 수 있다.
`get_papers_by_ids()`는 존재하지 않는 candidate를 제외할 수 있으므로 향후 검색 경계에서는
그 누락만으로 전체 검색을 반드시 실패 처리하지 않고 나머지 정상 evidence를 유지할 수 있다.
전체 RAG/DB 실행 exception, timeout, Tool output contract 위반은 별개의 실패이며
이를 빈 성공 package로 바꿔 숨기지 않는다.
빈 결과의 의미는 "현재 corpus와 query/검색 정책에서 반환 가능한 근거가 없었다"로 제한한다.
치료 효과 없음, 행동의 안전함, 세상에 관련 연구가 없음 또는 RAG 장애를 의미하지 않는다.

**Metadata와 운영 경계:** 기존 PMID/DOI/저널/출판연도/URL/제목/연구 유형/기관 등의
metadata를 유지한다. nullable metadata가 없으면 null이며 Agent/LLM이 추정하거나 생성하지 않는다.
스키마는 `new_research | guideline`을 허용하지만 현재 실제 검색은 `new_research`를 반환한다.
실제 RAG가 제공하지 않은 guideline을 Agent/Adapter가 만들거나 임의 승격하지 않는다.
V1에는 `paper_id`, `source_id`, `corpus_updated_at`, `authors`, evidence level A/B/C/D 필드나 mapping을 추가하지 않는다.
이 Tool은 환자/DB 데이터를 변경하거나 결과를 저장하는 side effect가 없다.
Real Handler/Adapter, `top_k → k` 변환, 정책 전달 및 실연결 검증은 후속 작업이며
현재 `production_registry()`는 계속 비어 있다. `EvidencePackage`의 기존 필드 구조를 유지한다.

## Context / Prompt 경계

`AgentContext`는 `request_id: str`, `user_id: UUID`, `patient_id: UUID`, `locale=ko-KR`를 담는
불변 모델이다. 인증/소유권을 검증한 서버가 생성해야 한다. 이것만으로 인증을 구현한 것은 아니다.
Tool Handler는 `(context, validated_input)`을 받는다. LLM이 patient/user ID를 입력하면 검증에서 거부한다.
Executor에는 출력 모델에 `patient_id`가 있을 때 Context와 비교하는 일반 검사가 남아 있다.
`PatientProfileOutput V1`에는 이 필드가 없으므로 해당 검사는 profile에 적용되지 않는다.
향후 Real Handler는 출력의 5개 필드로 projection하기 전에 조회 row의 identity와 접근 권한을
검증해야 한다. 출력 ID 제거 또는 schema 검증만으로 실제 환자 소유권 검증을 대체하지 않는다.

PromptBuilder를 생성자에 주입한다:

```python
engine = AgentLoopRunner(client, registry, prompt_builder=DefaultPromptBuilder())
result = engine.run(user_input, trusted_context)
```

Builder의 교체 지점:

- `build_system_prompt(context)`
- `build_initial_messages(user_input, context)`
- `build_tool_followup_messages(turn, outcomes)`
- `build_final_response_context(evidence_packages)`

기본 Builder는 정책(system), 서버 Context(system의 별도 JSON), 원문 사용자 입력(user),
모델 Tool Calls(assistant), 호출 ID별 결과(tool), 구조화 근거 묶음(user의 별도 데이터 message)을 유지한다.
사용자 입력은 strip/요약/재작성하지 않는다. Tool Result/Evidence를 system 정책으로 승격하지 않는다.
근거 묶음은 재판단 시 Builder가 제공하며, 원래 Tool 결과도 conversation에 보존한다.
Provider로 전송할 때 `kind`는 제거되지만 role, 개별 message, Tool Call ID/JSON 경계는 유지된다.
Query Rewriting/Intent Extraction/Compression은 이 Builder를 교체하여 추가할 수 있다.
현재는 구현하지 않았으며 원문 보존/신뢰 경계를 유지하는 책임은 새 Builder에도 적용된다.

입력 문자열로 시스템 메시지나 Tool JSON/서버 Context 객체를 덮어쓸 수 없도록 테스트했다.
이 구조와 정책은 모델의 모든 Prompt Injection 대응이나 의료 답변의 정확성을 보장하지 않는다.
실제 접근 권한은 서버/Handler에서 강제해야 한다. 운영 쓰기 Tool은 소유권, 재실행,
승인/감사 정책을 합의한 후 연결한다.

## 실행 제한 / 오류 / 로그

기본 상한: Tool round 5, 전체 Tool 호출 10. 두 값은 strict positive integer만 허용하며
0/음수/bool/float/NaN/infinity를 거부한다. 한 턴의 여러 호출은 순차 실행한다.
상한을 초과할 batch는 일부만 실행하지 않고 모두 거부한다. 정확히 상한을 실행한 뒤
추가 Tool 없는 최종 LLM 응답은 허용한다. 따라서 LLM 호출 수는 최대 6회다.
같은 Tool의 반복은 상한으로 종료하고, 중복 Call ID는 재실행하지 않고 `invalid_response`로 종료한다.
정상적인 같은 Tool 재조회와 반복을 구분하는 의미적 중복 제거는 이번 단계에서 구현하지 않는다.

Tool 오류는 `unknown_tool`, `invalid_arguments`, `tool_exception`, `empty_result`,
`invalid_output`, `serialization_error`로 LLM에 전달한다. Tool 오류 후 후속 Tool이나
최종 답변이 가능하다. Output의 빈 list는 정상적인 데이터 없음이며, `None`은 오류다.
직렬화 전에 출력 모델을 다시 검증한다. validation/exception 상세값은 외부에 포함하지 않는다.
Python `TimeoutError`(내부 실행 timeout 포함), HTTPX `TimeoutException`, OpenAI
`APITimeoutError`는 `tool_timeout`으로 처리하여 Agent를 failed로 종료한다.
예외 이름/메시지로 추측하지 않으며 일반 연결 오류 등은 기존 `tool_exception`을 유지한다.

기본 Handler 대기 제한은 30초, Agent 전체 request deadline은 120초다.
기존 생성자에 선택적 keyword-only 옵션을 추가했다:

```python
engine = AgentLoopRunner(client, registry,
    tool_timeout_seconds=30.0, request_timeout_seconds=120.0)
```

`ToolExecutor(registry, timeout_seconds=30.0)`도 직접 설정할 수 있다.
초 단위 값은 양의 유한 수만 허용하며 플랫폼 thread 대기 한도를 넘지 않아야 한다.
요청 deadline은 monotonic clock을 사용하고 round마다 초기화하지 않는다.
Builder, Registry definitions, LLM, ModelTurn 검증, Executor, Evidence 검증의 대기를 제한한다.
ContextVar로 deadline을 전파하고 종료 시 복원하므로 동시 요청 간 deadline을 공유하지 않는다.

동기 확장 지점은 제한된 daemon thread에서 실행한다. 일반 작업 최대 32개,
Handler 최대 16개이며 두 용량을 분리해 Executor/Handler 간 slot 교착을 방지한다.
용량 대기 시간도 timeout/deadline에 포함하며, 만료된 대기 작업은 시작하지 않는다.
timeout는 늦은 결과를 폐기하고 Agent를 failed로 종료하며 남은 batch/LLM 후속 호출/자동 재시도를 중단한다.
**이 대기 제한은 실행 중인 Python thread의 강제 종료나 저장 rollback을 보장하지 않는다.**
늦은 Handler가 계속 실행할 수 있으며 slot은 실제 종료 시 해제된다.
Python 실행 기회를 장시간 막는 native 코드에 대한 hard real-time deadline도 보장하지 않는다.
실제 DB/RAG 연결 시 SDK/DB statement timeout, 취소, 저장 멱등성을 별도로 적용해야 한다.

Builder/Executor/Evidence/Registry의 실행 예외는 고정 오류 코드와 failed로 반환하고
원래 예외/환자 데이터는 포함하지 않는다. 일반 Provider 오류·잘못된 응답의 기존 상태는 유지한다.
Executor의 outcome/trace는 상태에 추가하기 전에 재검증하고 호출 ID/이름/성공 상태를 검사한다.
최종 AgentResult 검증에 실패하면 오염된 metadata와 답변을 버리고
`result_validation_error`를 담은 최소 failed 결과를 반환한다.
Registry는 callable Handler, 구체적인 Pydantic 모델과 JSON Schema, 이름/설명/입출력 모델의
정본 ToolContract 일치를 검증한다. test mode도 정본 검증을 우회하지 않는다.

AgentResult 상태: `completed`, `limit_reached`, `provider_error`, `invalid_response`, `failed`.
`completed`는 루프가 최종 답변까지 완료되었다는 뜻이다. 중간 Tool 오류는 `errors`에 남고,
실제 의료 정확성/모든 Tool 성공을 뜻하지 않는다. Provider 실패와 빈/잘못된 응답은
최종 답변 없는 명확한 실패 상태로 반환한다.

기존 41개 기본 테스트의 이름과 검증 범위를 유지했다. 0 상한 검증은 새 정책에 맞춰
생성자 거부를 확인하며, 직렬화 실패는 정본 계약 대신 serializer에 실패를 주입한다.

로그: 서버 request_id, 등록된 tool_name, 성공 여부, 실행 시간, 결과 건수, 고정 오류 코드만 기록한다.
Tool arguments/result, 사용자 질문, 환자 기록, 전체 prompt, 예외/DB/키 내용은 기록하지 않는다.
외부 AgentResult도 전체 messages/reasoning을 포함하지 않는다.
FakeLLM의 메모리 snapshot은 합성 테스트 전용이며 운영 기록으로 사용하지 않는다.

## Fake / 운영 구분

`testing.fake_tools.fake_registry()`만 3개 Fake Tool을 등록한다.
Fake ToolSpec은 `test_only=True`이며 production Registry 등록 시 오류가 난다.
`production_registry()`는 의도적으로 비어 있다. 동작하는 척하는 운영 stub은 없다.
최근 기록 Fake는 `FIXTURE_NOW=2026-10-06T12:00:00Z` 기준 rolling window/최신순/limit을 사용하고,
`total_count`는 limit 적용 전 개수다. 동일 시각 hidden UUID tie-break와 nullable fixture도 결정적이다.
실제 권한 검증/DB 조회는 하지 않는다.
모든 근거 ID/DOI/PMID는 명백한 FAKE fixture다.

## 다음 실제 연결 단계: 팀 합의 필요

현재 DB 함수는 논문 수집/조회/분석/임베딩 관련 함수뿐이다.
3개 Tool 중 **현재 계약만으로 바로 운영 연결 가능한 Tool은 없다**. 실제 연결은 다음 작업이다.

| Tool | 필요한 backing 계약/합의 |
|---|---|
| 프로필 | Context의 사용자/환자 소유권과 row identity를 검증하는 조회 함수; sparse success와 profile not-found/failure 구분 후 V1 5개 필드로 변환 |
| 최근 기록 | V1은 trusted user/patient, patient_care only, rolling datetime window로 확정; 실제 Backend DTO/DB 조회 함수, authorization 적용, count/list snapshot 일관성, production Handler는 후속 |
| 근거 검색 | `EvidenceRetriever.search_evidence(query, top_k, context) -> EvidencePackage`; query embedding과 검색은 RAG 담당 |

기존 `search_similar(embedding, top_k, model_name)`는 UUID+score만 반환하고,
`get_papers_by_ids(paper_ids)`는 논문 상세를 반환한다. 현재 `app.ai.retrieval.search.search`
구현이 query embedding과 검색/상세 조회를 제공하지만 Agent의 운영 Handler에는 연결하지 않았다.
다음 Adapter에서 `top_k` → `k`, Context 전달 정책과 Agent EvidencePackage 변환을 처리한다.
내부 누락 ID는 `paper_id`로 매칭해야 하며 리스트 위치만으로 점수를 결합하면 안 된다.
RAG의 `evidence_type="new_research"` 고정과 full_text_available 기본 false는 이번에 변경하지 않았다.
care annotation persistence는 향후 `CareAnnotationWorkflow`가 structured output 검증/guardrail 후
deterministic하게 수행할 책임이다. 해당 Workflow와 DB persistence는 아직 구현하지 않았다.
실제 RAG와 DB 계약/공용 스키마를 이번 작업에서 수정하거나 새로 가정하지 않았다.

## 최종 답변 Structured Output V1 (선택적)

`engine.run(user_input, context)`는 기존 `AgentResult` 및 문자열 `final_answer`를 그대로
반환한다. 기존 결과 JSON에 새 필드가 추가되지 않는다. FakeLLM/LLMClient의
`generate(messages, tools) -> ModelTurn`도 그대로다.

```python
from app.ai.agent.outputs import ChatAnswerV1, FeedAnswerV1

chat = engine.run_structured(user_input, context, output_model=ChatAnswerV1)
feed = engine.run_structured(user_input, context, output_model=FeedAnswerV1)
```

`run_structured()`만 서버가 선택한 최종 JSON Schema/콘텐츠 정책을 system message로
추가한다. 기존 PromptBuilder 교체 지점과 Tool 호출/실행/재판단 루프는 유지한다.
구조화 정책은 첫 user message 직전에 삽입하며 Builder의 기존 메시지 순서는 보존한다.
기본 순서는 system 정책 → system Context → system 구조화 정책 → user 원문이다.
Structured 모드에서는 `build_final_response_context()`의 aggregate evidence copy를 생략한다.
call ID가 있는 원래 Tool Result의 전체 EvidencePackage가 재판단/최종 생성에 계속 전달된다.
legacy `run()`은 기존 aggregate evidence message와 Builder 호출 동작을 유지한다.
Tool Call이 없는 마지막 `ModelTurn.text`를 단일 JSON object로 검증한다.
Provider native response_format/strict schema 기능은 연결하지 않았다. 모델이 프롬프트를
따르지 않으면 서버 검증에서 거부한다. 자동 JSON 수정/markdown 추출/재요청은 하지 않는다.

반환 `StructuredAgentResult`는 `execution: AgentResult`, `output: ChatAnswerV1 | FeedAnswerV1 | None`,
`sources: list[ResolvedEvidence]`의 별도 wrapper다. Chat 성공 시 `execution.final_answer`는
검증된 `output.answer`에서만 파생한다. Feed 성공 시 `execution.final_answer=None`이며,
JSON을 문자열 답변에 넣지 않는다. 실패 시 output=None, sources=[], final_answer=None이다.
참조/JSON/콘텐츠 계약 위반은 `invalid_response` + `invalid_structured_output`으로 반환하고,
원문/검증 상세를 오류에 노출하지 않는다. 최종 검증도 기존 전체 request deadline에 포함된다.
Tool 오류 후 참조 없는 Chat 설명은 가능하며, `completed`는 의료적 정확성 보장이 아니다.

### 콘텐츠 모델과 제한

모든 아래 모델은 `AgentModel`의 extra 금지/비유한 수 금지 정책을 상속한다.
콘텐츠 문자열은 strict string이며 빈 문자열/공백만 있는 문자열을 거부하고 원문을 보존한다.
`schema_version`과 `response_type`은 기본값 없이 명시적으로 제출해야 한다.

| 모델 | 필드 및 제한 |
|---|---|
| `EvidenceReference` | `tool_call_id`: 1..256자, `evidence_index`: strict int >=0, 0-based |
| `ChatAnswerV1` | `schema_version="1"`, `response_type="chat"`, `answer`: 1..12,000자, `citation_refs`: 0..20개 고유 참조 |
| `FeedContentItemV1` | `source_ref`, `headline`: 1..200자, `summary_bullets`: 1..5개/각 1..500자, `personal_reason`: 1..1,000자, `category`: 단일 treatment/care/prevention/diagnosis |
| `FeedAnswerV1` | `schema_version="1"`, `response_type="feed"`, `items`: 0..5개, 동일 source_ref 중복 금지 |
| `PaperParagraphV1` | `text`: 1..2,000자 |
| `PaperBodyV1` | `easy`, `detail`: 각각 1..10개 PaperParagraphV1 |
| `GlossaryEntryV1` | `term`: 1..100자, `meaning`: 1..500자 |
| `PaperDetailContentV1` | `schema_version="1"`, `response_type="paper_detail"`, `source_ref`, summary_bullets(Feed와 동일), body, `personal_meaning`: null 또는 1..2,000자, `limitations`: 0..5개/각 1..500자, `glossary`: 0..10개 |

빈 Feed는 검색/필터링 후 결과가 없거나 개인화 근거가 부족한 정상 콘텐츠다.
정확히 5개를 강제하거나 없는 근거를 채우지 않는다. 순위는 생성된 items 순서이며,
최신성/Top-K 선택 정책 자체를 구현하거나 검증한 것은 아니다.
최종 Chat/Feed JSON 문자열은 파싱 전에 65,536자로 제한한다. 중복 JSON key,
NaN/Infinity, 잘못된 version/type, metadata/미래 V2 필드도 거부한다.

Paper Detail은 기존 summary_bullets/근거 참조를 재사용하는 보조 계약만 추가했다.
독립 실행 모델로 `run_structured()`에 전달할 수 없고 Workflow/API/저장은 없다.
개인화 Context가 없을 때 personal_meaning은 명시적으로 null이다. 문단별 citation 번호는
생성하지 않는다. 전체 콘텐츠의 source_ref만 검증하며 문단별 인용은 후속 별도 계약이다.
`body.easy/detail`은 현재 화면의 읽기 난이도와 맞춘 필드이며 미래의 `easy_summary`가 아니다.
현재 상세 화면의 복수형 `limitations`도 미래 3분할의 단수형 `limitation`과 별개다.

### 출처 참조 검증과 서버 조립 경계

AgentLoopRunner는 Executor 성공 및 EvidencePackage 재검증을 통과한 `search_evidence` 결과만
요청 내부 `evidence_by_call`에 call ID로 보관한다. 원래 Tool Result payload는 바꾸지 않는다.
모델은 role=tool message의 tool_call_id와 data.evidence 위치를 참조한다.
`resolve_evidence_references(output, evidence_by_call)`는 output을 다시 검증하고,
현재 요청의 성공 검색 존재/0-based index 범위를 검사한다. 실패한 검색/다른 Tool/다른 요청의
ID/없는 근거를 인용할 수 없다. helper를 직접 쓸 때도 성공 검색 snapshot만 제공해야 한다.
이 검증은 의료적 함의, 환자 사실, 인용 문장과 논문 내용의 일치까지 증명하지 않는다.

서버용 `ResolvedEvidence`는 source_ref와 실제 evidence_type/title/pmid/doi/journal/
publication_year/study_type/organization/source_url/full_text_available만 원본에서 복사한다.
없던 metadata는 null 그대로 유지하고 abstract/ai_summary/전체 환자 기록은 반환하지 않는다.
LLM이 이 metadata를 반환하는 schema는 없다. 이 클래스와 StructuredAgentResult는 서버용이며
LLM 최종 출력 모델은 ChatAnswerV1/FeedAnswerV1뿐이다.

| AI 콘텐츠 | 현재 Frontend 매핑 | 신뢰 가능한 서버 조립 |
|---|---|---|
| Chat `answer` | ChatMessage.text | message id/role은 Backend가 생성 |
| Chat `citation_refs` | ChatMessage.citations의 별도 출처 영역 | 서버가 원본 title/source_url/type을 조립. 표시 번호는 UI/Assembler 책임이며 answer 안 번호와 연결하지 않음. evidence_level mapping은 미확정 |
| Feed `headline` | FeedItem.title | AI 헤드라인과 원본 논문 title을 구분 |
| Feed `summary_bullets` | FeedItem.summary_bullets | 그대로 |
| Feed `personal_reason` | FeedItem.personal_reason | 실제 제공된 patient/care context와 근거에 한정 |
| Feed `category` | FeedItem.category | 단일 category 유지 |
| Feed `source_ref` | metadata lookup | id/journal/published_at/evidence_level/read_minutes/is_bookmarked는 LLM 생성 금지 |
| Paper `summary_bullets/body/personal_meaning/limitations/glossary` | PaperDetail의 같은 필드 | body 문단은 text만 제공; nullable personal_meaning 표시 정책은 별도 |
| Paper `source_ref` | metadata lookup | id/원 제목/journal/date/authors/DOI/pubmed_url/study_type/evidence_level은 신뢰 가능한 source에서 조립 |

Assembler/Frontend DTO 생성은 구현하지 않았다. 현재 RAG는 DB paper UUID, authors,
정확한 published_at 날짜, evidence_level을 반환하지 않는다. publication_year만으로 날짜를
만들거나 PMID를 UUID로 간주하지 않는다. 현재 Frontend의 필수 metadata를 모두 채울 수는
없으며, 누락 표현 및 DB ID 매핑은 후속 합의가 필요하다. Citation source_url이 null인 경우도
가짜 URL을 생성하지 않는다. Structured Chat의 answer에는 inline 인용 번호를 생성하지
않도록 안내하며, citation_refs만 authoritative 출처 연결로 사용한다. 기존 mock의 [1] 같은
본문 표기를 근거로 서버가 번호를 신뢰하지 않는다. 모델이 정책을 무시하고 번호를 출력해도
일반 answer 문자열일 뿐 출처 연결로 해석하지 않는다. 별도 marker validator는 도입하지 않았다.

정책은 relevance_score를 검색 유사도로만 사용하고 근거 등급/의학적 확신도로 해석하지 않는다.
근거 없는 인용·환자 사실·개인화 설명을 만들지 않고, 빈 검색을 효과 없음/안전함으로 해석하지
않도록 안내한다. new_research와 guideline을 구분하고, 현재 1..6 → A/B/C/D/guideline의
미확정 변환을 LLM이 결정하지 않게 한다. disclaimer는 현재 UI 고정 책임이다.

### V2 확장 및 남은 합의

V1에는 easy_summary/finding/comparison/limitation/복수 category를 넣지 않았다.
팀 합의 후 FeedAnswerV2/PaperDetailContentV2와 명시적 schema_version="2"를 정의하고,
서버가 허용 버전을 선택하며 소비자도 해당 버전을 지원하도록 한다. extra 금지인 V1에
필드를 조용히 추가하거나 V2 payload를 V1로 강제 파싱하지 않는다.
paper_analysis의 summary_finding/summary_comparison/summary_limitation은 그대로이며,
easy_summary migration이나 새 dependency는 없다.

다음 Tool Contract 구체화는 진행할 수 있다. 실제 연결 전에는 개인화 Context 출처,
등급 변환, metadata 누락 정책/ID 매핑, 순위·최신성 기준, 문장 수준 근거 검증,
Paper Detail 문단별 인용, nullable 개인화 UI 처리가 추가 합의 대상이다.
production Registry는 계속 비어 있고 Provider native schema 연동도 후속 선택 사항이다.

오프라인 검증 (`backend/`; live Provider test를 명시적으로 끈다):

```bash
MINDCARE_RUN_PROVIDER_INTEGRATION=0 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s app/ai/agent/tests -t . -v
MINDCARE_RUN_PROVIDER_INTEGRATION=0 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s . -t . -v
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m app.ai.agent.testing.demo
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m app.ai.agent.testing.structured_demo
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c 'import app.ai.agent.agent_loop_runner; import app.ai.agent.outputs; import app.ai.retrieval.search; import app.ai.retrieval.evidence'
git diff --check
```
