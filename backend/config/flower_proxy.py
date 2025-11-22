"""
Django view to proxy Flower requests with superuser authentication.
"""

import requests
from django.contrib.auth.decorators import user_passes_test
from django.http import HttpResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt


def is_superuser(user):
    """Check if user is a superuser."""
    return user.is_authenticated and user.is_superuser and user.is_active


@csrf_exempt
@user_passes_test(is_superuser, login_url="/admin/login/")
def flower_proxy(request, path=""):
    """
    Proxy requests to Flower, only allowing superusers.
    
    This view forwards all requests to the Flower service running
    on flower:5555, but only after verifying the user is a superuser.
    
    Note: WebSocket connections are handled by nginx, but authentication
    is still checked here for HTTP requests.
    """
    # Build Flower URL (remove leading /flower/ if present)
    if path.startswith("flower/"):
        path = path[7:]
    flower_url = f"http://flower:5555/{path}"
    if request.GET:
        query_string = request.GET.urlencode()
        flower_url += f"?{query_string}"

    # Forward the request to Flower
    try:
        # Get request body if present
        body = request.body if hasattr(request, "body") and request.body else None

        # Forward headers (excluding some that shouldn't be forwarded)
        headers = {}
        exclude_headers = {
            "HTTP_HOST",
            "HTTP_CONNECTION",
            "HTTP_CONTENT_LENGTH",
            "HTTP_UPGRADE",
        }
        
        for key, value in request.META.items():
            if key.startswith("HTTP_") and key not in exclude_headers:
                # Convert HTTP_HEADER_NAME to Header-Name
                header_name = key[5:].replace("_", "-").title()
                headers[header_name] = value

        # Make request to Flower
        response = requests.request(
            method=request.method,
            url=flower_url,
            headers=headers,
            data=body,
            stream=True,
            timeout=30,
            allow_redirects=False,
        )

        # Create Django response
        django_response = StreamingHttpResponse(
            streaming_content=response.iter_content(chunk_size=8192),
            status=response.status_code,
            content_type=response.headers.get("Content-Type", "text/html"),
        )

        # Copy relevant headers
        for key, value in response.headers.items():
            key_lower = key.lower()
            if key_lower not in [
                "content-length",
                "transfer-encoding",
                "connection",
                "upgrade",
            ]:
                django_response[key] = value

        return django_response

    except requests.exceptions.RequestException as e:
        return HttpResponse(
            f"Error connecting to Flower: {str(e)}", status=502
        )

