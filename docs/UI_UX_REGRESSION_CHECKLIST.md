# Review UI regression checklist

Use this checklist after changing the review UI. The navigation may change;
the evidence and API contracts must not.

## Upload and live review

- [ ] A PDF can be selected, either review mode can be chosen, and the upload
  sends the selected `mode` plus the PDF to `POST /api/jobs`.
- [ ] The review token stays in the browser session and still authorizes
  `GET /api/jobs/{id}/pages`.
- [ ] SSE status, extraction, finding, investigator, completion, failure, and
  reconnect states remain visible to the reviewer.
- [ ] A review with no extracted citations has a clear in-progress state;
  empty findings, evidence, filters, and export controls are not presented as
  actionable content.

## Findings and source evidence

- [ ] Citations refresh while findings arrive and all verdict badges retain
  their existing label, color, tooltip, and neutral wording.
- [ ] Check and result filters affect only the displayed citation list.
- [ ] The annotated-brief view preserves citation offsets, selectable
  highlights, and page-level AI-writing heat strips.
- [ ] Selecting a citation still loads source text, scrolls to cited
  paragraphs, and shows quote diff, proposition rationale, provenance, and
  outbound source link when each is available.
- [ ] An active investigator displays its `Searching the web` state and live
  view; a completed recovery still shows its official-source verdict.
- [ ] AI-writing signals remain explicitly labelled as signals rather than
  evidence and never change findings, counts, or filters.

## Reporting and secondary routes

- [ ] Both Markdown and PDF fix-list/evidence-table exports remain available
  for a review and preserve disclaimer, provenance, and neutral wording.
- [ ] `/eval` remains reachable from the review header and does not expose
  raw brief text or provider payloads.

## Usability and release checks

- [ ] Keyboard focus reaches upload, navigation buttons, filters, citation
  cards, evidence links, and exports in a sensible order.
- [ ] The review is usable at desktop and narrow/mobile widths.
- [ ] Run web ESLint and TypeScript checks before release; use a fresh
  Fivehouse review for the quote/diff path and a fresh Day review for the
  investigator/official-recovery path.
