"""
guide.py — the ATAT MCP guide content, as plain data.

Single source of truth for the workflow-sequencing + valid-value glossary
text, imported by both resources.py (atat://guide, for clients that support
MCP resources) and tools_meta.py (get_guide tool, for clients that don't).
"""

GUIDE = """\
# ATAT MCP guide

## Identity
Applications are addressed everywhere by their `uuid` field, never `id`
(a filesystem slug used internally). Every tool that takes `app_uuid` means
the `uuid` value from list_applications/get_application/submit_job's response.

## Valid values
- status: generated | reviewing | applied | acknowledged | interviewing |
  case_study | offered | rejected | ghosted | excluded | archived
- tier: T1 | T2 | T3 | EX1
- work_arrangement: remote | hybrid | office
- section_name: profile | experience | skills | education | certifications
- flag type: hotword | sentence_length | readability | accuracy | ai_texture
- report status: pending | accepted | rejected
- feedback rating: positive | negative

## End-to-end workflow (job ad -> submitted application)
1. `scrape_job_url(url)` — if it errors or the text looks thin (JS-rendered
   board, login wall), fall back to a browser tool to extract the JD text
   yourself, then continue with that text.
2. Before generating, check history: `search_applications`, `get_prompt_signals`,
   `get_success_stats` — bias the generation toward what's worked before.
3. `submit_job(jd_text, company, role, source_url, ...)` — creates the
   application, generates a CV, splits it into sections, runs the judge
   pipeline automatically, and renders an initial PDF.
4. Per section: `get_report(report_id)` to see judge flags.
   - Clean (zero active flags) -> `accept_report(report_id)`.
   - Flagged -> `regenerate_section(report_id, global_comment?)` to retry
     against the flags, or leave it and flag the whole application for human
     review — don't guess on subjective accuracy flags.
5. `render_cv(app_uuid)` after any accept/regenerate — cv.md changes don't
   auto-render a new PDF.
6. `generate_cover_letter(app_uuid, ...)`, then `render_cover_letter(app_uuid)`.
7. If the JD posed application questions: `add_question` for each, then
   `generate_answers(app_uuid)`.
8. `get_application_bundle(app_uuid)` — check `ready` is true (PDF exists)
   before handing this to a browser tool to fill out the actual application.
9. After the browser tool submits: `record_submission(app_uuid, portal, confirmation?)`.

## Avoiding duplicate applications from a work queue
If job ads are coming from an external queue (e.g. Todoist) that might hand
you the same URL twice across restarts, check `find_by_source_url(url)`
before calling `submit_job` — it does an exact match, unlike the fuzzy
`search_applications`.

## What this server does NOT do
No Todoist or browser tools live here — that orchestration is expected to
happen in the calling agent, which may hold other MCP servers at once.
Whether final submission happens autonomously or waits for human review is
also not enforced here; it's a property of how the agent is operated.
"""
