# Agent / Orchestration (Fake 단계)

사용자 입력 → LLM Tool 선택 → 입력 검증 → 순차 Handler 실행 → 출력 검증/JSON →
Tool Result → LLM 재판단 → 추가 Tool 또는 최종 응답을 구현한다.
이번 단계는 계약/Fake 검증이며 DB/RAG 연결, 의료 규칙, 챗봇 API 완성은 포함하지 않는다.

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
| `tools/contracts.py` | 6개 Tool의 입력/출력 Pydantic 모델, EvidencePackage |
| `registry.py` | ToolName/ToolContract 단일 출처, JSON Schema 생성, ToolSpec/Registry, 빈 운영 Registry |
| `executor.py` | 입력/출력 검증, Handler 실행, JSON 직렬화, 안전한 오류/메타데이터 로그 |
| `_execution.py` | 동기 호출 대기 timeout, 요청 deadline 전파, 제한된 daemon worker |
| `prompts.py` | PromptBuilder Protocol, 최소 정책과 DefaultPromptBuilder |
| `orchestrator.py` | 호출 루프, 한 응답의 여러 Tool 순차 실행, 상한, 최종 결과 |
| `clients/base.py` | Provider-independent LLMClient Protocol |
| `clients/openai_client.py` | SDK 주입, 모델 설정 주입, Chat Completions 변환/정규화 |
| `tools/evidence_tools.py` | 미래 RAG 서비스의 EvidenceRetriever Protocol; 실행 구현 없음 |
| `testing/fake_tools.py` | 고정 합성 fixture 기반 6개 Fake Handler/테스트 Registry |
| `testing/fake_llm.py` | 정해진 ModelTurn 순서와 요청 snapshot을 사용하는 FakeLLMClient |
| `testing/demo.py` | 최근 기록 → 근거 조회 → 최종 응답의 Fake 데모 |
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
| `get_patient_profile`: 현재 환자 프로필 | `PatientProfileInput` → `PatientProfileOutput` | `include_conditions: bool=true`, `include_care_environment: bool=true` | `patient_id: UUID`, `name?`, `dementia_stage?`, `diagnosis_date?`, `symptoms: list[str]`, `interests: list[str]`, `conditions?: list[str]`, `care_environment?: {type: str}` |
| `get_recent_care_logs`: 최근 기록 | `RecentCareLogsInput` → `RecentCareLogsOutput` | `days: int=14 (1..365)`, `log_types: list[patient_care/caregiver_selfcare]=[patient_care]`, `limit: int=50 (1..100)` | `period: {from_date,to_date}`, `logs: list[CareLog]`, `total_count: int>=0` |
| `get_patient_history`: 지표 시계열 | `PatientHistoryInput` → `PatientHistoryOutput` | `metric: str`, `period_days: int=90 (1..365)`, `aggregation: daily/weekly/monthly=weekly` | `metric`, `aggregation`, `data: list[{period_start: date,count: int>=0}]`, `summary: {latest_value,previous_value,change: int}` |
| `search_evidence`: RAG 근거 | `EvidenceSearchInput` → `EvidencePackage` | `query: str (1..2000자)`, `top_k: int=5 (1..20)` | `query`, `evidence: list[EvidenceItem]` |
| `save_ai_annotation`: 돌봄 기록 분석 저장 | `AIAnnotationInput` → `AIAnnotationOutput` | `log_id: UUID`, `categories: list[str]`, `tags: list[str]`, `summary: str`, `follow_up_recommended: bool=false`, `follow_up_topics: list[str]` | `success: bool`, `annotation_id?: UUID` (성공 시 필수) |
| `check_safety_flags`: 합의된 규칙 검사 | `SafetyFlagsInput` → `SafetyFlagsOutput` | `observations: list[{type: str,present: bool}]` | `flagged: bool`, `level: none/test_only`, `matched_rules: list[str]` |

`CareLog`: `log_id: UUID`, `logged_at: datetime`, `log_type: patient_care/caregiver_selfcare`,
`content?: str`, `mood_tag?: str`. `total_count`는 반환 제한 적용 전 일치하는 기록 수이며
반환된 `logs` 수보다 작을 수 없다. 기간은 시작/끝 날짜를 포함한다.
`logged_at`은 timezone offset이 필수이고, 각 offset의 local date가 기간 안에 있어야 한다.
동일한 `log_id` 중복은 거부한다. 환자/지역별 timezone 통일 정책은 실제 연결 시 합의한다.

