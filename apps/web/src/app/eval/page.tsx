import Link from "next/link";
import styles from "./page.module.css";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type Metric = {
  precision?: number;
  recall?: number;
  f1?: number;
  support?: number;
  labelled?: number;
  accuracy?: number;
};

type EvaluationPayload = {
  generated_at?: string;
  dataset_description?: string;
  dataset?: { description?: string; size?: number; briefs?: number; labelled_citations?: number; note?: string };
  metrics?: Record<string, Metric>;
  checks?: Record<string, Metric>;
  retrieval_ablation?: Record<string, Metric> | Array<{ mode: string } & Metric>;
};

function percent(value: number | undefined) {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—";
}

function displayName(value: string) {
  return value.replaceAll("_", " ");
}

async function loadLatest(): Promise<EvaluationPayload | null> {
  try {
    const response = await fetch(`${API_BASE}/api/eval/latest`);
    if (!response.ok) return null;
    return await response.json() as EvaluationPayload;
  } catch {
    return null;
  }
}

export default async function EvaluationPage() {
  const evaluation = await loadLatest();
  const metrics = Object.entries(evaluation?.metrics ?? evaluation?.checks ?? {});
  const ablation = Array.isArray(evaluation?.retrieval_ablation)
    ? evaluation.retrieval_ablation.map((item) => [item.mode, item] as const)
    : Object.entries(evaluation?.retrieval_ablation ?? {});
  const datasetDescription = evaluation?.dataset_description ?? evaluation?.dataset?.description;

  return <main className={styles.page}>
    <header className={styles.header}>
      <div>
        <div className={styles.brandRow}>
          {/* eslint-disable-next-line @next/next/no-img-element -- static mark, server component avoids client-only icon hook */}
          <img src="/pushpin.svg" alt="" width={18} height={18} className={styles.brandMark} />
          <p className={styles.eyebrow}>PinCite · Evaluation</p>
        </div>
        <h1>Measure the review system against labelled examples.</h1>
      </div>
      <Link href="/">Return to review</Link>
    </header>

    <section className={styles.intro}>
      <h2>About this evaluation</h2>
      <p>{datasetDescription ?? "The latest labelled evaluation results will appear here after the runner has completed."}</p>
      {evaluation?.dataset?.note && <p>{evaluation.dataset.note}</p>}
      {typeof evaluation?.dataset?.size === "number" && <p><strong>Labelled items:</strong> {evaluation.dataset.size}</p>}
      {typeof evaluation?.dataset?.labelled_citations === "number" && <p><strong>Labelled citations:</strong> {evaluation.dataset.labelled_citations}{typeof evaluation.dataset.briefs === "number" ? ` across ${evaluation.dataset.briefs} briefs` : ""}</p>}
      {evaluation?.generated_at && <p className={styles.muted}>Latest run: {new Date(evaluation.generated_at).toLocaleString()}</p>}
    </section>

    {!evaluation && <section className={styles.unavailable} role="status">
      <h2>Evaluation results are not available yet</h2>
      <p>The review application remains available. This page will show saved, aggregate evaluation results when the evaluation runner has produced them.</p>
    </section>}

    {evaluation && <>
      <section className={styles.card} aria-labelledby="check-metrics">
        <h2 id="check-metrics">Per-check precision and recall</h2>
        {metrics.length ? <div className={styles.tableScroll}><table>
          <thead><tr><th scope="col">Check</th><th scope="col">Precision</th><th scope="col">Recall</th><th scope="col">F1</th><th scope="col">Labelled items</th></tr></thead>
          <tbody>{metrics.map(([check, metric]) => <tr key={check}>
            <th scope="row">{displayName(check)}</th><td>{percent(metric.precision)}</td><td>{percent(metric.recall)}</td><td>{percent(metric.f1)}</td><td>{metric.support ?? metric.labelled ?? "—"}</td>
          </tr>)}</tbody>
        </table></div> : <p className={styles.muted}>No per-check metrics were returned by the latest run.</p>}
      </section>

      <section className={styles.card} aria-labelledby="retrieval-ablation">
        <h2 id="retrieval-ablation">Retrieval ablation</h2>
        <p className={styles.muted}>Where available, this compares proposition-judge accuracy under each retrieval mode.</p>
        {ablation.length ? <div className={styles.tableScroll}><table>
          <thead><tr><th scope="col">Mode</th><th scope="col">Judge accuracy</th><th scope="col">Precision</th><th scope="col">Recall</th></tr></thead>
          <tbody>{ablation.map(([mode, metric]) => <tr key={mode}>
            <th scope="row">{displayName(mode)}</th><td>{percent(metric.accuracy)}</td><td>{percent(metric.precision)}</td><td>{percent(metric.recall)}</td>
          </tr>)}</tbody>
        </table></div> : <p className={styles.muted}>Retrieval ablation has not been recorded for this pilot yet.</p>}
      </section>
    </>}
  </main>;
}
