"""Planner-only V1: trusted demo input -> validated planned retrieval tasks.

Core: scope/provenance honesty, approved queries, no private-text interpolation
or clinical inference. Exact aliases, ordering, greedy coverage, shape and
determinism belong to V1 policy. No DB/RAG/provider/Workflow execution or logs.
"""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping
import unicodedata

from pydantic import ConfigDict, Field, StrictStr, field_validator, model_validator

from ..schemas import AgentModel
from .feed_profile_planning import FeedProfilePlanningInputV1


SCHEMA_VERSION = "feed-retrieval-plan-1"
POLICY_VERSION = "feed-retrieval-policy-1"
MAX_RETRIEVAL_TASKS = 3
SignalScope = Literal["caregiver", "patient"]
SignalField = Literal["interests", "recent_safety_events", "symptoms", "relationship",
                      "lifestyle_tags", "dementia_stage", "latest_assessments"]
# Scope, lexical request-local patient_ref, this field order, then signal_key.
_FIELD_ORDER = ("interests", "recent_safety_events", "symptoms", "relationship",
                "lifestyle_tags", "dementia_stage", "latest_assessments")
_PRIORITY = MappingProxyType(dict(zip(_FIELD_ORDER, (1, 2, 3, 4, 4, 5, 6))))
_FIELDS_BY_SCOPE = MappingProxyType({
    "caregiver": frozenset({"relationship", "lifestyle_tags"}),
    "patient": frozenset({"interests", "recent_safety_events", "symptoms",
                          "dementia_stage", "latest_assessments"}),
})


class FeedRetrievalPlanningError(RuntimeError):
    """Whole-plan/configuration failure; never expose source or validation data."""

    def __init__(self):
        super().__init__("Feed retrieval planning failed")


def normalize_v1(value: str) -> str:
    """NFKC -> trim -> whitespace collapse to ASCII space -> casefold only."""
    return " ".join(unicodedata.normalize("NFKC", value).strip().split()).casefold()


@dataclass(frozen=True)
class _SourceRuleV1:
    scope: SignalScope
    field: SignalField
    signal_key: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class _TopicEntryV1:
    topic_key: str
    query: str
    sources: tuple[_SourceRuleV1, ...]


