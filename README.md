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

## One repo, both codebases

The complete Python firmware is tracked directly in this repository under
`./device-to-erp`. It is not a submodule, so a normal clone or pull fetches
**node + python together**:

```
device-to-erp-2.0/          (this repo — node)
  server.js  attendance.js  models.js  ...
  device-to-erp/            (python fingerprint firmware)
    main.py  taypro/  enroll.py  ...
```

All future Node and Python changes are committed and pushed to this 2.0 repo.
One Pi reboot picks up the latest of both.

## Files

| File | Purpose |
|---|---|
| `server.js` | Mongo + MQTT connect, routes `hr/attendance/up` messages |
| `attendance.js` | register / heartbeat / tap handlers (copied, trimmed) |
| `models.js` | `AttendanceDevice`, `AttendancePunch`, `HRUser` schemas (copied) |
| `scripts/install_pi.sh` | one-time Pi setup: mosquitto + systemd service |
| `selfcheck.js` | assert-based check of the pure logic (`npm run selfcheck`) |
| `device-to-erp/` | Python fingerprint firmware, tracked in this repo |

## Install on the Pi

```bash
git clone https://github.com/vaibhavrandale/device-to-erp-2.0.git ~/device-to-erp-2.0
cd ~/device-to-erp-2.0
bash scripts/install_pi.sh
```

On every restart the service does `git reset --hard origin/main` and
`npm install`, so **laptop `git push` → Pi `sudo reboot` = updated** for both
node and python. The installer copies the tracked production settings
from `config.deploy.env` into the private runtime `.env`, then installs and
starts both `taypro-attendance-server` and `taypro-fingerprint`. No manual
configuration or second install command is required.

## Enroll an employee (capture at the sensor, type ids into HR)

The HR dashboard sits on another network and this broker is loopback-only, so
the dashboard cannot start an enroll. Nothing needs to be run on the Pi
either — **an unrecognised finger is the capture request.**

1. New employee places a finger. The sensor does not recognise it, so the OLED
   asks for it a second time (two scans make a usable template).
2. The id appears on screen. Place the **second** finger straight away and it
   is captured as F2.
3. Both ids stay on screen for 60 seconds:

```
NOTE THESE IDS
F1  FP0007
F2  FP0008
Enter in HR form
```

4. Write them down, then type `FP0007` / `FP0008` into that employee's card
   fields in HR and save.

From then on a scan matches and publishes `hr/attendance/up` `a:tap` with
`c=FP0007`, and the node service inserts the punch. Until HR is filled in, the
scan is correctly rejected as an unknown card.

Do one employee at a time. The two fingers are treated as the same person only
if they arrive within `enroll_pair_window_s` (25s); after that the next unknown
finger starts a fresh F1, so a queue cannot mix one person's F2 into another's
record.

Knobs in `device-to-erp/config.deploy.json` — push and reboot to change them:

| Key | Default | Meaning |
|---|---|---|
| `auto_enroll_unknown` | `true` | Set `false` to go back to "no match" errors and capture only over SSH |
| `auto_enroll_timeout_s` | `20` | How long the sensor waits for the two placements |
| `enroll_pair_window_s` | `25` | Within this, a second unknown finger is the same employee |
| `enroll_ids_screen_s` | `60` | How long the ids stay readable |

A passer-by who touches the sensor once and walks away times out without
consuming a template slot, so idle touches do not fill the library.

### With SSH access (optional)

If you can reach the Pi, you can drive the same capture deliberately instead of
waiting for an unknown finger:

```bash
cd ~/device-to-erp-2.0/device-to-erp
.venv/bin/python enroll_now.py          # finger 1 then finger 2
.venv/bin/python enroll_now.py -f 2     # re-capture one finger
.venv/bin/python enroll_now.py -f 2 --id 8   # overwrite a specific page
```

The reader service holds the sensor open, so this asks it over the local broker
rather than grabbing `/dev/ttyUSB0`. Ids print in the terminal as well as on the
OLED.

## Migrate a Pi that has the old standalone code

Stop the old reader and keep its folder as a backup, then clone this combined
repository:

```bash
sudo systemctl stop taypro-fingerprint
sudo systemctl disable taypro-fingerprint
pkill -f "python3.*main.py" || true
pkill -f "boot_run.py" || true

cd ~
[ ! -d device-to-erp ] || mv device-to-erp device-to-erp-old
[ ! -d device-to-erp-2.0 ] || mv device-to-erp-2.0 device-to-erp-2.0-old
git clone https://github.com/vaibhavrandale/device-to-erp-2.0.git
cd device-to-erp-2.0
bash scripts/install_pi.sh
```

After verifying both services, the `*-old` backup folders can be deleted.

## Point the firmware at the local broker

In `device-to-erp/config.json` (or `config.deploy.json`):

```json
{
  "mqtt_host": "127.0.0.1",
  "mqtt_port": 1883,
  "mqtt_username": "",
  "mqtt_password": "",
  "mqtt_tls": false
}
```

The combined installer invokes the firmware's existing `install_service.py`
automatically.

## Notes

- **Device lat/lng**: optional. Missing coordinates used to silently
  skip scanning (`L:--` on the OLED). Punches now insert without them.
- **Enrollment**: an unrecognised finger is captured on the spot and its
  `FP####` id shown on the OLED (see above); type it into the employee's HR
  fingerprint field. Remote enroll from the HR dashboard is not part of this
  service — the dashboard is on another network and this broker is
  loopback-only.
- **Template pages**: the next free page comes from the sensor's own occupancy
  bitmap, so a deleted template's slot is reused instead of overwriting a live
  employee's finger (which `template_count() + 1` used to do).
- **Broker security**: mosquitto is localhost-only by default (no listener
  configured), so nothing off the Pi can reach it. If you ever need other
  devices on the LAN to publish, add a listener + password file in
  `/etc/mosquitto/conf.d/` — do not just open an anonymous listener.

## Stop old services (before first install)

```bash
sudo systemctl stop taypro-fingerprint
sudo systemctl disable taypro-fingerprint
pkill -f "python3.*main.py" || true
pkill -f "boot_run.py" || true
```

## Logs (copy-paste on Pi)

```bash
# Node attendance server (MQTT → MongoDB) — live
journalctl -u taypro-attendance-server -f

# Python fingerprint reader — live
journalctl -u taypro-fingerprint -f

# Last 100 lines (no follow)
journalctl -u taypro-attendance-server -n 100 --no-pager
journalctl -u taypro-fingerprint -n 100 --no-pager

# Service status
systemctl status taypro-attendance-server --no-pager
systemctl status taypro-fingerprint --no-pager

# Firmware boot log file
tail -f ~/device-to-erp-2.0/device-to-erp/data/boot_run.log
```

Open two SSH windows to watch both live logs at once.

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
