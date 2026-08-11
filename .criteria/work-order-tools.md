# Work-order-level tools

## Context
Work orders currently borrow tools from the preventive-maintenance template, so corrective work orders cannot list tools and a technician cannot record where a tool is staged for one specific job. This feature lets each work order carry its own tool rows while preserving the existing template tool system and the ScanTTY payload contract.

## Scope
- In: A `WorkOrderTool` model and one additive migration, template-tool copies at preventive work-order generation, ad-hoc work-order tools for corrective and preventive jobs, per-job `location_hint` editing, work-order-aware tool display fallback, printed-form rendering, work-order API actions, WorkOrderPage editing UI, and tests for backend, frontend, PDF, OMR, and migration behavior.
- Out: Changes to `MaintenanceTool` fields or the MaintenanceItemFormPage template-tool editor, data backfills for historical work orders, OMR checkboxes or scan target IDs for tools, inventory consumption or stock decrement for tools, tool cost tracking, purchase-order integration for tools, replacing `location_hint` with a structured location FK, tools on `ThirdPartyWorkOrder`, and tools sourced from `additional_maintenance_items`.

## Criteria

### AC-1: Migration is additive
- **Given** the migration generated for work-order-level tools is reviewed or applied to a test database
- **When** the migration operations are inspected
- **Then** exactly one new migration creates `WorkOrderTool` with the work-order, template-tool provenance, inventory item, ad-hoc flag, denormalized name, quantity, per-job location hint, required flag, notes, timestamps, ordering, and work-order index, and it does not alter any existing table, field, constraint, or index

### AC-2: Preventive generation copies template tools
- **Given** a preventive maintenance item has multiple `MaintenanceTool` rows with names, quantities, location hints, inventory links, required flags, and notes
- **When** a work order is generated from that maintenance item
- **Then** the work order contains one non-ad-hoc `WorkOrderTool` row per template tool, with copied display fields and provenance links, ordered required-first and then by case-folded name

### AC-3: Generated tool rows are frozen
- **Given** a generated work order already has non-ad-hoc tool rows copied from a maintenance template
- **When** the original `MaintenanceTool` row is edited or deleted
- **Then** the existing work-order detail API and printed form still show the copied tool name, quantity, location, required flag, and notes from the work order row

### AC-4: Per-job location edits do not mutate the template
- **Given** a generated work order has a non-ad-hoc tool row copied from a maintenance template
- **When** an authorized client changes that work-order tool row's `location_hint`
- **Then** the work-order detail API shows the edited location for that job, the source `MaintenanceTool` still has its original location, and a newly generated work order from the same template still uses the template location

### AC-5: Corrective work orders can add ad-hoc tools
- **Given** a corrective work order has no `maintenance_item`
- **When** an authorized client adds a tool with name, quantity, optional inventory item, location hint, required flag, and notes through the work-order tool API
- **Then** the API returns `201 Created`, stores an ad-hoc `WorkOrderTool` with no template-tool provenance, and the work-order detail API includes that tool

### AC-6: Add-tool validation is enforced
- **Given** an authorized client submits an invalid work-order tool payload, such as a blank name or non-positive quantity
- **When** the client calls the add-tool API
- **Then** the API returns `400 Bad Request` and no `WorkOrderTool` row is created

### AC-7: Any work-order tool location can be edited
- **Given** a work order has both a non-ad-hoc tool row and an ad-hoc tool row
- **When** an authorized client updates `location_hint` on each row through the work-order tool API
- **Then** both updates persist and the work-order detail API returns the new per-job locations for both rows

### AC-8: Ad-hoc tools can be removed
- **Given** a work order has an ad-hoc tool row
- **When** an authorized client removes that row through the work-order tool API
- **Then** the API returns `204 No Content` and the row no longer appears in the work-order detail API

### AC-9: Template-derived tools cannot be removed
- **Given** a work order has a non-ad-hoc tool row copied from a maintenance template
- **When** an authorized client attempts to remove that row through the work-order tool API
- **Then** the API returns `400 Bad Request` and the row remains visible on the work order

