"""6개 후보 Tool의 Agent 전용 입력/출력. 확장 필드는 DB 필드가 아니다."""
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, StrictBool, StrictInt, model_validator

from ..schemas import AgentModel


class PatientProfileInput(AgentModel):
    include_conditions: StrictBool = True
    include_care_environment: StrictBool = True


class CareEnvironment(AgentModel):
    type: str


class PatientProfileOutput(AgentModel):
    patient_id: UUID
    name: str | None = None
    dementia_stage: str | None = None
    diagnosis_date: date | None = None
    symptoms: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    # 현재 DB에 없는 선택적 확장. 합의 전 실제 데이터인 것처럼 채우지 않는다.
    conditions: list[str] | None = None
    care_environment: CareEnvironment | None = None


class RecentCareLogsInput(AgentModel):
    days: StrictInt = Field(default=14, ge=1, le=365)
    log_types: list[Literal["patient_care", "caregiver_selfcare"]] = Field(
        default_factory=lambda: ["patient_care"]
    )
    limit: StrictInt = Field(default=50, ge=1, le=100)


class DatePeriod(AgentModel):
    from_date: date
    to_date: date

    @model_validator(mode="after")
    def check_order(self):
        if self.from_date > self.to_date:
            raise ValueError("Invalid date period")
        return self


class CareLog(AgentModel):
    log_id: UUID
    logged_at: AwareDatetime
    log_type: Literal["patient_care", "caregiver_selfcare"]
    content: str | None = None
    mood_tag: str | None = None


class RecentCareLogsOutput(AgentModel):
    period: DatePeriod
    logs: list[CareLog]
    total_count: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def check_count(self):
        if self.total_count < len(self.logs):
            raise ValueError("Count cannot be less than returned logs")
        ids = [log.log_id for log in self.logs]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate care log IDs")
        # 출력 기간은 각 기록의 명시적 offset에서 본 local date를 기준으로 한다.
        if any(not self.period.from_date <= log.logged_at.date() <= self.period.to_date for log in self.logs):
            raise ValueError("Care log is outside the returned period")
        return self


class PatientHistoryInput(AgentModel):
    metric: str = Field(min_length=1, max_length=100)
    period_days: StrictInt = Field(default=90, ge=1, le=365)
    aggregation: Literal["daily", "weekly", "monthly"] = "weekly"


class HistoryPoint(AgentModel):
    period_start: date
    count: StrictInt = Field(ge=0)


class HistorySummary(AgentModel):
    latest_value: StrictInt
    previous_value: StrictInt
    change: StrictInt

    @model_validator(mode="after")
    def check_change(self):
        if self.change != self.latest_value - self.previous_value:
            raise ValueError("Change must be deterministic")
        return self


class PatientHistoryOutput(AgentModel):
    metric: str
    aggregation: Literal["daily", "weekly", "monthly"]
    data: list[HistoryPoint]
    summary: HistorySummary

    @model_validator(mode="after")
    def check_summary(self):
        dates = [point.period_start for point in self.data]
        if any(previous >= current for previous, current in zip(dates, dates[1:])):
            raise ValueError("History periods must be unique and ordered")
        latest = self.data[-1].count if self.data else 0
        previous = self.data[-2].count if len(self.data) >= 2 else 0
        if (self.summary.latest_value, self.summary.previous_value, self.summary.change) != (
                latest, previous, latest - previous):
            raise ValueError("History summary must match the returned data")
        return self


class EvidenceSearchInput(AgentModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: StrictInt = Field(default=5, ge=1, le=20)


class EvidenceItem(AgentModel):
    evidence_type: Literal["new_research", "guideline"]
    paper_id: UUID | None = None
    external_id: str | None = None
    title: str
    journal: str | None = None
    doi: str | None = None
    organization: str | None = None
    source_url: str | None = None
    published_date: date | None = None
    study_type: str | None = None
    # search_similar는 코사인 유사도 [-1, 1]을 반환한다.
    relevance_score: float = Field(ge=-1, le=1)
    ai_summary: str | None = None
    abstract: str | None = None


class EvidencePackage(AgentModel):
    query: str
    evidence: list[EvidenceItem]


class AIAnnotationInput(AgentModel):
    log_id: UUID
    categories: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=1, max_length=4000)
    follow_up_recommended: StrictBool = False
    follow_up_topics: list[str] = Field(default_factory=list)


class AIAnnotationOutput(AgentModel):
    success: StrictBool
    annotation_id: UUID | None = None

    @model_validator(mode="after")
    def check_identifier(self):
        if self.success and self.annotation_id is None:
            raise ValueError("Successful annotation requires an ID")
        return self


class Observation(AgentModel):
    type: str = Field(min_length=1)
    present: StrictBool


class SafetyFlagsInput(AgentModel):
    observations: list[Observation]


class SafetyFlagsOutput(AgentModel):
    flagged: StrictBool
    level: Literal["none", "test_only"]
    matched_rules: list[str]
    # 실제 임상 수준/규칙 enum은 담당자 합의 후 별도 변경한다.

    @model_validator(mode="after")
    def check_flags(self):
        if self.flagged:
            if self.level != "test_only" or not self.matched_rules:
                raise ValueError("Flagged result requires a level and matched rules")
        elif self.level != "none" or self.matched_rules:
            raise ValueError("Unflagged result must have no level or matched rules")
        if len(self.matched_rules) != len(set(self.matched_rules)) or any(not rule.strip() for rule in self.matched_rules):
            raise ValueError("Matched rules must be nonempty and unique")
        return self


class ToolResultEnvelope(AgentModel):
    """Prompt Builder에서 직렬화하는 결과 (성공 데이터는 각 Output으로 검증)."""

    success: bool
    data: dict[str, JsonValue] | None = None
    error: dict[str, str] | None = None
