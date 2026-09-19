"""ADR-005 P0 smoke test: does the Stagehand Python SDK work end-to-end
against a live Browserbase session, on this stack, without the TypeScript
sidecar fallback?

Run manually (not part of the automated test suite — it opens a real,
billable Browserbase session):

    uv run python -m investigator.smoke_test

Requires BROWSERBASE_API_KEY in the environment (see .env.example).
BROWSERBASE_PROJECT_ID is not required: Stagehand's Python SDK (4.1.0) does
not send project_id when creating a session — Browserbase resolves the
caller's default project server-side.

Outcome recorded 2026-09-18: PASSED. Opened a live session, navigated to
https://www.uscourts.gov (an allowlist-style official domain per
config/allowlist.yaml), read the real page title, closed the session
cleanly. See docs/adr/ADR-005-stagehand-python-sdk.md and this package's
README for the full record. Real investigator logic (search/fetch/extract/
match, FR-INV-*) is P3 scope and does not exist yet — this only proves the
SDK path is viable.
"""

import asyncio
import os
import sys

import truststore

# See apps/api/app/main.py for why this runs before any HTTPS client is built.
truststore.inject_into_ssl()

from stagehand import Stagehand, browserbase


async def run_smoke_test(target_url: str = "https://www.uscourts.gov") -> None:
    api_key = os.environ.get("BROWSERBASE_API_KEY")
    if not api_key:
        print("BROWSERBASE_API_KEY not set — cannot run the live smoke test.", file=sys.stderr)
        raise SystemExit(1)

    sh_browser = await browserbase.launch(api_key=api_key)
    print(f"[investigator smoke test] session_id={sh_browser.session_id}")

    sh = await Stagehand.create(browser=sh_browser)
    try:
        page = await sh.browser.context.new_page()
        await page.goto(target_url)
        title = await page.title()
        print(f"[investigator smoke test] navigated to {target_url!r}, title={title!r}")
    finally:
        await sh.close()
        print("[investigator smoke test] session closed")


if __name__ == "__main__":
    asyncio.run(run_smoke_test())
