import { useState } from "react";
import styles from "../app/page.module.css";

type ExportFormat = "md" | "pdf";

function filenameFrom(response: Response, fallback: string) {
  const match = /filename="?([^";]+)"?/i.exec(response.headers.get("content-disposition") ?? "");
  return match?.[1] ?? fallback;
}

export function ExportButtons({
  apiBase,
  jobId,
  onError,
}: {
  apiBase: string;
  jobId: string | null;
  onError: (message: string) => void;
}) {
  const [format, setFormat] = useState<ExportFormat>("md");
  const [downloading, setDownloading] = useState<string | null>(null);

  async function download(kind: "fixlist" | "evidence") {
    if (!jobId) return;
    setDownloading(kind);
    try {
      const response = await fetch(`${apiBase}/api/jobs/${jobId}/export?format=${kind}&type=${format}`);
      if (!response.ok) throw new Error(`Export could not be generated (HTTP ${response.status}).`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filenameFrom(response, `pincite-${kind}.${format}`);
      document.body.append(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      onError(error instanceof Error ? error.message : "Export could not be generated.");
    } finally {
      setDownloading(null);
    }
  }

  return <section className={styles.exportPanel} aria-label="Download review exports">
    <div>
      <p className={styles.eyebrow}>Share your review</p>
      <h2>Exports</h2>
      <p className={styles.summaryCopy}>Exports use neutral, evidence-first language and include the standard review disclaimer.</p>
    </div>
    <fieldset className={styles.exportFormats}>
      <legend>Format</legend>
      <label><input checked={format === "md"} name="export-format" onChange={() => setFormat("md")} type="radio" /> Markdown</label>
      <label><input checked={format === "pdf"} name="export-format" onChange={() => setFormat("pdf")} type="radio" /> PDF</label>
    </fieldset>
    <div className={styles.exportActions}>
      <button disabled={!jobId || downloading !== null} onClick={() => void download("fixlist")} type="button">
        {downloading === "fixlist" ? "Preparing…" : "Download fix list"}
      </button>
      <button disabled={!jobId || downloading !== null} onClick={() => void download("evidence")} type="button">
        {downloading === "evidence" ? "Preparing…" : "Download evidence table"}
      </button>
    </div>
    {!jobId && <p className={styles.muted}>Complete or start a review to enable exports.</p>}
  </section>;
}
