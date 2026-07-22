"""
DitakNet application entry point (``ditaknet/main.py``).

Startup order (see ``lifespan``):
  1. Logging + production/directory validation
  2. Database init (SQLite under ``DATA_DIR`` / ``DATABASE_URL``)
  3. State + alert engines and notifiers (console always; Telegram optional)
  4. Plugin registration (extends ``CHECK_REGISTRY``)
  5. Scheduler start (per-service jobs + system jobs)

Shutdown reverses scheduler/plugins and closes the DB connection.
First-run setup wizard at ``/setup`` when ``setup_complete`` is unset in the database.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from loguru import logger
from starlette.middleware.sessions import SessionMiddleware

from ditaknet import database as db
from ditaknet.api.deps import set_engines
from ditaknet.api.discovery import router as discovery_router
from ditaknet.api.branches import router as branches_router
from ditaknet.api.hr import router as hr_router
from ditaknet.api.employee_presence import router as employee_presence_router
from ditaknet.api.license import router as license_router
from ditaknet.api.profiles import router as profiles_router
from ditaknet.api.backups_ops import router as backups_ops_router
from ditaknet.api.device_detail import router as device_detail_router
from ditaknet.api.notifications import router as notifications_router
from ditaknet.api.navigation import router as navigation_router
from ditaknet.api.devices_ops import router as devices_ops_router
from ditaknet.api.assistant_api import router as assistant_router
from ditaknet.api.topology import router as topology_router
from ditaknet.api.bulk import router as bulk_router
from ditaknet.api.maintenance_tasks import router as maintenance_tasks_router
from ditaknet.api.setup import router as setup_api_router
from ditaknet.api.users import router as users_router
from ditaknet.api.system_health import activity_router, router as system_health_router
from ditaknet.api.system_logs import health_router as system_health_legacy_router
from ditaknet.api.system_logs import router as system_logs_api_router
from ditaknet.api.system_metrics import router as system_metrics_api_router
from ditaknet.api.v1 import router as v1_router
from ditaknet.api.v1.system import system_router
from ditaknet.config import settings
from ditaknet.core.alert_engine import AlertEngine
from ditaknet.core.licensing import license_service
from ditaknet.core.process_lock import acquire_runtime_lock
from ditaknet.core.runtime_settings import get_telegram_config
from ditaknet.core.scheduler import Scheduler
from ditaknet.core.state_engine import StateEngine
from ditaknet.health import basic_health, deep_health
from ditaknet.logging_config import configure_logging, log_startup_summary
from ditaknet.notifications.console import ConsoleNotifier
from ditaknet.notifications.telegram import TelegramNotifier
from ditaknet.plugins.manager import PluginManager
from ditaknet.resilience import (
    install_asyncio_exception_handler,
    install_fastapi_exception_handlers,
    install_request_id_middleware,
)
from ditaknet.startup import (
    StartupError,
    prepare_runtime_directories,
    validate_production_settings,
    validate_writable_directories,
)
from ditaknet.web.discovery_routes import router as discovery_web_router
from ditaknet.web.routes import router as web_router
from ditaknet.web.device_routes import router as device_web_router
from ditaknet.web.hr_routes import router as hr_web_router
from ditaknet.web.employee_presence_routes import router as employee_presence_web_router
from ditaknet.web.setup_routes import router as setup_web_router
from ditaknet.web.operations_routes import router as operations_web_router
from ditaknet.web.about_routes import router as about_web_router
from ditaknet.web.legal_routes import router as legal_web_router
from ditaknet.web.system_activity_routes import router as system_activity_web_router
from ditaknet.web.updates_routes import router as updates_web_router
from ditaknet.web.appearance_routes import router as appearance_web_router
from ditaknet.web.system_logs_routes import router as system_logs_web_router
from ditaknet.web.user_routes import router as user_web_router
from ditaknet.web.license_routes import router as license_web_router
from ditaknet.web.domain_routes import router as domain_web_router

system_router.include_router(license_router)


@asynccontextmanager
async def _database_runtime_ownership():
    """Own the database directory until every runtime writer has stopped."""

    runtime_lock = acquire_runtime_lock(settings.db_path.parent)
    try:
        await db.init_db(str(settings.db_path))
        yield
    finally:
        try:
            await db.close_db()
        finally:
            runtime_lock.release()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: validate environment, wire engines, start scheduler.

    Production (``APP_ENV=production``) fails fast on weak secrets.
    Development logs warnings but continues so local tests stay frictionless.
    """
    configure_logging()
    install_asyncio_exception_handler()
    logger.info("Starting {}…", settings.app_name)

    try:
        validate_production_settings()
        dir_status = prepare_runtime_directories()
        validate_writable_directories(dir_status)
    except StartupError as exc:
        logger.error("Startup validation failed: {}", exc)
        if settings.is_production:
            raise
        logger.warning("Continuing in non-production mode despite startup warnings")

    log_startup_summary()

    async with _database_runtime_ownership():
        interrupted_scans = await db.mark_interrupted_discovery_scans()
        if interrupted_scans:
            logger.warning(
                "Marked {} interrupted discovery scan(s) as failed",
                interrupted_scans,
            )
        await license_service.ensure_default_license()

        state_engine = StateEngine()
        alert_engine = AlertEngine()
        scheduler = Scheduler(state_engine=state_engine, alert_engine=alert_engine)

        alert_engine.register_notifier(ConsoleNotifier())
        tg_token, tg_chat = await get_telegram_config()
        if tg_token and tg_chat:
            alert_engine.register_notifier(
                TelegramNotifier(bot_token=tg_token, chat_id=tg_chat)
            )
            logger.info("Telegram notifications enabled")
        elif settings.telegram_enabled:
            alert_engine.register_notifier(TelegramNotifier())
            logger.info("Telegram notifications enabled")
        else:
            logger.info(
                "Telegram not configured — console notification fallback active"
            )

        plugin_manager = PluginManager()
        app_context = {
            "app": app,
            "state_engine": state_engine,
            "alert_engine": alert_engine,
            "scheduler": scheduler,
            "settings": settings,
        }
        await plugin_manager.load_all(app_context)

        set_engines(
            scheduler=scheduler,
            state_engine=state_engine,
            alert_engine=alert_engine,
        )

        if settings.scheduler_enabled:
            try:
                await scheduler.start()
            except Exception as exc:
                logger.error("Scheduler failed to start: {}", exc)
                if settings.is_production:
                    raise
                logger.warning("Continuing without scheduler in non-production mode")
        else:
            logger.warning("Scheduler disabled via SCHEDULER_ENABLED=false")

        from ditaknet.discovery.auto_import import auto_import_all_pending
        from ditaknet.resilience import create_background_task

        async def _startup_auto_import() -> None:
            try:
                active_scheduler = scheduler if settings.scheduler_enabled else None
                await auto_import_all_pending(scheduler=active_scheduler)
            except Exception as exc:
                logger.warning("Startup auto-import skipped: {}", exc)

        create_background_task(
            _startup_auto_import(), name="discovery_startup_auto_import"
        )

        try:
            from ditaknet.core.updates import start_update_checker

            start_update_checker()
        except Exception as exc:
            logger.warning("Update checker startup skipped: {}", exc)

        logger.info("{} ready", settings.app_name)
        try:
            yield
        finally:
            logger.info("Shutting down…")
            try:
                from ditaknet.core.updates import stop_update_checker

                await stop_update_checker()
            except Exception:
                pass
            if settings.scheduler_enabled:
                await scheduler.stop()
            await plugin_manager.unload_all()
            logger.info("Shutdown complete")


