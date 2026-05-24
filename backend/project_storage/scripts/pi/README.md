# OMS Project Storage — Raspberry Pi print daemon

A small Python service that polls the OMS Project Storage print queue
and drives a label or receipt printer.

## What it does

```
        OMS API                       Pi                  Printer
─────────────────────────────────────────────────────────────────────
GET   /api/project-storage/
        stints/print-queue/
      ← list of pending stints
                                    fetch each label PNG
                                    send PNG to printer
POST  .../<stint>/mark-printed/
                                                       label printed
```

The queue is anything with `printed_at IS NULL` that isn't already
removed or in purgatory. The daemon honours each stint's `print_target`
(`brother_ql` or `epson_tm`) unless overridden by env.

## Install

```bash
sudo mkdir -p /opt/oms-print-daemon
sudo cp print_daemon.py /opt/oms-print-daemon/
sudo cp project_storage_print.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now project_storage_print
```

Edit the `Environment=` lines in `/etc/systemd/system/project_storage_print.service`
to point at your OMS API and your printer.

### Brother QL label printer (recommended)

```bash
sudo pip3 install brother_ql Pillow requests
```

Plug the printer in via USB. Find the device:

```bash
ls -l /dev/usb/lp*
```

Set in the service unit:

```
Environment=OMS_PRINTER_KIND=brother_ql
Environment=OMS_BROTHER_DEVICE=/dev/usb/lp0
Environment=OMS_BROTHER_MODEL=QL-820NWB     # whichever model you have
Environment=OMS_BROTHER_LABEL=62            # 62mm continuous tape
```

The `pi` user needs to be in the `lp` group to write the device — the
service unit adds `SupplementaryGroups=lp dialout`.

### Epson TM receipt printer

```bash
sudo apt-get install cups cups-bsd
sudo pip3 install Pillow requests
```

Add the printer in CUPS (`http://<pi>:631/`). Then in the service unit:

```
Environment=OMS_PRINTER_KIND=epson_tm
Environment=OMS_EPSON_CUPS_QUEUE=TM_T20III    # the CUPS queue name
```

The daemon shells out to `lp -d $OMS_EPSON_CUPS_QUEUE label.png`.

## Run manually for testing

```bash
OMS_API_BASE=https://oms.example.com/api \
OMS_PRINTER_KIND=brother_ql \
OMS_BROTHER_DEVICE=/dev/usb/lp0 \
python3 print_daemon.py
```

You should see one log line per poll:

```
2026-05-24 09:15:22 INFO project_storage_print_daemon: polling https://… every 10s
2026-05-24 09:15:31 INFO project_storage_print_daemon: stint PS-AB23CDFG: fetching label
2026-05-24 09:15:31 INFO project_storage_print_daemon: stint PS-AB23CDFG: printing via brother_ql
2026-05-24 09:15:33 INFO project_storage_print_daemon: stint PS-AB23CDFG: confirming print to OMS
```

## Authentication

The two endpoints the daemon needs (`print-queue` and `mark-printed`)
are AllowAny on the OMS side because all the daemon can do with them
is print a label that's already destined for a known location. If you
front the OMS API with an auth proxy, set `OMS_API_TOKEN` in the
service unit and the daemon will send it as `Authorization: Bearer`.

## Recovery

- **Out of paper / printer offline** — the daemon logs an error per
  stint and moves on; the queue surfaces the same stints next poll.
- **OMS unreachable** — the daemon backs off exponentially up to 60s.
- **Wrong label printed / member needs a reprint** — go into Django
  admin, find the stint, clear `printed_at`. Next poll the daemon
  picks it up.

## Operational tips

- One Pi can drive both a Brother and an Epson at once: run two
  instances of the daemon with different `OMS_PRINTER_KIND` and units
  named `project_storage_print_brother` / `_epson`. Each respects
  its kind by overriding the per-stint `print_target`.
- Run the daemon on the same LAN segment as the printer if possible —
  USB-over-IP works but adds a failure surface.
