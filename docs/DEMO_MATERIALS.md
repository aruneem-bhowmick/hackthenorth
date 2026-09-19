# Demo materials and fresh-review runbook

These are public materials used in the live PinCite demonstration. Keep the
source URL visible during the demo and do not describe a derivative PDF as an
official court filing.

## Fivehouse: flagship quote-evidence review

- **Upload PDF:** [Fivehouse v. U.S. Department of Defense, D.E. 86 —
  response in opposition (9 pages)](https://www.courtlistener.com/recap/gov.uscourts.nced.221403/gov.uscourts.nced.221403.86.0.pdf)
- **Narrative anchor:** select the red `Ohio Valley, 556 F.3d at 201`
  citation after results arrive. Its `NOT_FOUND_IN_SOURCE` quote finding
  exposes the reviewed quotation and token-level source diff. Do not present
  every Fivehouse finding as equally conclusive: unresolved short forms and
  the Overton Park association are not part of this demo claim.

## Day: official-source recovery review

- **Authoritative source:** [Day v. Plumber's Shop & Assoc. LLC, 2025 NY Slip
  Op 51938(U)](https://www.nycourts.gov/reporter/3dseries/2025/2025_51938.htm)
- The New York Law Reporting Bureau publishes this source as an HTML opinion.
  No separate official PDF has been confirmed. In a normal browser, open the
  official page, use **Print → Save as PDF**, name the derivative file
  `day-official-opinion.pdf`, and upload it only to demonstrate recovery of
  the citation printed in that opinion. The source remains the official URL
  above; the PDF is merely an upload container.
- Expected path: `NOT_IN_DATABASE` in CourtListener, then investigator
  recovery to `VERIFIED_OFFICIAL`, with New York courts provenance. A bare
  HTTP client can meet Cloudflare protection; use the review application's
  Browserbase-backed investigator rather than claiming that a raw request is
  a successful recovery.

## Fresh browser-review checklist

1. Open <https://pincite-web-production.up.railway.app/> in an ordinary
   browser. Choose **Answering a brief**, select the Fivehouse PDF, and click
   **Review citations**. Keep this tab open: the review token is held only in
   that browser session.
2. Capture the first appearance of annotated citation highlights and the
   status text. Click the red Ohio Valley highlight/card, then capture the
   quote comparison and source evidence. Download one evidence-table export.
3. In a fresh review, upload the Day print-to-PDF. Select its citation while
   its state is **Searching the web** to capture the investigator/live-view
   card, then capture the final **Verified · official source** badge and
   provenance.
4. Capture the page-level AI-writing heat strip, including its “signal, not
   evidence” label. Do not use it as a finding or filter.

## Sentry post-fix check

The process trace is in the **pincite backend** Sentry project (the
FastAPI/worker service), not **pincite-web**. In Sentry Discover or Traces,
filter `environment:production transaction:process_job` and select a trace
created after Railway deployment `ffe3b6dc-81a6-48b6-a37e-ce9e3febf004`.
Confirm one batch `SELECT claims ... citation_id IN (...)` span and no series
of repeated `WHERE claims.citation_id = $1` spans. Record the trace URL and
measured count in `docs/sentry_findings.md`; do not replace the existing real
observation until the dashboard demonstrates the result.
