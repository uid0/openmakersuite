# Lockers (ForgeKey expansion)

OMS supports SIG-owned lockable enclosures controlled by ForgeKey
hardware. This document covers the data model and the access-decision
pipeline that landed in Phase 1+2 of the expansion. Phase 3 (MQTT
integration) and Phase 4+5 (audit + automation) are tracked as
separate follow-up PRs.

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

## What's not in Phase 1+2

The following land in later PRs:

- Phase 3: MQTT integration — the `lockers.services.commands.publish_unlock` helper, EMQX rule-engine HTTP webhooks for IR-break / reed-status / lockout, and the registration-acknowledge handshake.
- Phase 4: `LockerAccessEvent` state machine, IR-break correlation, mortise-key admin-physical-entry events, high-trust return acceptance flow.
- Phase 5: Celery beat — OTP janitor, propped-door alert, lockout reset task.
- Phase 6: Firmware contract (fail-secure, supervision, LED diagnostic patterns).
