"use client";

import { ChangeEvent, FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import styles from "./page.module.css";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type Mode = "before_filing" | "answering_brief";
type Finding = {
  id: string;
  check: string;
  verdict: string;
  confidence: number | null;
  notes: string[];
  evidence: Record<string, unknown>;
};
type Claim = {
  id: string;
  proposition_text: string | null;
  quote_text: string | null;
  quote_start: number | null;
  quote_end: number | null;
};
type Citation = {
  id: string;
  raw_text: string;
  normalized: string;
  kind: string;
  page: number | null;
  start_offset: number | null;
  end_offset: number | null;
  pinpoint: string | null;
  case_name: string | null;
  court_hint: string | null;
  year_hint: number | null;
  resolution_state: string | null;
  source_state: "queued" | "fetching" | "fetched" | "unavailable" | null;
  claims: Claim[];
  findings: Finding[];
};
type SourceParagraph = {
  id: string;
  opinion_part: string | null;
  para_no: number;
  page: number | null;
  text: string;
};
type Source = {
  id: string;
  case_name: string | null;
  court: string | null;
  decision_date: string | null;
  text: string | null;
  paragraphs: SourceParagraph[];
};
type DiffItem = {
  operation: "equal" | "insert" | "delete" | "replace";
  quote_tokens: string[];
  source_tokens: string[];
};
type CitationSpan = { id: string; start: number; end: number };
type BriefPage = { page: number; text: string; citations: CitationSpan[] };

function verdictDetails(verdict?: string) {
  const details: Record<string, { label: string; tone: "green" | "yellow" | "red" | "grey" }> = {
    VERIFIED: { label: "Case located", tone: "green" },
    AMBIGUOUS: { label: "More than one possible case", tone: "yellow" },
    NOT_IN_DATABASE: { label: "Not located in CourtListener", tone: "grey" },
    UNRECOGNIZED: { label: "Citation could not be recognised", tone: "grey" },
    PENDING: { label: "Checking source", tone: "grey" },
    VERBATIM: { label: "Quote matches source", tone: "green" },
    VERBATIM_WITH_PERMITTED_ALTERATIONS: { label: "Quote matches; legal alteration used", tone: "green" },
    ALTERED: { label: "Quote differs from source", tone: "yellow" },
    PARAPHRASE_IN_QUOTES: { label: "Quoted text appears paraphrased", tone: "yellow" },
    NOT_FOUND_IN_SOURCE: { label: "No matching language found", tone: "red" },
    SOURCE_UNAVAILABLE: { label: "Source unavailable", tone: "grey" },
  };
  return details[verdict ?? ""] ?? { label: verdict?.replaceAll("_", " ") || "Awaiting check", tone: "grey" as const };
}

function worstTone(findings: Finding[]) {
  const rank = { green: 1, grey: 2, yellow: 3, red: 4 } as const;
  return findings.reduce<"green" | "yellow" | "red" | "grey">((worst, finding) => {
    const candidate = verdictDetails(finding.verdict).tone;
    return rank[candidate] > rank[worst] ? candidate : worst;
  }, "grey");
}

function sourceIdFrom(findings: Finding[]) {
  for (const finding of findings) {
    const evidence = finding.evidence;
    const candidate = evidence.source_id ?? evidence.sourceId;
    if (typeof candidate === "string") return candidate;
    if (typeof evidence.source === "object" && evidence.source && "id" in evidence.source) {
      const id = (evidence.source as { id?: unknown }).id;
      if (typeof id === "string") return id;
    }
  }
  return null;
}

function paragraphIdFrom(findings: Finding[]) {
  for (const finding of findings) {
    const candidate = finding.evidence.paragraph_id ?? finding.evidence.paragraphId;
    if (typeof candidate === "string") return candidate;
    const paragraphs = finding.evidence.paragraphs;
    if (Array.isArray(paragraphs) && typeof paragraphs[0] === "object" && paragraphs[0] && "para_id" in paragraphs[0]) {
      const id = (paragraphs[0] as { para_id?: unknown }).para_id;
      if (typeof id === "string") return id;
    }
    const closest = finding.evidence.closest_actual_language;
    if (typeof closest === "object" && closest && "paragraph_id" in closest) {
      const id = (closest as { paragraph_id?: unknown }).paragraph_id;
      if (typeof id === "string") return id;
    }
  }
  return null;
}

function quoteFinding(citation: Citation | null) {
  return citation?.findings.find((finding) => finding.check === "quote" || finding.check === "QTE" || finding.check === "quote_fidelity") ?? null;
}

function readDiff(finding: Finding | null): DiffItem[] {
  const diff = finding?.evidence.diff;
  if (!Array.isArray(diff)) return [];
  return diff.flatMap((item) => {
    if (typeof item !== "object" || !item) return [];
    const candidate = item as { op?: unknown; operation?: unknown; quote_tokens?: unknown; source_tokens?: unknown };
    const operation = candidate.operation ?? candidate.op;
    if (operation !== "equal" && operation !== "insert" && operation !== "delete" && operation !== "replace") return [];
    return [{
      operation,
      quote_tokens: Array.isArray(candidate.quote_tokens) ? candidate.quote_tokens.filter((token): token is string => typeof token === "string") : [],
      source_tokens: Array.isArray(candidate.source_tokens) ? candidate.source_tokens.filter((token): token is string => typeof token === "string") : [],
    }];
  });
}

function AnnotatedPage({
  page,
  citationsById,
  selectedCitationId,
  onCitation,
}: {
  page: BriefPage;
  citationsById: Map<string, Citation>;
  selectedCitationId: string | null;
  onCitation: (citation: Citation) => void;
}) {
  const parts: ReactNode[] = [];
  let cursor = 0;
  for (const span of [...page.citations].sort((left, right) => left.start - right.start || right.end - left.end)) {
    const start = Math.max(cursor, Math.min(span.start, page.text.length));
    const end = Math.max(start, Math.min(span.end, page.text.length));
    if (start > cursor) parts.push(page.text.slice(cursor, start));
    const citation = citationsById.get(span.id);
    const tone = citation ? worstTone(citation.findings) : "grey";
    parts.push(citation ? (
      <button
        aria-pressed={span.id === selectedCitationId}
        className={`${styles.briefHighlight} ${styles[`tone${tone[0].toUpperCase()}${tone.slice(1)}`]}`}
        key={span.id}
        onClick={() => onCitation(citation)}
        type="button"
      >
        {page.text.slice(start, end)}
      </button>
    ) : <mark className={styles.briefPending} key={span.id}>{page.text.slice(start, end)}</mark>);
    cursor = end;
  }
  if (cursor < page.text.length) parts.push(page.text.slice(cursor));
  return <section className={styles.briefPage} aria-label={`Extracted page ${page.page}`}>
    <h3>Page {page.page}</h3>
    <div className={styles.briefText}>{parts}</div>
  </section>;
}

export default function Home() {
  const [mode, setMode] = useState<Mode>("before_filing");
  const [file, setFile] = useState<File | null>(null);
  const [briefUrl, setBriefUrl] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState("Ready for a PDF");
  const [citations, setCitations] = useState<Citation[]>([]);
  const [briefPages, setBriefPages] = useState<BriefPage[]>([]);
  const [selectedCitationId, setSelectedCitationId] = useState<string | null>(null);
  const [source, setSource] = useState<Source | null>(null);
  const [sourceStatus, setSourceStatus] = useState("Select a citation to inspect its source.");
  const [error, setError] = useState<string | null>(null);
  const eventSource = useRef<EventSource | null>(null);
  const sourcePane = useRef<HTMLDivElement | null>(null);

  const selectedCitation = useMemo(
    () => citations.find((citation) => citation.id === selectedCitationId) ?? null,
    [citations, selectedCitationId],
  );
  const selectedQuoteFinding = quoteFinding(selectedCitation);
  const citationsById = useMemo(() => new Map(citations.map((citation) => [citation.id, citation])), [citations]);

  useEffect(() => {
    return () => {
      eventSource.current?.close();
      if (briefUrl) URL.revokeObjectURL(briefUrl);
    };
  }, [briefUrl]);

  async function refreshCitations(activeJobId: string) {
    const response = await fetch(`${API_BASE}/api/jobs/${activeJobId}/citations`);
    if (!response.ok) throw new Error(`Could not refresh citations (HTTP ${response.status}).`);
    const data = (await response.json()) as { citations: Citation[] };
    setCitations(data.citations);
    setSelectedCitationId((current) => current ?? data.citations[0]?.id ?? null);
  }

  async function refreshBriefPages(activeJobId: string, reviewToken: string) {
    const response = await fetch(`${API_BASE}/api/jobs/${activeJobId}/pages`, {
      headers: { "X-Pincite-Review-Token": reviewToken },
    });
    if (!response.ok) throw new Error(`Could not load extracted brief text (HTTP ${response.status}).`);
    const data = (await response.json()) as { pages: BriefPage[] };
    setBriefPages(data.pages);
  }

  async function loadSource(citation: Citation) {
    const sourceId = sourceIdFrom(citation.findings);
    const paragraphId = paragraphIdFrom(citation.findings);
    setSelectedCitationId(citation.id);
    setSource(null);

    if (!sourceId) {
      if (citation.source_state === "queued" || citation.source_state === "fetching") {
        setSourceStatus("Authority verified; source text loading.");
      } else if (citation.source_state === "unavailable") {
        setSourceStatus("Authority verified, but source text was unavailable for quote comparison.");
      } else {
        setSourceStatus("This citation has no completed source evidence yet. Its result will appear here as checking finishes.");
      }
      return;
    }

    setSourceStatus("Loading source text…");
    try {
      const response = await fetch(`${API_BASE}/api/sources/${sourceId}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const nextSource = (await response.json()) as Source;
      setSource(nextSource);
      setSourceStatus("Source text loaded.");
      window.requestAnimationFrame(() => {
        const target = paragraphId ? document.getElementById(`source-paragraph-${paragraphId}`) : sourcePane.current;
        target?.scrollIntoView({ behavior: "smooth", block: "center" });
      });
    } catch {
      setSourceStatus("The source text could not be loaded. The citation result remains available for review.");
    }
  }

  function connectEvents(activeJobId: string, reviewToken: string) {
    eventSource.current?.close();
    const stream = new EventSource(`${API_BASE}/api/jobs/${activeJobId}/events`);
    eventSource.current = stream;

    stream.addEventListener("job.status", (event) => {
      const data = JSON.parse((event as MessageEvent<string>).data) as { status?: string };
      setJobStatus(data.status ? `Job ${data.status.replaceAll("_", " ")}` : "Job started");
    });
    stream.addEventListener("citations.extracted", () => {
      setJobStatus("Citations extracted — source checks are running.");
      void refreshCitations(activeJobId).catch((refreshError: unknown) => setError(String(refreshError)));
      void refreshBriefPages(activeJobId, reviewToken).catch((refreshError: unknown) => setError(String(refreshError)));
    });
    stream.addEventListener("finding.created", () => {
      setJobStatus("A citation result just arrived.");
      void refreshCitations(activeJobId).catch((refreshError: unknown) => setError(String(refreshError)));
    });
    for (const terminalEvent of ["job.completed", "job.failed"]) {
      stream.addEventListener(terminalEvent, () => {
        setJobStatus(terminalEvent === "job.completed" ? "Review complete" : "Review ended with an issue");
        void refreshCitations(activeJobId).catch((refreshError: unknown) => setError(String(refreshError)));
        stream.close();
      });
    }
    stream.onerror = () => setJobStatus("Connection interrupted — the latest saved review is still available.");
  }

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const nextFile = event.target.files?.[0] ?? null;
    if (briefUrl) URL.revokeObjectURL(briefUrl);
    setFile(nextFile);
    setBriefUrl(nextFile ? URL.createObjectURL(nextFile) : null);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      setError("Choose a PDF before starting the review.");
      return;
    }
    setError(null);
    setCitations([]);
    setBriefPages([]);
    setSelectedCitationId(null);
    setSource(null);
    setSourceStatus("Citations will appear here as they are extracted.");
    setJobStatus("Uploading brief…");

    const body = new FormData();
    body.append("file", file);
    body.append("mode", mode);
    try {
      const response = await fetch(`${API_BASE}/api/jobs`, { method: "POST", body });
      if (!response.ok) throw new Error(`Upload failed (HTTP ${response.status}).`);
      const data = (await response.json()) as { job_id: string; review_token: string };
      setJobId(data.job_id);
      setJobStatus("Brief uploaded — preparing review.");
      connectEvents(data.job_id, data.review_token);
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "The upload could not be completed.");
      setJobStatus("Ready for a PDF");
    }
  }

  const activeParagraphId = selectedCitation ? paragraphIdFrom(selectedCitation.findings) : null;
  const diff = readDiff(selectedQuoteFinding);

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>Pincite · P1 review</p>
          <h1>Check cited authority against the source text.</h1>
        </div>
        <p className={styles.disclaimer}>Pincite reports differences between a document and the sources it cites. It is not legal advice and does not assess anyone&apos;s intent. Always review the linked source text yourself.</p>
      </header>

      <section className={styles.uploadCard} aria-labelledby="upload-heading">
        <div>
          <h2 id="upload-heading">Start a brief review</h2>
          <p>Upload a federal brief. Pincite will identify citations and show the source material it checked.</p>
        </div>
        <form className={styles.form} onSubmit={handleSubmit}>
          <fieldset className={styles.modeChoices}>
            <legend>What are you reviewing?</legend>
            <label className={mode === "before_filing" ? styles.modeSelected : undefined}>
              <input checked={mode === "before_filing"} name="mode" onChange={() => setMode("before_filing")} type="radio" value="before_filing" />
              <span>Before you file</span>
            </label>
            <label className={mode === "answering_brief" ? styles.modeSelected : undefined}>
              <input checked={mode === "answering_brief"} name="mode" onChange={() => setMode("answering_brief")} type="radio" value="answering_brief" />
              <span>Answering a brief</span>
            </label>
          </fieldset>
          <label className={styles.filePicker}>
            <span>PDF brief</span>
            <input accept="application/pdf" onChange={chooseFile} type="file" />
            <strong>{file ? file.name : "Choose a PDF"}</strong>
          </label>
          <button className={styles.primaryButton} disabled={!file} type="submit">Review citations</button>
        </form>
        {error && <p className={styles.error} role="alert">{error}</p>}
      </section>

      <section className={styles.statusBar} aria-live="polite">
        <span className={styles.statusDot} aria-hidden="true" />
        <span>{jobStatus}</span>
        {jobId && <code>Review {jobId.slice(0, 8)}</code>}
      </section>

      <section className={styles.reviewGrid} aria-label="Citation review">
        <article className={styles.briefPane}>
          <div className={styles.paneHeader}>
            <div>
              <p className={styles.eyebrow}>Your brief</p>
              <h2>{file?.name ?? "Upload a brief to begin"}</h2>
            </div>
            <span>{citations.length} citation{citations.length === 1 ? "" : "s"}</span>
          </div>

          {briefPages.length ? <div className={styles.annotatedBrief}>
            {briefPages.map((page) => <AnnotatedPage
              citationsById={citationsById}
              key={page.page}
              onCitation={(citation) => void loadSource(citation)}
              page={page}
              selectedCitationId={selectedCitationId}
            />)}
          </div> : <p className={styles.emptyState}>{file ? "Extracting reviewable text and citation spans…" : "Upload a brief to view its annotated text."}</p>}

          <div className={styles.citationList} aria-label="Extracted citations">
            {citations.map((citation) => {
              const selected = citation.id === selectedCitationId;
              const tone = worstTone(citation.findings);
              return (
                <button
                  aria-pressed={selected}
                  className={`${styles.citationCard} ${styles[`tone${tone[0].toUpperCase()}${tone.slice(1)}`]}`}
                  key={citation.id}
                  onClick={() => void loadSource(citation)}
                  type="button"
                >
                  <span className={styles.citationTitle}>{citation.case_name ?? citation.raw_text}</span>
                  <span className={styles.citationMeta}>Page {citation.page ?? "—"}{citation.pinpoint ? ` · pinpoint ${citation.pinpoint}` : ""}</span>
                  <span className={styles.verdictRow}>
                    {citation.findings.length ? citation.findings.map((finding) => {
                      const details = verdictDetails(finding.verdict);
                      return <span className={`${styles.verdict} ${styles[`verdict${details.tone[0].toUpperCase()}${details.tone.slice(1)}`]}`} key={finding.id}>{details.label}</span>;
                    }) : <span className={`${styles.verdict} ${styles.verdictGrey}`}>Awaiting result</span>}
                  </span>
                </button>
              );
            })}
            {jobId && citations.length === 0 && <p className={styles.emptyState}>Looking for citations. Results will appear individually as they are saved.</p>}
          </div>
        </article>

        <article className={styles.sourcePane} ref={sourcePane}>
          <div className={styles.paneHeader}>
            <div>
              <p className={styles.eyebrow}>Source evidence</p>
              <h2>{source?.case_name ?? "Select a citation"}</h2>
            </div>
            {source?.court && <span>{source.court}</span>}
          </div>

          {!selectedCitation && <p className={styles.emptyState}>{sourceStatus}</p>}
          {selectedCitation && (
            <>
              <section className={styles.citationContext} aria-label="Selected citation">
                <p><strong>Citation:</strong> {selectedCitation.raw_text}</p>
                {selectedCitation.claims[0]?.quote_text && <p><strong>Quoted in brief:</strong> “{selectedCitation.claims[0].quote_text}”</p>}
              </section>

              {selectedQuoteFinding && <section className={styles.diffCard} aria-labelledby="diff-heading">
                <h3 id="diff-heading">Quote comparison</h3>
                <p className={`${styles.verdict} ${styles[`verdict${verdictDetails(selectedQuoteFinding.verdict).tone[0].toUpperCase()}${verdictDetails(selectedQuoteFinding.verdict).tone.slice(1)}`]}`}>{verdictDetails(selectedQuoteFinding.verdict).label}</p>
                {diff.length > 0 ? <div className={styles.diff} aria-label="Word-level quote diff">
                  {diff.map((item, index) => <span className={styles[`diff${item.operation[0].toUpperCase()}${item.operation.slice(1)}`]} key={`${item.operation}-${index}`}>
                    {item.operation === "insert" ? item.source_tokens.join(" ") : item.quote_tokens.join(" ")}
                    {" "}
                  </span>)}
                </div> : <p className={styles.muted}>A word-level comparison will appear when source evidence is available.</p>}
                {selectedQuoteFinding.notes.length > 0 && <p className={styles.muted}>{selectedQuoteFinding.notes.join(" · ")}</p>}
              </section>}

              <p className={styles.sourceStatus} aria-live="polite">{sourceStatus}</p>
              {source && <div className={styles.sourceText}>
                {source.paragraphs.map((paragraph) => <p className={paragraph.id === activeParagraphId ? styles.activeParagraph : undefined} id={`source-paragraph-${paragraph.id}`} key={paragraph.id}>
                  <span className={styles.paragraphMarker}>¶ {paragraph.para_no}{paragraph.page ? ` · p. ${paragraph.page}` : ""}</span>
                  {paragraph.text}
                </p>)}
                {!source.paragraphs.length && source.text && <p>{source.text}</p>}
              </div>}
            </>
          )}
        </article>
      </section>
    </main>
  );
}
