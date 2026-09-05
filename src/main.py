"""opencloud-docserver — FastAPI application entry point.

Stoic Linux document server for OpenCloud (OCIS) WOPI integration.

Run locally:   uv run uvicorn src.main:app --reload
Run via CLI:   python -m src.main
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Config, load_config
from .editor.router import router as editor_router
from .editor.session import SessionRegistry
from .lib.store import DocumentStore, ensure_dirs
from .wopi.protocol import WopiError
from .wopi.router import router as wopi_router

LOG = logging.getLogger("opencloud-docserver")
CONFIG: Config | None = None


def create_app(config: Config | None = None) -> FastAPI:
    """Build the FastAPI application, wiring storage and routes."""
    cfg = config or load_config()
    ensure_dirs(cfg.database, cfg.content_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = DocumentStore(cfg.database, cfg.content_dir)
        app.state.sessions = SessionRegistry()
        app.state.config = cfg
        LOG.info("docserver ready: host=%s wopi=%s", cfg.public_url, cfg.wopi_host or "(local)")
        yield

    app = FastAPI(title="opencloud-docserver", version="0.1.0", lifespan=lifespan)

    origins = cfg.cors_origin_list
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-WOPI-Lock", "X-WOPI-ItemVersion"],
    )

    app.include_router(wopi_router)
    app.include_router(editor_router)

    web_dir = Path(__file__).resolve().parent.parent / "web"
    app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

    @app.exception_handler(WopiError)
    async def wopi_error_handler(request: Request, exc: WopiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"error": exc.message})

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        templates = Jinja2Templates(directory=str(web_dir))
        return templates.TemplateResponse(request, "home.html", {})

    @app.get("/health")
    async def health(request: Request) -> dict:
        store = request.app.state.store
        return {
            "status": "ok",
            "documents": len(store.list()),
            "db": cfg.database,
        }

    return app


def main() -> None:
    """CLI entry point: runs uvicorn with config from config.toml/env."""
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    cfg = load_config()
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port)


app = create_app()

if __name__ == "__main__":
    main()