# Initial human-curated V1 research questions. No request-specific catalog API.
# Alias/query/mapping changes require a policy version change after V1 ships.
TOPIC_CATALOG_V1 = (
    _TopicEntryV1("patient_sleep", "Sleep management research for people with dementia", (
        _SourceRuleV1("patient", "interests", "sleep_management", ("수면관리", "수면 관리", "sleep management")),
        _SourceRuleV1("patient", "symptoms", "sleep_problem", ("수면장애", "수면 장애", "sleep problem")),
    )),
    _TopicEntryV1("music_therapy", "Music therapy research for people with dementia", (
        _SourceRuleV1("patient", "interests", "music_activity", ("음악", "음악치료", "music therapy", "music activity")),
    )),
    _TopicEntryV1("physical_activity", "Physical activity research for people with dementia", (
        _SourceRuleV1("patient", "interests", "physical_activity", ("운동", "운동치료", "exercise", "physical activity")),
    )),
    _TopicEntryV1("nutrition_support", "Nutrition support research for people with dementia", (
        _SourceRuleV1("patient", "interests", "nutrition_support", ("영양관리", "영양 관리", "nutrition")),
    )),
    _TopicEntryV1("memory_support", "Memory support research for people with dementia", (
        _SourceRuleV1("patient", "symptoms", "memory_difficulty", ("기억력 저하", "memory loss")),
    )),
    _TopicEntryV1("fall_prevention", "Fall prevention research for people with dementia", (
        _SourceRuleV1("patient", "interests", "fall_prevention", ("낙상예방", "fall prevention")),
        _SourceRuleV1("patient", "recent_safety_events", "fall", ("has_fall",)),
    )),
    _TopicEntryV1("wandering_support", "Wandering safety support research for people with dementia", (
        _SourceRuleV1("patient", "symptoms", "wandering", ("배회", "wandering")),
        _SourceRuleV1("patient", "recent_safety_events", "wandering", ("has_wandering",)),
    )),
    _TopicEntryV1("missing_prevention", "Missing person prevention research in dementia care", (
        _SourceRuleV1("patient", "recent_safety_events", "missing", ("has_missing",)),
    )),
    _TopicEntryV1("caregiver_sleep", "Sleep support research for family caregivers of people with dementia", (
        _SourceRuleV1("caregiver", "lifestyle_tags", "sleep_deprivation", ("수면부족", "sleep deprivation", "sleep problem")),
    )),
    _TopicEntryV1("caregiver_work_balance", "Balancing employment and family dementia caregiving research", (
        _SourceRuleV1("caregiver", "lifestyle_tags", "work_and_care", ("직장병행", "employed caregiver")),
    )),
    _TopicEntryV1("family_caregiving", "Support research for family caregivers of people with dementia", (
        _SourceRuleV1("caregiver", "relationship", "family_caregiver", ("자녀", "배우자", "child", "spouse")),
    )),
    _TopicEntryV1("mild_cognitive_impairment", "Care support research for people with mild cognitive impairment", (
        _SourceRuleV1("patient", "dementia_stage", "mci", ("경도인지장애", "MCI")),
    )),
    _TopicEntryV1("mild_dementia", "Care support research for people with mild dementia", (
        _SourceRuleV1("patient", "dementia_stage", "mild_dementia", ("경도", "mild dementia")),
    )),
    _TopicEntryV1("moderate_dementia", "Care support research for people with moderate dementia", (
        _SourceRuleV1("patient", "dementia_stage", "moderate_dementia", ("중등도", "moderate dementia")),
    )),
    _TopicEntryV1("severe_dementia", "Care support research for people with severe dementia", (
        _SourceRuleV1("patient", "dementia_stage", "severe_dementia", ("중증", "severe dementia")),
    )),
    _TopicEntryV1("cognitive_assessment", "Cognitive assessment tools and caregiver understanding in dementia research", (
        _SourceRuleV1("patient", "latest_assessments", "k_mmse", ("K-MMSE",)),
        _SourceRuleV1("patient", "latest_assessments", "mmse_ds", ("MMSE-DS",)),
        _SourceRuleV1("patient", "latest_assessments", "cist", ("CIST",)),
        _SourceRuleV1("patient", "latest_assessments", "k_moca", ("K-MoCA",)),
    )),
    _TopicEntryV1("functional_assessment", "Functional assessment tools and caregiver understanding in dementia research", (
        _SourceRuleV1("patient", "latest_assessments", "cdr", ("CDR",)),
        _SourceRuleV1("patient", "latest_assessments", "k_iadl", ("K-IADL",)),
    )),
)


@dataclass(frozen=True)
class _CatalogIndexV1:
    topics: Mapping[str, _TopicEntryV1]
    aliases: Mapping[tuple[str, str, str], str]
    signals: Mapping[tuple[str, str, str], str]


def _canonical_text(value: str) -> bool:
    return type(value) is str and bool(value.strip()) and value == value.strip()


def _validate_catalog_v1(catalog: tuple[_TopicEntryV1, ...]) -> _CatalogIndexV1:
    try:
        if type(catalog) is not tuple or not catalog:
            raise ValueError("Immutable catalog required")
        topics, aliases, signals, queries = {}, {}, {}, set()
        for entry in catalog:
            if (type(entry) is not _TopicEntryV1 or not _canonical_text(entry.topic_key)
                    or entry.topic_key in topics or not _canonical_text(entry.query)
                    or len(entry.query) > 2000 or entry.query in queries
                    or type(entry.sources) is not tuple or not entry.sources):
                raise ValueError("Invalid catalog topic")
            topics[entry.topic_key] = entry
            queries.add(entry.query)
            for rule in entry.sources:
                if (type(rule) is not _SourceRuleV1
                        or rule.field not in _FIELDS_BY_SCOPE.get(rule.scope, ())
                        or not _canonical_text(rule.signal_key)
                        or type(rule.aliases) is not tuple or not rule.aliases):
                    raise ValueError("Invalid catalog source")
                signal = (rule.scope, rule.field, rule.signal_key)
                if signal in signals and signals[signal] != entry.topic_key:
                    raise ValueError("Signal must map to one topic")
                signals[signal] = entry.topic_key
                for alias in rule.aliases:
                    if type(alias) is not str or not normalize_v1(alias):
                        raise ValueError("Invalid catalog alias")
                    key = (rule.scope, rule.field, normalize_v1(alias))
                    if key in aliases and aliases[key] != rule.signal_key:
                        raise ValueError("Ambiguous catalog alias")
                    aliases[key] = rule.signal_key
        return _CatalogIndexV1(MappingProxyType(topics), MappingProxyType(aliases), MappingProxyType(signals))
    except Exception:
        raise FeedRetrievalPlanningError() from None


