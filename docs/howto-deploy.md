# Deploy on the Pi

Goal: run suncast as a systemd service on buspi, with its configuration in `/etc/buspi/`.
The unit file is [`deploy/suncast.service`](https://github.com/ckeller42/suncast/blob/main/deploy/suncast.service),
the variable template is
[`deploy/suncast.env.example`](https://github.com/ckeller42/suncast/blob/main/deploy/suncast.env.example).

## Install

Create a virtual environment and install from the repository root:

```bash
python3 -m venv /home/pi/suncast-env
/home/pi/suncast-env/bin/pip install .
```

Prepare the database directory (`SUNCAST_DB` defaults to `/var/lib/suncast/suncast.db`):

```bash
sudo install -d -o pi -m 755 /var/lib/suncast
```

Install the unit and the environment file:

```bash
sudo cp deploy/suncast.service /etc/systemd/system/
sudo cp deploy/suncast.env.example /etc/buspi/suncast.env
sudo chown root:root /etc/systemd/system/suncast.service /etc/buspi/suncast.env
sudo chmod 644 /etc/systemd/system/suncast.service /etc/buspi/suncast.env
```

Edit `/etc/buspi/suncast.env` for your bucket and measurement names. The unit reads two
environment files: `/etc/buspi/secrets.env` (holds the required `INFLUXDB_TOKEN`, shared with the
other buspi services) and `/etc/buspi/suncast.env`. Keep the token out of the second one.

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now suncast
```

## Check

```bash
systemctl is-active suncast
journalctl -u suncast -f
curl -s localhost:8090/api/health      # port from SUNCAST_PORT, default 8090
```

The unit has `Restart=always` with a 15 second delay and starts after
`network-online.target` and `influxdb.service`.

## Upgrade

Pull the new revision, reinstall into the same environment and restart:

```bash
/home/pi/suncast-env/bin/pip install .
sudo systemctl restart suncast
```

The SQLite file is created with `CREATE TABLE IF NOT EXISTS`, so an existing database is
reused as it is. Compare `deploy/suncast.env.example` with your `/etc/buspi/suncast.env` after an
upgrade, a new release may add an optional variable.

## Change the configuration

Edit `/etc/buspi/suncast.env` and run `sudo systemctl restart suncast`. The configuration is read
once at start. All variables are listed in [](reference/configuration.md).
