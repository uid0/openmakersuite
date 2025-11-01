# TV Dashboard for Chromecast Display

A large-screen dashboard optimized for displaying on TVs via Chromecast, showing items that need reordering in real-time.

## Features

- **TV-Optimized Design**: Large fonts, high contrast colors, and clear visual hierarchy for distance viewing
- **Real-Time Updates**: Auto-refreshes every 30 seconds to show current inventory status
- **Visual Stock Indicators**: Color-coded urgency levels (Green/Yellow/Red) for quick assessment
- **Comprehensive Item Info**: Shows current stock, minimum stock, reorder quantities, suppliers, and lead times
- **Responsive Layout**: Adapts to different screen sizes while maintaining readability

## Accessing the TV Dashboard

### Via Web Browser
1. Navigate to: `http://localhost:3000/tv-dashboard` (development)
2. Or: `https://yourdomain.com/tv-dashboard` (production)

### Via Homepage
1. Go to the main homepage
2. Click on the "TV Dashboard" card
3. The dashboard will open in full-screen friendly mode

## Chromecast Setup

1. **Cast from Browser**: Use Chrome's cast button to display the dashboard on your TV
2. **Direct URL**: Share the TV dashboard URL for easy access
3. **Auto-Refresh**: The page will automatically update every 30 seconds - no interaction needed

## Display Information

The dashboard shows for each low-stock item:

- **Item Image**: Product photo when available
- **Item Name**: Clear, large text
- **Location**: Physical location in makerspace  
- **Category**: Item category for organization
- **Stock Levels**: Current vs. minimum stock with visual indicators
- **Reorder Info**: Quantity to order, supplier name, lead time
- **Urgency Status**: OUT OF STOCK / CRITICAL / LOW STOCK indicators

## Status Indicators

- 🟢 **All Good**: No items displayed (all well-stocked)
- 🟡 **Low Stock**: Item below minimum but still available
- 🔴 **Critical**: Item very low or out of stock
- ⚫ **Out of Stock**: Zero inventory with pulsing alert

## Technical Details

- **Auto-Refresh**: 30-second intervals
- **API Endpoint**: Uses `/api/inventory/items/low_stock/` 
- **Responsive**: Works on screens from 720p to 4K
- **Performance**: Optimized for smooth display on Chromecast devices
- **Error Handling**: Shows connection status and retry information

## TV/Chromecast Best Practices

1. **Full Screen**: Use F11 or browser full-screen mode before casting
2. **Stable Connection**: Ensure strong WiFi for both computer and Chromecast
3. **Screen Timeout**: Disable computer sleep mode for continuous display
4. **High Contrast**: The design works well in bright or dim lighting conditions

## URL Access

- **Development**: `http://localhost:3000/tv-dashboard`
- **Production**: Set via `FRONTEND_URL` environment variable

Perfect for displaying in common areas, workshops, or logistics areas where staff need quick visibility into inventory status!