_CATALOG_V1 = _validate_catalog_v1(TOPIC_CATALOG_V1)


class FeedSignalRefV1(AgentModel):
    """Request-local canonical provenance; never a persistent patient identity."""

    model_config = ConfigDict(strict=True, frozen=True, revalidate_instances="always")
    scope: SignalScope
    patient_ref: StrictStr | None
    field: SignalField
    signal_key: StrictStr = Field(min_length=1)

    @model_validator(mode="after")
    def validate_identity(self):
        if self.field not in _FIELDS_BY_SCOPE[self.scope]:
            raise ValueError("Unsupported signal scope/field")
        if self.scope == "caregiver":
            if self.patient_ref is not None:
                raise ValueError("Caregiver ref must be null")
        elif self.patient_ref is None or not self.patient_ref.strip():
            raise ValueError("Patient ref must not be blank")
        if (self.scope, self.field, self.signal_key) not in _CATALOG_V1.signals:
            raise ValueError("Signal key is not canonical")
        return self


def _ref_key(ref: FeedSignalRefV1) -> tuple[int, str, int, str]:
    return (0 if ref.scope == "caregiver" else 1, ref.patient_ref or "",
            _FIELD_ORDER.index(ref.field), ref.signal_key)


class FeedRetrievalTaskV1(AgentModel):
    model_config = ConfigDict(strict=True, revalidate_instances="always")
    task_id: StrictStr = Field(pattern=r"^task_[1-3]$")
    topic_key: StrictStr = Field(min_length=1)
    query: StrictStr = Field(min_length=1, max_length=2000)
    top_k: Literal[5]
    signal_refs: list[FeedSignalRefV1] = Field(min_length=1)

    @field_validator("top_k", mode="before")
    @classmethod
    def validate_top_k(cls, value):
        if type(value) is not int or value != 5:
            raise ValueError("V1 top_k must be integer 5")
        return value

    @model_validator(mode="after")
    def validate_topic(self):
        entry = _CATALOG_V1.topics.get(self.topic_key)
        if entry is None or self.query != entry.query:
            raise ValueError("Topic/query must exactly match catalog")
        keys = [_ref_key(ref) for ref in self.signal_refs]
        if len(set(keys)) != len(keys) or keys != sorted(keys):
            raise ValueError("Signal refs must be unique and canonically ordered")
        if any(_CATALOG_V1.signals[(ref.scope, ref.field, ref.signal_key)] != self.topic_key
               for ref in self.signal_refs):
            raise ValueError("Signal does not support topic")
        return self


class FeedRetrievalPlanV1(AgentModel):
    """V1 policy shape, mutable lists; future execution must snapshot/revalidate."""

    model_config = ConfigDict(strict=True, revalidate_instances="always")
    schema_version: Literal["feed-retrieval-plan-1"]
    policy_version: Literal["feed-retrieval-policy-1"]
    tasks: list[FeedRetrievalTaskV1] = Field(max_length=MAX_RETRIEVAL_TASKS)

    @model_validator(mode="after")
    def validate_tasks(self):
        if [task.task_id for task in self.tasks] != [f"task_{i}" for i in range(1, len(self.tasks) + 1)]:
            raise ValueError("Task IDs must match final order")
        for field in ("topic_key", "query"):
            values = [getattr(task, field) for task in self.tasks]
            if len(set(values)) != len(values):
                raise ValueError("Duplicate planned topic/query")
        return self


