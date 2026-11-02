import logging
import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.auth import auth_required, require_auth, router as auth_router
from api.routes.applications import router as applications_router
from api.routes.counts import router as counts_router
from api.routes.cover_letter import router as cover_letter_router
from api.routes.generate import router as generate_router
from api.routes.inbox import router as inbox_router
from api.routes.insights import router as insights_router
from api.routes.jobs import router as jobs_router
from api.routes.prompts import router as prompts_router
from api.routes.questions import router as questions_router
from api.routes.render import router as render_router
from api.routes.review import router as review_router
from api.routes.scrape import router as scrape_router
from api.routes.sections import router as sections_router
from api.routes.settings import router as settings_router

log = logging.getLogger(__name__)


class _QuietPolls(logging.Filter):
    """
    Drop uvicorn access-log lines for the web UI's background polls — the nav badge
    counts, fetched every 30s from every open tab — when they succeed. Failures still log.
    """
    QUIET = ("/jobs?status=draft", "/inbox/counts", "/counts")

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 5:
            _, method, path, _, status = args[:5]
            if method == "GET" and str(path).startswith(self.QUIET) and int(status) < 400:
                return False
        return True


logging.getLogger("uvicorn.access").addFilter(_QuietPolls())


def _scheduled_gmail_sync() -> None:
    """Queue a Gmail sync as a job, so it shows in Recent activity like any other."""
    from pipeline.inbox.sync import start_sync_job

    try:
        start_sync_job(created_by="agent")
    except Exception as e:  # not connected, etc. — log and try again next interval
        log.warning("Scheduled Gmail sync not started: %s", e)


def create_app() -> FastAPI:
    protected = auth_required()
    app = FastAPI(
        title="ATAT — Application Tracking and Automation Tool",
        description="LLM-powered CV generation and job application tracking.",
        version="0.1.0",
        # Every route needs a session when ATAT_PASSWORD is set (see api/auth.py). The API
        # docs would list the whole surface to anyone, so they are off in that case.
        dependencies=[Depends(require_auth)],
        docs_url=None if protected else "/docs",
        redoc_url=None if protected else "/redoc",
        openapi_url=None if protected else "/openapi.json",
    )

    # ATAT_EXTRA_CORS_ORIGINS (comma-separated) admits another frontend origin — e.g. a
    # second dev server on a spare port — without editing this list.
    extra_origins = [o.strip() for o in os.getenv("ATAT_EXTRA_CORS_ORIGINS", "").split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:18080", "http://localhost:3001", *extra_origins],
        allow_credentials=True,   # the session cookie, if the UI talks to the API cross-origin
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    async def on_startup():
        log.info("Running database migrations...")
        try:
            from db.migrate import run_migrations
            run_migrations()
        except Exception as e:
            log.error(f"Migration failed: {e}")
            raise

        # ── Job runner ────────────────────────────────────────────────────────
        # Started now rather than on first use, so jobs orphaned by a previous run
        # (killed mid-generation, or queued but never claimed) are recovered at startup.
        try:
            from pipeline.jobs.runner import get_runner
            get_runner()
        except Exception as e:
            log.error(f"Job runner startup failed (will retry on first job): {e}")

        # ── Scheduler ─────────────────────────────────────────────────────────
        # One BackgroundScheduler for every periodic task; it's only started if at
        # least one of them is enabled.
        from pipeline.config import (
            AUTO_GHOST_DAYS, AUTO_GHOST_ENABLED, GMAIL_ENABLED,
            GMAIL_SYNC_INTERVAL_MINUTES, SCHEDULER_INTERVAL_HOURS,
        )
        if AUTO_GHOST_ENABLED or GMAIL_ENABLED:
            try:
                from apscheduler.schedulers.background import BackgroundScheduler

                scheduler = BackgroundScheduler(daemon=True)
                if AUTO_GHOST_ENABLED:
                    from api.routes.applications import run_auto_ghost_job

                    scheduler.add_job(
                        run_auto_ghost_job,
                        trigger="interval",
                        hours=SCHEDULER_INTERVAL_HOURS,
                        id="auto_ghost",
                        replace_existing=True,
                    )
                    log.info(
                        "Auto-ghost scheduled (interval: %dh, threshold: %d days)",
                        SCHEDULER_INTERVAL_HOURS, AUTO_GHOST_DAYS,
                    )
                else:
                    log.info("Auto-ghost disabled (AUTO_GHOST_ENABLED=false)")
                if GMAIL_ENABLED:
                    from datetime import datetime, timedelta

                    scheduler.add_job(
                        _scheduled_gmail_sync,
                        trigger="interval",
                        minutes=GMAIL_SYNC_INTERVAL_MINUTES,
                        next_run_time=datetime.now() + timedelta(seconds=30),
                        id="gmail_sync",
                        replace_existing=True,
                    )
                    log.info("Gmail sync scheduled (interval: %d min)", GMAIL_SYNC_INTERVAL_MINUTES)
                scheduler.start()
                app.state.scheduler = scheduler
            except Exception as e:
                log.error(f"Scheduler startup failed: {e}")
        else:
            log.info("Scheduler disabled (AUTO_GHOST_ENABLED=false, GMAIL_ENABLED=false)")

    @app.on_event("shutdown")
    async def on_shutdown():
        scheduler = getattr(app.state, "scheduler", None)
        if scheduler and scheduler.running:
            scheduler.shutdown(wait=False)
            log.info("Scheduler stopped")

    app.include_router(auth_router)
    app.include_router(applications_router)
    app.include_router(counts_router)
    app.include_router(cover_letter_router)
    app.include_router(generate_router)
    app.include_router(inbox_router)
    app.include_router(insights_router)
    app.include_router(jobs_router)
    app.include_router(prompts_router)
    app.include_router(questions_router)
    app.include_router(render_router)
    app.include_router(review_router)
    app.include_router(scrape_router)
    app.include_router(sections_router)
    app.include_router(settings_router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
