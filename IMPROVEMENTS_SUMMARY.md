# Backend Improvements Summary

## Overview

Significant improvements have been made to the inventory management system based on practical makerspace requirements. These changes enhance flexibility, usability, and accuracy of the system.

## 1. Auto-Generated SKU with UUID7

### What Changed
- SKU field now auto-generates if not provided
- Uses UUID7 (time-ordered UUID) for better sorting and indexing
- Falls back to UUID4 for compatibility with older Python versions

### Benefits
- No need to manually create SKUs
- Guaranteed uniqueness
- Time-ordered for better database performance
- Can still provide custom SKUs if needed

### Implementation
```python
def generate_sku() -> str:
    """Generate a unique SKU using UUID7 (time-ordered UUID)."""
    try:
        return str(uuid.uuid7())  # Python 3.11+
    except AttributeError:
        return str(uuid.uuid4())  # Fallback

# In InventoryItem.save():
if not self.sku:
    self.sku = generate_sku()
```

### Usage
```python
# Auto-generated SKU
item = InventoryItem.objects.create(name="Widget", ...)
# item.sku will be automatically set

# Custom SKU
item = InventoryItem.objects.create(name="Widget", sku="CUSTOM-001", ...)
# item.sku will be "CUSTOM-001"
```

## 2. Location Model

### What Changed
- Converted `location` from CharField to ForeignKey
- Created new `Location` model for predefined storage areas
- Existing location data automatically migrated to Location objects

### Benefits
- Consistent location names across inventory
- Easy to manage and update location information
- Can mark locations as inactive
- Better organization and filtering

### Location Model Fields
```python
class Location:
    name: str                    # Unique location name
    description: str             # Details about location
    is_active: bool              # Active/inactive status
    created_at: datetime
    updated_at: datetime
```

### Usage
```python
# Create locations
workshop = Location.objects.create(name="Main Workshop")
electronics = Location.objects.create(
    name="Electronics Lab",
    description="Temperature controlled, ESD safe"
)

# Assign to items
item.location = workshop
item.save()
```

## 3. WebP Support and URL-Based Image Download

### What Changed
- Removed ProcessedImageField, using standard ImageField
- Added `image_url` field for URL-based image download
- Support for WebP, JPEG, and PNG formats
- Automatic image download on save

### Benefits
- Modern WebP format for smaller file sizes
- Can import images from URLs (e.g., supplier websites)
- Reduced manual image uploading
- Better performance with smaller images

### Implementation
```python
# In InventoryItem.save():
if self.image_url and not self.image:
    response = requests.get(self.image_url, timeout=10)
    # Detect format from content-type or URL
    # Save downloaded image to self.image
```

### Usage
```python
# Upload file directly
item.image = uploaded_file
item.save()

# Or provide URL
item.image_url = "https://example.com/product.webp"
item.save()  # Image automatically downloaded
```

## 4. Auto-Generated Thumbnails

### What Changed
- Thumbnail field now uses `ImageSpecField` (auto-generated)
- Thumbnails created on-the-fly from main image
- WebP format with 85% quality
- 300x300px resize with aspect ratio maintained

### Benefits
- No need to manually create thumbnails
- Always in sync with main image
- Disk space saved (generated on request)
- Consistent thumbnail size

### Implementation
```python
thumbnail = ImageSpecField(
    source='image',
    processors=[ResizeToFit(300, 300)],
    format='WEBP',
    options={'quality': 85}
)
```

### Usage
```html
<!-- Main image -->
<img src="{{ item.image.url }}" />

<!-- Thumbnail (auto-generated) -->
<img src="{{ item.thumbnail.url }}" />
```

## 5. Multiple Suppliers Per Item

### What Changed
- Removed direct supplier fields from InventoryItem
- Created `ItemSupplier` through model for many-to-many relationship
- Each item can now have multiple suppliers with different prices/SKUs/lead times

### Benefits
- Compare prices across suppliers
- Track different SKUs per supplier
- Different lead times per supplier
- Mark primary/preferred supplier
- Enable/disable specific supplier options

### ItemSupplier Model Fields
```python
class ItemSupplier:
    item: InventoryItem
    supplier: Supplier
    supplier_sku: str            # Supplier's SKU for this item
    supplier_url: str            # Link to product page
    unit_cost: Decimal           # Price from this supplier
    average_lead_time: int       # Days to deliver
    is_primary: bool             # Preferred supplier
    is_active: bool              # Currently using
    notes: str                   # Supplier-specific notes
```

### Usage
```python
# Add multiple suppliers to an item
ItemSupplier.objects.create(
    item=widget,
    supplier=home_depot,
    supplier_sku="HD-12345",
    unit_cost=Decimal("25.99"),
    average_lead_time=2,
    is_primary=True
)

ItemSupplier.objects.create(
    item=widget,
    supplier=amazon,
    supplier_sku="B08XYZ",
    unit_cost=Decimal("29.99"),
    average_lead_time=1
)

# Get lowest cost
lowest_cost = item.lowest_unit_cost  # $25.99

# Get primary supplier
primary = item.primary_supplier  # home_depot
```

## Database Migration Details

### Migration Steps
The migration safely handles existing data:

1. **Create Location Model** - New table for storage locations
2. **Migrate Location Data** - Extract unique locations from existing items
3. **Update Location References** - Convert strings to ForeignKey relationships
4. **Create ItemSupplier Model** - New through table
5. **Update Image Fields** - Support WebP and URL downloads
6. **Remove Old Fields** - Clean up deprecated supplier fields

