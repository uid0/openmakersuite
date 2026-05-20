# Reactive workflow transitions

## Context
OpenMakerSuite has many operational workflows that already call APIs from React, but after a successful mutation they often reload an entire page, table, or detail panel through the same initial loader. That makes routine actions feel page-refresh-y: operators lose focus, scroll position, expanded panels, partially entered context, and the sense that they are working inside a live app.

The product should treat common operational mutations as reactive state transitions. Purchase receiving is a good first proving path, but the standard applies across reorder triage, purchase orders, work orders, asset maintenance, public scan/checklist flows, location/category/admin lists, webhook management, electrical records, SIG workflows, and any page where a successful mutation currently causes a full context reset.

## Scope
- In: A cross-app reactive mutation pattern, an inventory of high-impact refresh-y workflows, local React state updates from mutation responses, scoped pending/error states, non-disruptive reconciliation fetches, and pilot implementations for purchase receiving plus at least one non-purchasing workflow.
- Out: Replacing REST with WebSockets/SSE, introducing a required global client-state library, changing backend business rules, changing permission policy, redesigning page visual hierarchy, and converting every low-impact admin refresh in one PR.

## Criteria

### AC-1: Refresh-y workflow inventory exists
- **Given** maintainers want to make OMS feel less page-refresh-y
- **When** they review the frontend resilience documentation
- **Then** there is an inventory of mutation flows that currently reload whole pages, tables, or panels after success, grouped by product area and prioritized by operator impact

### AC-2: Reactive mutation standard is documented
- **Given** Claude implements or changes a mutation-driven workflow
- **When** the workflow can update from the mutation response or a known local state patch
- **Then** the implementation follows a documented standard: patch visible state first, preserve user context, use scoped pending/error UI, and reserve full blocking reloads for cases with an explicit reason

### AC-3: Purchase receiving proves the pattern
- **Given** an authenticated staff user marks a reorder request received or marks a purchase order delivered
- **When** the API returns the updated request or purchase-order payload
- **Then** the visible row/page updates from that response without replacing the whole requests table or purchase-order page with its initial loading state

### AC-4: A non-purchasing workflow proves the pattern
- **Given** an authenticated user completes one selected non-purchasing workflow from the refresh-y inventory, such as a work-order state transition, asset maintenance action, checklist action, location/category mutation, webhook action, electrical record edit, or SIG update
- **When** the API call succeeds
- **Then** the relevant list, card, detail panel, or section updates in place without a full context reset

### AC-5: Mutations have scoped pending state
- **Given** a mutation API call is in flight
- **When** the user looks at the affected control, row, form, or panel
- **Then** only the affected operation is disabled or marked busy, unrelated controls remain usable where safe, and duplicate submissions for that operation are prevented

### AC-6: Mutation failures preserve context
- **Given** a mutation API call fails because of validation, permission, network, or dependency error
- **When** the error is shown to the user
- **Then** the existing page context remains visible, user-entered retry context is preserved where possible, and the user can retry without navigating or reloading the page

### AC-7: Background reconciliation is non-disruptive
- **Given** a workflow performs a follow-up fetch after a successful local update to reconcile derived fields
- **When** that follow-up fetch starts, completes, or fails
- **Then** it must not set the whole page/table/panel to its initial loading state, must not clear current selection/filter/scroll context, and must not overwrite a newer local mutation result with stale data

### AC-8: Tests prove the React-y contract
- **Given** the frontend test suite runs
- **When** it exercises the pilot purchasing and non-purchasing workflows
- **Then** tests assert that mutation responses update local UI state immediately, full loading placeholders are not shown after submit, duplicate submit is prevented while pending, and failure leaves existing UI context intact
