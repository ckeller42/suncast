"""Address search via OSM Nominatim (pure parse + injected fetch).

Nominatim usage policy: identify the app via User-Agent, light usage only —
one-shot searches from the map UI, never polling.
"""

import json
from collections.abc import Callable
from urllib.parse import quote

FetchFn = Callable[[str, dict], tuple[int, bytes]]  # (url, headers) -> (status, body)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "suncast/0.1 (+https://github.com/ckeller42/suncast)"


class GeocodeError(Exception):
    pass


def parse_results(status: int, body: bytes, limit: int = 5) -> list[dict]:
    """Nominatim JSON -> [{label, lat, lon}], newest-relevance first."""
    if status != 200:
        raise GeocodeError(f"HTTP {status}")
    try:
        data = json.loads(body)
    except ValueError as e:
        raise GeocodeError(f"bad json: {e}") from e
    out = []
    for item in data[:limit]:
        try:
            out.append(
                {
                    "label": str(item["display_name"]),
                    "lat": float(item["lat"]),
                    "lon": float(item["lon"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def search(q: str, fetch: FetchFn, limit: int = 5) -> list[dict]:
    url = f"{NOMINATIM_URL}?q={quote(q)}&format=json&limit={limit}"
    status, body = fetch(url, {"User-Agent": USER_AGENT})
    return parse_results(status, body, limit)


def default_fetch(url: str, headers: dict) -> tuple[int, bytes]:
    import httpx

    r = httpx.get(url, headers=headers, timeout=10)
    return r.status_code, r.content
