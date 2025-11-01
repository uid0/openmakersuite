# Enhanced TV Dashboard - Reordered Items Tracking

## Overview

The TV Dashboard has been completely redesigned to focus on **items that have been reordered** rather than items that need reordering. This provides much better visibility into what's actually in the pipeline and when items are expected to arrive.

## Key Features

### 🎯 **Focus on Reordered Items**
- **No Low Stock Clutter**: Only shows items with active reorder requests (pending/approved/ordered)
- **Pipeline Visibility**: Track items from request to delivery
- **Clean Display**: No duplicate or unnecessary information

### 🏢 **Customizable Branding**
- **Organization Logo**: Display your makerspace logo
- **Custom Title**: Default "Dallas Makerspace Inventory" or customize
- **Subtitle Support**: Shows "Items on Order" or custom subtitle
- **Professional Look**: TV-ready branding for public displays

### 📊 **Enhanced Status Tracking**
- **Color-Coded Status**: Visual indicators for different reorder states
- **Expected Delivery**: Countdown timers for ordered items
- **Request Details**: Who requested, when, and how much

## Configuration

### Environment Variables

Set these in your environment or `.env` file:

```bash
# Branding Configuration
REACT_APP_DASHBOARD_TITLE="Your Makerspace Inventory"
REACT_APP_DASHBOARD_SUBTITLE="Items on Order"
REACT_APP_DASHBOARD_LOGO="https://yourdomain.com/logo.png"
REACT_APP_SHOW_LOGO="true"

# API Configuration  
REACT_APP_API_URL="http://localhost:8000"
```

### Logo Requirements
- **Format**: PNG, JPG, or SVG
- **Size**: Optimized for 6rem height (96px)
- **Aspect Ratio**: Logo will maintain aspect ratio
- **Location**: Can be local file or hosted URL

## Dashboard States

### 1. **Items on Order** (Active Display)
Shows all items with active reorder requests:

**Status Indicators:**
- 🟡 **REORDER REQUESTED**: Pending admin approval
- 🟣 **APPROVED FOR ORDER**: Ready to be ordered
- 🟢 **ORDERED**: Order placed, shows expected delivery

**Information Displayed:**
- Item name, image, location, category
- Current stock vs. minimum stock levels
- Request status and quantity ordered
- Expected delivery countdown for ordered items
- Who requested and when

### 2. **No Items on Order** (Clean State)
When no active reorder requests exist:
- ✅ **Clean Message**: "No Items on Order"
- **Positive Messaging**: "All reorder requests have been completed"
- **No Clutter**: Empty state is clean and informative

## Display Features

### **Smart Status Display**
Each item card shows:
- **Visual Status Badge**: Color-coded reorder status
- **Expected Delivery**: "Expected in 5 days", "Expected tomorrow"
- **Order Information**: Quantity ordered and order details
- **Stock Context**: Current vs. minimum stock levels

### **Delivery Tracking**
For ordered items:
- **Countdown Display**: Days until expected delivery
- **Order Date**: When the order was placed
- **Lead Time Calculation**: Uses average lead time + order date
- **Visual Emphasis**: Green highlighting for ordered items

### **Real-Time Updates**
- **30-Second Refresh**: Automatic updates
- **Status Changes**: Real-time status updates as admins process orders
- **New Requests**: Automatically appears when new reorders are submitted

## API Endpoints

### New Endpoint: `/api/inventory/items/reordered/`
Returns items with active reorder requests:

```json
[
  {
    "id": "item-uuid",
    "name": "Arduino Uno R3",
    "reorder_status": "ordered",
    "expected_delivery_date": "2025-11-08",
    "active_reorder_request": {
      "status": "ordered",
      "quantity": 25,
      "requested_at": "2025-11-01T10:00:00Z",
      "ordered_at": "2025-11-01T14:00:00Z",
      "requested_by": "John Doe"
    }
  }
]
```

## Use Cases

### **For Staff Visibility**
- See what's been ordered and when it's expected
- Track multiple orders across different suppliers
- Understand inventory pipeline at a glance

### **For Management**
- Monitor reorder workflow efficiency  
- See outstanding orders and expected delivery dates
- Track who's requesting what and when

### **For Public Display**
- Professional-looking branded display
- Clean, informative interface
- Suitable for lobby or common area displays

## Workflow Integration

### **Request → Display Flow**
1. **User scans QR code** → Creates reorder request
2. **Item appears on dashboard** with "REORDER REQUESTED" status
3. **Admin approves** → Status changes to "APPROVED FOR ORDER" 
4. **Admin marks as ordered** → Status changes to "ORDERED" with expected delivery
5. **Admin marks as received** → Item disappears from dashboard

### **No More Confusion**
- **Single Source of Truth**: Dashboard shows exactly what's in progress
- **No Duplicates**: Items only appear once regardless of multiple scans
- **Clear Status**: Everyone knows the current state of each order

## Technical Details

### **Backend Changes**
- New `reordered` API endpoint in `InventoryItemViewSet`
- Enhanced model methods for reorder status tracking
- Public API access (no authentication required)

### **Frontend Architecture**
- Dedicated API instance for TV display (no auth headers)
- Environment-based configuration system
- Responsive design for TV and mobile viewing
- Real-time updates with error handling

### **Performance**
- Efficient queries using existing reorder relationships
- Minimal API calls with smart caching
- Optimized for continuous display operation

## Access URLs

- **Development**: `http://localhost:3000/tv-dashboard`
- **Production**: `https://yourdomain.com/tv-dashboard`

## Benefits Over Previous Version

### **Before** (Low Stock Focus):
- ❌ Showed items that "needed" reordering (many false positives)
- ❌ Duplicated items that already had requests
- ❌ No visibility into order status or delivery dates
- ❌ Generic branding, not professional

### **After** (Reordered Items Focus):
- ✅ Shows only items actually being reordered
- ✅ Clear status and delivery tracking
- ✅ Professional branded appearance
- ✅ Real-time pipeline visibility
- ✅ Clean, actionable information

The enhanced TV Dashboard now provides exactly what a makerspace needs: **clear visibility into what's been ordered and when it's expected to arrive**, with professional branding suitable for public display.

Perfect for casting to TVs in common areas, workshops, or administrative offices! 📺🚀
