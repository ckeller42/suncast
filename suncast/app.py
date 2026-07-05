import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from suncast import geocode as geocode_mod
from suncast import jobs
from suncast.calibrate import apply_factor, best_window, calibration, metrics
from suncast.config import Config, load
from suncast.influx import InfluxReader, make_query_fn, make_write_fn
from suncast.jobs import Deps
from suncast.models import PanelConfig
from suncast.providers.forecast_solar import (
    ForecastSolar,
    ProviderError,
    RateLimited,
    default_fetch,
)
from suncast.store import Store

logger = logging.getLogger(__name__)

JOB_INTERVAL_S = 3600

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _cal_dict(cal) -> dict:
    return {
        "factor": cal.factor,
        "p25": cal.p25,
        "p75": cal.p75,
        "samples": cal.samples,
        "calibrated": cal.calibrated,
    }


async def _job_loop(app: FastAPI) -> None:
    while True:
        try:
            jobs.daily_tick(
                Deps(
                    provider=app.state.provider,
                    store=app.state.store,
                    influx=app.state.influx,
                    now=lambda: datetime.now(UTC),
                    write=getattr(app.state, "write", None),
                    forecast_measurement=app.state.cfg.forecast_measurement,
                    drift_km_max=app.state.cfg.drift_km_max,
                )
            )
        except Exception:
            logger.exception("daily_tick failed")
        await asyncio.sleep(JOB_INTERVAL_S)


def create_app(cfg: Config, provider, store: Store, influx, write=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if not getattr(app.state, "no_jobs", False):
            task = asyncio.create_task(_job_loop(app))
        yield
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(lifespan=lifespan)
    app.state.cfg = cfg
    app.state.provider = provider
    app.state.write = write
    app.state.store = store
    app.state.influx = influx

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {})

    @app.get("/history")
    def history_page(request: Request):
        return templates.TemplateResponse(request, "history.html", {})

    @app.post("/api/forecast")
    def forecast(body: dict = Body(...)):  # noqa: B008
        lat = body.get("lat")
        lon = body.get("lon")
        if lat is None or lon is None:
            raise HTTPException(status_code=422, detail="lat and lon are required")
        if (
            not isinstance(lat, int | float)
            or isinstance(lat, bool)
            or not isinstance(lon, int | float)
            or isinstance(lon, bool)
        ):
            raise HTTPException(status_code=422, detail="lat and lon must be numbers")

        days = body.get("days", 3)
        if not isinstance(days, int) or not (1 <= days <= 6):
            raise HTTPException(status_code=422, detail="days must be int 1..6")

        panel_body = body.get("panel")
        if panel_body:
            try:
                panel = PanelConfig(**panel_body)
            except TypeError as e:
                raise HTTPException(status_code=422, detail="invalid panel") from e
        else:
            panel = store.get_panel()

        try:
            series = provider.forecast(lat, lon, panel, days)
        except RateLimited as e:
            raise HTTPException(status_code=429, detail=str(e)) from e
        except ProviderError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e

        ratios = [r.ratio for r in store.ratios()]
        cal = calibration(ratios, cfg.window_days, cfg.min_samples, cfg.clamp_lo, cfg.clamp_hi)
        out = apply_factor(series, cal)
        best = best_window(series.points)

        return {
            "location": {"lat": lat, "lon": lon},
            "factor": _cal_dict(cal),
            "hourly": out["hourly"],
            "daily": out["daily"],
            "best_windows": best,
        }

    @app.get("/api/history")
    def history(days: int = 30):
        ratios_newest_first = store.ratios()
        # Newest `days` rows, then chronological for display. (Reverse-then-
        # slice kept the OLDEST rows once history exceeded `days`.)
        ratios_oldest_first = list(reversed(ratios_newest_first[:days]))

        raw_pairs = [(r.forecast_wh, r.actual_wh) for r in ratios_oldest_first]
        metrics_raw = metrics(raw_pairs)

        cal = calibration(
            [r.ratio for r in ratios_newest_first],
            cfg.window_days,
            cfg.min_samples,
            cfg.clamp_lo,
            cfg.clamp_hi,
        )

        return {
            "days": [
                {
                    "day": r.day,
                    "forecast_wh": r.forecast_wh,
                    "actual_wh": r.actual_wh,
                    "ratio": r.ratio,
                }
                for r in ratios_oldest_first
            ],
            "metrics_raw": metrics_raw,
            "factor": _cal_dict(cal),
        }

    @app.get("/api/config")
    def get_config():
        return asdict(store.get_panel())

    @app.post("/api/config")
    def post_config(body: dict = Body(...)):  # noqa: B008
        try:
            panel = PanelConfig(**body)
        except TypeError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e

        int_fields = ("panel_wp", "charger_limit_w")
        numeric_fields = ("tilt_deg", "azimuth_deg", "damping")
        valid = all(
            isinstance(getattr(panel, f), int) and not isinstance(getattr(panel, f), bool)
            for f in int_fields
        ) and all(
            isinstance(getattr(panel, f), int | float) and not isinstance(getattr(panel, f), bool)
            for f in numeric_fields
        )
        if not valid:
            raise HTTPException(status_code=422, detail="invalid panel value types")

        store.set_panel(panel)
        return asdict(panel)

    @app.get("/api/geocode")
    def geocode(q: str = ""):
        q = q.strip()
        if not q:
            raise HTTPException(status_code=422, detail="q is required")
        try:
            fetch = getattr(app.state, "geocode_fetch", None) or geocode_mod.default_fetch
            return {"results": geocode_mod.search(q, fetch)}
        except geocode_mod.GeocodeError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e

    @app.get("/api/current-location")
    def current_location():
        loc = influx.latest_location()
        if loc is None:
            raise HTTPException(status_code=404, detail="no location available")
        lat, lon, range_m, age_s = loc
        return {"lat": lat, "lon": lon, "range_m": range_m, "age_s": age_s}

    @app.get("/api/health")
    def health():
        try:
            influx_ok = influx.latest_location() is not None
        except Exception:
            influx_ok = False

        try:
            last_snapshot_age_s = store.last_snapshot_age_s(datetime.now(UTC))
        except Exception:
            last_snapshot_age_s = None

        try:
            ratios_count = len(store.ratios())
        except Exception:
            ratios_count = None

        return {
            "influx_ok": influx_ok,
            "last_snapshot_age_s": last_snapshot_age_s,
            "ratios": ratios_count,
        }

    return app


def main() -> None:
    cfg = load(os.environ)
    store = Store(cfg.db_path)
    provider = ForecastSolar(default_fetch, cfg.cache_ttl_s)
    influx = InfluxReader(cfg, make_query_fn(cfg))
    app = create_app(cfg, provider, store, influx, write=make_write_fn(cfg))
    uvicorn.run(app, host="0.0.0.0", port=cfg.port)


if __name__ == "__main__":
    main()
