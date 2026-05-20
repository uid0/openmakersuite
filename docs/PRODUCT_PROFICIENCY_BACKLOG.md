# Product Proficiency Backlog Refresh

Date: 2026-05-08

This document is the cleanup companion to `docs/PRODUCT_PROFICIENCY_ROADMAP.md`. It is not a second roadmap. Its job is to translate the refreshed roadmap into GitHub issue actions:

- what looks done enough to close
- what should stay open
- what should be rewritten before more work is queued
- what new issue, if any, is still missing

## Current Read

The original proficiency issue stack was valuable, but the repo has moved materially since it was written. The biggest shift is that the "baseline" work largely exists now:

- permission matrix and drift guard
- API error/list contracts
- risk register and metrics dashboard
- frontend journey inventory
- task inventory
- production env validator
- deployment runbooks
- Kubernetes and Helm assets

The backlog should therefore stop tracking "create the baseline" and start tracking only the remaining gaps.

## Recommended Issue Actions

| Issue | Proposed Action | Reason |
| --- | --- | --- |
| `#327` | Close as done | The reorder queue permission problem it described is now reflected in the current contract and matrix. |
| `#328` | Close as done or rewrite narrowly | The matrix and drift detection landed. The remaining gap is narrower: test coverage completion and a few intent decisions. |
| `#329` | Keep open and expand scope slightly | Still valid. It should also absorb K8s/Helm probe parity with `livez` / `readyz`. |
| `#330` | Keep open | Still matches the remaining ad hoc API error responses. |
| `#331` | Close after this doc refresh lands | The "docs cite things that do not exist" problem is much smaller now; the broad framing is stale. |
| `#332` | Keep open, broaden title/body | The issue should cover the remaining critical journey coverage gap, including accessibility/test-owner gaps, not only a narrow Playwright slice. |
| `#333` | Keep open, narrow scope | The policy/redactor layer exists; the remaining work is rollout to the follow-up surfaces named in `docs/OBSERVABILITY_PRIVACY.md`. |
| `#334` | Keep as umbrella only | The domain split issues are the real actionable backlog now. |
| `#335` | Keep open | Still a real product-operations gap. |
| `#336` | Keep open, rewrite evidence | Restore verification and smoke enforcement still matter, but some evidence in the issue body is stale. |
| `#337` | Close as done | The validator and tests are much farther along than the original issue body suggests. |
| `#338` | Close or rewrite as a fresh tracker | The tracker still points at issues that are partly done, split, or stale. |

## Existing Split Issues Worth Keeping

These already look like the right granularity for follow-up work and do not need replacement issues:

- `#352` device authorization, lockout, and firmware audit trail
- `#353` purchase order audit trail
- `#354` receipts, donations, and donor receipt audit trail
- `#355` maintenance work order audit trail
- `#356` vendor compliance audit trail
- `#357` webhook configuration and invocation audit trail
- `#358` site settings and customization audit trail
- `#359` unified staff review surface for audit data

## Missing Issue Draft

One remaining gap is not captured cleanly by the current issue stack now that `#328` is mostly baseline work:

### Proposed new issue

Title: `Complete remaining permission coverage and resolve member-vs-staff intent gaps`

Suggested body:

```md
## Summary

The API permission matrix now exists and has drift detection, but product proficiency is still only partial because not every matrix row has a proving test and a few documented boundaries still need an explicit product decision.

## Evidence

- `docs/PROFICIENCY_METRICS.md` still marks permission coverage as partial.
- `docs/API_PERMISSION_MATRIX.md` still calls out known intent gaps for vendor and maintenance read surfaces.
- The roadmap refresh now treats matrix creation as done, and coverage/intent completion as the remaining permission work.

## Needed work

- Reconcile the remaining member-vs-staff intent gaps in the matrix.
- Add proving tests for the remaining endpoint classes that are not yet covered.
- Keep `docs/API_PERMISSION_MATRIX.md`, `backend/config/api_permission_matrix.yaml`, and the tests in sync.
- Update `docs/PROFICIENCY_METRICS.md` once the coverage gap is actually closed.

## Criteria

AC-2, AC-3, AC-4, AC-18, AC-37, AC-38
```

Recommended labels:

- `backend`
- `api`
- `permissions`
- `security`

## Approval Checklist

If we want the GitHub backlog to match the refreshed roadmap, the cleanest next actions are:

1. Close `#327`
2. Close or retarget `#328`
3. Close `#331`
4. Close `#337`
5. Close or rewrite `#338`
6. Update `#329`, `#332`, `#333`, and `#336`
7. Optionally create the new permission-coverage issue above

That gives us a smaller backlog with less duplicate wording and less "already done" noise.
