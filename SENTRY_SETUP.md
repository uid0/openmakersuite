# Sentry Error Tracking Setup

This project is configured with Sentry for error tracking and performance monitoring in both the backend (Django) and frontend (React).

## Overview

**Backend DSN:** `https://af885209b7663c58d3fe82ace2863941@o4510248461074432.ingest.us.sentry.io/4510248465661952`

**Frontend DSN:** `https://48f74e122b301e64b6a7f8bebd8b59c4@o4510248461074432.ingest.us.sentry.io/4510248471691264`

## Features Enabled

### Backend (Django)
- ✅ Exception tracking
- ✅ Performance monitoring (traces)
- ✅ Profiling
- ✅ Django integration (automatic request tracking)
- ✅ Celery integration (background task tracking)
- ✅ Redis integration
- ✅ SQL query tracking

### Frontend (React)
- ✅ Exception tracking
- ✅ Performance monitoring (traces)
- ✅ Session replay
- ✅ React Router integration
- ✅ Error boundary with fallback UI
- ✅ Axios interceptor for API error tracking

## Configuration

### Environment Variables

The Sentry DSNs are configured via environment variables:

**Backend:**
```bash
SENTRY_DSN=https://af885209b7663c58d3fe82ace2863941@o4510248461074432.ingest.us.sentry.io/4510248465661952
SENTRY_ENVIRONMENT=development  # or production, staging, etc.
```

**Frontend:**
```bash
REACT_APP_SENTRY_DSN=https://48f74e122b301e64b6a7f8bebd8b59c4@o4510248461074432.ingest.us.sentry.io/4510248471691264
```

These are already configured in:
- `.env.example` (with the actual DSNs)
- `docker-compose.yml` (with defaults)

### Sample Rates

**Development:**
- Traces: 100% (captures all transactions)
- Profiles: 100%
- Session Replay: 10% normal, 100% on errors

**Production:**
- Traces: 10% (reduces overhead)
- Profiles: 10%
- Session Replay: 10% normal, 100% on errors

These can be adjusted in:
- Backend: [backend/config/settings.py](backend/config/settings.py#L171)
- Frontend: [frontend/src/index.tsx](frontend/src/index.tsx#L24)

## Testing Sentry Integration

### Backend Test

Create a test endpoint to verify Sentry is working:

```python
# In backend/config/urls.py, add:
def trigger_error(request):
    division_by_zero = 1 / 0

urlpatterns = [
    # ... existing patterns
    path('api/sentry-test/', trigger_error),
]
```

Then visit: `http://localhost:8000/api/sentry-test/`

You should see the error in your Sentry dashboard.

### Frontend Test

Add a test button to any page:

```tsx
<button onClick={() => {
  throw new Error('Test Sentry error!');
}}>
  Test Sentry
</button>
```

Click the button and check Sentry dashboard.

## What Gets Tracked

### Backend
- All unhandled exceptions
- HTTP request details (URL, method, status)
- User information (if authenticated)
- Database queries (configurable)
- Celery task failures
- Redis operations

### Frontend
- Unhandled JavaScript errors
- React component errors (via Error Boundary)
- Failed API calls (via axios interceptor)
- User navigation (via React Router)
- Session replays (recordings of user sessions)
- Performance metrics

## Viewing Errors

1. Go to [Sentry Dashboard](https://sentry.io/)
2. Select your organization
3. You'll see two projects:
   - Backend project for Django errors
   - Frontend project for React errors
4. Click on any error to see:
   - Stack trace
   - Breadcrumbs (user actions leading to error)
   - Environment details
   - User information
   - Session replay (frontend only)

## Privacy Considerations

### PII (Personally Identifiable Information)

**Backend:** `send_default_pii=True` is enabled, which means:
- User email/username may be sent
- Request headers and cookies may be captured

**Frontend:** Session replay is enabled, which records:
- User interactions
- DOM changes
- Network requests

**For Production:**
Consider:
1. Setting `send_default_pii=False` if dealing with sensitive data
2. Adjusting `maskAllText` and `blockAllMedia` in Session Replay
3. Implementing custom scrubbing for sensitive fields

## Disabling Sentry

To disable Sentry (e.g., in development):

**Backend:**
```bash
# Remove or leave empty in .env
SENTRY_DSN=
```

**Frontend:**
```bash
# Remove or leave empty in .env
REACT_APP_SENTRY_DSN=
```

The code checks if DSN is set before initializing Sentry.

## Production Recommendations

1. **Set environment properly:**
   ```bash
   SENTRY_ENVIRONMENT=production
   ```

2. **Reduce sample rates** to minimize performance impact:
   - Backend: 5-10% traces
   - Frontend: 5-10% traces, 5% session replay

3. **Set up alerts** in Sentry dashboard:
   - High error rate
   - New error types
   - Performance degradation

4. **Create releases** to track which version of code caused errors:
   ```bash
   # Example with sentry-cli
   sentry-cli releases new v1.0.0
   sentry-cli releases set-commits v1.0.0 --auto
   sentry-cli releases finalize v1.0.0
   ```

## Support

For Sentry-specific issues:
- [Sentry Documentation](https://docs.sentry.io/)
- [Django Integration](https://docs.sentry.io/platforms/python/guides/django/)
- [React Integration](https://docs.sentry.io/platforms/javascript/guides/react/)
