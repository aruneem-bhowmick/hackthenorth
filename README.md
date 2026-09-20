<p align="center">
  <img src="assets/pincite-banner.svg" alt="PinCite" width="420">
</p>

<p align="center">
  <em>Because a brief is only as strong as the evidence backing it.</em>
</p>

# The Problem

There are already _[2,045 documented cases](https://www.damiencharlotin.com/hallucinations/)_ of AI hallucinations in international court filings, and those are only the ones that someone caught. The most dangerous errors use real-looking citations, altered quotes, or cases that do not support the claim.

# Our Value

**PinCite** levels the playing field by instantly fact-checking each citation against the actual source evidence. And beyond just delivering a verdict of AI hallucination, PinCite provides undeniable proof by displaying the original court texts side-by-side with the brief, enabling anyone to verify legal claims for free.

# Technical Implementation

PinCite begins with a legal brief. It extracts each cited case, quotation, and surrounding claim, then checks the citation against **CourtListener**, a public legal-opinion database. If the database cannot confidently resolve it, **Browserbase** and **Stagehand** take over: a bounded browser agent searches approved official court sites, extracts the decision into a typed record, preserves the source URL and session provenance, and can show the reviewer its live investigation.

For each unresolved opinion, **OpenAI** extracts the claim being supported and creates embeddings; **Elastic** indexes the opinion by paragraph and retrieves the most relevant source passages. OpenAI then judges the claim only against those retrieved passages, while deterministic checks validate quotations and reject unsupported model output.

**GPTZero** adds clearly labelled AI-writing and hallucination signals for triage, and **Sentry** traces the entire path from upload through retrieval, agent activity, AI calls, and reviewer interaction.

Together, the PinCite pipeline turns a legal brief into an auditable evidence trail: does the case exist, is the quote real, and does the authority actually support the claim?

### Built With

<table>
  <tr valign="top">
    <td><b>Sponsors</b></td>
    <td>
      <a href="https://www.browserbase.com"><img src="assets/logos/browserbase.png" height="40" alt="Browserbase"></a>&nbsp;&nbsp;
      <a href="https://openai.com"><img src="assets/logos/openai.png" height="40" alt="OpenAI"></a>&nbsp;&nbsp;
      <a href="https://www.elastic.co"><img src="assets/logos/elastic.svg" height="40" alt="Elastic"></a>&nbsp;&nbsp;
      <a href="https://gptzero.me"><img src="assets/logos/gptzero.png" height="40" alt="GPTZero"></a>&nbsp;&nbsp;
      <a href="https://sentry.io"><img src="assets/logos/sentry.svg" height="40" alt="Sentry"></a>
    </td>
  </tr>
  <tr valign="top">
    <td><b>Other<br>Technologies</b></td>
    <td>
      <img src="https://img.shields.io/badge/Python%203.12%2B-3776AB?style=flat&logo=python&logoColor=white" alt="Python 3.12+">
      <img src="https://img.shields.io/badge/TypeScript-3178C6?style=flat&logo=typescript&logoColor=white" alt="TypeScript">
      <img src="https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white" alt="FastAPI">
      <img src="https://img.shields.io/badge/Next.js%2016-000000?style=flat&logo=nextdotjs&logoColor=white" alt="Next.js 16">
      <img src="https://img.shields.io/badge/React%2019-20232A?style=flat&logo=react&logoColor=61DAFB" alt="React 19">
      <br>
      <img src="https://img.shields.io/badge/PostgreSQL-4169E1?style=flat&logo=postgresql&logoColor=white" alt="PostgreSQL">
      <img src="https://img.shields.io/badge/Redis-FF4438?style=flat&logo=redis&logoColor=white" alt="Redis">
      <img src="https://img.shields.io/badge/Docker-2496ED?style=flat&logo=docker&logoColor=white" alt="Docker">
      <img src="https://img.shields.io/badge/eyecite-5C6370?style=flat" alt="eyecite">
      <img src="https://img.shields.io/badge/Jina%20Embeddings-5C6370?style=flat" alt="Jina Embeddings">
      <img src="https://img.shields.io/badge/uv-DE5FE9?style=flat&logo=uv&logoColor=white" alt="uv">
    </td>
  </tr>
</table>
