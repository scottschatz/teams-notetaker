"""
FastAPI Application Factory

Creates and configures the FastAPI web application with all routers and middleware.
"""

import logging
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import uvicorn

from ..core.config import get_config
from ..core.logging_config import setup_logging


logger = logging.getLogger(__name__)

# Rate limiter instance (shared across routes)
limiter = Limiter(key_func=get_remote_address)


def create_app() -> FastAPI:
    """
    Create and configure FastAPI application.

    Returns:
        Configured FastAPI app
    """
    config = get_config()

    app = FastAPI(
        title="Teams Meeting Transcript Summarizer",
        description="Automatically summarize Teams meeting transcripts using AI",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc"
    )

    # Configure middleware
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production, specify allowed origins
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"]
    )

    # Configure rate limiting
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Set up templates
    templates_dir = Path(__file__).parent / "templates"
    templates_dir.mkdir(exist_ok=True)

    app.state.templates = Jinja2Templates(directory=str(templates_dir))

    # Set up static files
    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(exist_ok=True)

    # Create subdirectories
    (static_dir / "css").mkdir(exist_ok=True)
    (static_dir / "js").mkdir(exist_ok=True)

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Store config in app state
    app.state.config = config

    # Register routers (imported here to avoid circular imports)
    from .routers import auth, dashboard, meetings, health, admin, diagnostics, analytics

    app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
    app.include_router(dashboard.router, prefix="/dashboard", tags=["Dashboard"])
    app.include_router(admin.router, tags=["Admin"])
    app.include_router(meetings.router, prefix="/api/meetings", tags=["Meetings API"])
    app.include_router(health.router, prefix="/api", tags=["Health"])
    app.include_router(diagnostics.router, tags=["Diagnostics"])
    app.include_router(analytics.router, tags=["Analytics"])

    # Root redirect
    @app.get("/", response_class=HTMLResponse)
    async def root():
        """Redirect to dashboard."""
        return RedirectResponse(url="/dashboard")

    # Exception handlers
    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        """Handle 404 errors."""
        templates = request.app.state.templates
        return templates.TemplateResponse(
            "404.html",
            {"request": request},
            status_code=404
        )

    logger.info("FastAPI application created successfully")

    return app


def run_server(host: str = "0.0.0.0", port: int = 8000, reload: bool = False):
    """
    Run the FastAPI server using uvicorn.

    Args:
        host: Host to bind to
        port: Port to bind to
        reload: Enable auto-reload on code changes
    """
    # Set up logging
    setup_logging(log_file="logs/web.log")

    logger.info(f"Starting web server on {host}:{port} (reload: {reload})")

    # Create app
    app = create_app()

    # Run server
    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=reload,
        log_level="info"
    )


if __name__ == "__main__":
    run_server(reload=True)
