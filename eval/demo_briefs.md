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
- **Citation count:** not yet extracted — first real exercise for FR-EXT-001
  once eyecite is wired up in P1.

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
- **Citation count:** not yet extracted.

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
