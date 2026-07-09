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
2. **Check for a dupe before doing anything else** — see the section below.
   If one exists, don't regenerate; pick up from its current status instead.
3. Before generating, check history: `search_applications`, `get_prompt_signals`,
   `get_success_stats`, and `get_recent_notes()` (past generation_notes —
   the guidance given on previous CVs, distinct from a specific
   application's freeform `notes` field) — bias the generation toward what's
   worked before, and fold anything still relevant into this call's
   `generation_notes`. If the JD calls out something specific (a technology,
   a methodology, a kind of achievement) that you're not sure the library
   covers well, use `search_library(query)` to find the relevant experience
   entry/persona before writing `generation_notes` that references it —
   don't guess at what's in the library, check it. Also pull
   `get_reference_cvs(tier=...)` — real CVs from applications that actually
   got *screened favorably* (interviewing/case_study/offered by default,
   not just applied — a CV that never got a response isn't evidence it
   worked) — and use their phrasing/structure/emphasis as the starting
   template rather than reconstructing everything from the raw cv-library
   each time. If it comes back empty for this persona/tier, that's real
   information (no proven-successful example exists yet) — don't silently
   widen to weaker-signal statuses without saying so.
4. `submit_job(jd_text, company, role, source_url, ...)` — creates the
   application, generates a CV, splits it into sections, runs the judge
   pipeline automatically, and renders an initial PDF.
5. `list_sections(app_uuid)` — each section comes back with `latest_report`.
   **`latest_report.id` is the `report_id`** every tool below needs. Don't
   use `accepted_report_id` for this — it's NULL until something's been
   accepted, so right after submit_job it's `latest_report.id` or nothing.
   Then per section: `get_report(report_id)` to see judge flags.
   - Clean (zero active flags) -> `accept_report(report_id)`.
   - Flagged -> `regenerate_section(report_id, global_comment?)` to retry
     against the flags, then re-run `list_sections` (or just note the
     `new_report_id` regenerate_section returns) to get the new report_id —
     or leave it and flag the whole application for human review, don't
     guess on subjective accuracy flags.
   - Want the full retry history for a section, not just the latest attempt?
     `get_section_chain(app_uuid, section_name)`.
6. `render_cv(app_uuid)` after any accept/regenerate — cv.md changes don't
   auto-render a new PDF.
7. `generate_cover_letter(app_uuid, ...)`, then `render_cover_letter(app_uuid)`.
8. If the JD posed application questions: `add_question` for each, then
   `generate_answers(app_uuid)`.
9. `get_application_bundle(app_uuid)` — check `ready` is true (PDF exists)
   before handing this to a browser tool to fill out the actual application.
10. After the browser tool submits: `record_submission(app_uuid, portal, confirmation?)`.

## Checking for a dupe before generating (the main reason to look at recent applications)
The whole point of glancing at recent applications is catching something you
(or a previous, interrupted session) already created for this same job — not
browsing history for its own sake. Do this every time, before `submit_job`:

1. Got a URL? `find_by_source_url(url)` — exact match, cheapest check, always
   do this first if there's a URL.
2. No hit, or no URL to check? `list_applications()` with no arguments — the
   default page is the 25 most recent applications, newest first, which is
   exactly the set that matters here: a dupe is by definition something
   recent. Scan it for the same company + role. You will essentially never
   need to page past the first page (`offset`) for this — if it's not in the
   most recent 25, it's not a recent dupe.
3. Still unsure (JD was pasted with no URL, and the recent list doesn't
   obviously show it)? `search_applications(company)` or
   `search_applications(role)` as a fuzzy fallback.

If any of these turn up a match, don't call `submit_job` again — resume
whatever step that application is already at (check its `status` via
`get_application`).

Note: `list_applications` is paginated (`limit`/`offset`, response includes
`total`/`has_more`) and each row is a summary — `jd_text`/`cv_markdown`/
`reasoning`/`notes`/`generation_notes` are stripped to keep the response
small. That's irrelevant for dupe-checking (you only need company/role/
status/source_url from the summary); it matters if you're doing a broader
history scan, where you'd page through with `offset` and call
`get_application(uuid)` for full content on anything specific.

## Fixing a recurring mistake permanently, not per-CV
If you (or the human) notice the *same* thing needs correcting across
multiple CVs — a phrase to avoid, a formatting habit, a framing that keeps
coming out wrong — don't just fix it in this one CV's `generation_notes` and
move on; that same correction will be needed again next time. Instead call
`add_personal_rule(rule)` once. It's appended to `personal_additions.md`,
which is loaded into the system prompt on *every* future `submit_job` call
— a one-time fix instead of a standing chore. `get_personal_additions()`
shows what's already there (check before adding, to avoid near-duplicate
rules). Note: this only affects fresh `submit_job` generations, not a
`regenerate_section` retry already in progress — a different, narrower
prompt is used for retries.

## The cv-library — raw source material, not generated output
`get_cv_markdown(app_uuid)` returns a *tailored, already-generated* CV for
one application. That's different from the cv-library — the canonical,
untailored experience entries/personas/skills every generation actually
draws from (`pipeline.tailorer.load_experience_files` etc. under the hood).
The library is the better source for "what could I include" — it's the
ground truth, not a derivative of it:
- `list_experience_entries()` / `get_experience_entry(filename)` — job
  history, one entry per role.
- `list_personas()` / `get_persona(name)` — different framings of the same
  experience (e.g. "sales-ops-leader" vs "account-executive").
- `get_skills()` — the full skills inventory.
- `get_meta()` — name/contact/links used in every CV header.
- `search_library(query)` — keyword search across all of the above without
  loading everything; returns matching snippets, not full files.

## What this server does NOT do
No Todoist or browser tools live here — that orchestration is expected to
happen in the calling agent, which may hold other MCP servers at once.
Whether final submission happens autonomously or waits for human review is
also not enforced here; it's a property of how the agent is operated.
"""
