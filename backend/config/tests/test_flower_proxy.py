"""
Tests for Flower proxy view.
"""

from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory

import pytest

from config.flower_proxy import flower_proxy, is_superuser

User = get_user_model()


@pytest.mark.unit
class TestFlowerProxy:
    """Tests for Flower proxy view."""

    def test_is_superuser_check(self, db):
        """Test is_superuser function."""
        superuser = User.objects.create_user(
            username="superuser",
            email="super@example.com",
            password="testpass",
            is_superuser=True,
            is_active=True,
        )
        regular_user = User.objects.create_user(
            username="regular",
            email="regular@example.com",
            password="testpass",
            is_superuser=False,
            is_active=True,
        )
        inactive_user = User.objects.create_user(
            username="inactive",
            email="inactive@example.com",
            password="testpass",
            is_superuser=True,
            is_active=False,
        )

        assert is_superuser(superuser) is True
        assert is_superuser(regular_user) is False
        assert is_superuser(inactive_user) is False

    @patch("config.flower_proxy.requests.request")
    def test_flower_proxy_success(self, mock_request, db, admin_user):
        """Test successful proxy request."""
        factory = RequestFactory()
        request = factory.get("/flower/")

        # Mock user authentication
        request.user = admin_user

        # Mock Flower response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.iter_content.return_value = [b"<html>Flower</html>"]
        mock_request.return_value = mock_response

        response = flower_proxy(request, path="")

        assert response.status_code == 200
        mock_request.assert_called_once()

    @patch("config.flower_proxy.requests.request")
    def test_flower_proxy_error(self, mock_request, db, admin_user):
        """Test proxy request with Flower connection error."""
        import requests

        factory = RequestFactory()
        request = factory.get("/flower/")
        request.user = admin_user

        # Mock connection error
        mock_request.side_effect = requests.exceptions.RequestException("Connection failed")

        response = flower_proxy(request, path="")

        assert response.status_code == 502
        assert b"Error connecting to Flower" in response.content
