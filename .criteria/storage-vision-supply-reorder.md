# Storage Vision Supply Reorder MVP

## Context
OpenMakerSuite already supports inventory locations, stock reconciliation, reorder requests, media uploads, Celery workers, and mobile camera workflows. This feature adds a review-gated computer-vision workflow that lets staff capture supply-area photos from phones or fixed cameras, detect marker-backed empty or low supply slots, and approve the resulting stock/reorder actions.

## Scope
- In: Marker-assisted supply-area monitoring, phone capture, fixed-camera upload tokens, server-side CPU/Celery processing, review queue, approved stock reconciliation, pending reorder creation, 30-day image retention, staff Facilities UI, API docs, and tests.
- Out: Native Android/iOS apps, GPU or cloud CV services, open-ended arbitrary component recognition, automatic unreviewed inventory mutations, MakerBox occupancy, project-storage occupancy, and member-facing anonymous image uploads.

## Criteria

### AC-1: Storage vision app is mounted
- **Given** the backend starts with the default OMS settings
- **When** a staff user opens the API schema or requests `/api/storage-vision/`
- **Then** the storage vision routes for areas, slots, cameras, captures, and observations are available under `/api/storage-vision/`

### AC-2: Feature flag disables write paths
- **Given** `STORAGE_VISION_ENABLED=false`
- **When** a client submits a capture, creates a camera, creates a slot, or approves an observation
- **Then** the API returns a disabled-feature error and does not create or mutate storage-vision, inventory, or reorder records

### AC-3: Staff can manage monitored supply areas
- **Given** an authenticated staff or Logistics user
- **When** they create, list, retrieve, or update a storage vision area linked to an active `inventory.Location`
- **Then** the API persists the area name, location, description, active flag, and timestamps and returns the saved representation

### AC-4: Non-staff cannot manage monitored supply areas
- **Given** an anonymous user or authenticated user who is not staff and not in Logistics
- **When** they create, update, or list storage vision areas
- **Then** the API returns `403` or `401` and does not disclose setup data

### AC-5: Staff can map marker-backed supply slots
- **Given** an active storage vision area and an active `InventoryItem`
- **When** a staff or Logistics user creates a slot with an expected marker code, item, area, optional notes, active flag, and empty/low confidence threshold
- **Then** OMS stores the slot and rejects duplicate active marker codes with a validation error

### AC-6: Slot marker labels are printable
- **Given** an active slot
- **When** a staff or Logistics user requests `GET /api/storage-vision/slots/{id}/marker/`
- **Then** OMS returns a printable image or PDF containing the human-readable marker code, item name, area name, and a machine-readable QR marker that encodes only the slot marker payload

### AC-7: Fixed cameras can be provisioned without user JWTs
- **Given** a staff or Logistics user
- **When** they create or rotate a fixed camera source
- **Then** OMS returns the camera metadata and shows the raw upload token only in the create or rotate response, while subsequent list/retrieve responses expose only a token fingerprint and last-used metadata

### AC-8: Fixed cameras can report heartbeat
- **Given** an active camera with a valid scoped bearer token
- **When** it posts to `/api/storage-vision/cameras/{id}/heartbeat/`
- **Then** OMS updates `last_seen_at`, stores optional status metadata, and returns the camera status without requiring a user JWT

### AC-9: Phone captures use authenticated staff upload
- **Given** a staff or Logistics user viewing an active area
- **When** they upload a JPEG or PNG image to `/api/storage-vision/captures/` with `area`, optional `captured_at`, and optional client quality metadata
- **Then** OMS creates a capture with source `phone`, stores the original image, returns `202 Accepted`, and enqueues asynchronous processing

### AC-10: Fixed-camera captures use scoped camera token upload
- **Given** an active camera assigned to an active area
- **When** it uploads a JPEG or PNG image to `/api/storage-vision/captures/` with its scoped bearer token
- **Then** OMS creates a capture linked to that camera and area, updates the camera last-seen fields, returns `202 Accepted`, and enqueues asynchronous processing

### AC-11: Anonymous capture upload is rejected
- **Given** no user session, no user JWT, and no valid camera token
- **When** a client posts to `/api/storage-vision/captures/`
- **Then** the API rejects the request and does not store the uploaded file

### AC-12: Capture validation protects workers
- **Given** a capture upload
- **When** the file is missing, over the configured max size, not JPEG/PNG, or cannot be decoded by Pillow/OpenCV
- **Then** the API rejects it before enqueueing inference and returns a validation error that does not include file-system paths or raw exception traces

### AC-13: Capture processing records lifecycle state
- **Given** a valid queued capture
- **When** the Celery task starts, finishes, or fails
- **Then** the capture status transitions through `queued`, `processing`, and either `processed` or `failed`, with timestamps, processor version, and sanitized failure reason recorded

### AC-14: Marker detection maps known slots
- **Given** a processed image containing one or more readable storage-vision QR slot markers
- **When** the vision service processes the image with OpenCV/Pillow
- **Then** OMS links each known marker to its `VisionSlot`, records marker bounding data and confidence, and records unmatched marker payloads without creating observations for unknown slots

### AC-15: Missing markers produce reviewable failure data
- **Given** a processed image with no readable storage-vision markers
- **When** processing completes
- **Then** the capture is marked `processed`, no supply observations are created, and the capture includes a machine-readable reason such as `no_markers_detected`

### AC-16: Slot observations classify empty, low, or full
- **Given** a known active slot marker is detected
- **When** OMS evaluates the associated evidence crop
- **Then** it creates an observation with one of `empty`, `low`, `full`, or `unknown`, a confidence value, an evidence crop thumbnail, and the heuristic/model version used

