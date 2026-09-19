"""P0 exit-gate smoke test (SPEC.md §4, phase P0 "Skeleton").

Exercises the real HTTP/SSE surface end-to-end: upload a PDF, follow the job
through queued -> processing -> completed, confirm job.completed arrives
over SSE. Does NOT start the stack itself — run `docker compose up` first.

    uv run pytest tests/e2e/test_p0_hello_job.py
"""

import httpx

API_BASE = "http://localhost:8000"

# Smallest structurally-valid PDF: catalog -> pages -> one empty page.
_MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n"
    b"%%EOF"
)


def test_p0_hello_job() -> None:
    with httpx.Client(base_url=API_BASE, timeout=30.0) as client:
        health = client.get("/api/health")
        assert health.status_code == 200, "API not reachable — is `docker compose up` running?"

        resp = client.post(
            "/api/jobs",
            files={"file": ("smoke.pdf", _MINIMAL_PDF, "application/pdf")},
            data={"mode": "before_filing"},
        )
        assert resp.status_code == 200, resp.text
        job_id = resp.json()["job_id"]

        events_seen: list[str] = []
        with client.stream("GET", f"/api/jobs/{job_id}/events") as stream:
            event_name: str | None = None
            for line in stream.iter_lines():
                if line.startswith("event: "):
                    event_name = line.removeprefix("event: ")
                elif line.startswith("data: ") and event_name:
                    events_seen.append(event_name)
                    if event_name in ("job.completed", "job.failed"):
                        break
                    event_name = None

        assert "job.completed" in events_seen, f"never saw job.completed, saw: {events_seen}"

        status = client.get(f"/api/jobs/{job_id}")
        assert status.status_code == 200
        assert status.json()["status"] == "completed"
