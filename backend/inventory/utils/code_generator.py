"""
Utility for generating unique 6-character codes for assets, items, and locations.

Codes use capital letters and numbers, excluding ambiguous characters:
I, 0, O, 1, L
"""

import random
import string

from django.db import transaction


# Characters to use: A-Z and 0-9, excluding I, 0, O, 1, L
# This gives us: A-H, J-N, P-Z, 2-9 = 25 letters + 8 numbers = 33 characters
VALID_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_unique_code(model_class, field_name="access_code", length=6):
    """
    Generate a unique code for a model instance.

    Args:
        model_class: The Django model class
        field_name: Name of the field to store the code (default: 'access_code')
        length: Length of the code (default: 6)

    Returns:
        A unique code string
    """
    max_attempts = 1000  # Prevent infinite loops

    for _ in range(max_attempts):
        code = "".join(random.choices(VALID_CHARS, k=length))
        # Check if code already exists
        if not model_class.objects.filter(**{field_name: code}).exists():
            return code

    raise ValueError(f"Could not generate unique code after {max_attempts} attempts")


def generate_code():
    """
    Generate a random 6-character code.

    Returns:
        A random code string
    """
    return "".join(random.choices(VALID_CHARS, k=6))

