import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes.applications import router as applications_router
from api.routes.generate import router as generate_router
from api.routes.prompts import router as prompts_router
from api.routes.questions import router as questions_router
from api.routes.render import router as render_router
from api.routes.review import router as review_router
from api.routes.scrape import router as scrape_router
from api.routes.sections import router as sections_router
from api.routes.settings import router as settings_router

log = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="ATAT — Application Tracking and Automation Tool",
        description="LLM-powered CV generation and job application tracking.",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://localhost:3001"],
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

        # ── Scheduler ─────────────────────────────────────────────────────────
        from pipeline.config import AUTO_GHOST_ENABLED, SCHEDULER_INTERVAL_HOURS
        if AUTO_GHOST_ENABLED:
            try:
                from apscheduler.schedulers.background import BackgroundScheduler
                from api.routes.applications import run_auto_ghost_job

                scheduler = BackgroundScheduler(daemon=True)
                scheduler.add_job(
                    run_auto_ghost_job,
                    trigger="interval",
                    hours=SCHEDULER_INTERVAL_HOURS,
                    id="auto_ghost",
                    replace_existing=True,
                )
                scheduler.start()
                app.state.scheduler = scheduler
                log.info(
                    "Auto-ghost scheduler started (interval: %dh, threshold: %d days)",
                    SCHEDULER_INTERVAL_HOURS,
                    __import__("pipeline.config", fromlist=["AUTO_GHOST_DAYS"]).AUTO_GHOST_DAYS,
                )
            except Exception as e:
                log.error(f"Scheduler startup failed: {e}")
        else:
            log.info("Auto-ghost scheduler disabled (AUTO_GHOST_ENABLED=false)")

    @app.on_event("shutdown")
    async def on_shutdown():
        scheduler = getattr(app.state, "scheduler", None)
        if scheduler and scheduler.running:
            scheduler.shutdown(wait=False)
            log.info("Auto-ghost scheduler stopped")

    app.include_router(applications_router)
    app.include_router(generate_router)
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
