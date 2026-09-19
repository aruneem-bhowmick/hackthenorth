import styles from "../app/page.module.css";

export type SummaryFinding = {
  check: string;
  verdict: string;
};

export type SummaryCitation = {
  findings: SummaryFinding[];
};

export type VerdictTone = "green" | "yellow" | "red" | "grey";
export type CheckFilter = "all" | string;
export type ToneFilter = "all" | VerdictTone;

export function SummaryPanel({
  citations,
  checkFilter,
  onCheckFilter,
  toneFilter,
  onToneFilter,
  toneForVerdict,
  labelForVerdict,
}: {
  citations: SummaryCitation[];
  checkFilter: CheckFilter;
  onCheckFilter: (value: CheckFilter) => void;
  toneFilter: ToneFilter;
  onToneFilter: (value: ToneFilter) => void;
  toneForVerdict: (verdict?: string) => VerdictTone;
  labelForVerdict: (verdict?: string) => string;
}) {
  const counts = new Map<string, number>();
  for (const citation of citations) {
    if (!citation.findings.length) {
      counts.set("PENDING", (counts.get("PENDING") ?? 0) + 1);
      continue;
    }
    for (const finding of citation.findings) {
      counts.set(finding.verdict, (counts.get(finding.verdict) ?? 0) + 1);
    }
  }
  const checks = [...new Set(citations.flatMap((citation) => citation.findings.map((finding) => finding.check)))].sort();

  return <section className={styles.summaryPanel} aria-label="Review summary and filters">
    <div>
      <p className={styles.eyebrow}>Review summary</p>
      <h2>Findings at a glance</h2>
      <p className={styles.summaryCopy}>Counts reflect the checks received so far. Filters only change this review list.</p>
    </div>
    <dl className={styles.summaryCounts}>
      {[...counts.entries()].map(([verdict, count]) => <div key={verdict}>
        <dt className={`${styles.summaryTone} ${styles[`summaryTone${toneForVerdict(verdict)[0].toUpperCase()}${toneForVerdict(verdict).slice(1)}`]}`}>{labelForVerdict(verdict)}</dt>
        <dd>{count}</dd>
      </div>)}
      {!counts.size && <p className={styles.muted}>Results will be counted as checks finish.</p>}
    </dl>
    <div className={styles.summaryFilters}>
      <label>
        <span>Check</span>
        <select value={checkFilter} onChange={(event) => onCheckFilter(event.target.value)}>
          <option value="all">All checks</option>
          {checks.map((check) => <option key={check} value={check}>{check.replaceAll("_", " ")}</option>)}
        </select>
      </label>
      <label>
        <span>Result</span>
        <select value={toneFilter} onChange={(event) => onToneFilter(event.target.value as ToneFilter)}>
          <option value="all">All results</option>
          <option value="green">Located / supports</option>
          <option value="yellow">Review recommended</option>
          <option value="red">Needs attention</option>
          <option value="grey">Pending / unavailable</option>
        </select>
      </label>
    </div>
  </section>;
}

export function citationMatchesFilters(
  citation: SummaryCitation,
  checkFilter: CheckFilter,
  toneFilter: ToneFilter,
  toneForVerdict: (verdict?: string) => VerdictTone,
) {
  if (!citation.findings.length) {
    return (checkFilter === "all") && (toneFilter === "all" || toneFilter === "grey");
  }
  return citation.findings.some((finding) =>
    (checkFilter === "all" || finding.check === checkFilter)
    && (toneFilter === "all" || toneForVerdict(finding.verdict) === toneFilter),
  );
}
