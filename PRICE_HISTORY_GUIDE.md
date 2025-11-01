# Price History Feature Guide

## Overview

The price history feature automatically tracks all price changes for your inventory items, allowing you to:
- Monitor price trends over time
- Analyze cost fluctuations from suppliers
- Make informed purchasing decisions
- Generate historical pricing reports

## How It Works

### Automatic Tracking
Every time you update pricing information for an item-supplier relationship, the system automatically:
1. **Records the change** in a `PriceHistory` record
2. **Calculates percentage changes** compared to previous prices
3. **Timestamps the change** for historical analysis
4. **Categorizes the change type** (created, updated, supplier_changed)

### What Gets Tracked
- **Unit Cost**: Cost per individual unit
- **Package Cost**: Total cost for one package
- **Quantity per Package**: Number of units in each package
- **Change Type**: Type of modification that triggered the record
- **Timestamp**: When the change occurred
- **Notes**: Optional notes about the price change

## API Endpoints

### 1. Item-Supplier Relationships
```http
GET /api/inventory/item-suppliers/
```
Returns all item-supplier relationships with recent price history (last 10 records).

**Query Parameters:**
- `item_id`: Filter by specific item UUID
- `supplier_id`: Filter by specific supplier ID
- `active_only=true`: Only active supplier relationships

**Example Response:**
```json
{
  "id": 123,
  "item_name": "Resistor 10K",
  "supplier_name": "Electronics Plus",
  "unit_cost": "0.07",
  "package_cost": "7.00",
  "quantity_per_package": 100,
  "is_primary": true,
  "recent_price_history": [
    {
      "id": 456,
      "unit_cost": "0.07",
      "package_cost": "7.00",
      "change_type": "updated",
      "recorded_at": "2024-01-15T10:30:00Z",
      "price_change_percentage": "40.00"
    }
  ]
}
```

### 2. Full Price History for Item-Supplier
```http
GET /api/inventory/item-suppliers/{id}/price_history/
```
Get complete price history for a specific item-supplier relationship.

**Query Parameters:**
- `start_date`: Filter from date (YYYY-MM-DD)
- `end_date`: Filter to date (YYYY-MM-DD)

### 3. Global Price History
```http
GET /api/inventory/price-history/
```
Returns all price history records across the system.

**Query Parameters:**
- `item_id`: Filter by item UUID
- `supplier_id`: Filter by supplier ID  
- `change_type`: Filter by change type (created, updated, supplier_changed)
- `start_date`: Filter from date
- `end_date`: Filter to date

### 4. Recent Price Changes
```http
GET /api/inventory/price-history/recent_changes/
```
Get recent price changes across all items.

**Query Parameters:**
- `days`: Number of days to look back (default: 30)

### 5. Item Details with Price Trends
```http
GET /api/inventory/items/{uuid}/
```
Returns detailed item information including price trend summary.

**Price Trend Summary Example:**
```json
{
  "price_trend_summary": {
    "trend": "increasing",
    "change_percentage": "40.00",
    "latest_cost": "0.07",
    "previous_cost": "0.05",
    "last_updated": "2024-01-15T10:30:00Z"
  }
}
```

## Usage Examples

### Tracking Price Changes
When you update an item's pricing through the API or admin:

```python
# This automatically creates a price history record
item_supplier.unit_cost = Decimal('0.08')
item_supplier.save()  # Price history record created automatically
```

### Analyzing Price Trends
```python
# Get price trend for an item
from inventory.models import InventoryItem

item = InventoryItem.objects.get(name="My Item")
primary_supplier = item.primary_item_supplier

if primary_supplier:
    recent_history = primary_supplier.price_history.all()[:5]
    for record in recent_history:
        print(f"Date: {record.recorded_at}, Price: ${record.unit_cost}, Change: {record.price_change_percentage}%")
```

### API Usage Examples
```bash
# Get all price history for a specific item
curl "http://localhost:8000/api/inventory/price-history/?item_id=12345678-1234-1234-1234-123456789abc"

# Get recent price changes (last 7 days)
curl "http://localhost:8000/api/inventory/price-history/recent_changes/?days=7"

# Get price history for a specific item-supplier relationship
curl "http://localhost:8000/api/inventory/item-suppliers/123/price_history/"
```

## Database Schema

### PriceHistory Model
- `id`: Primary key
- `item_supplier`: Foreign key to ItemSupplier
- `unit_cost`: Unit cost at time of record
- `package_cost`: Package cost at time of record  
- `quantity_per_package`: Quantity per package at time of record
- `change_type`: Type of change (created, updated, supplier_changed)
- `recorded_at`: Timestamp of the change
- `notes`: Optional notes about the change

### Key Features
- **Automatic creation**: No manual intervention needed
- **Percentage calculation**: Automatic calculation of price change percentages
- **Indexed queries**: Optimized for fast retrieval by item, supplier, and date
- **Read-only API**: Price history records cannot be modified through the API

## Best Practices

1. **Regular Price Updates**: Keep supplier prices current to build comprehensive history
2. **Use Notes Field**: Add context to significant price changes
3. **Monitor Trends**: Use the trend analysis to negotiate better prices
4. **Set Alerts**: Consider setting up notifications for significant price changes
5. **Historical Analysis**: Use date range queries for seasonal analysis

## Migration Notes

- The database migration (`0009_pricehistory.py`) is already created
- Existing ItemSupplier records will create initial price history on first update
- No data loss - all current pricing information is preserved

## Testing

Run the included test script to verify functionality:
```bash
cd backend
python test_price_history.py
```

This will demonstrate:
- Automatic price history creation
- Price change calculations  
- Trend analysis
- Available API endpoints

## Integration Tips

### Frontend Integration
- Use the `price_trend_summary` field for quick trend indicators
- Display recent price changes in item detail views
- Create charts using the historical data
- Show alerts for significant price increases

### Reporting
- Export price history data for spreadsheet analysis
- Create automated reports for purchasing decisions
- Track supplier price volatility
- Monitor seasonal price patterns

The price history feature provides a solid foundation for cost analysis and trend monitoring in your makerspace inventory management system.
