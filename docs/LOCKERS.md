# Lockers (ForgeKey expansion)

OMS supports SIG-owned lockable enclosures controlled by ForgeKey
hardware. This document covers the data model, the access-decision
pipeline, and the web console / API used to run the fleet. The console
(live monitoring, operator unlock + OTP administration, and locker
setup / device binding) and the MQTT command + telemetry integration
are all shipped; the remaining audit-correlation and automation work is
tracked under [Status & roadmap](#status--roadmap) below.

## Concepts

A **Locker** (`backend/lockers/models.Locker`) is a physical lockable
enclosure that:

- Lives at a `Location`.
- Is owned by a SIG (a Django `Group`). SIG admins of that group can
  configure required certifications and accept high-trust returns.
- May reference a `current_asset` — the inventory `Asset` currently
  stored inside.
- May be flagged `is_high_trust`. High-trust lockers require a SIG
  admin to accept the returned asset before another user can take it.
- Optionally drives a strip of WS2818 LEDs inside the locker for
  visual cues to the user (idle / unlocking / lockout).

A **LockerDevice** is a link from a `Locker` to one or more
`forgekey.ESP32Device` rows. Each link has a `role`:

| Role | What the device does |
|---|---|
| `latch` | Drives the latch / strike |
| `reed_switch` | Reports door open/closed state |
| `ir_break` | Reports asset removal mid-cycle (Phase 4) |
| `keypad` | Receives the user's OTP |
| `led_strip` | Drives the WS2818 strip |
| `mortise_key` | Reports admin physical-key entries |

A **LockerOtp** is a short-TTL (60-minute default), single-use,
6–8 digit access PIN. Generated when an authorised user requests
access; redeemed when the keypad reports the matching code; revocable
by an admin before use.

## Authorisation

`backend/lockers/services/access.py::decide_locker_access(user, locker)`
returns an `AccessDecision` with one of these reasons:

| Reason | Allowed | When |
|---|---|---|
| `staff_bypass` | yes | `is_staff` or `is_superuser` |
| `logistics_bypass` | yes | Member of the Logistics group |
| `sig_admin_bypass` | yes | SIG admin of the locker's `owning_sig` |
| `certified` | yes | Holds every active required certification |
| `missing_certification` | no | Holds some but not all required certs (decision lists which) |
| `no_required_certs` | no | Locker has no active required certs and the user has no bypass — locker not yet provisioned for self-serve |
| `locker_inactive` | no | Locker is deactivated; pre-empts every bypass to avoid spurious "keypad rejected" reports |
| `anonymous` | no | No authenticated user |

The bypass order is staff → Logistics → SIG admin → certified user.
This matches the operator-set rule from gh #374 (staff and SIG leaders
have full reach over maintenance / locker resources; volunteers may
only access what they're certified for).

`generate_otp(user, locker, ...)` runs the same decision and either
returns a saved `LockerOtp` or raises `OtpDenied(decision)` carrying
the structured reason.

`redeem_otp(locker, code)` finds an active OTP for the
`(locker, code)` pair, marks it used, and returns it. Returns `None`
when the code is unknown, expired, revoked, or already used.

### Who may *manage* a locker

Opening a locker (access) and configuring one (management) are separate
gates. `access.py::can_user_manage_locker(user, locker)` — and
`can_user_manage_sig(user, sig)` for the create case, before any locker
row exists — return true for staff / superusers, Logistics members, and
SIG admins of the owning SIG. This gate guards locker **setup** (create /
edit / delete), **device binding**, and **OTP administration** (list /
revoke). A merely-certified member can request access but cannot manage
the locker.

## Certifications

`backend/membership/models.Certification` is a SIG-owned certification
record. `UserCertification` is the per-user grant with audit fields
(`granted_by`, `granted_at`, `revoked_by`, `revoked_at`, `notes`).

`membership.utils.is_certified(user, certification)` returns true iff
the user has an *active* grant for an *active* certification.
Inactive certifications stop gating access regardless of grant
history; revoked grants stop counting immediately. Revocation is
non-destructive — rows stay for audit history.

A locker's `required_certifications` is an `M2M` to `Certification`.
Only active certifications gate access; toggling a cert's `is_active`
to false effectively stops requiring it without rewriting the locker
configuration.

## Command JWT

`backend/forgekey/services/jwt_signing.py::make_command_jwt(mac, cmd, exp_seconds=60)`
signs a short-TTL ECDSA-P256 JWT carrying `{mac, cmd, exp}`. This is
the credential a locker firmware verifies before acting on a
`forgekey/<mac>/cmd` MQTT message. The signing key is the same one
EMQX uses to verify device-auth JWTs, so firmware only carries one
public-key trust root.

The default TTL is 60 seconds — short enough that a captured command
can't be replayed minutes later, long enough to tolerate network
latency and ±1 s NTP drift.

## Power source

`Locker.power_source` (`PowerSource` enum) classifies the wiring:

- `poe`, `usb`, `ac_outlet`, `battery` — powered (telemetry expected)
- `unpowered` — mechanical-only keypad; dashboards skip the
  propped-door + lockout polling because there's nothing to poll

## Web console & API

The locker fleet is run from **Facilities → Lockers**
(`/facilities/lockers`, staff-only) and the matching DRF API under
`/api/lockers/` (`backend/lockers/views.LockerViewSet` +
`backend/lockers/urls.py`).

### Endpoints

| Method + path | Purpose | Gate |
|---|---|---|
| `GET /lockers/` · `GET /lockers/{id}/` | Fleet list + detail — each locker carries its bound devices and latest `cabinet_lock/status` (secure / online / state). | authenticated |
| `POST /lockers/` | Create a locker. | manage owning SIG |
| `PATCH /lockers/{id}/` · `DELETE /lockers/{id}/` | Edit / delete a locker. | manage locker |
| `POST /lockers/{id}/devices/` | Bind an `ESP32Device` in a `role`; `is_primary` demotes the existing primary for that role; a duplicate `(locker, device, role)` is a 409. | manage locker |
| `DELETE /lockers/{id}/devices/{assignment_id}/` | Unbind a device. | manage locker |
| `POST /lockers/{id}/unlock/` | Sign + publish the ES256 unlock command to the latch. | access decision |
| `POST /lockers/{id}/issue-otp/` | Mint an OTP (returns the code). | access decision |
| `GET /lockers/{id}/otps/` · `POST /lockers/{id}/revoke-otp/` | List recent / revoke an outstanding OTP. | manage locker |
| `GET /lockers/available-certifications/` | Active certifications, for the setup form's required-certs picker. | authenticated |

`POST` / `PATCH` use `LockerWriteSerializer` (slug auto-derives from the
name when omitted; accepts `required_certifications`) and echo the full
detail representation back, so the UI gets the bound devices + status
without a second fetch.

### Web console surfaces

- **Monitoring** — a fleet table with secure / online / state per
  locker, stat cards, and an intrusion highlight (ALARM or a sustained
  not-secure reading). Polls every 30 s.
- **Operator unlock + OTP** — an Unlock button per row, and an
  access-codes modal to issue / copy / revoke OTPs.
- **Setup** — a "New locker" button and a per-row "Setup" action open a
  drawer to create / edit a locker (location, owning SIG, stored asset,
  power source, LED count, high-trust, required certs, active) and to
  bind / unbind its ESP32 devices by role.

### Telemetry + command webhooks

EMQX's rule engine forwards firmware events to authenticated HTTP
receivers (`backend/lockers/views.py`):

- `POST /api/lockers/events/lock-status/` — upserts the locker's latest
  `LockerStatus` (secure, reed, latch, IR, mortise, item-present,
  firmware version) that drives the monitoring console.
- `POST /api/lockers/events/lockout/` · `events/ir-break/` ·
  `events/reed-status/` — logged today; correlation into access events
  is Phase 4 (below).
- `POST /api/lockers/registration/ack/` — replies to a freshly-flashed
  locker's bring-up handshake (`publish_init_ack`) so it exits its
  initialization LED pattern.

Outbound command publishers live in
`backend/lockers/services/commands.py`: `publish_unlock`,
`publish_lockout`, `publish_clear_lockout`, `publish_init_ack` — each
signs a `make_command_jwt` (see [Command JWT](#command-jwt)) and
publishes to the latch device's `forgekey/<mac>/cmd` topic.

## Status & roadmap

**Shipped**

- Phase 1+2 — data model, access decision, certification gating.
- Phase 3 — MQTT command publishers (`publish_unlock` et al.), the EMQX
  webhook receivers, and the registration-ack handshake.
- Web console — monitoring, operator unlock + OTP administration, and
  locker setup / device binding (the API + surfaces above).

**Remaining**

- Phase 4 — `LockerAccessEvent` state machine, IR-break ↔ reed
  correlation ("item removed"), mortise-key admin-entry events, and the
  high-trust return-acceptance flow. The `lockout` / `ir_break` /
  `reed_status` receivers log today and become state transitions here.
- Phase 5 — Celery beat: OTP janitor, propped-door alert, lockout-reset.
- Phase 6 — firmware contract (fail-secure, supervision, LED diagnostic
  patterns).
