"use client";

import { ChangeEvent, FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { ExportButtons } from "../components/export-buttons";
import { CitationSignals, Signal, SignalHeatStrip } from "../components/signal-display";
import { CheckFilter, citationMatchesFilters, SummaryPanel, ToneFilter } from "../components/summary-panel";
import styles from "./page.module.css";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type Mode = "before_filing" | "answering_brief";
type Finding = {
  id: string;
  check: string;
  verdict: string;
  confidence: number | null;
  notes: string[];
  rationale: string | null;
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
  signals: Signal[];
};
type SourceParagraph = {
  id: string;
  opinion_part: string | null;
  para_no: number;
  page: number | null;
  text: string;
};
type Provenance = {
  url: string | null;
  retrieved_at: string;
  method: string;
  sha256: string;
  snapshot_ref: string | null;
  session_ref: string | null;
};
type Source = {
  id: string;
  case_name: string | null;
  court: string | null;
  decision_date: string | null;
  text: string | null;
  paragraphs: SourceParagraph[];
  provenance: Provenance | null;
};
type DiffItem = {
  operation: "equal" | "insert" | "delete" | "replace";
  quote_tokens: string[];
  source_tokens: string[];
};
type CitationSpan = { id: string; start: number; end: number };
type BriefPage = { page: number; text: string; citations: CitationSpan[] };
type VerdictDetails = {
  label: string;
  tone: "green" | "yellow" | "red" | "grey";
  tooltip: string;
};

function verdictDetails(verdict?: string): VerdictDetails {
  const details: Record<string, VerdictDetails> = {
    VERIFIED: { label: "Case located", tone: "green", tooltip: "PinCite found a matching record for this citation in CourtListener." },
    VERIFIED_OFFICIAL: { label: "Verified · official source", tone: "green", tooltip: "PinCite found this case on an official court or government source after checking beyond CourtListener." },
    WEAKLY_CORROBORATED: { label: "Mention found; source not verified", tone: "yellow", tooltip: "PinCite found a matching mention but could not verify the opinion from an official source." },
    NOT_FOUND_ANYWHERE: { label: "Could not be located anywhere", tone: "red", tooltip: "PinCite checked CourtListener and completed a bounded search for an official source without locating this citation." },
    AMBIGUOUS: { label: "More than one possible case", tone: "yellow", tooltip: "PinCite found more than one possible source and could not choose one reliably." },
    NOT_IN_DATABASE: { label: "Not located in CourtListener", tone: "grey", tooltip: "PinCite did not find a matching record in CourtListener." },
    UNRECOGNIZED: { label: "Citation could not be recognised", tone: "grey", tooltip: "PinCite could not read this citation well enough to check it." },
    PENDING: { label: "Searching the web", tone: "grey", tooltip: "PinCite is searching for an official source before reaching an existence result." },
    VERBATIM: { label: "Quote matches source", tone: "green", tooltip: "The quoted words match the source passage PinCite checked." },
    VERBATIM_WITH_PERMITTED_ALTERATIONS: { label: "Quote matches; legal alteration used", tone: "green", tooltip: "The quote matches the source, including a standard bracket or ellipsis alteration." },
    ALTERED: { label: "Quote differs from source", tone: "yellow", tooltip: "Some quoted wording differs from the source passage. Review the linked source text." },
    PARAPHRASE_IN_QUOTES: { label: "Quoted text appears paraphrased", tone: "yellow", tooltip: "The quoted wording appears similar to, but does not match, source language." },
    NOT_FOUND_IN_SOURCE: { label: "No matching language found", tone: "red", tooltip: "PinCite did not find the quoted language in the source material it checked." },
    SOURCE_UNAVAILABLE: { label: "Source unavailable", tone: "grey", tooltip: "PinCite could not obtain source text for this comparison." },
    SUPPORTS: { label: "Source supports this point", tone: "green", tooltip: "The cited source passages directly support the point PinCite checked." },
    PARTIAL: { label: "Source partly supports this point", tone: "yellow", tooltip: "The source passages address only part of the point, or come from a non-majority opinion." },
    CONTRADICTS: { label: "Source points the other way", tone: "red", tooltip: "The cited source passages point in a different direction from the point PinCite checked." },
    NOT_ADDRESSED: { label: "Source does not address this point", tone: "yellow", tooltip: "The retrieved source passages do not discuss the point PinCite checked." },
    UNVERIFIABLE: { label: "Support could not be verified", tone: "grey", tooltip: "PinCite could not validate this result from the available source evidence." },
  };
  return details[verdict ?? ""] ?? {
    label: verdict?.replaceAll("_", " ") || "Awaiting check",
    tone: "grey",
    tooltip: "PinCite does not yet have a plain-language explanation for this result.",
  };
}

function VerdictBadge({ verdict }: { verdict?: string }) {
  const details = verdictDetails(verdict);
  return <span
    aria-label={`${details.label}. ${details.tooltip}`}
    className={`${styles.verdict} ${styles[`verdict${details.tone[0].toUpperCase()}${details.tone.slice(1)}`]}`}
    title={details.tooltip}
  >{details.label}</span>;
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

function citedParagraphIdsFrom(findings: Finding[]) {
  const ids = new Set<string>();
  for (const finding of findings) {
    const cited = finding.evidence.cited_paragraph_ids ?? finding.evidence.citedParagraphIds;
    if (Array.isArray(cited)) {
      for (const id of cited) if (typeof id === "string") ids.add(id);
    }
    const candidate = finding.evidence.paragraph_id ?? finding.evidence.paragraphId;
    if (typeof candidate === "string") ids.add(candidate);
    const paragraphs = finding.evidence.paragraphs;
    if (Array.isArray(paragraphs)) {
      for (const paragraph of paragraphs) {
        if (typeof paragraph !== "object" || !paragraph || !("para_id" in paragraph)) continue;
        const id = (paragraph as { para_id?: unknown }).para_id;
        if (typeof id === "string") ids.add(id);
      }
    }
    const closest = finding.evidence.closest_actual_language;
    if (typeof closest === "object" && closest && "paragraph_id" in closest) {
      const id = (closest as { paragraph_id?: unknown }).paragraph_id;
      if (typeof id === "string") ids.add(id);
    }
  }
  return [...ids];
}

function sourceParagraphIdsFor(source: Source, evidenceIds: string[]) {
  return evidenceIds.flatMap((evidenceId) => {
    const directMatch = source.paragraphs.find((paragraph) => paragraph.id === evidenceId);
    if (directMatch) return [directMatch.id];

    // Elastic's paragraph document IDs are stable `source_id:p{para_no}`
    // values. The source API exposes the persisted paragraph UUID, so map the
    // former to the latter before scrolling or applying a source highlight.
    const paraNo = /:p(\d+)$/.exec(evidenceId)?.[1];
    const paragraph = paraNo ? source.paragraphs.find((item) => item.para_no === Number(paraNo)) : null;
    return paragraph ? [paragraph.id] : [];
  });
}

function quoteFinding(citation: Citation | null) {
  return citation?.findings.find((finding) => finding.check === "quote" || finding.check === "QTE" || finding.check === "quote_fidelity") ?? null;
}

function propositionFinding(citation: Citation | null) {
  return citation?.findings.find((finding) => finding.check === "proposition") ?? null;
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
  signals,
}: {
  page: BriefPage;
  citationsById: Map<string, Citation>;
  selectedCitationId: string | null;
  onCitation: (citation: Citation) => void;
  signals: Signal[];
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
    <SignalHeatStrip page={page.page} signals={signals} />
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
  const [pageSignals, setPageSignals] = useState<Signal[]>([]);
  const [selectedCitationId, setSelectedCitationId] = useState<string | null>(null);
  const [source, setSource] = useState<Source | null>(null);
  const [activeInvestigations, setActiveInvestigations] = useState<Record<string, string | null>>({});
  const [activeParagraphIds, setActiveParagraphIds] = useState<Set<string>>(() => new Set());
  const [sourceStatus, setSourceStatus] = useState("Select a citation to inspect its source.");
  const [error, setError] = useState<string | null>(null);
  const [checkFilter, setCheckFilter] = useState<CheckFilter>("all");
  const [toneFilter, setToneFilter] = useState<ToneFilter>("all");
  const [showStartPanel, setShowStartPanel] = useState(true);
  const [showTools, setShowTools] = useState(false);
  const [reviewView, setReviewView] = useState<"findings" | "brief">("findings");
  const eventSource = useRef<EventSource | null>(null);
  const signalRefreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const sourcePane = useRef<HTMLDivElement | null>(null);

  const selectedCitation = useMemo(
    () => citations.find((citation) => citation.id === selectedCitationId) ?? null,
    [citations, selectedCitationId],
  );
  const selectedQuoteFinding = quoteFinding(selectedCitation);
  const selectedPropositionFinding = propositionFinding(selectedCitation);
  const citationsById = useMemo(() => new Map(citations.map((citation) => [citation.id, citation])), [citations]);
  const filteredCitations = useMemo(
    () => citations.filter((citation) => citationMatchesFilters(citation, checkFilter, toneFilter, (verdict) => verdictDetails(verdict).tone)),
    [citations, checkFilter, toneFilter],
  );

  useEffect(() => {
    return () => {
      eventSource.current?.close();
      if (signalRefreshTimer.current) clearInterval(signalRefreshTimer.current);
      if (briefUrl) URL.revokeObjectURL(briefUrl);
    };
  }, [briefUrl]);

  async function refreshCitations(activeJobId: string) {
    const response = await fetch(`${API_BASE}/api/jobs/${activeJobId}/citations`);
    if (!response.ok) throw new Error(`Could not refresh citations (HTTP ${response.status}).`);
    const data = (await response.json()) as { citations: Citation[]; page_signals?: Signal[] };
    const nextCitations = data.citations.map((citation) => ({ ...citation, signals: citation.signals ?? [] }));
    setCitations(nextCitations);
    setPageSignals(data.page_signals ?? []);
    // Keep an explicitly selected citation across polling, but do not open
    // source evidence until the reviewer asks for it. This avoids presenting
    // an incomplete evidence pane as if it were a completed result.
    setSelectedCitationId((current) => current && nextCitations.some((citation) => citation.id === current) ? current : null);
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
    const paragraphIds = citedParagraphIdsFrom(citation.findings);
    setSelectedCitationId(citation.id);
    setSource(null);
    setActiveParagraphIds(new Set(paragraphIds));

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
      const sourceParagraphIds = sourceParagraphIdsFor(nextSource, paragraphIds);
      setSource(nextSource);
      setActiveParagraphIds(new Set(sourceParagraphIds));
      setSourceStatus("Source text loaded.");
      window.requestAnimationFrame(() => {
        const target = sourceParagraphIds[0] ? document.getElementById(`source-paragraph-${sourceParagraphIds[0]}`) : sourcePane.current;
        target?.scrollIntoView({ behavior: "smooth", block: "center" });
      });
    } catch {
      setSourceStatus("The source text could not be loaded. The citation result remains available for review.");
    }
  }

  function highlightParagraphs(paragraphIds: string[]) {
    const sourceParagraphIds = source ? sourceParagraphIdsFor(source, paragraphIds) : paragraphIds;
    setActiveParagraphIds(new Set(sourceParagraphIds));
    window.requestAnimationFrame(() => {
      const target = sourceParagraphIds[0] ? document.getElementById(`source-paragraph-${sourceParagraphIds[0]}`) : sourcePane.current;
      target?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }

  function connectEvents(activeJobId: string, reviewToken: string) {
    eventSource.current?.close();
    if (signalRefreshTimer.current) clearInterval(signalRefreshTimer.current);
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
      // Signal tasks are intentionally independent and have no SSE event:
      // poll the already-existing citations contract only while this job is
      // active so a signal can appear even if no later finding is written.
      signalRefreshTimer.current = setInterval(() => {
        void refreshCitations(activeJobId).catch((refreshError: unknown) => setError(String(refreshError)));
      }, 5_000);
    });
    stream.addEventListener("finding.created", () => {
      setJobStatus("A citation result just arrived.");
      void refreshCitations(activeJobId).catch((refreshError: unknown) => setError(String(refreshError)));
    });
    stream.addEventListener("investigator.started", (event) => {
      const data = JSON.parse((event as MessageEvent<string>).data) as { citation_id?: string; live_view_url?: string | null };
      if (!data.citation_id) return;
      setActiveInvestigations((current) => ({ ...current, [data.citation_id!]: data.live_view_url ?? null }));
      setJobStatus("Searching the web for an official source…");
      void refreshCitations(activeJobId).catch((refreshError: unknown) => setError(String(refreshError)));
    });
    stream.addEventListener("investigator.completed", (event) => {
      const data = JSON.parse((event as MessageEvent<string>).data) as { citation_id?: string };
      if (data.citation_id) {
        setActiveInvestigations((current) => {
          const next = { ...current };
          delete next[data.citation_id!];
          return next;
        });
      }
      setJobStatus("Web source check completed.");
      void refreshCitations(activeJobId).catch((refreshError: unknown) => setError(String(refreshError)));
    });
    for (const terminalEvent of ["job.completed", "job.failed"]) {
      stream.addEventListener(terminalEvent, () => {
        setJobStatus(terminalEvent === "job.completed" ? "Review complete" : "Review ended with an issue");
        void refreshCitations(activeJobId).catch((refreshError: unknown) => setError(String(refreshError)));
        if (signalRefreshTimer.current) clearInterval(signalRefreshTimer.current);
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
    setPageSignals([]);
    setSelectedCitationId(null);
    setSource(null);
    setActiveInvestigations({});
    setActiveParagraphIds(new Set());
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
      setShowStartPanel(false);
      setShowTools(false);
      setReviewView("findings");
      connectEvents(data.job_id, data.review_token);
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "The upload could not be completed.");
      setJobStatus("Ready for a PDF");
    }
  }

  const diff = readDiff(selectedQuoteFinding);
  const propositionParagraphIds = selectedPropositionFinding ? citedParagraphIdsFrom([selectedPropositionFinding]) : [];
  const hasReview = jobId !== null;
  const hasResults = citations.length > 0;

  function openNewReview() {
    if (briefUrl) URL.revokeObjectURL(briefUrl);
    setFile(null);
    setBriefUrl(null);
    setShowStartPanel(true);
  }

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>PinCite · Citation review</p>
          <h1>Check cited authority against the source text.</h1>
        </div>
        <div className={styles.headerLinks}>
          <Link href="/eval">Evaluation</Link>
          <p className={styles.disclaimer}>PinCite reports differences between a document and the sources it cites. It is not legal advice and does not assess anyone&apos;s intent. Always review the linked source text yourself.</p>
        </div>
      </header>

      {showStartPanel ? <section className={styles.uploadCard} aria-labelledby="upload-heading">
        <div>
          <h2 id="upload-heading">Start a brief review</h2>
          <p>Upload a federal brief. PinCite will identify citations and show the source material it checked.</p>
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
      </section> : <section className={styles.reviewHeader} aria-label="Current review">
        <div>
          <p className={styles.eyebrow}>Current review</p>
          <h2>{file?.name ?? "Uploaded PDF"}</h2>
        </div>
        <button className={styles.secondaryButton} onClick={openNewReview} type="button">Start another review</button>
      </section>}

      {hasReview && <section className={styles.statusBar} aria-live="polite">
        <span className={styles.statusDot} aria-hidden="true" />
        <span>{jobStatus}</span>
        <code>Review {jobId.slice(0, 8)}</code>
      </section>}

      {hasReview && !hasResults && <section className={styles.processingCard} aria-live="polite">
        <p className={styles.eyebrow}>Review in progress</p>
        <h2>Results will appear as they are ready.</h2>
        <p>PinCite is extracting citations and checking sources. The review workspace opens when the first citation is available.</p>
      </section>}

      {hasReview && hasResults && <>
        <section className={styles.workspaceHeader} aria-label="Review navigation">
          <div>
            <p className={styles.eyebrow}>Review workspace</p>
            <h2>{citations.length} citation{citations.length === 1 ? "" : "s"} ready to review</h2>
          </div>
          <div className={styles.workspaceActions}>
            <button aria-pressed={reviewView === "findings"} className={styles.viewButton} onClick={() => setReviewView("findings")} type="button">Findings</button>
            <button aria-pressed={reviewView === "brief"} className={styles.viewButton} onClick={() => setReviewView("brief")} type="button">Annotated brief</button>
            <button aria-expanded={showTools} className={styles.secondaryButton} onClick={() => setShowTools((current) => !current)} type="button">{showTools ? "Hide tools" : "Filters & exports"}</button>
          </div>
        </section>

        {showTools && <section className={styles.p4Tools}>
          <SummaryPanel
            checkFilter={checkFilter}
            citations={citations}
            labelForVerdict={(verdict) => verdictDetails(verdict).label}
            onCheckFilter={setCheckFilter}
            onToneFilter={setToneFilter}
            toneFilter={toneFilter}
            toneForVerdict={(verdict) => verdictDetails(verdict).tone}
          />
          <ExportButtons apiBase={API_BASE} jobId={jobId} onError={setError} />
        </section>}

        <section className={`${styles.reviewGrid} ${selectedCitation ? styles.reviewGridWithEvidence : styles.reviewGridSingle}`} aria-label="Citation review">
        <article className={styles.briefPane}>
          <div className={styles.paneHeader}>
            <div>
              <p className={styles.eyebrow}>{reviewView === "findings" ? "Findings" : "Your brief"}</p>
              <h2>{reviewView === "findings" ? "Choose a citation to inspect its evidence" : file?.name ?? "Uploaded PDF"}</h2>
            </div>
            <span>{citations.length} citation{citations.length === 1 ? "" : "s"}</span>
          </div>

          {reviewView === "brief" && briefPages.length ? <div className={styles.annotatedBrief}>
            {briefPages.map((page) => <AnnotatedPage
              citationsById={citationsById}
              key={page.page}
              onCitation={(citation) => void loadSource(citation)}
              page={page}
              signals={pageSignals.filter((signal) => signal.section_ref === String(page.page) || signal.section_ref === `page:${page.page}`)}
              selectedCitationId={selectedCitationId}
            />)}
          </div> : reviewView === "brief" ? <p className={styles.emptyState}>Extracting reviewable text and citation spans…</p> : null}

          {reviewView === "findings" && <div className={styles.citationList} aria-label="Extracted citations">
            {filteredCitations.map((citation) => {
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
                      return <VerdictBadge key={finding.id} verdict={finding.verdict} />;
                    }) : <VerdictBadge verdict="PENDING" />}
                  </span>
                </button>
              );
            })}
            {jobId && citations.length === 0 && <p className={styles.emptyState}>Looking for citations. Results will appear individually as they are saved.</p>}
            {citations.length > 0 && filteredCitations.length === 0 && <p className={styles.emptyState}>No citations match these filters.</p>}
          </div>}
        </article>

        {selectedCitation && <article className={styles.sourcePane} ref={sourcePane}>
          <div className={styles.paneHeader}>
            <div>
              <p className={styles.eyebrow}>Source evidence</p>
              <h2>{source?.case_name ?? "Select a citation"}</h2>
            </div>
            {source?.court && <span>{source.court}</span>}
          </div>

          <>
              <section className={styles.citationContext} aria-label="Selected citation">
                <p><strong>Citation:</strong> {selectedCitation.raw_text}</p>
                {selectedCitation.claims[0]?.proposition_text && <p><strong>Point checked:</strong> {selectedCitation.claims[0].proposition_text}</p>}
                {selectedCitation.claims[0]?.quote_text && <p><strong>Quoted in brief:</strong> “{selectedCitation.claims[0].quote_text}”</p>}
              </section>

              <CitationSignals signals={selectedCitation.signals ?? []} />

              {Object.hasOwn(activeInvestigations, selectedCitation.id) && <section className={styles.investigatorCard} aria-live="polite">
                <h3>Searching the web…</h3>
                <p>PinCite is looking for an official source before reaching an existence result.</p>
                {activeInvestigations[selectedCitation.id] ? <iframe
                  className={styles.liveView}
                  referrerPolicy="no-referrer"
                  src={activeInvestigations[selectedCitation.id] ?? undefined}
                  title="Live investigator browser session"
                /> : <p className={styles.muted}>Opening the investigator&apos;s browser session…</p>}
              </section>}

              {selectedQuoteFinding && <section className={styles.diffCard} aria-labelledby="diff-heading">
                <h3 id="diff-heading">Quote comparison</h3>
                <VerdictBadge verdict={selectedQuoteFinding.verdict} />
                {diff.length > 0 ? <div className={styles.diff} aria-label="Word-level quote diff">
                  {diff.map((item, index) => <span className={styles[`diff${item.operation[0].toUpperCase()}${item.operation.slice(1)}`]} key={`${item.operation}-${index}`}>
                    {item.operation === "insert" ? item.source_tokens.join(" ") : item.quote_tokens.join(" ")}
                    {" "}
                  </span>)}
                </div> : <p className={styles.muted}>A word-level comparison will appear when source evidence is available.</p>}
                {selectedQuoteFinding.notes.length > 0 && <p className={styles.muted}>{selectedQuoteFinding.notes.join(" · ")}</p>}
              </section>}

              {selectedPropositionFinding && <section className={styles.rationaleCard} aria-labelledby="rationale-heading">
                <h3 id="rationale-heading">Proposition support</h3>
                <VerdictBadge verdict={selectedPropositionFinding.verdict} />
                <p>{selectedPropositionFinding.rationale ?? "PinCite could not provide a rationale from the available source evidence."}</p>
                {propositionParagraphIds.length > 0 && <p className={styles.evidenceLinks}>
                  <strong>Source passages:</strong>{" "}
                  {propositionParagraphIds.map((paragraphId, index) => <span key={paragraphId}>
                    {index > 0 && ", "}
                    <a
                      href={`#source-paragraph-${source ? sourceParagraphIdsFor(source, [paragraphId])[0] ?? paragraphId : paragraphId}`}
                      onClick={() => highlightParagraphs(propositionParagraphIds)}
                    >Paragraph {index + 1}</a>
                  </span>)}
                </p>}
                {selectedPropositionFinding.notes.length > 0 && <p className={styles.muted}>{selectedPropositionFinding.notes.join(" · ")}</p>}
              </section>}

              <p className={styles.sourceStatus} aria-live="polite">{sourceStatus}</p>
              {source?.provenance && <section className={styles.provenance} aria-label="Source provenance">
                <strong>Retrieved from:</strong>{" "}
                {source.provenance.url ? <a href={source.provenance.url} rel="noreferrer" target="_blank">{source.provenance.url}</a> : <span>stored source record</span>}
                <span> · {source.provenance.method.replaceAll("_", " ")}</span>
              </section>}
              {source && <div className={styles.sourceText}>
                {source.paragraphs.map((paragraph) => <p className={activeParagraphIds.has(paragraph.id) ? styles.activeParagraph : undefined} id={`source-paragraph-${paragraph.id}`} key={paragraph.id}>
                  <span className={styles.paragraphMarker}>¶ {paragraph.para_no}{paragraph.page ? ` · p. ${paragraph.page}` : ""}</span>
                  {paragraph.text}
                </p>)}
                {!source.paragraphs.length && source.text && <p>{source.text}</p>}
              </div>}
          </>
        </article>
        }
        </section>
      </>}
    </main>
  );
}
