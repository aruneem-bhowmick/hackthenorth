# ADR-010 — Defer FR-QTE-004's Elastic dependency to P2

**Status:** Accepted

**Context:** `SPEC.md` FR-QTE-004 (P1, Must) requires that when quote alignment
fails, the system "retrieve and display the closest passage via Elastic."
But Elastic paragraph indexing (`FR-IDX-*`) is scoped to P2 — P1 can't
literally satisfy FR-QTE-004 as written without pulling P2 infrastructure
forward. `TRACKS.md`'s own pipeline-stage mapping ties Elastic to the quote
fidelity stage ("candidate lookup"), so this gap exists in the planning
docs themselves, not something introduced during implementation.

**Decision:** For P1, implement the FR-QTE-004 fallback as a local,
in-process fuzzy match over the paragraphs of the opinion fetched for the
quote claim — no Elastic dependency. P1 retrieves opinions only for resolved
citations with quote claims; P2 expands FR-RES-004 to retrieve every resolved
authority. When P2 builds the real `pincite-paragraphs` index for FR-PRP-001's
hybrid retrieval, swap FR-QTE-004's candidate lookup to query that same index
instead of the local fallback.

**Consequences:**
- P1 needs no Elastic Cloud credential and stays consistent with ADR-002's
  "quote fidelity is deterministic and mechanical" principle — the closest-
  passage fallback is single-document search, which doesn't benefit from
  Elasticsearch's hybrid/semantic strengths anyway.
- The "closest actual language" behavior FR-QTE-004 promises to the user is
  unchanged — only the underlying search implementation differs by phase.
- P2 becomes a small swap (same caller-facing contract: best-matching
  passage text + location), not new logic, once the paragraph index exists
  for FR-PRP-001 regardless.
- FR-QTE-004's phase annotation should read "P1 (local fallback); Elastic-
  backed lookup from P2" rather than implying Elastic is required at P1.
