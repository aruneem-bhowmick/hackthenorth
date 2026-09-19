"""P0 exit-gate smoke test (SPEC.md §4, phase P0 "Skeleton").

Exercises the real HTTP/SSE surface end-to-end: upload a PDF, follow the job
through queued -> processing -> completed, confirm job.completed arrives
over SSE. Does NOT start the stack itself — run `docker compose up` first.

    uv run pytest tests/e2e/test_p0_hello_job.py
"""

import httpx

API_BASE = "http://localhost:8000"

# Small, valid text-layer PDF. P1 correctly rejects PDFs with no extractable
# text (FR-ING-004), so the inherited P0 hello-job test must use a real text
# layer rather than an empty page.
_MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n"
    b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
    b"5 0 obj\n<< /Length 48 >>\nstream\nBT /F1 12 Tf 72 720 Td (Pincite smoke test.) Tj ET\nendstream\nendobj\n"
    b"xref\n0 6\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000241 00000 n \n0000000311 00000 n \n"
    b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n411\n%%EOF\n"
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
