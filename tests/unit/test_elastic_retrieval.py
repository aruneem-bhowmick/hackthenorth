from __future__ import annotations

from types import SimpleNamespace

import pytest

from worker.elastic import (
    EMBEDDING_DIMS,
    FINDINGS_INDEX,
    PARAGRAPHS_INDEX,
    ElasticUnavailable,
    ensure_indices,
    index_source_paragraphs,
)
from worker.retrieval import (
    RetrievedParagraph,
    reciprocal_rank_fusion,
    retrieve_paragraphs,
    retrieve_quote_candidates,
)


class FakeIndices:
    def __init__(self) -> None:
        self.created: list[tuple[str, dict]] = []

    async def exists(self, *, index: str) -> bool:
        return any(created_index == index for created_index, _ in self.created)

    async def create(self, *, index: str, mappings: dict) -> None:
        self.created.append((index, mappings))


class FakeElastic:
    def __init__(self) -> None:
        self.indices = FakeIndices()
        self.bulk_operations: list[dict] | None = None
        self.search_calls: list[dict] = []

    async def bulk(self, *, operations: list[dict], refresh: bool) -> dict:
        self.bulk_operations = operations
        return {"errors": False}

    async def search(self, **kwargs: object) -> dict:
        self.search_calls.append(dict(kwargs))
        if "knn" in kwargs:
            return {"hits": {"hits": [{"_source": {"para_no": 2, "opinion_part": "majority", "text": "semantic"}}]}}
        return {
            "hits": {
                "hits": [
                    {"_source": {"para_no": 1, "opinion_part": "unknown", "page": 4, "text": "lexical"}},
                    {"_source": {"para_no": 2, "opinion_part": "majority", "text": "semantic"}},
                ]
            }
        }


@pytest.mark.asyncio
async def test_indices_are_idempotent_and_use_p2_paragraph_mapping() -> None:
    client = FakeElastic()

    await ensure_indices(client)
    await ensure_indices(client)

    assert [index for index, _ in client.indices.created] == [PARAGRAPHS_INDEX, FINDINGS_INDEX]
    paragraph_mapping = client.indices.created[0][1]["properties"]
    assert paragraph_mapping["text"]["type"] == "text"
    assert paragraph_mapping["embedding"] == {
        "type": "dense_vector",
        "dims": EMBEDDING_DIMS,
        "index": True,
        "similarity": "cosine",
    }


@pytest.mark.asyncio
async def test_paragraph_indexing_has_stable_ids_and_never_assumes_majority() -> None:
    client = FakeElastic()
    source = SimpleNamespace(id="src_123", case_name="Example", court="Test Court")
    paragraphs = [
        SimpleNamespace(para_no=1, opinion_part=None, page=None, text="First."),
        SimpleNamespace(para_no=2, opinion_part="dissent", page=7, text="Second."),
    ]

    await index_source_paragraphs(
        client,
        source,
        paragraphs,
        embeddings_by_para={1: [0.1] * EMBEDDING_DIMS, 2: [0.2] * EMBEDDING_DIMS},
    )

    assert client.bulk_operations is not None
    assert client.bulk_operations[0] == {"index": {"_index": PARAGRAPHS_INDEX, "_id": "src_123:p1"}}
    assert client.bulk_operations[1]["opinion_part"] == "unknown"
    assert client.bulk_operations[3]["opinion_part"] == "dissent"


@pytest.mark.asyncio
async def test_hybrid_retrieval_scopes_both_queries_and_fuses_rrf() -> None:
    client = FakeElastic()

    paragraphs = await retrieve_paragraphs(
        client,
        "src_123",
        "the proposition",
        2,
        query_embedding=[0.5] * EMBEDDING_DIMS,
    )

    assert [paragraph.para_id for paragraph in paragraphs] == ["src_123:p2", "src_123:p1"]
    assert len(client.search_calls) == 2
    assert client.search_calls[0]["query"]["bool"]["filter"] == [{"term": {"source_id": "src_123"}}]
    assert client.search_calls[1]["knn"]["filter"] == {"term": {"source_id": "src_123"}}


@pytest.mark.asyncio
async def test_quote_candidates_are_bm25_only_and_source_scoped() -> None:
    client = FakeElastic()

    paragraphs = await retrieve_quote_candidates(client, "src_123", "quoted language", limit=24)

    assert [paragraph.para_id for paragraph in paragraphs] == ["src_123:p1", "src_123:p2"]
    assert len(client.search_calls) == 1
    assert "knn" not in client.search_calls[0]
    assert client.search_calls[0]["query"]["bool"]["filter"] == [{"term": {"source_id": "src_123"}}]


def test_rrf_is_deterministic_and_deduplicates_paragraphs() -> None:
    first = RetrievedParagraph("src:p1", "majority", "one", 1, None)
    second = RetrievedParagraph("src:p2", "majority", "two", 2, None)

    result = reciprocal_rank_fusion([first, second], [second, first], top_k=2)

    assert [paragraph.para_id for paragraph in result] == ["src:p1", "src:p2"]


@pytest.mark.asyncio
async def test_wrong_dimension_is_an_optional_indexing_failure() -> None:
    client = FakeElastic()
    source = SimpleNamespace(id="src_123", case_name=None, court=None)
    paragraph = SimpleNamespace(para_no=1, opinion_part=None, page=None, text="First.")

    with pytest.raises(ElasticUnavailable):
        await index_source_paragraphs(client, source, [paragraph], embeddings_by_para={1: [1.0]})
