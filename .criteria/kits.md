# Inventory Kits

## Context
Purchasers often buy one supplier SKU that contains several stock items, such as an Eufy Ink Kit containing Cyan, Magenta, Yellow, Black, and Cleaning cartridges. OMS needs kits to be purchasable as one purchase-order line while receiving them credits stock to the component items, not to the kit itself.

## Scope
- In: InventoryItem-based kits using `InventoryItem.is_kit` and `KitComponent`, nested-writable kit APIs, default kit visibility filters, supplier ordering data, purchase-order kit lines with a durable order-time component snapshot, one additive `reorder_queue` migration for that snapshot field, kit receipt decomposition, component reorder-request closure, kit management UI, purchase-order kit selection/detail/receiving UI, component "supplied by kits" display, reactive mutation behavior, regenerated API permission/list docs, the stale Django note correction in `AGENTS.md`, and focused backend/frontend tests.
- Out: A standalone `Kit` model, a standalone kits Django app, a standalone `/kit-components/` endpoint, nested kits, serialized components inside kits, kit-level stock, kits as reorder-request targets, kits as reorder-queue action rows, cost allocation across components, automatic "buy the kit instead" substitution, kit demand forecasting, multi-supplier kits, `?supplier=&kit=` deep-link prefill, changes to `create_optimized_order`, `by_supplier`, or the op-tm70 approval gate beyond excluding kits from action surfaces, barcode-receive path consolidation, and fixing the pre-existing voided-line receipt gap.

## Criteria

### AC-1: Staff can create a purchasable kit SKU
- **Given** an authenticated staff user, an active supplier, and five active non-kit, non-serialized component items
- **When** the user creates an "Eufy Ink Kit" with supplier SKU `T3200`, unit cost `$89.99`, and each component quantity set to `1`
- **Then** the API returns a created InventoryItem-shaped kit with `is_kit=true`, `is_requestable=false`, no kit stock credited, the supplier SKU/cost data, and the five component rows

### AC-2: Anonymous clients can read kits but not write them
- **Given** an existing active kit
- **When** an anonymous client lists or retrieves kits and then attempts to create or update one
- **Then** the read requests succeed, the write request returns an authentication or permission error, and no kit data is changed

### AC-3: Kit list filters are externally visible
- **Given** active and inactive kits across different suppliers and components
- **When** a client lists `/api/inventory/kits/` with search, `is_active`, `supplier`, or `component` filters
- **Then** the response contains only matching kits and defaults to name ordering

### AC-4: No standalone kit-component API exists
- **Given** a client inspects the API schema or route list
- **When** they search for a standalone kit-component endpoint
- **Then** no `/api/inventory/kit-components/` route is exposed

### AC-5: Kits cannot be stocked or serialized
- **Given** an authenticated staff user
- **When** the user creates or updates a kit with `is_serialized=true` or a nonzero stock count
- **Then** the API returns a validation error and persists no invalid kit state

### AC-6: Kits require at least one component
- **Given** an authenticated staff user
- **When** the user creates or updates a kit with an empty component list
- **Then** the API returns a validation error and does not save an empty bill of materials through the kit API

### AC-7: Component quantities must be positive
- **Given** an authenticated staff user editing a kit
- **When** the user submits a component quantity of `0` or a negative value
- **Then** the API rejects the request and leaves the existing component quantities unchanged

### AC-8: A kit cannot contain itself
- **Given** an existing kit
- **When** a request attempts to add that same kit as one of its own components
- **Then** the API or database constraint rejects the self-reference and no component credit path can include the kit itself

### AC-9: A kit cannot contain another kit
- **Given** two existing kits
- **When** a request attempts to add one kit as a component of the other
- **Then** the API returns a validation error and no nested kit component row is created

### AC-10: Serialized component items fail loud
- **Given** an existing serialized inventory item
- **When** a request attempts to add it as a kit component
- **Then** the API returns a validation error and the serialized item is not included in the kit

### AC-11: Duplicate components are rejected
- **Given** an authenticated staff user editing a kit
- **When** the submitted component list contains the same component item more than once
- **Then** the API returns a validation error and creates no duplicate kit-component rows