### AC-10: Legacy work orders fall back to template tools
- **Given** a preventive work order has no `WorkOrderTool` rows and its maintenance template has tools
- **When** the work-order detail API builds the tools payload
- **Then** the response contains the same tool rows that the template-only implementation returned, including string UUID `id` values from the template tools and the same name, quantity, `location_hint`, required flag, and notes values

### AC-11: Work-order rows override template fallback
- **Given** a preventive work order has at least one `WorkOrderTool` row and its maintenance template also has `MaintenanceTool` rows
- **When** the work-order detail API builds the tools payload
- **Then** the response contains only the work-order tool rows and does not append or merge template-tool rows

### AC-12: ScanTTY tool keys stay pinned
- **Given** one work order is served through the legacy template fallback branch and another is served from `WorkOrderTool` rows
- **When** the tools payload is serialized for the work-order detail API or work-order context
- **Then** every tool object in both branches has exactly the keys `id`, `name`, `quantity`, `location_hint`, `is_required`, and `notes`, with `id` serialized as a string UUID and no extra or renamed keys

### AC-13: Explicit per-job location takes precedence
- **Given** a `WorkOrderTool` row has both a `location_hint` and an inventory item whose location has a name
- **When** the work-order detail API and printed form render that tool
- **Then** both surfaces show the row's `location_hint` as the location for the job

### AC-14: Inventory location is the fallback location
- **Given** a `WorkOrderTool` row has a blank `location_hint` and an inventory item whose location has a name
- **When** the work-order detail API and printed form render that tool
- **Then** both surfaces show the inventory item's location name as the tool location under the existing `location_hint` display contract

### AC-15: Missing location resolves to blank
- **Given** a `WorkOrderTool` row has a blank `location_hint` and no inventory item location
- **When** the work-order detail API serializes the tool
- **Then** the `location_hint` value is an empty string rather than `null` or a template-derived value

### AC-16: Corrective tools render on the printed form
- **Given** a corrective work order has one or more ad-hoc tool rows
- **When** the work-order PDF is generated
- **Then** the "Tools Required" table lists those tools with name, quantity, resolved location, and required status

### AC-17: Tools do not affect OMR targets
- **Given** a work order has task, material, and LOTO marks and one or more `WorkOrderTool` rows
- **When** OMR target IDs and the template drift signature are computed
- **Then** the target ID set and drift signature are the same as they are for the same work order without tool rows, and no tool-derived target IDs are emitted

### AC-18: Tools are not consumed as inventory
- **Given** a `WorkOrderTool` row is linked to an `InventoryItem`
- **When** the tool is added, edited, removed if ad-hoc, or the work order is completed
- **Then** the item's stock count is unchanged and no usage log, material-usage row, cost entry, receipt, reorder, or purchase-order linkage is created for the tool

### AC-19: API schema documents work-order tool actions
- **Given** a maintainer opens the API schema or docs
- **When** they inspect work-order endpoints
- **Then** the add-tool, edit-tool-location, and remove-tool actions are documented with request bodies, response shapes, auth expectations, and validation errors

### AC-20: WorkOrderPage shows corrective tools
- **Given** the frontend receives a corrective work-order payload with tools
- **When** a user opens WorkOrderPage
- **Then** the Tools Required section renders those tools instead of the empty "No tools specified" state

### AC-21: WorkOrderPage edits per-job locations
- **Given** WorkOrderPage renders a work order with template-derived and ad-hoc tool rows
- **When** a permitted user changes a tool's location and the API responds successfully
- **Then** the visible tool row updates to the returned per-job location without changing any maintenance-template tool editor state

### AC-22: WorkOrderPage adds ad-hoc tools
- **Given** a permitted user is viewing a work order
- **When** they add an ad-hoc tool from the Tools Required section and the API responds successfully
- **Then** the new tool appears in the section with its name, quantity, resolved location, required status, and notes

### AC-23: WorkOrderPage removes only ad-hoc tools
- **Given** WorkOrderPage renders both template-derived and ad-hoc tool rows
- **When** the user views available tool actions
- **Then** remove is available only for ad-hoc rows, and removing an ad-hoc row through the UI deletes it from the visible section after a successful API response

## Verification Commands
- `cd backend && pytest`
- `cd frontend && npm test`
- `pre-commit run --all-files`
- `cd backend && python manage.py makemigrations --check`
- `cd backend && python manage.py check_permission_matrix`
