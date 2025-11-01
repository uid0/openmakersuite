# TV Dashboard Configuration

## Footer Message Rotation

The TV Dashboard footer now supports configurable rotating messages that can be managed via environment variables or a backend API.

### Environment Variable Configuration

Add these variables to your `.env` file in the frontend directory:

```bash
# Footer Message Configuration
# JSON array of messages to rotate through
REACT_APP_FOOTER_MESSAGES=["Tracking items from request to delivery","Scan QR codes to request reorders","Keeping your makerspace stocked","Real-time inventory management","Automated supply chain tracking"]

# Message rotation interval in seconds (default: 10)
REACT_APP_MESSAGE_ROTATION_SECONDS=10
```

### Message Format

Messages should be provided as a JSON array of strings:

```bash
# Single message (no rotation)
REACT_APP_FOOTER_MESSAGES=["Tracking items from request to delivery"]

# Multiple messages with custom timing
REACT_APP_FOOTER_MESSAGES=["Welcome to Dallas Makerspace","Scan QR codes to request supplies","Keeping makers equipped","Check item status in real-time"]
REACT_APP_MESSAGE_ROTATION_SECONDS=15
```

### Advanced Configuration Example

```bash
# Complete TV Dashboard Configuration
REACT_APP_DASHBOARD_TITLE="Dallas Makerspace Inventory"
REACT_APP_DASHBOARD_SUBTITLE="Supply Management"
REACT_APP_DASHBOARD_LOGO="https://dallasgit.com/logo.png"
REACT_APP_SHOW_LOGO=true
REACT_APP_FOOTER_MESSAGES=["🔧 Tracking tools and supplies","📦 QR codes for instant reorders","⚡ Real-time inventory updates","🎯 Keeping makers productive","🚀 Automated supply management"]
REACT_APP_MESSAGE_ROTATION_SECONDS=8
```

## Backend API Extension

For dynamic message management, you can extend the system with a backend API:

### 1. Django Model (example)

```python
# backend/dashboard/models.py
from django.db import models

class DashboardMessage(models.Model):
    message = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['order', 'created_at']
    
    def __str__(self):
        return self.message
```

### 2. API Endpoint (example)

```python
# backend/dashboard/views.py
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from .models import DashboardMessage

@api_view(['GET'])
@permission_classes([AllowAny])
def get_dashboard_messages(request):
    messages = DashboardMessage.objects.filter(is_active=True)
    return Response({
        'messages': [msg.message for msg in messages],
        'rotation_interval': 10  # seconds
    })
```

### 3. Frontend Integration (example)

```javascript
// In TVDashboard.tsx - replace environment variable logic
const [footerMessages, setFooterMessages] = useState([]);

useEffect(() => {
  // Fetch messages from API
  const fetchMessages = async () => {
    try {
      const response = await tvAPI.get('/api/dashboard/messages/');
      setFooterMessages(response.data.messages);
    } catch (error) {
      // Fallback to default messages
      setFooterMessages(['Tracking items from request to delivery']);
    }
  };
  
  fetchMessages();
  // Refresh messages hourly
  const interval = setInterval(fetchMessages, 3600000);
  return () => clearInterval(interval);
}, []);
```

### 4. Admin Interface (example)

```python
# backend/dashboard/admin.py
from django.contrib import admin
from .models import DashboardMessage

@admin.register(DashboardMessage)
class DashboardMessageAdmin(admin.ModelAdmin):
    list_display = ['message', 'is_active', 'order', 'created_at']
    list_filter = ['is_active', 'created_at']
    list_editable = ['is_active', 'order']
    ordering = ['order', 'created_at']
```

## Usage Examples

### Quick Setup
1. Add messages to your `.env` file
2. Restart your React development server
3. Messages will rotate automatically

### Custom Messages for Events
```bash
# During maker events
REACT_APP_FOOTER_MESSAGES=["🎉 Maker Faire 2024 in progress","Welcome visitors to Dallas Makerspace","Scan items to see our inventory system","Ask staff about membership benefits"]

# During maintenance
REACT_APP_FOOTER_MESSAGES=["⚠️ Scheduled maintenance in progress","Some items may be temporarily unavailable","Normal operations resume tomorrow"]

# Holiday messages
REACT_APP_FOOTER_MESSAGES=["🎄 Happy Holidays from Dallas Makerspace","Limited hours during holiday break","Emergency supplies available 24/7"]
```

### Message Rotation Features

- **Smooth Animation**: Messages fade in/out with subtle animation
- **Configurable Timing**: Set rotation speed from 1-60 seconds
- **Single Message**: Set one message to disable rotation
- **Dynamic Loading**: Ready for backend API integration
- **Fallback Handling**: Graceful degradation if configuration fails

## Implementation Status

✅ **Environment Variable Configuration**: Ready to use  
✅ **Message Rotation**: Smooth fade animations  
✅ **Configurable Timing**: Custom rotation intervals  
✅ **Error Handling**: Graceful fallbacks  
🔧 **Backend API**: Framework ready for implementation  
🔧 **Admin Interface**: Can be added when needed  

The system is production-ready for environment variable configuration and can be easily extended with dynamic backend management when needed.