### AC-12: Components used by kits are protected from deletion
- **Given** an inventory item that is currently used as a kit component
- **When** a staff user attempts to delete that inventory item
- **Then** deletion is refused, the component item remains, and the kit's component breakdown remains unchanged

### AC-13: Component updates preserve surviving row identity
- **Given** a kit with components A and B
- **When** the user updates component A's quantity, removes B, and adds component C
- **Then** the response shows A with its original component-row identifier and new quantity, B absent, and C as a new component row

### AC-14: Inventory item list hides kits by default
- **Given** the catalog contains ordinary items and kits
- **When** a client requests `/api/inventory/items/` without kit parameters, with `?include_kits=true`, and with `?is_kit=true`
- **Then** the default response excludes kits, `include_kits=true` includes kits alongside ordinary items, and `is_kit=true` returns only kits

### AC-15: Component detail exposes kits that supply it
- **Given** an ordinary component item that belongs to one or more active kits
- **When** any client requests `GET /api/inventory/items/{id}/kits/`
- **Then** the API returns the kits that contain that component without requiring authentication

### AC-16: Kits never become reorder candidates
- **Given** a kit with stock and minimum-stock values that would make an ordinary item look low
- **When** low-stock or reorder-request surfaces are evaluated
- **Then** the kit is not reported as needing reorder and no reorder request targets the kit item

### AC-17: Optimized ordering excludes kits as action rows
- **Given** ordinary items and kits that are below their minimum stock values
- **When** a staff user generates an optimized order
- **Then** kit items are absent from the generated action rows while ordinary low-stock items still appear

### AC-18: Reorder data excludes kits from both stock and request paths
- **Given** a kit that is low and a kit that has an open request-like status path available through existing data
- **When** a staff user requests reorder data
- **Then** neither kit appears as an actionable reorder item from the low-stock query or the items-with-requests path

### AC-19: Low components show supplier kit context
- **Given** a low component item supplied by a kit whose supplier otherwise has no actionable rows
- **When** a staff user requests reorder data
- **Then** the response includes that supplier and shows the component as supplied by the kit as informational context without creating a kit action row

### AC-20: Stock snapshots exclude kits
- **Given** ordinary items and kits exist in inventory
- **When** the stock snapshot task records inventory levels
- **Then** snapshot rows are created for ordinary items and not for kits

### AC-21: Demand forecasts exclude kits
- **Given** ordinary items and kits exist in inventory
- **When** the demand forecast task runs
- **Then** forecast calculations include ordinary items and exclude kits

### AC-22: Supplier ordering data exposes kits safely
- **Given** one supplier with purchasable kits and another supplier with none
- **When** the purchase-order form loads supplier ordering data
- **Then** kits are available in a dedicated kit collection for the first supplier and the form can render the second supplier without crashing when no kits are present

### AC-23: Ordering kits creates one purchase-order line
- **Given** the Eufy Ink Kit costs `$89.99`
- **When** a purchaser orders quantity `2` of that kit
- **Then** the purchase order contains one kit line with quantity `2` and line total `$179.98`, not five component lines

### AC-24: Kit purchase-order lines expose a component preview
- **Given** a purchase order containing a kit line and an ordinary item line
- **When** a client retrieves the purchase order
- **Then** the kit line has `is_kit_line=true` and a read-only component preview, while the ordinary item line has no component preview and keeps its existing response shape

### AC-25: The preview matches the receipt effect
- **Given** a kit purchase-order line with a component preview from its order-time component snapshot
- **When** that same quantity is received
- **Then** the component item stock deltas exactly match the preview quantities shown on the line

### AC-26: Receiving a kit credits components, not the kit
- **Given** a purchase order line for quantity `2` of a kit containing five components at quantity `1` each
- **When** the line is received
- **Then** each component item's stock increases by `2` and the kit item's stock remains unchanged

### AC-27: Partial kit receipts are additive
- **Given** a kit purchase-order line with quantity `3` and zero received
- **When** a receiver records a receipt of `1` kit and later a receipt of `2` kits
- **Then** component stock increases by the first receipt quantity and then by the second receipt quantity without recounting the earlier receipt

