"""
Custom authentication classes for Django REST Framework.
"""
from rest_framework import authentication
from rest_framework_simplejwt.authentication import JWTAuthentication


class CSRFExemptJWTAuthentication(JWTAuthentication):
    """
    JWT Authentication that exempts requests from CSRF checks.
    
    This is safe because JWT tokens are cryptographically signed and
    cannot be forged like session cookies.
    """

    def authenticate(self, request):
        """
        Authenticate the request using JWT token.
        """
        result = super().authenticate(request)
        if result:
            # Mark the request as exempt from CSRF checks
            request._dont_enforce_csrf_checks = True
        return result


class CSRFExemptSessionAuthentication(authentication.SessionAuthentication):
    """
    Session Authentication that exempts API requests from CSRF checks.
    
    This is used for API endpoints where we want session auth but don't
    want to require CSRF tokens (e.g., when using JWT as primary auth).
    """

    def enforce_csrf(self, request):
        """
        Don't enforce CSRF for API requests.
        This method is called by DRF's SessionAuthentication to check CSRF.
        By not calling the parent's enforce_csrf, we skip CSRF validation.
        """
        pass  # Skip CSRF enforcement for API requests