app = FastAPI(
    title=f"{settings.app_name} API",
    description=(
        "DitakNet local network visibility, monitoring, discovery, alerting, "
        "and IT support API."
    ),
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)
install_fastapi_exception_handlers(app)
install_request_id_middleware(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.effective_secret_key,
    session_cookie="ditaknet_session",
    max_age=86400 * 7,
    same_site="lax",
    # Docker/TrueNAS installs are often opened over local HTTP. Honor the
    # explicit setting so local admin login does not create an unusable
    # Secure cookie; HTTPS deployments can set SESSION_COOKIE_SECURE=true.
    https_only=settings.session_cookie_secure,
)


class _AssetCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/static/") and path.endswith((".css", ".js")):
            response.headers["Cache-Control"] = "public, max-age=0, must-revalidate"
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response


app.add_middleware(_AssetCacheMiddleware)

app.include_router(v1_router)
app.include_router(discovery_router, prefix="/api")
app.include_router(employee_presence_router, prefix="/api")
app.include_router(hr_router, prefix="/api")
app.include_router(branches_router, prefix="/api")
app.include_router(profiles_router, prefix="/api")
app.include_router(backups_ops_router, prefix="/api")
app.include_router(license_router, prefix="/api")
app.include_router(notifications_router, prefix="/api")
app.include_router(navigation_router, prefix="/api")
app.include_router(device_detail_router, prefix="/api")
app.include_router(devices_ops_router, prefix="/api")
app.include_router(assistant_router, prefix="/api")
app.include_router(topology_router, prefix="/api")
app.include_router(bulk_router, prefix="/api")
app.include_router(maintenance_tasks_router, prefix="/api")
app.include_router(setup_api_router, prefix="/api")
app.include_router(users_router, prefix="/api")
app.include_router(system_health_router, prefix="/api")
app.include_router(activity_router, prefix="/api")
app.include_router(system_metrics_api_router, prefix="/api")
app.include_router(system_logs_api_router, prefix="/api")
app.include_router(system_health_legacy_router, prefix="/api")
app.include_router(system_router, prefix="/api")

