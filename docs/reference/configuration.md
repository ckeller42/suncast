# Configuration

suncast is configured with environment variables only, read once at start by
[`suncast/config.py`](https://github.com/ckeller42/suncast/blob/main/suncast/config.py). The
tables below are included from the README, the one place they are written down.
`tests/test_docs_config.py` fails if the README tables and `config.py` drift apart (names,
defaults, required flags).

Required variables are marked with `*`. If one of them is missing the process exits with
`suncast: set INFLUX_URL, INFLUX_ORG, INFLUXDB_TOKEN`.

## Service

```{include} ../../README.md
:start-after: <!-- config-service:start -->
:end-before: <!-- config-service:end -->
```

In production the variables live in `/etc/buspi/suncast.env`, and `INFLUXDB_TOKEN` in
`/etc/buspi/secrets.env`, see [](../howto-deploy.md).

## Offline tools

`suncast-backfill` and `suncast-backtest` read all service variables above plus these. They
are described in [](../howto-offline-tools.md).

```{include} ../../README.md
:start-after: <!-- config-offline:start -->
:end-before: <!-- config-offline:end -->
```