### AC-28: Kit over-receipt is accepted and flagged

> **Supersedes the original AC-28, "Kit over-receipt is rejected before stock changes."** The captain replaced rejection with the decision to record what arrived, flag the difference, and never silently round; see `receipt_refusal` in `backend/reorder_queue/services/receiving.py` for the rationale.

- **Given** a kit purchase-order line for two kits with an order-time component snapshot and one kit already received
- **When** a receiver records a receipt of two more kits
- **Then** the API returns success, credits each component with two kits' worth from the line's snapshot while leaving kit stock unchanged, and reports three kits received with `quantity_variance=1` and `receipt_state=over_received`

### AC-29: Legacy empty kit breakdowns do not break physical receiving
- **Given** a legacy kit purchase-order line with no stored kit snapshot and no current kit component rows
- **When** a receiver records a receipt for that kit line
- **Then** the receipt does not return a server error, no component stock is credited, and a warning is available to operators or logs

### AC-30: Full kit receipt closes linked component reorder requests
- **Given** open reorder requests exist for kit component items
- **When** the kit purchase-order line becomes fully received
- **Then** the linked component reorder requests are closed using the delivery date, while partial kit receipts leave them open

### AC-31: Kit receipt ledger remains one purchased SKU entry
- **Given** a received kit purchase-order line
- **When** accounting or receipt ledger records are inspected
- **Then** there is one purchase-order receipt entry for the kit SKU and kit total cost against the kit's owning group, with no allocated component cost entries

### AC-32: Barcode receiving fails loud for kit lines
- **Given** a purchase order line for a kit
- **When** a user attempts to receive it through the existing UPC/barcode receipt endpoint
- **Then** the API returns `400` with a kit-line unsupported message and does not mutate stock

### AC-33: Kit management routes are registered and discoverable
- **Given** an authenticated user opens the frontend
- **When** they navigate by URL, route map, sidebar, Inventory workspace card, or Purchasing overview card
- **Then** `/inventory/kits`, `/inventory/kits/new`, and `/inventory/kits/:kitId` render kit pages instead of a 404, and frontend tests explicitly cover the route label, navigation cards, and non-404 route behavior

### AC-34: Kit pages support reactive create and edit
- **Given** a user is on the kit list or kit detail page
- **When** they create or update a kit successfully
- **Then** the visible kit data updates from the mutation response without returning the page to its initial loading placeholder

### AC-35: Component picker is optimized for adding multiple items
- **Given** a user is editing kit components
- **When** they search for component inventory items
- **Then** the picker performs debounced server-side item search, excludes items already in the kit, and defaults new component quantity to `1`

### AC-36: Component picker supports the fast keyboard loop
- **Given** a user has selected a component item and focused its quantity field
- **When** they press Enter to commit the row
- **Then** the component is added, the picker clears, focus returns to the picker, and the user can continue adding rows without a page reload

### AC-37: Purchase-order form presents kits before ordinary items
- **Given** a supplier has purchasable kits and ordinary inventory items
- **When** a purchaser opens the purchase-order form for that supplier
- **Then** the Kits section appears before Inventory Items, and suppliers with no kits still render the rest of the form without error

### AC-38: Kit quantity counts purchased kits and updates totals
- **Given** a purchaser selects a kit on the purchase-order form
- **When** they enter quantity `2`
- **Then** the form shows one selected kit line, a line total based on two kits, and grand totals and line-item counts that include the kit line

### AC-39: Low components preselect their supplier kit
- **Given** a kit contains at least one low-stock component
- **When** the purchase-order form loads for that supplier
- **Then** the kit is checked by default; if none of its components are low, the kit is unchecked by default

### AC-40: Kit rows expose accessible component breakdowns
- **Given** a kit row is visible on the purchase-order form
- **When** a user expands it or changes the kit quantity
- **Then** the row shows an accessible component table and a live summary such as `2 kits -> 10 units`, including the per-component "You get" quantities

