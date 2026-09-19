# Candidate demo / evaluation briefs

Closes the SPEC.md §13 risk-mitigation gap ("Identify 3 candidate briefs in
P0; confirm availability early") — done in P1 prep instead, via the
CourtListener API (authenticated). References only, per this directory's
purpose (SPEC.md §15) — no PDFs stored here, no case content reproduced.

## 1. Fivehouse v. U.S. Department of Defense — flagship candidate

The case OVERVIEW.md §1.3 names directly ("a federal magistrate judge in
the Eastern District of North Carolina ordered senior leaders of a U.S.
Attorney's Office to appear at a show-cause hearing").

- **Docket:** No. 2:25-cv-00041, E.D.N.C. — CourtListener docket ID `71231282`
- **What happened:** AUSA Rudy Renfer's response brief (opposing Fivehouse's
  motion to supplement the administrative record) misrepresented case
  holdings and included fabricated quotations. Pro se plaintiff Derence
  Fivehouse (a retired Air Force colonel) caught it.
- **The offending brief** — D.E. 86, "RESPONSE in Opposition regarding 82
  MOTION to Supplement the Administrative Record," filed 2025-12-23, 9 pages:
  `https://www.courtlistener.com/recap/gov.uscourts.nced.221403/gov.uscourts.nced.221403.86.0.pdf`
- **Plaintiff's reply flagging the problem** — D.E. 89, pp. 4-5, filed
  2025-12-30, 13 pages:
  `https://www.courtlistener.com/recap/gov.uscourts.nced.221403/gov.uscourts.nced.221403.89.0.pdf`
- **Court's order acknowledging the claim, ordering a surreply** — D.E. 98,
  filed 2026-01-08 (Magistrate Judge Robert T. Numbers II):
  `https://www.courtlistener.com/recap/gov.uscourts.nced.221403/gov.uscourts.nced.221403.98.0.pdf`
- **Later show-cause order** (AUSA leadership ordered to appear; Renfer
  resigned at the hearing) — D.E. 119, filed 2026-03-02:
  `https://www.courtlistener.com/docket/71231282/119/fivehouse-v-us-department-of-defense/`
- **Availability:** all four documents confirmed `is_available: true` via
  the CourtListener API — free on RECAP, not PACER-gated.
- **P4 pilot eval label:** ground truth extracted and independently
  cross-verified 2026-09-19 (see `eval/labels/fivehouse.yaml`). Three real
  cases (`556 F.3d 177`, `637 F.3d 259`, `899 F.3d 260`) are quoted or
  characterized inaccurately in D.E. 86; two (`411 U.S. 138`, `401 U.S. 402`)
  are cited accurately and serve as negative controls.

## 2. Cole v. Amain.com, Inc. — strong backup candidate

- **Docket:** No. 4:25-cv-04217, C.D. Illinois — CourtListener docket ID
  `71996354` (originally filed N.D. Illinois as 1:25-cv-04084, transferred)
- **What happened:** Plaintiff's counsel David Baldemar Reyes's motion for
  default judgment against Hobby Town Unlimited contained fabricated and
  inaccurate citations. Judge Sara Darrow caught it while ruling on the
  motion itself.
- **The offending brief** — D.E. 25, "MOTION for default judgment," filed
  2026-01-27, 7 pages:
  `https://www.courtlistener.com/recap/gov.uscourts.ilcd.98203/gov.uscourts.ilcd.98203.25.0.pdf`
- **Order ruling on the motion + show-cause** — D.E. 27, filed 2026-07-24,
  13 pages — explicitly: *"Attorney David Baldemar Reyes... is ORDERED TO
  SHOW CAUSE why he should not be sanctioned for the inclusion of
  fabricated and inaccurate citations"*:
  `https://www.courtlistener.com/recap/gov.uscourts.ilcd.98203/gov.uscourts.ilcd.98203.27.0.pdf`
- **Final sanctions order** — D.E. 33, filed 2026-09-02: $1,000 fine +
  referral to the Illinois Attorney Registration and Disciplinary
  Commission.
- **Availability:** D.E. 25 and D.E. 27 confirmed `is_available: true`.
- **P4 pilot eval label:** ground truth extracted and independently
  cross-verified 2026-09-19 (see `eval/labels/cole.yaml`). Two citations
  (`337 F.R.D. 659`, `249 F. Supp. 3d 676`) are genuinely fabricated —
  confirmed HTTP 404 on CourtListener's citation-lookup, matching the
  court's own footnoted "could not be located on either Lexis or Westlaw."
  Three real cases are cited for propositions they do not support
  (`998 F.3d 772`, `430 F.3d 432`, `546 F.3d 918`); three more are cited
  accurately and serve as negative controls.

## 3. International Partners for Ethical Care v. Ferguson — P1 final smoke brief

This is the repeatable **final P1 smoke-test input**, separate from the
sanctions-case demo candidates above. It is a public, text-layer federal brief
hosted by the Supreme Court itself; the PDF is intentionally not committed.

- **Docket:** No. 25-840, Supreme Court of the United States.
- **Document:** *Brief of Amicus Curiae Dr. Erica E. Anderson in Support of
  Petitioners*, filed February 17, 2025, on a petition from the Ninth Circuit.
- **Official source:**
  `https://www.supremecourt.gov/DocketPDF/25/25-840/396422/20260217135200647_25-840%20Amicus%20Brief%20of%20Erica%20E.%20Anderson.pdf`
- **Re-verified 2026-09-19:** 31 pages, 619,731 bytes, SHA-256
  `bec2a6dfe04f4d342ee54d35793fbcc5c53b56ea9d5d39c64558e0bd2d675195`.
- **Current P1 pipeline baseline:** 118 citation records (94 distinct
  normalised strings), including 58 full citations, 29 short forms, 16 *id.*
  citations, 15 *supra* citations, and 63 attached quotation claims.

It exceeds the P1 exit gate by a wide margin and exercises the extraction
resolver rather than only repeated full cites. Before a final run, re-download
the official PDF, verify its SHA-256, and rerun extraction; a changed file or
materially changed count invalidates this baseline. This file is for the
deployment/P1-gate smoke test only—not a finding of misconduct and not the
flagship sanctions-case demonstration.

## 4. Day v. Plumber's Shop & Assoc. LLC — P3 known-missing recovery fixture

- **Citation:** 2025 NY Slip Op 51938(U) [87 Misc. 3d 1248(A)].
- **Court/date:** Supreme Court, New York County, September 16, 2025.
- **Official opinion:**
  `https://www.nycourts.gov/reporter/3dseries/2025/2025_51938.htm`
  (published by the New York State Law Reporting Bureau pursuant to Judiciary
  Law §431).
- **CourtListener check, re-verified 2026-09-19:** an authenticated
  citation-lookup request for `2025 NY Slip Op 51938(U)` returned `404` with
  no clusters. This is a real P3 `NOT_IN_DATABASE` trigger, not an invented
  missing authority.
- **Access-control preparation:** the exact official host
  `www.nycourts.gov` is allowlisted in `config/allowlist.yaml`; no broad
  wildcard was added.
- **Demo caveat:** ordinary browser-session handling retrieves the page with
  HTTP 200, while a bare stateless request may receive a Cloudflare 403. A
  live Browserbase / Stagehand spike must still succeed before this is claimed
  as an exit-gate-ready demo run.

## P4 pilot evaluation set (scope note)

`eval/labels/*.yaml` holds hand-labelled, cross-verified ground truth for
Fivehouse and Cole above — the two candidates with a court order that names
specific citation-level problems. SPEC.md §12 targets 10-15 labelled
positives, 5-10 negatives, and 5 known-missing cases; this pilot set has 2
positives (with negative controls embedded in each) and 1 known-missing
fixture (Day v. Plumber's Shop, above). This is a deliberate scope decision,
not an oversight — sourcing and hand-verifying real court filings at SPEC's
target scale is substantial research time this session prioritized against
shipping a working, honestly-labelled pilot instead. The P4 evaluation page
presents these results labelled explicitly as a pilot-scale set, consistent
with `OVERVIEW.md`'s "honesty about uncertainty" value, rather than
overstating coverage.

## Dropped: Adams v. Matrix Providers Inc.

No. 1:23-cv-01996, D. Colorado. Charlotin database lists a 2026-08-27
sanction (1 fabricated case + 1 false quote), but the matching CourtListener
docket entry (2026-08-28, docket ID `67675545`) is a bare "Minute Order"
with `is_available: false` — no retrievable PDF via free RECAP access.
Not usable as a demo/eval brief without PACER access, which is out of
scope (CON-ACC-002, NFR/access-control policy). Not replaced yet — two
strong candidates is enough to unblock P1; a third (and the full 10-15
needed for SPEC.md §12's eval set) is P4 threshold-calibration work, not
a P1 blocker.
