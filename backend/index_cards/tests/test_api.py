"""API tests for index card generation endpoints."""

from __future__ import annotations

import base64
import os
import shutil
import tempfile
from typing import Dict

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from rest_framework.test import APIClient

from inventory.models import InventoryItem


class IndexCardAPITests(TestCase):
    """Ensure the API endpoints create previews and batches correctly."""

    def setUp(self) -> None:
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(
            username="cardmaker", email="cardmaker@example.com", password="strong-password"
        )
        self.client.force_authenticate(self.user)
        self.item = InventoryItem.objects.create(
            name="Soldering Wire",
            description="Lead-free soldering wire, 0.8mm thickness.",
            reorder_quantity=10,
            current_stock=3,
            minimum_stock=4,
        )

    def test_preview_endpoint_returns_base64_pdf(self) -> None:
        url = reverse("index_cards:preview")
        response = self.client.post(url, {"item_id": str(self.item.id)}, format="json")

        self.assertEqual(response.status_code, 200)
        payload: Dict[str, str] = response.json()
        self.assertEqual(payload["item_id"], str(self.item.id))
        decoded = base64.b64decode(payload["preview"])
        self.assertTrue(decoded.startswith(b"%PDF"))

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_generate_endpoint_writes_pdf_to_media(self) -> None:
        url = reverse("index_cards:generate")
        response = self.client.post(
            url,
            {"item_ids": [str(self.item.id)], "filename": "soldering_cards"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertTrue(data["file_path"].endswith(".pdf"))
        self.assertTrue(os.path.exists(data["absolute_path"]))

        with open(data["absolute_path"], "rb") as pdf_file:
            self.assertEqual(pdf_file.read(4), b"%PDF")

        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
