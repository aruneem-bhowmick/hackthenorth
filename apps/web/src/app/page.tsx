"use client";

import { useRef, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type LogEntry = { event: string; data: unknown; at: string };

export default function Home() {
  const [mode, setMode] = useState("before_filing");
  const [jobId, setJobId] = useState<string | null>(null);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  function appendLog(event: string, data: unknown) {
    setLog((prev) => [...prev, { event, data, at: new Date().toLocaleTimeString() }]);
  }

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setLog([]);
    esRef.current?.close();

    const form = e.currentTarget;
    const fileInput = form.elements.namedItem("file") as HTMLInputElement;
    const file = fileInput.files?.[0];
    if (!file) {
      setError("Choose a PDF first.");
      return;
    }

    const body = new FormData();
    body.append("file", file);
    body.append("mode", mode);

    const res = await fetch(`${API_BASE}/api/jobs`, { method: "POST", body });
    if (!res.ok) {
      setError(`Upload failed: HTTP ${res.status}`);
      return;
    }
    const { job_id } = (await res.json()) as { job_id: string };
    setJobId(job_id);

    const es = new EventSource(`${API_BASE}/api/jobs/${job_id}/events`);
    esRef.current = es;
    for (const eventName of ["job.status", "job.completed", "job.failed"]) {
      es.addEventListener(eventName, (ev) => {
        appendLog(eventName, JSON.parse((ev as MessageEvent).data));
        if (eventName === "job.completed" || eventName === "job.failed") {
          es.close();
        }
      });
    }
    es.onerror = () => appendLog("connection.error", null);
  }

  return (
    <main style={{ fontFamily: "sans-serif", maxWidth: 640, margin: "2rem auto", padding: "0 1rem" }}>
      <h1>Pincite — P0 skeleton</h1>
      <p>
        Minimum P0 flow (SPEC.md §4): upload a PDF, watch the job move through{" "}
        <code>queued → processing → completed</code> over SSE. The real review UI
        (highlights, split pane, verdicts) is P1+.
      </p>

      <form onSubmit={handleSubmit}>
        <div>
          <label>
            PDF: <input type="file" name="file" accept="application/pdf" />
          </label>
        </div>
        <div style={{ marginTop: 8 }}>
          <label>
            Mode:{" "}
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="before_filing">Before you file</option>
              <option value="answering_brief">Answering a brief</option>
            </select>
          </label>
        </div>
        <button type="submit" style={{ marginTop: 8 }}>
          Upload
        </button>
      </form>

      {error && <p style={{ color: "crimson" }}>{error}</p>}

      {jobId && (
        <p>
          Job: <code>{jobId}</code>
        </p>
      )}

      <ul>
        {log.map((entry, i) => (
          <li key={i}>
            <code>{entry.at}</code> — <strong>{entry.event}</strong>{" "}
            {entry.data ? JSON.stringify(entry.data) : ""}
          </li>
        ))}
      </ul>
    </main>
  );
}
