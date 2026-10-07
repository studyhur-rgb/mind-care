"""RAG 모델의 직렬화 형식과 Agent Tool 경계를 검증한다. 실제 검색/DB/API 호출 없음."""
import json
from typing import get_type_hints
from unittest import TestCase

from pydantic import ValidationError

from app.ai.retrieval.search import EvidenceItem as RAGEvidenceItem
from app.ai.retrieval.search import EvidencePackage as RAGEvidencePackage

from ..orchestrator import AgentOrchestrator
from ..registry import TOOL_CONTRACTS, ToolName, ToolRegistry, ToolSpec, production_registry
from ..schemas import ModelTurn
from ..testing.fake_llm import FakeLLMClient
from ..tools import contracts as c
from ..tools.evidence_tools import EvidenceRetriever
from .test_workflows import CONTEXT, call, envelopes, tool_turn


class EvidenceContractTests(TestCase):
    def package(self):
        return RAGEvidencePackage(query="synthetic", evidence=[
            RAGEvidenceItem(evidence_type="new_research", pmid="FAKE-PMID", title="[FAKE] research",
                publication_year=2025, study_type="systematic_review", relevance_score=0.93,
                ai_summary="[FAKE] summary", abstract="[FAKE] abstract", full_text_available=True,
                journal="Fake Journal", doi="FAKE-DOI", organization="Fake Organization",
                source_url="https://example.invalid/research"),
            RAGEvidenceItem(evidence_type="guideline", title="[FAKE] guideline", relevance_score=-0.5),
        ])

    def test_rag_field_types_required_fields_and_defaults_match(self):
        self.assertEqual(list(c.EvidencePackage.model_fields), list(RAGEvidencePackage.model_fields))
        self.assertEqual(list(c.EvidenceItem.model_fields), list(RAGEvidenceItem.model_fields))
        for name, rag_field in RAGEvidenceItem.model_fields.items():
            with self.subTest(field=name):
                agent_field = c.EvidenceItem.model_fields[name]
                self.assertEqual(agent_field.annotation, rag_field.annotation)
                self.assertEqual(agent_field.is_required(), rag_field.is_required())
                self.assertEqual(agent_field.default, rag_field.default)

    def test_rag_payload_roundtrip_including_nulls_and_guideline(self):
        payload = self.package().model_dump(mode="json")
        validated = c.EvidencePackage.model_validate(payload)
        self.assertEqual(validated.model_dump(mode="json"), payload)
        self.assertEqual(json.loads(validated.model_dump_json()), payload)
        guideline = validated.evidence[1]
        self.assertIsNone(guideline.pmid)
        self.assertIsNone(guideline.publication_year)
        self.assertFalse(guideline.full_text_available)

    def test_removed_db_fields_are_rejected(self):
        item = {"evidence_type": "new_research", "title": "[FAKE]", "relevance_score": 0.5}
        for name, value in (("paper_id", "00000000-0000-0000-0000-000000000004"),
                            ("external_id", "FAKE-PMID"), ("published_date", "2025-08-01")):
            with self.subTest(field=name), self.assertRaises(ValidationError):
                c.EvidenceItem.model_validate({**item, name: value})

    def test_rag_output_passes_executor_evidence_and_prompt_validation(self):
        for package in (self.package(), RAGEvidencePackage(query="synthetic", evidence=[])):
            with self.subTest(items=len(package.evidence)):
                registry = ToolRegistry((ToolSpec(TOOL_CONTRACTS[ToolName.SEARCH_EVIDENCE],
                    lambda context, args: package),), mode="test")
                client = FakeLLMClient([tool_turn(call(ToolName.SEARCH_EVIDENCE, {"query": "synthetic"})),
                                        ModelTurn(text="synthetic final answer")])
                result = AgentOrchestrator(client, registry).run("synthetic", CONTEXT)
                self.assertEqual(result.status, "completed")
                self.assertEqual(result.errors, [])
                self.assertEqual(result.total_tool_calls, 1)
                payload = package.model_dump(mode="json")
                self.assertEqual(envelopes(client)[0]["data"], payload)
                messages = [message for message in client.requests[-1][0] if message.kind == "evidence"]
                self.assertEqual(len(messages), 1)
                self.assertEqual(json.loads(messages[0].content)["evidence_packages"], [payload])

    def test_protocol_input_and_production_registration_are_unchanged(self):
        hints = get_type_hints(EvidenceRetriever.search_evidence)
        self.assertIs(hints["return"], c.EvidencePackage)
        self.assertEqual(list(c.EvidenceSearchInput.model_fields), ["query", "top_k"])
        self.assertEqual(c.EvidenceSearchInput(query="synthetic").model_dump(),
                         {"query": "synthetic", "top_k": 5})
        self.assertIs(TOOL_CONTRACTS[ToolName.SEARCH_EVIDENCE].output_model, c.EvidencePackage)
        self.assertEqual(production_registry().definitions(), [])
