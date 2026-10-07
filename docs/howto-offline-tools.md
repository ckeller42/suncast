# Run the backfill and backtest tools

Goal: fill the Grafana forecast-vs-absorbed panel with history, or check whether a better
PV model than the flat factor exists. Both tools are console scripts installed with the
package. They are offline tools run by hand on the Pi, not part of the service, and they never
feed the live calibration.

## Load the service configuration

Both tools load the same configuration as the service (`INFLUX_URL`, `INFLUX_ORG`,
`INFLUXDB_TOKEN`, the bucket and measurement names, `SUNCAST_DB`). Source the service env first:

```bash
set -a; . /etc/buspi/secrets.env; . /etc/buspi/suncast.env; set +a
```

They add three variables of their own, listed in [](reference/configuration.md):
`HOME_LAT`, `HOME_LON` and `BACKTEST_OUT_DIR`.

Both tools walk every day from the first `pv_power` sample in the last 120 days up to yesterday
(UTC). For each day the van's location is the mean of that day's `geo` track, days without
location history fall back to `HOME_LAT` / `HOME_LON`.

## Backfill historical expected PV

```bash
/home/pi/suncast-env/bin/suncast-backfill
```

For each day, `suncast-backfill` fetches Open-Meteo's ERA5 reanalysis (the irradiance that
actually occurred), converts it to expected panel watts with the stored panel configuration and
writes it to InfluxDB in the `FORECAST_MEASUREMENT` measurement (default `solar_forecast`) with
the fields `raw_w`, `corrected_w` and `factor=1.0`, tagged `provider=open-meteo-era5`.

- The result is an expected-potential line, not a forecast.
- Re-running rewrites the same points.
- One failing day is logged and skipped, it never aborts the run. The summary prints how many
  days were written and skipped, with the first ten skip reasons.
- The tool reads the panel through the service's store, which creates `SUNCAST_DB` and a default
  panel row if they are absent.

## Backtest candidate models

```bash
BACKTEST_OUT_DIR=/home/pi/suncast-results /home/pi/suncast-env/bin/suncast-backtest
```

`suncast-backtest` scores candidate potential-prediction models (flat factor and temperature
derate variants) against the Victron history, using ERA5 reanalysis. All metrics are
leave-one-day-out and use bulk-charge hours only, like the live calibration. It reads the stored
panel configuration read-only (defaults if none exists), prints a results table and writes it to
`{BACKTEST_OUT_DIR}/{run_date}-backtest.md` (run date in UTC), headed
`# Backtest results ({run_date}, data through {end_day})`.

The filename carries the run date, so a run on a later day never overwrites an earlier verdict.
A second run on the same day overwrites that day's file.

`BACKTEST_OUT_DIR` defaults to `docs/superpowers/results`, relative to the current working
directory. Run from the repository root or set it explicitly on the Pi, as above.
