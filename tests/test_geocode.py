import pytest

from suncast.geocode import GeocodeError, parse_results, search

BODY = (
    b'[{"display_name": "Stuttgart, Baden-W\xc3\xbcrttemberg", "lat": "48.7758", "lon": "9.1829"},'
    b' {"display_name": "Stuttgart, Arkansas", "lat": "34.5", "lon": "-91.5"},'
    b' {"broken": true}]'
)


def test_parse_results_maps_and_skips_broken():
    out = parse_results(200, BODY)
    assert out[0] == {"label": "Stuttgart, Baden-Württemberg", "lat": 48.7758, "lon": 9.1829}
    assert len(out) == 2  # broken entry skipped


def test_parse_results_errors():
    with pytest.raises(GeocodeError, match="503"):
        parse_results(503, b"")
    with pytest.raises(GeocodeError):
        parse_results(200, b"not json")


def test_search_builds_url_and_sends_user_agent():
    calls = {}

    def fetch(url, headers):
        calls["url"], calls["headers"] = url, headers
        return 200, BODY

    out = search("Stuttgart Hbf", fetch)
    assert "q=Stuttgart%20Hbf" in calls["url"] and "format=json" in calls["url"]
    assert "suncast" in calls["headers"]["User-Agent"]
    assert len(out) == 2
