"""Ties deterministic fault-signature matching (signatures.py) together with RAG-retrieved
incidents and an optional LLM narrative into a single RootCauseAnalysis.

Precedence is deliberate and matches the Week 8 spec ("must not blindly trust retrieved
incidents. Historical incidents are evidence, not commands"):
1. A matched fault signature (deterministic, evidence-based) always wins over retrieval.
2. Retrieval can only *raise or lower confidence* in an existing signature match (agreement vs
   disagreement), or, when no signature matched at all, offer a *low-confidence* suggestion.
3. The LLM is asked only to narrate the conclusion already reached by (1)/(2) - it cannot change
   `suspected_root_cause_service` or `confidence`. See llm/prompts.py's build_rca_reasoning_prompt
   docstring.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Optional

from .. import config as ai_config
from ..llm.ollama_client import OllamaClient, OllamaUnavailableError
from ..llm.prompts import build_rca_reasoning_prompt
from ..rag.models import RetrievalResult
from . import signatures
from .models import MetricAnomaly, RootCauseAnalysis

log = logging.getLogger("ai_orchestration.rca.analyzer")


def _build_metric_anomalies(service: str, relevant_feature_values: dict) -> list[MetricAnomaly]:
    anomalies = []
    for name, value in relevant_feature_values.items():
        if value is None:
            continue
        baseline = signatures.baseline_reference(service, name)
        if baseline is None:
            description = "no documented baseline reference available for this service/metric"
        elif value > baseline * 1.2:
            description = f"above documented baseline reference ({baseline:.0f})"
        elif value < baseline * 0.8:
            description = f"below documented baseline reference ({baseline:.0f})"
        else:
            description = f"within documented baseline reference range (~{baseline:.0f})"
        anomalies.append(MetricAnomaly(
            metric_name=name, observed_value=float(value), baseline_reference=baseline,
            deviation_description=description,
        ))
    return anomalies


def _narrate_with_llm(
    ollama_client: OllamaClient,
    suspected_fault_type: Optional[str],
    root_cause_service: str,
    symptom_service: str,
    metric_anomalies: list[MetricAnomaly],
    retrieval: RetrievalResult,
) -> Optional[str]:
    if not ollama_client.is_available():
        log.info("Ollama unavailable at %s - RCA proceeds without an LLM narrative", ollama_client.base_url)
        return None

    prompt = build_rca_reasoning_prompt(
        suspected_fault_type=suspected_fault_type,
        suspected_root_cause_service=root_cause_service,
        symptom_service=symptom_service,
        metric_anomalies=[m.model_dump() for m in metric_anomalies],
        retrieved_incidents=[inc.model_dump() for inc in retrieval.incidents],
    )
    try:
        raw_response = ollama_client.generate(prompt, expect_json=True)
        parsed = json.loads(raw_response)
    except (OllamaUnavailableError, json.JSONDecodeError) as exc:
        log.warning("RCA LLM narrative unavailable: %s", exc)
        return None

    observed_evidence, inference = parsed.get("observed_evidence"), parsed.get("inference")
    if not observed_evidence or not inference:
        log.warning("RCA LLM response missing required keys: %s", parsed)
        return None
    return f"OBSERVED EVIDENCE: {observed_evidence}\nINFERENCE: {inference}"


def analyze(anomaly_result_dict: dict, retrieval: RetrievalResult, ollama_client: OllamaClient) -> RootCauseAnalysis:
    service = anomaly_result_dict["service"]
    relevant_feature_values = anomaly_result_dict.get("relevant_feature_values") or {}

    metric_anomalies = _build_metric_anomalies(service, relevant_feature_values)
    sig_matches = signatures.match_signatures(service, relevant_feature_values)

    if sig_matches:
        best = sig_matches[0]
        suspected_fault_type = best.fault_type
        root_cause_service, symptom_service = best.root_cause_service, best.symptom_service
        confidence = "medium"
        determination_method = "metric_signature"
        evidence_summary = (
            f"Anomaly on {service} matched the '{best.fault_type}' signature via "
            f"{best.matched_metric}={best.observed_value:g} (threshold {best.threshold:g}, "
            f"{best.excess_ratio:.1f}x over threshold)."
        )
    else:
        suspected_fault_type = None
        root_cause_service = symptom_service = service
        confidence = "low"
        determination_method = "fallback_unknown"
        evidence_summary = (
            f"No known fault signature matched the anomalous metrics observed on {service}; "
            f"defaulting root cause to the symptom-visible service pending further evidence."
        )

    if retrieval.status == "retrieved" and retrieval.incidents:
        fault_type_votes = Counter(inc.fault_type for inc in retrieval.incidents)
        top_retrieved_fault_type, _ = fault_type_votes.most_common(1)[0]

        if sig_matches:
            if top_retrieved_fault_type == suspected_fault_type:
                confidence = "high"
                determination_method = "metric_signature+rag_agreement"
                evidence_summary += (
                    f" {fault_type_votes[top_retrieved_fault_type]}/{len(retrieval.incidents)} "
                    f"retrieved similar incidents agree (fault_type='{top_retrieved_fault_type}')."
                )
            elif ai_config.RAG_MAY_OVERRIDE_SIGNATURE:
                # Week 16 ablation only - never the shipped behaviour. Lets retrieval win a
                # disagreement, i.e. builds the conventional RAG-first arrangement this project
                # argues against, so its cost can be measured instead of asserted.
                overridden_from = suspected_fault_type
                suspected_fault_type = top_retrieved_fault_type
                closest_agreeing = next(
                    (inc for inc in retrieval.incidents if inc.fault_type == top_retrieved_fault_type),
                    retrieval.incidents[0],
                )
                root_cause_service = closest_agreeing.root_cause_service
                determination_method = "rag_override"
                evidence_summary += (
                    f" ABLATION (RAG_MAY_OVERRIDE_SIGNATURE): retrieved incidents mostly suggest "
                    f"'{top_retrieved_fault_type}', overriding the metric-signature conclusion "
                    f"'{overridden_from}'."
                )
            else:
                determination_method = "metric_signature+rag_disagreement"
                evidence_summary += (
                    f" Retrieved incidents mostly suggest '{top_retrieved_fault_type}' instead - "
                    f"kept the metric-signature conclusion since direct evidence outweighs retrieval."
                )
        else:
            closest = retrieval.incidents[0]
            suspected_fault_type = closest.fault_type
            root_cause_service = closest.root_cause_service
            # symptom_service intentionally stays as the actual service the anomaly was observed
            # on, not whatever the retrieved incident's symptom_service metadata says.
            confidence = "low"
            determination_method = "rag_only"
            evidence_summary = (
                f"No known fault signature matched; the closest retrieved incident "
                f"({closest.incident_id}, distance={closest.distance:.3f}) suggests "
                f"'{closest.fault_type}' (root cause: {closest.root_cause_service}) as a low-confidence lead."
            )

    llm_reasoning = _narrate_with_llm(
        ollama_client, suspected_fault_type, root_cause_service, symptom_service, metric_anomalies, retrieval,
    )

    return RootCauseAnalysis(
        suspected_fault_type=suspected_fault_type,
        suspected_root_cause_service=root_cause_service,
        symptom_service=symptom_service,
        affected_services=sorted({root_cause_service, symptom_service}),
        relevant_metrics=[m.metric_name for m in metric_anomalies],
        metric_anomalies=metric_anomalies,
        evidence_summary=evidence_summary,
        llm_reasoning=llm_reasoning,
        confidence=confidence,
        determination_method=determination_method,
    )
