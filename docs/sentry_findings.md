# Sentry findings log

This log is intentionally a template until observations are made in the real
Sentry dashboard. Do not add inferred, synthetic, or local-only observations:
the P4 exit gate requires at least three findings from deployed traces, logs,
replays, or profiles.

## Entry template

- **Observed at:** ISO-8601 timestamp
- **Environment / trace or issue link:**
- **Signal:** What the dashboard showed (log, trace span, replay, or profile).
- **Impact:** User-visible or operational consequence, if any.
- **Action / outcome:** What was changed, or why no change was needed.

## Pending real observations

1. Pending: confirm a deployed review produces a structured CourtListener
   resolution log and correlated worker trace.
2. Pending: confirm an OpenAI span has model and token attributes without
   brief, quote, or source text.
3. Pending: confirm a review-screen Session Replay masks all document text,
   inputs, and media.
4. Pending: inspect ingestion and quote-alignment profiles from a real demo
   run.

## Recorded real observations

1. **Observed at:** 2026-09-19 (Sentry UI reported “2 hours ago”; the
   captured event detail did not include an absolute timestamp.)
   **Environment / trace or issue link:** production; N+1 Query issue event
   `9a3931ab`; trace `00e0c9003cd54f3789298a8769b87c12`.
   **Signal:** The `process_job` transaction (7.39 s) contains 22 repeating
   `SELECT claims ... WHERE claims.citation_id = $1::UUID` spans, interleaved
   with claim updates. Sentry classified this as a new, low-priority N+1
   query.
   **Impact:** The run incurred one database round trip per affected citation.
   Individual reads were about 2 ms, so this was not a user-visible incident
   in the observed run, but the cost grows with citation count.
   **Action / outcome:** Batch-load claims for the affected citation IDs into
   a map before proposition persistence, then verify a comparable deployed
   trace no longer triggers the detector. No suppression or production change
   has been made yet.
