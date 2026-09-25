"""Test-only: mount the valuation + alerts routers on the app when the integrator has not included them yet."""

from __future__ import annotations

from starlette.routing import Mount


def ensure_routers(app) -> None:
    from app.api.routers import alerts, valuation

    if any(getattr(r, "path", "").startswith("/api/valuation") for r in app.routes):
        return
    app.include_router(valuation.router)
    app.include_router(alerts.router)
    mounts = [r for r in app.router.routes if isinstance(r, Mount)]
    for m in mounts:  # keep the SPA catch-all last
        app.router.routes.remove(m)
        app.router.routes.append(m)