### AC-17: Low-confidence observations require explicit review
- **Given** a slot classification below the slot threshold
- **When** processing creates the observation
- **Then** the observation status is `pending`, the suggested action is `review_only`, and no inventory or reorder records are changed

### AC-18: Empty or low observations suggest reorder action
- **Given** a slot classification of `empty` or `low` at or above the slot threshold
- **When** processing creates the observation
- **Then** the observation status is `pending`, the suggested action is `reconcile_empty`, and the observation links to the mapped inventory item

### AC-19: Duplicate pending observations are suppressed
- **Given** an item already has a pending storage-vision observation for the same slot and suggested action
- **When** another capture produces the same result before the existing observation is approved or rejected
- **Then** OMS updates duplicate/evidence metadata on the existing pending observation or records a duplicate relation, and does not create another actionable queue row

### AC-20: Staff can review observations
- **Given** a staff or Logistics user
- **When** they request `/api/storage-vision/observations/?status=pending&area=&item=`
- **Then** OMS returns filterable observations with area, slot, item, capture thumbnail, evidence crop, classification, confidence, suggested action, age, and latest duplicate count

### AC-21: Approving an empty observation reconciles stock
- **Given** a pending observation with suggested action `reconcile_empty`
- **When** a staff or Logistics user approves it
- **Then** OMS creates a review action, marks the observation approved, applies a `StockReconciliation` for the mapped item with `actual_count=0` and reason `vision_supply_check`, and records the observation identifier in the reconciliation notes or metadata-visible text

### AC-22: Approved reconciliation can create a pending reorder
- **Given** approving an observation reconciles the mapped item to a count at or below its minimum stock
- **When** the approval completes
- **Then** OMS creates or links the same pending `ReorderRequest` behavior used by stock reconciliation and the resulting reorder is visible in the existing reorder/admin queue

### AC-23: Approve is idempotent
- **Given** an observation has already been approved or rejected
- **When** another client attempts to approve it again
- **Then** OMS returns a conflict response and does not create a second reconciliation, review action, or reorder request

### AC-24: Rejecting an observation does not mutate inventory
- **Given** a pending observation
- **When** a staff or Logistics user rejects it with a reason
- **Then** OMS records the rejection review action, marks the observation rejected, and does not change `InventoryItem.current_stock`, `StockReconciliation`, or `ReorderRequest`

### AC-25: Bulk approval reports partial results safely
- **Given** a staff or Logistics user submits selected observation IDs to `/api/storage-vision/observations/bulk-approve/`
- **When** some observations are approvable and others are already resolved, forbidden, or invalid
- **Then** OMS approves only valid pending observations, skips invalid rows with per-observation reasons, and returns approved and skipped counts without duplicating inventory mutations

### AC-26: Original evidence is retained for 30 days
- **Given** `STORAGE_VISION_RETENTION_DAYS=30`
- **When** the scheduled retention task runs
- **Then** original capture images older than 30 days are deleted while observation rows, review actions, derived thumbnails, classifications, confidence, and audit metadata remain available

### AC-27: Retention is documented and scheduled
- **Given** maintainers inspect deployment docs and Celery task docs
- **When** they search for storage vision retention
- **Then** the docs list the retention task, default cadence, environment variable, and smoke-check command or endpoint

### AC-28: Facilities UI exposes setup and review
- **Given** a staff or Logistics user opens the Facilities area of the frontend
- **When** storage vision is enabled
- **Then** they can navigate to Storage Vision, manage areas, manage slots, print marker labels, provision camera tokens, upload a phone capture, view processing status, and approve or reject observations

### AC-29: Frontend blocks non-staff setup/review
- **Given** an anonymous user or non-staff/non-Logistics user opens Storage Vision routes
- **When** they try to access setup, capture, camera token, or review screens
- **Then** the frontend shows the existing permission-denied pattern and does not render sensitive token or setup data

### AC-30: Frontend API types include vision reconciliation reason
- **Given** the frontend reconciliation API types are compiled
- **When** `vision_supply_check` is used as a stock reconciliation reason
- **Then** TypeScript accepts the value and the reconciliation history can display the human-readable reason

### AC-31: API schema documents public contract
- **Given** a maintainer opens `/api/schema/` or `/api/docs/`
- **When** they inspect storage vision endpoints
- **Then** request bodies, response shapes, auth expectations, multipart upload fields, and error responses are documented

### AC-32: Backend tests cover storage vision behavior
- **Given** the backend test suite runs
- **When** storage vision tests execute
- **Then** they cover models, serializers, permissions, phone upload, camera token upload, capture processing status, marker detection success/failure, approve, reject, bulk approve, duplicate suppression, retention, and reconciliation/reorder integration

### AC-33: Frontend tests cover storage vision journeys
- **Given** the frontend unit tests run
- **When** storage vision page and service tests execute
- **Then** they cover setup, capture upload, review queue filters, approve/reject behavior, camera token display rules, loading states, empty states, and error states

### AC-34: Playwright proves the staff operating loop
- **Given** the backend and frontend are running with storage vision enabled and test fixtures loaded
- **When** the Playwright smoke test configures an area and slot, uploads a fixture image with the slot marker, waits for processing, approves the pending observation, and opens the existing reorder queue
- **Then** the pending reorder created through the approved stock reconciliation is visible to staff

### AC-35: Deployment defaults are safe
- **Given** a new OMS deployment uses default environment values
- **When** storage vision is enabled
- **Then** CPU inference is used, no cloud service credentials are required, max upload size is bounded, original image retention defaults to 30 days, and all stock/reorder mutations still require staff or Logistics review
