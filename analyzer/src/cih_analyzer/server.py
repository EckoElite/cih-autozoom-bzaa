"""Entry point of the container: `python -m cih_analyzer.server`. Reads settings from the environment, opens the
database in the appdata folder, starts the scanner, the Plex sync and the workers, and serves the API."""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import create_app
from .config import ConfigError, Settings
from .pathmap import PathMap
from .plex import RedactingFilter
from .scheduler import Scheduler
from .store import Store

log = logging.getLogger("cih_analyzer")


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(RedactingFilter())


def build(settings: Settings) -> tuple[FastAPI, Scheduler, Store]:
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    store = Store(settings.db_path)
    pathmap = PathMap(settings.path_map)
    scheduler = Scheduler(settings, store, pathmap)
    inner = create_app(settings, store, scheduler, pathmap)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        scheduler.start()
        try:
            yield
        finally:
            scheduler.stop()
            store.close()

    inner.router.lifespan_context = lifespan
    return inner, scheduler, store


def main() -> int:
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level)
    if not settings.media_roots:
        log.warning("MEDIA_ROOTS is empty: nothing will be scanned")
    if not settings.plex_enabled:
        log.info("PLEX_URL or PLEX_TOKEN is not set: Plex ids, recently added and transcode pausing are off")
    log.info("starting: %s", settings)  # the repr of Settings hides the token and the API key
    app, _, _ = build(settings)
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.port, log_level=settings.log_level.lower())  # noqa: S104 - LAN service in a container
    return 0


if __name__ == "__main__":
    sys.exit(main())
