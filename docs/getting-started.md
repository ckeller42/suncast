# Getting started

This walks through the first run of suncast from a clone of the repository and one forecast
request. For the permanent install on the Pi, see [](howto-deploy.md).

suncast needs a reachable InfluxDB. It reads the Victron MPPT history and the location track
from it, and it mirrors the forecast back into it. Without InfluxDB the process refuses to
start (see [](reference/configuration.md)).

## 1. Install

```bash
uv sync --locked          # runtime dependencies from uv.lock
```

`pip install .` in a virtual environment works as well; the Pi install uses that.

## 2. Set the required variables

```bash
export INFLUX_URL=http://localhost:8086
export INFLUX_ORG=home
export INFLUXDB_TOKEN=...          # read access to the Victron and geo buckets, write access to the forecast bucket (`FORECAST_BUCKET`)
export SUNCAST_DB=./suncast.db     # default is /var/lib/suncast/suncast.db
```

Everything else has a default. If one of the three `INFLUX*` variables is missing, `suncast`
exits with `suncast: set INFLUX_URL, INFLUX_ORG, INFLUXDB_TOKEN`.

## 3. Start the service

```bash
uv run suncast
```

The web server listens on port `8090` (`SUNCAST_PORT`) on all interfaces. On start it creates
the SQLite database and its parent directory if they do not exist, and starts the daily job
loop in the background.

## 4. Request a forecast

Open `http://localhost:8090/` for the map page, or call the API directly. The coordinates
below are a placeholder:

```bash
curl -s -X POST localhost:8090/api/forecast \
  -H 'content-type: application/json' \
  -d '{"lat": 48.0, "lon": 9.0, "days": 3}'
```

The response holds the raw and the calibrated hourly series. While fewer than
`SUNCAST_MIN_SAMPLES` calibration days exist, `factor.calibrated` is `false` and the factor
is `1.0`, so raw and calibrated curves are identical. The first calibration sample appears
after the daily job has stored a snapshot and one full day has passed, see
[](reference/calibration.md).

`GET /api/health` shows whether a location fix could be read from InfluxDB, the age of the last
snapshot and how many calibration days are stored.