static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")
app.include_router(web_router)
app.include_router(device_web_router)
app.include_router(license_web_router)
app.include_router(domain_web_router)
app.include_router(discovery_web_router)
app.include_router(employee_presence_web_router)
app.include_router(hr_web_router)
app.include_router(setup_web_router)
app.include_router(operations_web_router)
app.include_router(about_web_router)
app.include_router(legal_web_router)
app.include_router(system_activity_web_router)
app.include_router(system_logs_web_router)
app.include_router(updates_web_router)
app.include_router(appearance_web_router)
app.include_router(user_web_router)


_SETUP_EXEMPT = (
    "/setup",
    "/about",
    "/support",
    "/health",
    "/static",
    "/info",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/setup",
    "/api/system/version",
    "/api/system/update-status",
    "/api/system/about",
    "/login",
    "/logout",
)


@app.middleware("http")
async def setup_gate_middleware(request, call_next):
    """Redirect unconfigured installs to the setup wizard."""
    path = request.url.path
    if any(path.startswith(prefix) for prefix in _SETUP_EXEMPT):
        return await call_next(request)
    try:
        from ditaknet.core.setup_state import needs_setup_redirect

        if await needs_setup_redirect():
            if path.startswith("/api"):
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Setup required", "setup_url": "/setup"},
                )
            return RedirectResponse(url="/setup", status_code=303)
    except Exception as exc:
        logger.warning("Setup gate check failed for {}: {}", path, exc)
    return await call_next(request)


@app.middleware("http")
async def security_headers(request, call_next):
    """Attach conservative browser security headers to every response."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=()",
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'",
    )
    if request.url.scheme == "https":
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
    return response


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return RedirectResponse(url="/static/img/favicon.png", status_code=307)


@app.get("/info", tags=["root"])
async def app_info():
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
        "dashboard": "/dashboard",
    }


@app.get("/health", tags=["health"])
async def health_check():
    return await basic_health()


@app.get("/health/deep", tags=["health"])
async def health_check_deep():
    return await deep_health()
