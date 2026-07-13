"""Donation number composition (gh #887).

``Donation.save()`` delegates the ``DON-YYYY-NNN`` composition here so the
override stays a thin delegator. The single production caller and the audit
path both read ``donation_number`` immediately after save, so numbering stays
synchronous on the create path.
"""

from __future__ import annotations


def next_donation_number(year: int) -> str:
    """Return the next ``DON-YYYY-NNN`` number for ``year``.

    Reads the highest existing number for the year and increments its trailing
    counter, falling back to ``001`` when none exist or the counter can't be
    parsed.
    """
    from donations.models import Donation

    last_donation = (
        Donation.objects.filter(donation_number__startswith=f"DON-{year}-")
        .order_by("-donation_number")
        .first()
    )
    if last_donation and last_donation.donation_number:
        try:
            next_num = int(last_donation.donation_number.split("-")[-1]) + 1
        except (ValueError, IndexError):
            next_num = 1
    else:
        next_num = 1

    return f"DON-{year}-{next_num:03d}"