@dataclass(frozen=True)
class _SupportedSignalV1:
    scope: SignalScope
    patient_ref: str | None
    field: SignalField
    value: str


def _project_supported_signals_v1(planning: FeedProfilePlanningInputV1) -> tuple[_SupportedSignalV1, ...]:
    # Trusted construction owns auth/time validation. Do not reparse raw input,
    # call model_validate without timezone context, or read unsupported narrative.
    if not isinstance(planning, FeedProfilePlanningInputV1) or planning.schema_version != "feed-profile-planning-1":
        raise ValueError("Trusted V1 planning input required")
    result = []

    def values(items):
        if type(items) is not list:
            raise ValueError("Supported collection must be a list")
        return tuple(items)

    def add(scope, patient_ref, field, values):
        for value in values:
            if type(value) is not str:
                raise ValueError("Invalid supported signal type")
            result.append(_SupportedSignalV1(scope, patient_ref, field, value))

    caregiver = planning.caregiver_profile
    if caregiver is not None:
        if caregiver.relationship is not None:
            add("caregiver", None, "relationship", (caregiver.relationship,))
        add("caregiver", None, "lifestyle_tags", values(caregiver.lifestyle_tags))
    patient_refs = set()
    for patient in values(planning.managed_patient_profiles):
        ref = patient.patient_ref
        if type(ref) is not str or not ref.strip() or ref in patient_refs:
            raise ValueError("Invalid patient refs")
        patient_refs.add(ref)
        add("patient", ref, "interests", values(patient.interests))
        add("patient", ref, "symptoms", values(patient.symptoms))
        if patient.dementia_stage is not None:
            add("patient", ref, "dementia_stage", (patient.dementia_stage,))
        for row in values(patient.recent_safety_events):
            for flag in ("has_fall", "has_wandering", "has_missing"):
                value = getattr(row, flag)
                if type(value) is not bool:
                    raise ValueError("Invalid safety flag")
                if value:
                    add("patient", ref, "recent_safety_events", (flag,))
        add("patient", ref, "latest_assessments", tuple(row.assessment_type for row in values(patient.latest_assessments)))
    return tuple(result)


def _match_catalog_v1(signal: _SupportedSignalV1, catalog: _CatalogIndexV1) -> str | None:
    """Exact matching seam; no substring, semantic or request-specific policy."""
    return catalog.aliases.get((signal.scope, signal.field, normalize_v1(signal.value)))


def _extract_canonical_signals_v1(
    snapshot: tuple[_SupportedSignalV1, ...], catalog: _CatalogIndexV1,
) -> tuple[FeedSignalRefV1, ...]:
    refs = set()
    for signal in snapshot:
        key = _match_catalog_v1(signal, catalog)
        if key is not None:  # Unknown/legacy text is normal skip, never rewrite.
            refs.add(FeedSignalRefV1(scope=signal.scope, patient_ref=signal.patient_ref,
                                    field=signal.field, signal_key=key))
    return tuple(sorted(refs, key=_ref_key))


@dataclass(frozen=True)
class _TopicCandidateV1:
    topic_key: str
    query: str
    signal_refs: tuple[FeedSignalRefV1, ...]
    groups: frozenset[tuple[str, str | None]]
    priority: int


def _merge_topic_candidates_v1(
    refs: tuple[FeedSignalRefV1, ...], catalog: _CatalogIndexV1,
) -> tuple[_TopicCandidateV1, ...]:
    grouped = {}
    for ref in refs:
        topic = catalog.signals[(ref.scope, ref.field, ref.signal_key)]
        grouped.setdefault(topic, set()).add(ref)
    result = []
    for topic, supporting in sorted(grouped.items()):
        ordered = tuple(sorted(supporting, key=_ref_key))
        result.append(_TopicCandidateV1(
            topic, catalog.topics[topic].query, ordered,
            frozenset((ref.scope, ref.patient_ref) for ref in ordered),
            min(_PRIORITY[ref.field] for ref in ordered),
        ))
    return tuple(result)


