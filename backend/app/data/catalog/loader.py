"""Catalog persistence: seed YAML -> SQLite `series` table; CRUD helpers used by the API and the refresh jobs."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from sqlalchemy import select

from app.data.providers.base import DEFAULT_TRANSFORM_BY_KIND, SeriesSpec
from app.data.store.models import Series, SeriesMeta
from app.data.store.sqlite import session_scope

log = logging.getLogger(__name__)
SEED_PATH = Path(__file__).with_name("seed_series.yaml")


def spec_from_row(row: Series) -> SeriesSpec:
    return SeriesSpec(
        series_id=row.series_id,
        provider=row.provider,
        provider_key=row.provider_key,
        field_name=row.field_name,
        name=row.name,
        description=row.description or "",
        freq=row.freq,  # type: ignore[arg-type]
        unit=row.unit,
        unit_mult=row.unit_mult,
        sa=row.sa,
        value_kind=row.value_kind,  # type: ignore[arg-type]
        default_transform=row.default_transform,  # type: ignore[arg-type]
        country=row.country,
        category=row.category,
        tags=list(row.tags or []),
        publication_lag_days=row.publication_lag_days,
        release_rule=row.release_rule,
        supports_vintage=row.supports_vintage,
        vintage_policy=row.vintage_policy,  # type: ignore[arg-type]
        plausible_min=row.plausible_min,
        plausible_max=row.plausible_max,
        license_note=row.license_note,
        fallbacks=list(row.fallbacks or []),
        formula=row.formula,
        params=dict(row.params or {}),
        enabled=row.enabled,
        schema_version=row.schema_version,
    )


def row_from_spec(spec: SeriesSpec) -> Series:
    d = spec.model_dump()
    return Series(**d)


def normalize_seed(item: dict) -> SeriesSpec:
    sid = item["series_id"]
    provider, key, fld = SeriesSpec.parse_id(sid)
    item = {**item, "provider": provider, "provider_key": key, "field_name": item.get("field_name", fld)}
    if "default_transform" not in item:
        item["default_transform"] = DEFAULT_TRANSFORM_BY_KIND.get(item.get("value_kind", "other"), "level")
    return SeriesSpec(**item)


async def load_seed(path: Path = SEED_PATH) -> int:
    """Insert seed rows that don't exist yet. Returns the number inserted."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    inserted = 0
    async with session_scope() as s:
        existing = set((await s.execute(select(Series.series_id))).scalars().all())
        for item in data.get("series", []):
            spec = normalize_seed(item)
            if spec.series_id in existing:
                continue
            s.add(row_from_spec(spec))
            s.add(SeriesMeta(series_id=spec.series_id))
            inserted += 1
    if inserted:
        log.info("catalog: seeded %d series", inserted)
    return inserted


async def get_spec(series_id: str) -> SeriesSpec | None:
    async with session_scope() as s:
        row = await s.get(Series, series_id)
        return spec_from_row(row) if row else None


async def upsert_spec(spec: SeriesSpec) -> SeriesSpec:
    async with session_scope() as s:
        row = await s.get(Series, spec.series_id)
        if row is None:
            s.add(row_from_spec(spec))
            s.add(SeriesMeta(series_id=spec.series_id))
        else:
            for k, v in spec.model_dump().items():
                setattr(row, k, v)
    return spec


async def delete_spec(series_id: str) -> bool:
    async with session_scope() as s:
        row = await s.get(Series, series_id)
        if row is None:
            return False
        await s.delete(row)
        return True


async def list_specs(provider: str | None = None, country: str | None = None, category: str | None = None, q: str | None = None, enabled_only: bool = False) -> list[SeriesSpec]:
    async with session_scope() as s:
        stmt = select(Series)
        if provider:
            stmt = stmt.where(Series.provider == provider)
        if country:
            stmt = stmt.where(Series.country == country)
        if category:
            stmt = stmt.where(Series.category == category)
        if enabled_only:
            stmt = stmt.where(Series.enabled.is_(True))
        rows = (await s.execute(stmt.order_by(Series.category, Series.series_id))).scalars().all()
    specs = [spec_from_row(r) for r in rows]
    if q:
        ql = q.lower()
        specs = [sp for sp in specs if ql in sp.series_id.lower() or ql in sp.name.lower() or any(ql in t for t in sp.tags)]
    return specs


async def get_meta(series_id: str) -> dict | None:
    async with session_scope() as s:
        m = await s.get(SeriesMeta, series_id)
        if m is None:
            return None
        return {
            "series_id": m.series_id,
            "first_ts": m.first_ts,
            "last_ts": m.last_ts,
            "n_obs": m.n_obs,
            "fetched_at": m.fetched_at,
            "last_status": m.last_status,
            "last_error": m.last_error,
            "rows_last_fetch": m.rows_last_fetch,
            "active_fallback": m.active_fallback,
        }


async def list_meta() -> dict[str, dict]:
    async with session_scope() as s:
        rows = (await s.execute(select(SeriesMeta))).scalars().all()
    return {m.series_id: {"first_ts": m.first_ts, "last_ts": m.last_ts, "n_obs": m.n_obs, "fetched_at": m.fetched_at, "last_status": m.last_status, "last_error": m.last_error} for m in rows}
