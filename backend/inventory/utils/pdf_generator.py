"""
PDF generation utilities for inventory items.

This module provides wrapper functions around the IndexCardRenderer
to maintain compatibility with existing tests and provide simple
interfaces for generating PDF cards.
"""

from typing import Sequence

from index_cards.services import IndexCardRenderer
from inventory.models import InventoryItem


def generate_item_card(item: InventoryItem, blank_card: bool = False) -> bytes:
    """Generate a PDF card for a single inventory item.
    
    Args:
        item: The inventory item to generate a card for
        blank_card: If True, generates a blank card with only QR code
        
    Returns:
        PDF content as bytes
    """
    renderer = IndexCardRenderer(blank_cards=blank_card)
    return renderer.render_preview(item, blank_card=blank_card)


def generate_bulk_cards(items: Sequence[InventoryItem], blank_cards: bool = False) -> bytes:
    """Generate PDF cards for multiple inventory items.
    
    Args:
        items: Sequence of inventory items to generate cards for
        blank_cards: If True, generates blank cards with only QR codes
        
    Returns:
        PDF content as bytes containing all cards
    """
    renderer = IndexCardRenderer(blank_cards=blank_cards)
    return renderer.render_to_bytes(items, blank_cards=blank_cards)