def _select_topics_greedy_v1(candidates: tuple[_TopicCandidateV1, ...]) -> tuple[_TopicCandidateV1, ...]:
    """Expand new provenance coverage first; not global maximum coverage."""
    remaining, selected, covered = list(candidates), [], set()
    while remaining and len(selected) < MAX_RETRIEVAL_TASKS:
        winner = min(remaining, key=lambda item: (
            -len(item.groups - covered), item.priority, item.topic_key,
            tuple(_ref_key(ref) for ref in item.signal_refs),
        ))
        selected.append(winner)
        covered.update(winner.groups)
        remaining.remove(winner)
    return tuple(selected)


def _expected_tasks_v1(snapshot: tuple[_SupportedSignalV1, ...], catalog: _CatalogIndexV1) -> list[dict]:
    signals = _extract_canonical_signals_v1(snapshot, catalog)
    selected = _select_topics_greedy_v1(_merge_topic_candidates_v1(signals, catalog))
    return [{"task_id": f"task_{index}", "topic_key": item.topic_key,
             "query": item.query, "top_k": 5,
             "signal_refs": [ref.model_dump() for ref in item.signal_refs]}
            for index, item in enumerate(selected, start=1)]


def _revalidate_retrieval_plan_v1(plan: FeedRetrievalPlanV1) -> FeedRetrievalPlanV1:
    if not isinstance(plan, FeedRetrievalPlanV1):
        raise ValueError("Plan instance required")
    # Primitive payload forces validation through constructed/mutated descendants.
    return FeedRetrievalPlanV1.model_validate(plan.model_dump(mode="python", warnings="error"))


def _build_retrieval_plan_v1(snapshot, catalog) -> FeedRetrievalPlanV1:
    return FeedRetrievalPlanV1.model_validate({
        "schema_version": SCHEMA_VERSION, "policy_version": POLICY_VERSION,
        "tasks": _expected_tasks_v1(snapshot, catalog),
    })


def _validate_plan_snapshot_v1(plan, snapshot, catalog) -> FeedRetrievalPlanV1:
    validated = _revalidate_retrieval_plan_v1(plan)
    expected = {"schema_version": SCHEMA_VERSION, "policy_version": POLICY_VERSION,
                "tasks": _expected_tasks_v1(snapshot, catalog)}
    if validated.model_dump() != expected:
        raise ValueError("Incomplete or incorrect V1 plan")
    return validated


def validate_retrieval_plan_v1(
    plan: FeedRetrievalPlanV1, planning: FeedProfilePlanningInputV1,
) -> FeedRetrievalPlanV1:
    """Revalidate full structure and complete expected selection/provenance.

    Same trusted-input precondition as Planner. Returns a freshly validated DTO,
    not execution permission; mutable output needs future execution admission.
    """
    try:
        catalog = _validate_catalog_v1(TOPIC_CATALOG_V1)
        snapshot = _project_supported_signals_v1(planning)
        return _validate_plan_snapshot_v1(plan, snapshot, catalog)
    except Exception:
        raise FeedRetrievalPlanningError() from None


class FeedRetrievalPlannerV1:
    """Accept only trusted, already scope/time-validated demo input.

    Caller supplies Mapping validation context upstream. This class is not a
    raw parser, authorization layer, timezone resolver or search executor.
    Supported values are immediately frozen; input DTOs are never working state.
    """

    def __call__(self, planning: FeedProfilePlanningInputV1) -> FeedRetrievalPlanV1:
        try:
            catalog = _validate_catalog_v1(TOPIC_CATALOG_V1)
            snapshot = _project_supported_signals_v1(planning)
            plan = _build_retrieval_plan_v1(snapshot, catalog)
            return _validate_plan_snapshot_v1(plan, snapshot, catalog)
        except Exception:
            raise FeedRetrievalPlanningError() from None
