# TV Dashboard Location-Specific URLs

The TV Dashboard now supports location-specific configurations to provide targeted inventory displays for different areas of your makerspace.

## 🌐 **Available URLs**

### **General Dashboard** (All Items)

```
https://yourdomain.com/tv-dashboard
```

Shows all reordered items across all locations.

### **Location-Specific Dashboards**

```
https://yourdomain.com/tv-dashboard/woodshop
https://yourdomain.com/tv-dashboard/electronics
https://yourdomain.com/tv-dashboard/metalworking
https://yourdomain.com/tv-dashboard/crafts
https://yourdomain.com/tv-dashboard/3dprinting
https://yourdomain.com/tv-dashboard/classroom
```

Replace `location` with any location name from your inventory system. The dashboard will:

- Filter items to show only those from that specific location
- Display location-specific branding (if configured)
- Update the page title to include the location name

## ⚙️ **Location-Specific Configuration**

Add environment variables to customize each location's dashboard:

### **Environment Variables Pattern**

```bash
# Pattern: REACT_APP_DASHBOARD_{SETTING}_{LOCATION_UPPERCASE}
REACT_APP_DASHBOARD_TITLE_WOODSHOP=Dallas Makerspace - Woodshop
REACT_APP_DASHBOARD_SUBTITLE_WOODSHOP=Wood & Tools Inventory
REACT_APP_DASHBOARD_LOGO_WOODSHOP=https://example.com/woodshop-logo.png

REACT_APP_DASHBOARD_TITLE_ELECTRONICS=Dallas Makerspace - Electronics Lab
REACT_APP_DASHBOARD_SUBTITLE_ELECTRONICS=Electronic Components & Tools
REACT_APP_DASHBOARD_LOGO_ELECTRONICS=https://example.com/electronics-logo.png
```

### **Available Settings**

- `TITLE_[LOCATION]` - Custom dashboard title
- `SUBTITLE_[LOCATION]` - Custom dashboard subtitle
- `LOGO_[LOCATION]` - Custom logo URL

## 📱 **QR Code Financial Transparency**

The Financial Transparency section now displays as a QR code instead of a clickable link, making it accessible from TV displays:

- **Scannable QR Code**: Points to `/transparency` page
- **Visual Design**: Prominent display with clear labeling
- **TV-Friendly**: No need for mouse/remote interaction

## 🛡️ **Anti-Burn-In Features**

The dashboard includes automatic screen burn-in prevention:

### **Pixel Shifting**

- Shifts entire display by 1-3 pixels every 3 minutes
- Smooth 2-second transitions
- Prevents static elements from burning into screens

### **Content Reordering**

- Randomly reorders item cards every 5 minutes
- Uses Fisher-Yates shuffle algorithm for fair distribution
- Smooth 1-second transitions between arrangements

### **CSS Transitions**

- All movements use hardware-accelerated transforms
- Gentle easing functions for comfortable viewing
- Maintains readability during transitions

## 📺 **TV Setup Recommendations**

### **Chromecast Setup**

1. Cast the desired location URL to your TV
2. Enable "Keep screen on" in Chrome settings
3. Set TV to turn off after 8+ hours of inactivity

### **Dedicated TV Computer**

1. Set the location URL as the homepage
2. Configure auto-refresh every 30 seconds
3. Enable fullscreen mode (F11)
4. Disable screen savers and power management

### **Smart TV Apps**

1. Use the TV's web browser
2. Bookmark location-specific URLs
3. Set appropriate display timeouts

## 🔧 **Example Configurations**

### **Multi-Location Makerspace**

```bash
# Main dashboard - all locations
https://makerspace.com/tv-dashboard

# Woodshop TV
https://makerspace.com/tv-dashboard/woodshop

# Electronics Lab TV
https://makerspace.com/tv-dashboard/electronics

# 3D Printing Area TV
https://makerspace.com/tv-dashboard/3dprinting
```

### **Environment Variables**

```bash
# Woodshop branding
REACT_APP_DASHBOARD_TITLE_WOODSHOP=Makerspace Woodshop
REACT_APP_DASHBOARD_SUBTITLE_WOODSHOP=Wood Tools & Materials
REACT_APP_DASHBOARD_LOGO_WOODSHOP=/assets/woodshop-icon.png

# Electronics branding
REACT_APP_DASHBOARD_TITLE_ELECTRONICS=Electronics Lab
REACT_APP_DASHBOARD_SUBTITLE_ELECTRONICS=Components & Test Equipment
REACT_APP_DASHBOARD_LOGO_ELECTRONICS=/assets/electronics-icon.png
```

## 🎯 **Best Practices**

1. **Location Names**: Use clear, descriptive location names that match your inventory system
2. **Branding**: Use high-contrast logos that are visible from a distance
3. **TV Placement**: Position TVs where members naturally look when entering an area
4. **URL Management**: Create simple redirects or QR codes for easy TV setup
5. **Testing**: Verify location filtering works with your inventory data

## 🚀 **Quick Start**

1. **Choose Location**: Identify the area name from your inventory system
2. **Set URL**: Visit `/tv-dashboard/[location-name]`
3. **Configure Branding**: Add environment variables for custom titles/logos
4. **Deploy to TV**: Cast or browse to the URL on your display device
5. **Verify**: Check that only relevant items appear and anti-burn-in is working

The location-based filtering is case-insensitive and uses partial matching, so "Electronics Lab" location will match `/tv-dashboard/electronics`.