시계열 `data`는 period_start 기준 오름차순이며 중복 기간을 거부한다.
summary의 최근/이전 값은 마지막/마지막 이전 point와 일치해야 한다.
빈 시계열은 (0,0,0), 한 point는 (latest,0,latest) 규칙을 기존 Fake와 동일하게 사용한다.
annotation은 `success=false`일 때 ID 생략/null을 허용하고, 성공 시 ID를 요구한다.
Executor의 바깥 success는 실행/검증 성공이며 내부 저장 success와 구분한다.
Safety는 flagged일 때 test_only + 비어 있지 않은 고유 규칙을 요구하고,
unflagged일 때 none + 빈 규칙을 요구한다. 임상 규칙/등급은 추가하지 않았다.

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
`conditions/care_environment`는 선택적 Agent 확장이며 현재 DB 지원을 의미하지 않는다.
예시 `categories/recorded_at/structured_data` 대신 DB의 `log_type/logged_at`을 사용한다.
수면 등의 category, 장기 metric 집계, annotation은 아직 DB에 존재하는 계약이 아니다.

## Context / Prompt 경계

`AgentContext`는 `request_id: str`, `user_id: UUID`, `patient_id: UUID`, `locale=ko-KR`를 담는
불변 모델이다. 인증/소유권을 검증한 서버가 생성해야 한다. 이것만으로 인증을 구현한 것은 아니다.
Tool Handler는 `(context, validated_input)`을 받는다. LLM이 patient/user ID를 입력하면 검증에서 거부한다.
`log_id` 같은 리소스 ID는 실제 저장 Adapter가 반드시 Context의 사용자/환자 소유권을 검증해야 한다.
Fake annotation은 고정 합성 `FAKE_LOG_ID`만 수락하며 실제 환자 권한 검증을 흉내 내지 않는다.
출력 모델에 `patient_id`가 있으면 Executor가 Context와 비교한다. 현재 해당 모델은
PatientProfileOutput이다. 불일치는 데이터 직렬화/LLM 전달 전에 거부하고 Agent를 failed로 종료한다.
최근 기록/시계열에는 환자 ID 필드가 없으므로 이 검사가 실제 소유권 조회를 대체하지 않는다.

PromptBuilder를 생성자에 주입한다:

```python
engine = AgentOrchestrator(client, registry, prompt_builder=DefaultPromptBuilder())
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
engine = AgentOrchestrator(client, registry,
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

`testing.fake_tools.fake_registry()`만 6개 Fake Tool을 등록한다.
Fake ToolSpec은 `test_only=True`이며 production Registry 등록 시 오류가 난다.
`production_registry()`는 의도적으로 비어 있다. 동작하는 척하는 운영 stub은 없다.
Fake 기준 날짜는 2026-10-06이다. 최근 기록의 기간/필터/limit과 장기 fixture 집계는 결정적이다.
Fake annotation UUID는 Context 환자 ID와 입력으로 결정하며 실제 저장은 하지 않는다.
안전 검사는 `TEST_ONLY_SENTINEL`만 사용하고, 실제 `sudden_confusion` 등 임상 규칙을 생성하지 않는다.
`level=test_only`는 의료 중증도가 아니다. 모든 근거 ID/DOI/PMID는 명백한 FAKE fixture다.

## 다음 실제 연결 단계: 팀 합의 필요

현재 DB 함수는 논문 수집/조회/분석/임베딩 관련 함수뿐이다.
6개 Tool 중 **현재 계약만으로 바로 운영 연결 가능한 Tool은 없다**. 실제 연결은 다음 작업이다.

| Tool | 필요한 backing 계약/합의 |
|---|---|
| 프로필 | Context의 사용자/환자 소유권을 검증하는 환자 프로필 조회 함수; 선택 확장 필드의 출처 |
| 최근 기록 | 사용자/환자 + 날짜 범위 + log_type + limit 기반 조회/전체 건수; 타임존/자기 돌봄 기록 범위 |
| 장기 변화 | metric 추출의 구조화 데이터 출처, aggregation/기간/빈 값 정의, 결정적 집계 함수 |
| 근거 검색 | `EvidenceRetriever.search_evidence(query, top_k, context) -> EvidencePackage`; query embedding과 검색은 RAG 담당 |
| 분석 저장 | Context 소유권 검증, annotation 저장 위치/모델, idempotency/재실행, 저장 정책 |
| 안전 검사 | 담당자가 승인한 규칙 세트/버전, 실제 level enum/의료적 의미 |

기존 `search_similar(embedding, top_k, model_name)`는 UUID+score만 반환하고,
`get_papers_by_ids(paper_ids)`는 논문 상세를 반환한다. 현재 `app.ai.retrieval.search.search`
구현이 query embedding과 검색/상세 조회를 제공하지만 Agent의 운영 Handler에는 연결하지 않았다.
다음 Adapter에서 `top_k` → `k`, Context 전달 정책과 Agent EvidencePackage 변환을 처리한다.
내부 누락 ID는 `paper_id`로 매칭해야 하며 리스트 위치만으로 점수를 결합하면 안 된다.
RAG의 `evidence_type="new_research"` 고정과 full_text_available 기본 false는 이번에 변경하지 않았다.
`save_summary(AnalysisIn)`는 논문 분석 저장이므로 돌봄 기록 annotation 저장에 재사용하지 않는다.
실제 RAG와 DB 계약/공용 스키마를 이번 작업에서 수정하거나 새로 가정하지 않았다.