### AC-41: Double-order conflicts are visible and reversible
- **Given** a selected kit and selected ordinary item rows overlap on component items
- **When** the purchase-order form renders the Inventory Items section
- **Then** a conflict banner names the overlapping items, provides one action to deselect those ordinary item rows, and each overlapping item row shows a persistent "in kit" chip even when the kit is unchecked

### AC-42: Purchase-order detail and receipt use the ordered kit snapshot
- **Given** a purchase order was created for a kit with components A and B, and the kit's live component rows are edited afterward to remove A, change B, or add C
- **When** a user retrieves the purchase-order detail and then receives that kit line
- **Then** the displayed kit breakdown and component stock credits exactly match the purchase-order line snapshot captured at order time, not the kit's current live definition

### AC-43: Receiving preview updates before submit
- **Given** a receiver opens the receive flow for a kit line
- **When** they type different receipt quantities
- **Then** the UI immediately updates a consequence row such as `Receiving 2 kits adds 10 units across 5 items` before the receiver submits

### AC-44: Kit receive mutations are reactive
- **Given** a receiver submits a kit receipt from the purchase-order page
- **When** the API call is pending, succeeds, or fails
- **Then** only the affected receive controls are disabled while pending, duplicate submit is prevented, success patches the visible order from the response without an initial page reload, and failure leaves the receive panel and typed quantity visible for retry

### AC-45: Item detail shows supplied-by kits only when relevant
- **Given** a user opens an ordinary inventory item detail page
- **When** the item belongs to one or more kits, belongs to no kits, or the supplied-by request fails
- **Then** the Overview tab shows a "Supplied by kits" card only for the first case and otherwise omits the card without blocking the rest of the page

### AC-46: New kit mutations follow the reactive mutation standard
- **Given** any new kit create, kit update, component edit, kit selection, or receipt mutation added for this feature
- **When** the mutation succeeds or fails
- **Then** visible state is patched from the response where available, pending and error UI is scoped to the affected control or row, duplicate submit is prevented while pending, and user context is not replaced by a full-page loading state

### AC-47: API permission and list-contract docs are regenerated
- **Given** the implementation adds kit routes and changes inventory item list visibility
- **When** `python manage.py check_permission_matrix` runs and maintainers inspect the API docs
- **Then** the permission matrix is clean, kit endpoints are documented with their read/write permissions, and the API list contract states that `/api/inventory/items/` excludes kits by default unless kit parameters opt in

### AC-48: Stale Django project docs are corrected
- **Given** maintainers compare `AGENTS.md`, `backend/requirements.txt`, and the latest migration header
- **When** they inspect the Django version and constraint guidance
- **Then** the docs agree that the backend is on Django `6.0.7` and mention Django 6's `CheckConstraint(condition=...)` form instead of stale Django 5.1 guidance

### AC-49: Kit schema changes use only the authorized migrations
- **Given** migrations are inspected after implementation
- **When** maintainers review the generated migration files
- **Then** the kit schema changes consist of the inventory migration for `InventoryItem.is_kit` plus `KitComponent` and exactly one additive `reorder_queue/0028_purchaseorderitem_kit_snapshot.py` migration that only adds nullable `PurchaseOrderItem.kit_snapshot = JSONField(null=True, blank=True)`, with no `CheckConstraint` change, no fourth purchase-order-item target slot, no `unique_together` or index change, and no consumer branching

### AC-50: Ordinary purchase-order lines keep their existing contract
- **Given** a purchase order contains an ordinary item line and no kit line
- **When** a client retrieves the purchase order and maintainers inspect the stored purchase-order item
- **Then** the ordinary line response is byte-identical to the pre-kit response shape, no kit component preview is exposed, and `PurchaseOrderItem.kit_snapshot` is `NULL`

### AC-51: Snapshot migration leaves schemas clean
- **Given** the authorized inventory and `reorder_queue` migrations have been committed
- **When** `cd backend && python manage.py makemigrations --check` runs
- **Then** Django reports no pending model changes for `inventory` or `reorder_queue`

## Verification Commands
- `cd backend && pytest`
- `cd frontend && npm test`
- `pre-commit run --all-files`
- `cd backend && python manage.py makemigrations --check`
- `cd backend && python manage.py check_permission_matrix`
