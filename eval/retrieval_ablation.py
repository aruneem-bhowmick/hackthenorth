"""Deterministic, offline retrieval-ablation scoring for the evaluation page.

This module deliberately scores a supplied, versioned corpus/query fixture.  It
does not call Elasticsearch, OpenAI, or the production pipeline: an ablation is
only meaningful when all modes are run against the same frozen corpus and gold
paragraph identifiers.  In particular, it never invents a judge-accuracy
number from retrieval results.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


CONTRACT_VERSION = 1
MODES = ("bm25", "hybrid", "hybrid_rerank")
_TOKEN = re.compile(r"[a-z0-9]+")


def load_retrieval_ablation_fixture(path: str | Path) -> dict[str, Any]:
    """Load a YAML fixture; validation is performed by :func:`score_retrieval_ablation`."""

    # Keep the runner's YAML dependency in one familiar place and avoid a new
    # production dependency for an optional offline evaluation feature.
    import yaml

    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("retrieval-ablation fixture must contain a mapping")
    return payload


def score_retrieval_ablation(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Score BM25, hybrid, and hybrid+rereank retrieval against gold IDs.

    Fixture contract (version 1)::

        schema_version: 1
        dataset: {id: short-name, description: human-readable scope}
        top_k: 3
        rerank_candidate_k: 9             # optional; default is top_k * 3
        corpus:
          - {id: paragraph-1, text: "..."}
        queries:
          - id: q-1
            text: "..."
            relevant_paragraph_ids: [paragraph-1]
            semantic_scores: {paragraph-1: 0.9, ...}
            rerank_scores: {paragraph-1: 0.95, ...}

    ``semantic_scores`` and ``rerank_scores`` must cover the complete frozen
    corpus.  Requiring that explicit coverage prevents an omitted score from
    silently becoming an unreported experimental choice.  The output is safe
    to merge at the root of ``eval/results/latest.json`` under
    ``retrieval_ablation`` and ``retrieval_ablation_metadata``.
    """

    fixture = _validate_fixture(payload)
    corpus = fixture["corpus"]
    queries = fixture["queries"]
    top_k = fixture["top_k"]
    rerank_candidate_k = fixture["rerank_candidate_k"]
    corpus_ids = [row["id"] for row in corpus]
    lexical_scores = _bm25_scores(corpus, [query["text"] for query in queries])

    per_mode: dict[str, list[dict[str, float | int]]] = {mode: [] for mode in MODES}
    for query_index, query in enumerate(queries):
        bm25 = lexical_scores[query_index]
        semantic = query["semantic_scores"]
        rerank = query["rerank_scores"]
        hybrid = _hybrid_scores(bm25, semantic, corpus_ids)

        ranked_bm25 = _rank(bm25, corpus_ids)
        ranked_hybrid = _rank(hybrid, corpus_ids)
        candidate_ids = ranked_hybrid[:rerank_candidate_k]
        ranked_rerank = sorted(
            candidate_ids,
            key=lambda paragraph_id: (-rerank[paragraph_id], ranked_hybrid.index(paragraph_id), paragraph_id),
        )
        relevant = set(query["relevant_paragraph_ids"])
        for mode, ranked in (
            ("bm25", ranked_bm25),
            ("hybrid", ranked_hybrid),
            ("hybrid_rerank", ranked_rerank),
        ):
            per_mode[mode].append(_query_metrics(ranked[:top_k], relevant))

    result = {
        mode: _aggregate_metrics(rows, top_k=top_k)
        for mode, rows in per_mode.items()
    }
    return {
        "retrieval_ablation": result,
        "retrieval_ablation_metadata": {
            "contract_version": CONTRACT_VERSION,
            "evaluation_type": "offline_frozen_corpus_retrieval",
            "dataset": fixture["dataset"],
            "corpus_paragraphs": len(corpus),
            "queries": len(queries),
            "gold_definition": "relevant_paragraph_ids are hand-labelled evidence paragraph identifiers",
            "mode_definitions": {
                "bm25": "deterministic lexical BM25 over the frozen corpus",
                "hybrid": "equal-weight min-max fusion of BM25 and supplied semantic scores",
                "hybrid_rerank": "supplied rerank scores reorder the top hybrid candidate set",
            },
            "top_k": top_k,
            "rerank_candidate_k": rerank_candidate_k,
            "judge_metrics_included": False,
            "note": "Retrieval metrics are not proposition-judge accuracy. Run the same constrained judge with per-mode evidence to report judge accuracy separately.",
        },
    }


