def test_index_serves_map(tmp_path):
    from tests.test_api import client

    c = client(tmp_path)
    r = c.get("/")
    assert r.status_code == 200
    assert "vendor/leaflet/leaflet.js" in r.text and 'id="map"' in r.text


def test_history_page(tmp_path):
    from tests.test_api import client

    c = client(tmp_path)
    assert c.get("/history").status_code == 200


def test_leaflet_vendored():
    from pathlib import Path

    v = Path("suncast/static/vendor/leaflet")
    assert (v / "leaflet.js").stat().st_size > 100_000
    assert (v / "leaflet.css").exists()


def test_index_has_address_search(tmp_path):
    from tests.test_api import client

    r = client(tmp_path).get("/")
    assert 'id="addr"' in r.text and 'id="addr-results"' in r.text
