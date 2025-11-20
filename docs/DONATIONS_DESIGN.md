# Donations App Design Proposal

## Overview

The donations app tracks **bulk donations separately from Assets**, with optional linking when donated items become assets or inventory items. This design keeps donations as a distinct workflow while allowing integration with the existing asset and inventory systems.

## Key Design Principles

1. **Separation of Concerns**: Donations are tracked independently - not every donation becomes an asset
2. **Flexible Disposition**: Items can be kept, sold, auctioned, donated out, recycled, or disposed
3. **Optional Linking**: When a donation item becomes useful, it can optionally link to an Asset or InventoryItem
4. **Bulk Tracking**: Track entire donation events with multiple items
5. **Lifecycle Management**: Track items from receipt through final disposition
6. **Cost Tracking**: Track associated costs (transportation, etc.) to prioritize cost-effective donations
7. **SIG/Committee Support**: Kept items can be assigned to the makerspace or to specific SIGs/Committees

## Models

### 1. Donation

Represents a bulk donation receipt event.

**Key Features:**

- Auto-generated donation numbers (DON-2024-001 format)
- Donor information (name, email, phone, address)
- Receipt tracking (date, received by, notes)
- Status workflow (pending → reviewed → processing → completed)
- Tax receipt tracking
- Estimated value for record-keeping
- **Associated costs tracking** (transportation, handling, etc.)
- **Net value calculation** (estimated value minus costs)

**Example Use Case:**
"Received 50 items from Acme Corp on 2024-01-15, including electronics, tools, and materials"

### 2. DonationItem

Individual items within a donation.

**Key Features:**

- Links to parent Donation
- Item description and quantity
- Condition assessment (excellent, good, fair, poor, unusable)
- Status tracking (pending_review, usable, unusable, processing, disposed)
- **Optional links** to Asset or InventoryItem (only when item becomes one)
- Tracks remaining quantity not yet disposed

**Example Use Case:**
"3D Printer (x1) - Good condition - Usable"
"Broken Electronics (x5) - Poor condition - Unusable"

### 3. Disposition

Tracks what happened to each donation item.

**Key Features:**

- Multiple dispositions per item (for partial disposals)
- Disposition types: kept, sold, **auctioned**, donated_out, recycled, disposed, returned, parted_out, other
- Quantity tracking (can dispose items in parts)
- **Sale method tracking** (direct sale vs auction) for sold/auctioned items
- Sale price tracking (if sold or auctioned)
- **Kept destination tracking** (makerspace vs SIG/Committee) for kept items
- **SIG/Committee assignment** (via Django Group) for items given to SIGs
- Recipient information (if donated out or sold)
- **Optional link** to created Asset (when kept item becomes tracked asset)
- Date and user tracking

**Example Use Cases:**

- "Kept 1x 3D Printer for makerspace use" → Creates Asset link
- "Kept 2x Soldering Stations for Electronics SIG" → Links to SIG Group
- "Sold 5x Electronics directly for $50" → Direct sale
- "Auctioned 1x Vintage Equipment for $200" → Auction sale
- "Donated 10x Tools to Local School"
- "Recycled 2x Broken Items"

## Workflow Example

1. **Receipt**: Create Donation with donor info and date
2. **Item Entry**: Add DonationItems (name, quantity, condition)
3. **Review**: Mark donation as reviewed, assess items
4. **Disposition**: For each item, create Disposition records:
   - Usable items → "Kept" disposition → Optionally create Asset
   - Sellable items → "Sold" disposition → Record sale price
   - Unusable items → "Disposed" or "Recycled" disposition
5. **Completion**: Mark donation as completed when all items disposed

## Integration Points

### With Assets

- When a donation item is "kept", you can optionally create an Asset and link it
- The `DonationItem.asset` field stores this link
- The `Disposition.created_asset` field tracks which disposition created the asset

### With Inventory

- When a donation item becomes consumable inventory, link to InventoryItem
- The `DonationItem.inventory_item` field stores this link

### Key Point

**Donations remain separate** - linking to Assets/Inventory is optional and only happens when items are actually used. Many donations may never become assets.

## Status Workflows

### Donation Status

- `pending` → Initial receipt
- `reviewed` → Items have been assessed
- `processing` → Dispositions are being made
- `completed` → All items have been disposed
- `cancelled` → Donation cancelled/voided

### DonationItem Status

- `pending_review` → Not yet assessed
- `usable` → Can be used
- `unusable` → Cannot be used
- `processing` → Currently being disposed
- `disposed` → Fully disposed

## Next Steps

1. Review this design with your actual workflow
2. Adjust disposition types if needed
3. Add any additional fields required
4. Create API endpoints (views/serializers)
5. Create frontend interface

## Reporting

The `DonationReportingService` provides:

1. **Quarterly Reports**: By quarter with donation details and summaries
2. **Yearly Reports**: Full year with breakdown by donor
3. **Donor Summaries**: All donations from a specific donor

Reports include:

- Total donations count
- Total unique donors
- Total items and quantities
- Total estimated values
- Total associated costs
- Net values (estimated value minus costs)
- Breakdown by donor

## Permissions

**Who can accept donations:**

- Board of Directors
- Officers
- Committee/SIG Leaders

This will be implemented in the API views/permissions layer (not in models).

## Questions for You

1. **Disposition Types**: Are the current types sufficient, or do you need others?
2. **Workflow**: Does this match your process, or are there additional steps?
3. **Reporting**: Do the quarterly/yearly reports meet your needs, or do you need additional reports?
4. **Notifications**: Should there be webhooks/alerts for certain events?
5. **Cost Tracking**: Is the current cost tracking sufficient, or do you need more detailed cost breakdowns?
