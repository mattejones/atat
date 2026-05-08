import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes.applications import router as applications_router
from api.routes.generate     import router as generate_router
from api.routes.prompts      import router as prompts_router
from api.routes.render       import router as render_router
from api.routes.review       import router as review_router
from api.routes.scrape       import router as scrape_router
from api.routes.sections     import router as sections_router
from api.routes.settings     import router as settings_router

log = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="ATAT — Application Tracking and Automation Tool",
        description="LLM-powered CV generation and job application tracking.",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
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

    app.include_router(applications_router)
    app.include_router(generate_router)
    app.include_router(prompts_router)
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
