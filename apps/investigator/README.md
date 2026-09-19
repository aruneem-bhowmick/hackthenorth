# apps/investigator

Stagehand/Browserbase integration backing the fallback resolution agent
(SPEC.md FR-INV-*). **Not implemented yet** — that's P3 scope (search →
fetch → extract → match against `config/allowlist.yaml` / `denylist.yaml`).

## ADR-005 outcome (P0 gate)

`docs/adr/ADR-005-stagehand-python-sdk.md` required deciding, at the P0
gate, whether Stagehand's **Python** SDK works on this stack or whether to
fall back to a TypeScript sidecar (`POST /investigate`).

**Result: Python SDK.** `investigator/smoke_test.py` opened a live
Browserbase session via `stagehand==4.1.0` / `browserbase==1.19.0`,
navigated to `https://www.uscourts.gov`, read the real page title, and
closed the session cleanly — no sidecar needed.

One implementation note worth keeping for P3: Stagehand's Python SDK
(4.1.0) never sends `project_id` when creating a Browserbase session
(`BrowserbaseBrowser.launch()` has no such parameter, and Stagehand's
internal session-create call doesn't include it either) — it worked anyway,
so Browserbase resolves the caller's default project server-side from the
API key alone. `BROWSERBASE_PROJECT_ID` in `.env` is unused by this SDK
path; kept for the REST-based smoke check in `apps/api/app/smoke.py`,
which calls `GET /v1/projects/{project_id}` directly and does need it.

## Running the smoke test yourself

```
cd apps/investigator
uv run python -m investigator.smoke_test
```

Requires `BROWSERBASE_API_KEY` in the environment. Opens a real, billable
Browserbase session — not part of the automated test suite.
