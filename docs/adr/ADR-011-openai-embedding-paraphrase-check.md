# ADR-011: Use OpenAI `text-embedding-3-small` for P1 paraphrase recovery

Status: Accepted (explicit P1 implementation decision, 2026-09-19)

P1's deterministic quote aligner remains the primary check.  When it cannot
find a quote lexically, it may submit that quote and at most 32 local paragraphs
from the already-resolved CourtListener opinion to OpenAI's
`text-embedding-3-small` embeddings endpoint.  The resulting cosine score is
compared to the committed `paraphrase_semantic_similarity` threshold.

This does not introduce an LLM verdict or change any P1 verdict vocabulary.
The model is used only for the semantic prerequisite of
`PARAPHRASE_IN_QUOTES`; evidence remains the retrieved source paragraph and
the result records whether the semantic check completed.  An unavailable key
or provider preserves the existing `NOT_CONFIGURED` state rather than failing
the citation.

The request is deliberately bounded to control cost and latency for the demo.
Changing the threshold still requires a separate ADR and calibration evidence.