### Backwards Compatibility
- Existing location strings automatically converted to Location objects
- Old images continue to work
- Data preserved during migration

## Updated Admin Interface

### New Features
- Location management page
- ItemSupplier inline editor in InventoryItem admin
- Image URL field for easy image importing
- Auto-generated SKU (read-only in admin)
- Multiple suppliers per item with inline editing

### Admin Changes
```python
# Location Admin
@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name', 'description']

# ItemSupplier Inline
class ItemSupplierInline(admin.TabularInline):
    model = ItemSupplier
    extra = 1
    fields = ['supplier', 'supplier_sku', 'supplier_url',
              'unit_cost', 'average_lead_time', 'is_primary', 'is_active']

# Updated InventoryItem Admin
@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    inlines = [ItemSupplierInline]  # Add suppliers inline
    readonly_fields = ['id', 'sku', ...]  # SKU is auto-generated
```

## API Changes (To Be Implemented)

### Required Serializer Updates
- Add `LocationSerializer`
- Add `ItemSupplierSerializer`
- Update `InventoryItemSerializer` to include:
  - `location` as nested object
  - `suppliers` as nested array
  - `image_url` field
  - Read-only `lowest_unit_cost` property

### New Endpoints Needed
- `/api/inventory/locations/` - List/create locations
- Item suppliers managed through nested serializers or separate endpoints

## Testing Considerations

### Tests to Update
1. **Factory Updates** - Adjust factories for new fields
2. **Model Tests** - Test SKU generation, image download, supplier relationships
3. **API Tests** - Update for new structure
4. **Migration Tests** - Verify data migration works correctly

### New Tests Needed
1. UUID7 generation and fallback
2. Image download from URL
3. WebP image handling
4. Multiple suppliers per item
5. Primary supplier selection
6. Location model functionality

## Configuration Changes

### requirements.txt
Added:
```
requests==2.31.0  # For image URL downloads
```

### Settings
No changes required - uses existing imagekit configuration

## Usage Examples

### Creating an Item with Multiple Suppliers

```python
# Create the item
item = InventoryItem.objects.create(
    name="M4 x 10mm Screw",
    description="Stainless steel machine screw",
    image_url="https://example.com/screw.webp",
    location=workshop_location,
    current_stock=500,
    minimum_stock=100,
    reorder_quantity=500
)
# SKU auto-generated, image auto-downloaded

# Add suppliers
ItemSupplier.objects.create(
    item=item,
    supplier=home_depot,
    supplier_sku="HD-SS-M4-10",
    supplier_url="https://homedepot.com/...",
    unit_cost=Decimal("0.15"),
    average_lead_time=2,
    is_primary=True
)

ItemSupplier.objects.create(
    item=item,
    supplier=mcmaster,
    supplier_sku="91290A113",
    supplier_url="https://mcmaster.com/...",
    unit_cost=Decimal("0.12"),
    average_lead_time=1
)

# Query
print(item.sku)  # Auto-generated UUID7
print(item.lowest_unit_cost)  # $0.12 (McMaster)
print(item.primary_supplier.name)  # "Home Depot"
print(item.thumbnail.url)  # Auto-generated thumbnail
```

### Managing Locations

```python
# Create locations
Location.objects.bulk_create([
    Location(name="Main Workshop"),
    Location(name="Electronics Lab"),
    Location(name="Wood Shop"),
    Location(name="Storage Room A"),
    Location(name="Storage Room B"),
])

# Filter items by location
workshop_items = InventoryItem.objects.filter(
    location__name="Main Workshop"
)

# Deactivate a location
old_storage = Location.objects.get(name="Old Storage")
old_storage.is_active = False
old_storage.save()
```

## Benefits Summary

### For Administrators
- ✅ Less manual data entry (auto SKU, auto thumbnails)
- ✅ Better price comparison across suppliers
- ✅ Organized storage locations
- ✅ Faster image importing from URLs

### For Users
- ✅ Consistent location names
- ✅ Access to multiple suppliers per item
- ✅ Faster page loads (WebP images)
- ✅ Better mobile experience (smaller images)

### For Developers
- ✅ More flexible data model
- ✅ Better database performance (UUID7, proper indexes)
- ✅ Cleaner code architecture
- ✅ Type-safe with annotations

## Next Steps

1. ✅ Models updated
2. ✅ Migrations created and applied
3. ✅ Admin interface updated
4. ⏳ Update serializers (in progress)
5. ⏳ Update views if needed
6. ⏳ Update tests
7. ⏳ Update frontend to use new structure
8. ⏳ Documentation

## Rollback Plan

If needed, migrations can be rolled back:

```bash
# Rollback to before these changes
python manage.py migrate inventory 0002

# Note: This will lose ItemSupplier relationships
# and convert Locations back to text
```

## Questions or Issues

If you encounter any issues with these changes:

1. Check the migration was applied: `python manage.py showmigrations inventory`
2. Verify Location objects were created: `Location.objects.all()`
3. Test image download: Create item with `image_url`
4. Check SKU generation: Create item without SKU

## Conclusion

These improvements make the inventory management system significantly more practical for real-world makerspace use while maintaining backwards compatibility and data integrity. The system is now more flexible, efficient, and user-friendly.
