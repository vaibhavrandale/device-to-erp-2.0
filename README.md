# device-to-erp 2.0 — Pi-local attendance server

Everything runs on the Pi. The fingerprint firmware (`device-to-erp`) stays
unchanged — only its broker address changes. Instead of the cloud MQTT broker
and the full `taypro-console-backend`, the Pi runs:

```
R307 fingerprint firmware (device-to-erp, python — unchanged)
        │ MQTT hr/attendance/up            (localhost)
        ▼
mosquitto (local broker, 127.0.0.1:1883)
        ▼
server.js (this repo, node)
        │ inserts AttendancePunch
        ▼
MongoDB (same DB the console backend uses)
```

The code is a trimmed copy of `taypro-console-backend/attendance`:
register / heartbeat / tap only. Sockets, device logs, remote enroll,
reports and frappe transfer are intentionally not included — this service
does one thing: **insert attendance punches into the DB**.

## One repo, both codebases (git submodule)

The Python firmware lives in its own repo
(`github.com/vaibhavrandale/device-to-erp`) and is pulled in here as a git
**submodule** at `./device-to-erp`. So a single clone/pull of this repo
fetches **node + python together**:

```
device-to-erp-2.0/          (this repo — node)
  server.js  attendance.js  models.js  ...
  device-to-erp/            (submodule — python firmware, tracks its main)
    main.py  taypro/  enroll.py  ...
```

Clone with `--recurse-submodules`; the install script and the systemd unit
both run `git submodule update --init --remote --recursive`, which advances
the submodule to the **tip of the firmware's `main`** on every boot. Edit
the firmware in its own repo and push there; edit node here and push here —
one Pi reboot picks up the latest of both.

## Files

| File | Purpose |
|---|---|
| `server.js` | Mongo + MQTT connect, routes `hr/attendance/up` messages |
| `attendance.js` | register / heartbeat / tap handlers (copied, trimmed) |
| `models.js` | `AttendanceDevice`, `AttendancePunch`, `HRUser` schemas (copied) |
| `scripts/install_pi.sh` | one-time Pi setup: mosquitto + systemd service |
| `selfcheck.js` | assert-based check of the pure logic (`npm run selfcheck`) |
| `device-to-erp/` | git submodule — the Python fingerprint firmware |

## Install on the Pi

```bash
git clone --recurse-submodules <this repo> ~/device-to-erp-2.0
cd ~/device-to-erp-2.0
bash scripts/install_pi.sh        # creates .env on first run
nano .env                         # set MONGODB_URI
bash scripts/install_pi.sh        # installs + starts the service
```

On every restart the service does `git reset --hard origin/main`,
`git submodule update --init --remote --recursive` (latest firmware) and
`npm install`, so **laptop `git push` → Pi `sudo reboot` = updated** for
both node and python. (`.env` is gitignored and survives.)

To also run the fingerprint firmware itself on boot, install its own
service once from the submodule (unchanged from the standalone repo):

```bash
python3 device-to-erp/scripts/install_service.py
```

## Point the firmware at the local broker

In the submodule's `device-to-erp/config.json` (or `config.deploy.json`):

```json
{
  "mqtt_host": "127.0.0.1",
  "mqtt_port": 1883,
  "mqtt_username": "",
  "mqtt_password": "",
  "mqtt_tls": false
}
```

Firmware install/boot (`install_service.py`, `boot_run.py`) is unchanged.

## Notes

- **Device lat/lng**: a tap is rejected until the device has coordinates
  (same rule as production). Either set `latitude`/`longitude` in the
  firmware `config.json`, or set them on the `attendancedevices` document
  in the DB — the server pushes them to the device via the `a:config`
  down-message on the next heartbeat if you set `config_pending: true`.
- **Enrollment**: run `python3 enroll.py` on the Pi (from `device-to-erp`),
  then put the printed `FP####` id into the employee's HR fingerprint field.
  Remote enroll from the HR dashboard is not part of this service.
- **Broker security**: mosquitto is localhost-only by default (no listener
  configured), so nothing off the Pi can reach it. If you ever need other
  devices on the LAN to publish, add a listener + password file in
  `/etc/mosquitto/conf.d/` — do not just open an anonymous listener.

## Quick test without the sensor

```bash
mosquitto_pub -t hr/attendance/up -m '{"a":"register","hw":"b827ebaabbcc","d":"office-pi","n":"Office Pi"}'
mosquitto_pub -t hr/attendance/up -m '{"a":"tap","hw":"b827ebaabbcc","d":"office-pi","c":"FP0001","latitude":18.52,"longitude":73.85}'
journalctl -u taypro-attendance-server -f   # watch the result
```

## Logic check (no DB / broker needed)

```bash
npm run selfcheck
```
