# P4 pilot eval set — first live baseline run (2026-09-19)

Both pilot briefs (`eval/labels/fivehouse.yaml`, `eval/labels/cole.yaml`) were run
through the live production deployment (`pincite-backend-production.up.railway.app`)
this session, not a local/mocked pipeline. This is a real end-to-end check of
what's actually deployed right now, and it surfaced genuine gaps worth fixing
before the demo, not just confirmation of the hand-labels.

| Brief | Job ID | Pages | Citations | Duration |
|---|---|---|---|---|
| Fivehouse (D.E. 86) | `b5aba23a-dae2-4040-b8e7-566d01fe7e6a` | 9 | 14 | ~20s |
| Cole (D.E. 25) | `2f65174b-a7c0-4f9d-b247-4fcb85a77ac0` | 7 | 18 | ~2m19s (investigator runs) |

Jobs expire per `NFR-PRIV-001` (24h) — this file is the durable record; the
live job data itself will not persist.

## What worked correctly

- **Every real-case existence call matched the hand label, 10/10** checked
  (Camp v. Pitts, Overton Park, Ohio Valley, Dow AgroSciences, Sierra Club v.
  DOI, Liebhart, Goodman [ambiguous, as labelled], Sierra Club v. Franklin,
  Doe v. Mutual of Omaha, NFB v. Target, Scherr — all `VERIFIED` except
  Goodman's genuinely ambiguous multi-cluster case).
- **Fuller v. Athleta, LLC (337 F.R.D. 659)** — a citation independently
  confirmed fabricated (404 on CourtListener) — correctly ran through the
  investigator to completion and reached `NOT_FOUND_ANYWHERE`. This is the
  flagship safety story (ADR-004: look before accusing) working end-to-end
  against a citation nobody hand-fed the system.
- **Camp v. Pitts** (accurate negative control) scored `VERBATIM` quote +
  `SUPPORTS` proposition — clean, correct positive confirmation, not just an
  absence of false alarms.
- **Scherr v. Marriott** (accurate, cited 3x in Cole) scored `SUPPORTS` on
  2 of 3 occurrences — the proposition judge does correctly confirm accurate
  citations, not just fail closed on everything.

## Gaps this run surfaced (not previously known)

1. **Case-name extraction truncates on several real citations.** "Ohio
   Valley Envtl. Coal. v. Aracoma Coal Co." → captured as "Coal. v. Aracoma
   Coal Co."; "e360 Insight v. The Spamhaus Project" → "Insight v. The
   Spamhaus Project"; "Goodman v. Illinois Dep't of Financial & Prof'l
   Regulation" → "Prof'l Regulation"; "Nat'l Fed'n of the Blind v.
   Thrustmaster..." → "Nat' Fed'   Blind v. Thrustmaster...". Likely an
   eyecite case-name-span issue on names containing abbreviations/apostrophes
   before "v.".

2. **Short-form citation antecedent resolution fails for at least 2 real
   cases despite a valid full-citation antecedent existing in the same
   document.** "Ohio Valley, 556 F.3d at 201" (2 occurrences) and "Overton
   Park, 401 U.S. at 415/420" (2 occurrences) all show
   `resolution_state=unresolved`, even though the full citation is present
   earlier in the same brief. Plausibly downstream of finding #1 — if
   short-form linking matches on captured case name, a truncated antecedent
   name would break the match. Worth checking against `packages/pipeline`'s
   antecedent-resolution logic directly.

3. **The same normalized citation gets inconsistent existence outcomes
   across duplicate occurrences within one document.** "e360 Insight v.
   Spamhaus" (500 F.3d 594, cited twice in Cole) resolved to `VERIFIED` once
   and `WEAKLY_CORROBORATED` (via investigator) once. "Walsh v. Dania Inc."
   (716 F. Supp. 3d 655, also cited twice) resolved to `WEAKLY_CORROBORATED`
   once and stayed `UNVERIFIABLE` (looks stuck, not obviously investigated to
   a terminal state) once. Same citation, same document, different verdicts —
   worth checking for a race between concurrent citation tasks racing to
   populate `SourceAcquisition`/`LookupCache` (NFR-REL-002 idempotency).

4. **One of the two genuinely fabricated Cole citations was not caught with
   a clear verdict.** Fuller v. Athleta correctly reached `NOT_FOUND_ANYWHERE`
   (see above), but Nat'l Fed'n of the Blind v. Thrustmaster of Am., Inc.
   (249 F. Supp. 3d 676 — also independently confirmed 404 on CourtListener,
   equally fabricated) landed in `resolution_state=unrecognized` and stayed at
   plain `UNVERIFIABLE`, never reaching a terminal post-investigation verdict.
   **This means the demo, run against this exact brief today, would currently
   miss flagging one of the two fabricated citations with a clear red
   result.** Worth checking whether the `unrecognized` (CourtListener 400/
   empty-lookup) branch actually triggers `process_investigation` the same
   way the `not_in_database` (404) branch does — P3.md's plan says it should;
   this run suggests it may not, in practice, reach a terminal verdict.

5. **Proposition-support recall on "real case, misattributed holding" is
   currently 0/6 for a confident verdict.** Every citation in this pilot
   where a real case was quoted or characterized inaccurately (Ohio Valley,
   Dow AgroSciences, Sierra Club v. DOI, Liebhart, Goodman, Sierra Club v.
   Franklin) resolved to `UNVERIFIABLE` on the proposition check, not a
   confident `CONTRADICTS`/`PARTIAL`. This isn't a bug — `UNVERIFIABLE` is
   the SPEC-mandated honest fallback when confidence is low — but it is a
   real, measured limitation: **the specific failure mode both `OVERVIEW.md`
   and this pilot's own source cases center the product's story on
   (misrepresented holdings) is not yet reliably caught with a confident
   verdict.** The literal-fabricated-quote pattern fares somewhat better (2
   of the ~10 short-form quote checks in Fivehouse did reach
   `NOT_FOUND_IN_SOURCE`). This is exactly the kind of number the P4
   evaluation page (`FR-EVL-001`) exists to surface — recommend treating it
   as the top demo-risk item, ahead of any UI/export polish.

## Recommendation

None of the above blocks shipping the pilot eval page (`FR-EVL-002` can and
should present these numbers honestly — a 0/6 confident-detection rate on
the hardest failure mode is a legitimate, disclosable finding, not a reason
to hide the page). But #4 (one fabricated citation not reaching a terminal
verdict) and #5 (misrepresented-holding recall) are the two items most worth
fixing before a live demo, since they sit directly on the product's stated
thesis. Treat this file as the evaluation runner's first real output —
`eval/runner.py` (P4 plan step 6) should reproduce this comparison
automatically rather than requiring a manual run each time.
