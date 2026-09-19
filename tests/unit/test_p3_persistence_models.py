"""P3 durable-schema contracts shared by API and worker services."""

import hashlib
import uuid

import pytest
from app.db import InvestigatorRun as ApiInvestigatorRun
from app.db import Provenance as ApiProvenance
from worker.db import InvestigatorRun as WorkerInvestigatorRun
from worker.db import Provenance as WorkerProvenance
from worker.db import Source, SourceAcquisition
from worker.tasks import _ensure_courtlistener_provenance


def test_api_and_worker_keep_the_p3_tables_in_lockstep() -> None:
    for api_model, worker_model, expected_columns in (
        (
            ApiProvenance,
            WorkerProvenance,
            {
                "id",
                "source_id",
                "url",
                "retrieved_at",
                "method",
                "sha256",
                "snapshot_ref",
                "session_ref",
            },
        ),
        (
            ApiInvestigatorRun,
            WorkerInvestigatorRun,
            {
                "id",
                "citation_id",
                "status",
                "searches",
                "fetches",
                "started_at",
                "ended_at",
                "live_view_url",
                "outcome",
            },
        ),
    ):
        assert set(api_model.__table__.columns.keys()) == expected_columns
        assert set(worker_model.__table__.columns.keys()) == expected_columns


def test_p3_rows_are_uniquely_owned_by_their_source_or_citation() -> None:
    assert ApiProvenance.__table__.c.source_id.unique is True
    assert ApiInvestigatorRun.__table__.c.citation_id.unique is True


class _Savepoint:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class _ProvenanceSession:
    def __init__(self, existing_id: uuid.UUID | None = None) -> None:
        self.existing_id = existing_id
        self.added: list[object] = []

    async def scalar(self, _statement):
        return self.existing_id

    def begin_nested(self) -> _Savepoint:
        return _Savepoint()

    def add(self, item: object) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_courtlistener_source_persistence_adds_sha256_provenance() -> None:
    source = Source(id=uuid.uuid4(), kind="opinion", text="Stored opinion text")
    acquisition = SourceAcquisition(
        cluster_id="42",
        source_url="https://www.courtlistener.com/api/rest/v4/clusters/42/",
    )
    session = _ProvenanceSession()

    await _ensure_courtlistener_provenance(session, source, acquisition)

    assert len(session.added) == 1
    provenance = session.added[0]
    assert isinstance(provenance, WorkerProvenance)
    assert provenance.source_id == source.id
    assert provenance.url == acquisition.source_url
    assert provenance.method == "courtlistener"
    assert provenance.sha256 == hashlib.sha256(source.text.encode("utf-8")).hexdigest()
    assert provenance.retrieved_at == acquisition.retrieved_at


@pytest.mark.asyncio
async def test_existing_courtlistener_provenance_is_not_replaced() -> None:
    source = Source(id=uuid.uuid4(), kind="opinion", text="Stored opinion text")
    acquisition = SourceAcquisition(cluster_id="42")
    session = _ProvenanceSession(existing_id=uuid.uuid4())

    await _ensure_courtlistener_provenance(session, source, acquisition)

    assert session.added == []