def _validate_fixture(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != CONTRACT_VERSION:
        raise ValueError(f"retrieval-ablation schema_version must be {CONTRACT_VERSION}")
    dataset = payload.get("dataset")
    if not isinstance(dataset, Mapping) or not isinstance(dataset.get("id"), str) or not isinstance(dataset.get("description"), str):
        raise ValueError("retrieval-ablation dataset requires string id and description")
    corpus_payload = payload.get("corpus")
    if not isinstance(corpus_payload, Sequence) or isinstance(corpus_payload, (str, bytes)) or not corpus_payload:
        raise ValueError("retrieval-ablation corpus must be a non-empty list")
    corpus: list[dict[str, str]] = []
    for row in corpus_payload:
        if not isinstance(row, Mapping) or not isinstance(row.get("id"), str) or not isinstance(row.get("text"), str):
            raise ValueError("each corpus paragraph requires string id and text")
        corpus.append({"id": row["id"], "text": row["text"]})
    corpus_ids = [row["id"] for row in corpus]
    if len(set(corpus_ids)) != len(corpus_ids):
        raise ValueError("retrieval-ablation corpus paragraph ids must be unique")

    top_k = payload.get("top_k", 6)
    candidate_k = payload.get("rerank_candidate_k", top_k * 3 if isinstance(top_k, int) else None)
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
        raise ValueError("retrieval-ablation top_k must be a positive integer")
    if top_k > len(corpus):
        raise ValueError("retrieval-ablation top_k cannot exceed frozen corpus size")
    if not isinstance(candidate_k, int) or isinstance(candidate_k, bool) or candidate_k < top_k:
        raise ValueError("retrieval-ablation rerank_candidate_k must be an integer >= top_k")

    queries_payload = payload.get("queries")
    if not isinstance(queries_payload, Sequence) or isinstance(queries_payload, (str, bytes)) or not queries_payload:
        raise ValueError("retrieval-ablation queries must be a non-empty list")
    queries: list[dict[str, Any]] = []
    corpus_id_set = set(corpus_ids)
    for query in queries_payload:
        if not isinstance(query, Mapping) or not isinstance(query.get("id"), str) or not isinstance(query.get("text"), str):
            raise ValueError("each query requires string id and text")
        relevant = query.get("relevant_paragraph_ids")
        if not isinstance(relevant, Sequence) or isinstance(relevant, (str, bytes)) or not relevant or not all(isinstance(item, str) for item in relevant):
            raise ValueError(f"query {query['id']} requires non-empty relevant_paragraph_ids")
        if not set(relevant).issubset(corpus_id_set):
            raise ValueError(f"query {query['id']} references a gold paragraph outside corpus")
        semantic = _validated_scores(query.get("semantic_scores"), corpus_id_set, query["id"], "semantic_scores")
        rerank = _validated_scores(query.get("rerank_scores"), corpus_id_set, query["id"], "rerank_scores")
        queries.append(
            {
                "id": query["id"],
                "text": query["text"],
                "relevant_paragraph_ids": list(relevant),
                "semantic_scores": semantic,
                "rerank_scores": rerank,
            }
        )
    if len({query["id"] for query in queries}) != len(queries):
        raise ValueError("retrieval-ablation query ids must be unique")
    return {
        "dataset": {"id": dataset["id"], "description": dataset["description"]},
        "corpus": corpus,
        "queries": queries,
        "top_k": top_k,
        "rerank_candidate_k": min(candidate_k, len(corpus)),
    }


def _validated_scores(value: object, corpus_ids: set[str], query_id: str, field: str) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != corpus_ids:
        raise ValueError(f"query {query_id} {field} must score every corpus paragraph exactly once")
    scores: dict[str, float] = {}
    for paragraph_id, score in value.items():
        if not isinstance(paragraph_id, str) or not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(float(score)):
            raise ValueError(f"query {query_id} {field} contains an invalid score")
        scores[paragraph_id] = float(score)
    return scores


def _bm25_scores(corpus: Sequence[Mapping[str, str]], query_texts: Sequence[str]) -> list[dict[str, float]]:
    """Return corpus-scoped BM25 scores for every query without external services."""

    tokenized = [_tokens(row["text"]) for row in corpus]
    document_frequency: Counter[str] = Counter(token for document in tokenized for token in set(document))
    average_length = sum(len(document) for document in tokenized) / len(tokenized)
    scores_by_query: list[dict[str, float]] = []
    for query_text in query_texts:
        scores: dict[str, float] = {}
        for row, document in zip(corpus, tokenized, strict=True):
            counts = Counter(document)
            score = 0.0
            for token in set(_tokens(query_text)):
                frequency = counts[token]
                if not frequency:
                    continue
                idf = math.log(1 + (len(corpus) - document_frequency[token] + 0.5) / (document_frequency[token] + 0.5))
                denominator = frequency + 1.2 * (1 - 0.75 + 0.75 * len(document) / average_length)
                score += idf * frequency * 2.2 / denominator
            scores[row["id"]] = score
        scores_by_query.append(scores)
    return scores_by_query


def _hybrid_scores(bm25: Mapping[str, float], semantic: Mapping[str, float], corpus_ids: Sequence[str]) -> dict[str, float]:
    lexical = _min_max(bm25)
    semantic_normalized = _min_max(semantic)
    return {paragraph_id: (lexical[paragraph_id] + semantic_normalized[paragraph_id]) / 2 for paragraph_id in corpus_ids}


def _min_max(scores: Mapping[str, float]) -> dict[str, float]:
    low, high = min(scores.values()), max(scores.values())
    if high == low:
        return {key: 0.0 for key in scores}
    return {key: (value - low) / (high - low) for key, value in scores.items()}


def _rank(scores: Mapping[str, float], corpus_ids: Sequence[str]) -> list[str]:
    # Corpus order is a deterministic final tie-breaker, then ID protects
    # callers whose fixture parser doesn't retain order.
    original_order = {paragraph_id: index for index, paragraph_id in enumerate(corpus_ids)}
    return sorted(scores, key=lambda paragraph_id: (-scores[paragraph_id], original_order[paragraph_id], paragraph_id))


def _query_metrics(ranked: Sequence[str], relevant: set[str]) -> dict[str, float | int]:
    hits = sum(paragraph_id in relevant for paragraph_id in ranked)
    first_relevant_rank = next((index for index, paragraph_id in enumerate(ranked, start=1) if paragraph_id in relevant), None)
    return {
        "precision": hits / len(ranked),
        "recall": hits / len(relevant),
        "mrr": 1 / first_relevant_rank if first_relevant_rank is not None else 0.0,
        "hits": hits,
        "returned": len(ranked),
        "gold": len(relevant),
    }


def _aggregate_metrics(rows: Sequence[Mapping[str, float | int]], *, top_k: int) -> dict[str, Any]:
    query_count = len(rows)
    return {
        "metric_kind": "retrieval",
        "metric_definition": "macro-average retrieval precision@k, recall@k, and reciprocal rank against labelled evidence paragraphs",
        "top_k": top_k,
        "queries": query_count,
        "precision": sum(float(row["precision"]) for row in rows) / query_count,
        "recall": sum(float(row["recall"]) for row in rows) / query_count,
        "mrr": sum(float(row["mrr"]) for row in rows) / query_count,
        "hits_at_k": sum(int(row["hits"]) for row in rows),
        "returned_at_k": sum(int(row["returned"]) for row in rows),
        "gold_relevant_paragraphs": sum(int(row["gold"]) for row in rows),
    }


def _tokens(value: str) -> list[str]:
    return _TOKEN.findall(value.casefold())
