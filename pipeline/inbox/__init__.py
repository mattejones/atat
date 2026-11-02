"""
pipeline.inbox — read-only Gmail sync that links application emails to applications.

  gmail_client  fetch messages (Gmail API, gmail.readonly) into EmailMessage
  auth          one-time OAuth connect (`python -m pipeline.inbox.auth`) and status
  prefilter     cheap deterministic hints + a batched LLM triage: is this job-related?
  matcher       shortlist the applications an email could be about
  classifier    per-email LLM call: what kind of email, which application, evidence
  proposals     classification + current status -> the change to suggest (never backwards)
  sync          the whole pass, run as the gmail_sync job
  review        accept / relink / dismiss — the only path that changes an application

Named `inbox` rather than `email` so it can't shadow the stdlib email package.
"""
