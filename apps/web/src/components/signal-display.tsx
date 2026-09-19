import type { CSSProperties } from "react";
import styles from "../app/page.module.css";

export type Signal = {
  provider: string;
  kind: string;
  score: number;
  created_at: string;
  section_ref?: string | null;
};

function scoreLabel(score: number) {
  return `${Math.round(Math.max(0, Math.min(1, score)) * 100)}%`;
}

export function SignalHeatStrip({ page, signals }: { page: number; signals: Signal[] }) {
  if (!signals.length) return null;
  const score = Math.max(...signals.map((signal) => signal.score));
  return <aside className={styles.signalHeatStrip} aria-label={`AI-writing signal for page ${page}`}>
    <span aria-hidden="true" className={styles.signalHeat} style={{ "--signal-strength": String(Math.max(.15, Math.min(1, score))) } as CSSProperties} />
    <span><strong>AI-writing signal — not evidence</strong><br />Page {page}: {scoreLabel(score)}</span>
  </aside>;
}

export function CitationSignals({ signals }: { signals: Signal[] }) {
  if (!signals.length) return null;
  return <section className={styles.signalCard} aria-label="Citation signals">
    <h3>Signals</h3>
    <p>Signal, not evidence. It does not change this citation&apos;s findings.</p>
    <ul>
      {signals.map((signal, index) => <li key={`${signal.kind}-${signal.created_at}-${index}`}>
        <strong>{signal.kind.replaceAll("_", " ")}</strong>: {scoreLabel(signal.score)} <span>({signal.provider})</span>
      </li>)}
    </ul>
  </section>;
}
