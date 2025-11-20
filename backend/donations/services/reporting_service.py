"""
Reporting service for donations.

Provides quarterly and yearly reports on donations by donor,
total items received, and associated costs.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Dict

from ..models import Donation


class DonationReportingService:
    """Service for generating donation reports."""

    @staticmethod
    def get_quarterly_report(year: int, quarter: int | None = None) -> Dict[str, Any]:
        """
        Generate quarterly donation report.

        Args:
            year: Year for the report
            quarter: Quarter number (1-4). If None, returns all quarters for the year.

        Returns:
            Dictionary containing report data with structure:
            {
                'year': int,
                'quarters': [
                    {
                        'quarter': int,
                        'donations': [
                            {
                                'donation': Donation,
                                'donor_name': str,
                                'date_received': date,
                                'total_items': int,
                                'total_quantity': int,
                                'estimated_value': Decimal,
                                'associated_costs': Decimal,
                                'net_value': Decimal,
                            }
                        ],
                        'summary': {
                            'total_donations': int,
                            'total_donors': int,
                            'total_items': int,
                            'total_quantity': int,
                            'total_estimated_value': Decimal,
                            'total_costs': Decimal,
                            'total_net_value': Decimal,
                        }
                    }
                ],
                'yearly_summary': {
                    'total_donations': int,
                    'total_donors': int,
                    'total_items': int,
                    'total_quantity': int,
                    'total_estimated_value': Decimal,
                    'total_costs': Decimal,
                    'total_net_value': Decimal,
                }
            }
        """
        if quarter:
            quarters = [quarter]
        else:
            quarters = [1, 2, 3, 4]

        report_data = {
            "year": year,
            "quarters": [],
            "yearly_summary": {
                "total_donations": 0,
                "total_donors": 0,
                "total_items": 0,
                "total_quantity": 0,
                "total_estimated_value": Decimal("0.00"),
                "total_costs": Decimal("0.00"),
                "total_net_value": Decimal("0.00"),
            },
        }

        all_donations = (
            Donation.objects.filter(
                date_received__year=year,
                status__in=[Donation.REVIEWED, Donation.PROCESSING, Donation.COMPLETED],
            )
            .select_related("received_by", "reviewed_by")
            .prefetch_related("items")
        )

        for q in quarters:
            # Calculate quarter date range
            quarter_start_month = (q - 1) * 3 + 1
            quarter_end_month = q * 3

            quarter_start = date(year, quarter_start_month, 1)
            if quarter_end_month == 12:
                quarter_end = date(year, 12, 31)
            else:
                quarter_end = date(year, quarter_end_month + 1, 1)

            quarter_donations = all_donations.filter(
                date_received__gte=quarter_start, date_received__lt=quarter_end
            )

            quarter_data = {
                "quarter": q,
                "donations": [],
                "summary": {
                    "total_donations": 0,
                    "total_donors": 0,
                    "total_items": 0,
                    "total_quantity": 0,
                    "total_estimated_value": Decimal("0.00"),
                    "total_costs": Decimal("0.00"),
                    "total_net_value": Decimal("0.00"),
                },
            }

            donors = set()
            for donation in quarter_donations:
                donors.add(donation.donor_name)
                quarter_data["donations"].append(
                    {
                        "donation": donation,
                        "donor_name": donation.donor_name,
                        "date_received": donation.date_received,
                        "donation_number": donation.donation_number,
                        "total_items": donation.total_items,
                        "total_quantity": donation.total_quantity,
                        "estimated_value": donation.estimated_value or Decimal("0.00"),
                        "associated_costs": donation.associated_costs or Decimal("0.00"),
                        "net_value": donation.net_value,
                    }
                )

                # Update summary
                quarter_data["summary"]["total_donations"] += 1
                quarter_data["summary"]["total_items"] += donation.total_items
                quarter_data["summary"]["total_quantity"] += donation.total_quantity
                quarter_data["summary"][
                    "total_estimated_value"
                ] += donation.estimated_value or Decimal("0.00")
                quarter_data["summary"]["total_costs"] += donation.associated_costs or Decimal(
                    "0.00"
                )
                quarter_data["summary"]["total_net_value"] += donation.net_value

            quarter_data["summary"]["total_donors"] = len(donors)

            # Update yearly summary
            report_data["yearly_summary"]["total_donations"] += quarter_data["summary"][
                "total_donations"
            ]
            report_data["yearly_summary"]["total_items"] += quarter_data["summary"]["total_items"]
            report_data["yearly_summary"]["total_quantity"] += quarter_data["summary"][
                "total_quantity"
            ]
            report_data["yearly_summary"]["total_estimated_value"] += quarter_data["summary"][
                "total_estimated_value"
            ]
            report_data["yearly_summary"]["total_costs"] += quarter_data["summary"]["total_costs"]
            report_data["yearly_summary"]["total_net_value"] += quarter_data["summary"][
                "total_net_value"
            ]

            report_data["quarters"].append(quarter_data)

        # Calculate unique donors for the year
        all_donors = set()
        for q_data in report_data["quarters"]:
            for donation_data in q_data["donations"]:
                all_donors.add(donation_data["donor_name"])
        report_data["yearly_summary"]["total_donors"] = len(all_donors)

        return report_data

    @staticmethod
    def get_yearly_report(year: int) -> Dict[str, Any]:
        """
        Generate yearly donation report.

        Args:
            year: Year for the report

        Returns:
            Dictionary containing report data with structure:
            {
                'year': int,
                'donations': [
                    {
                        'donation': Donation,
                        'donor_name': str,
                        'date_received': date,
                        'total_items': int,
                        'total_quantity': int,
                        'estimated_value': Decimal,
                        'associated_costs': Decimal,
                        'net_value': Decimal,
                    }
                ],
                'summary': {
                    'total_donations': int,
                    'total_donors': int,
                    'total_items': int,
                    'total_quantity': int,
                    'total_estimated_value': Decimal,
                    'total_costs': Decimal,
                    'total_net_value': Decimal,
                },
                'by_donor': {
                    'donor_name': {
                        'donation_count': int,
                        'total_items': int,
                        'total_quantity': int,
                        'total_estimated_value': Decimal,
                        'total_costs': Decimal,
                        'total_net_value': Decimal,
                    }
                }
            }
        """
        donations = (
            Donation.objects.filter(
                date_received__year=year,
                status__in=[Donation.REVIEWED, Donation.PROCESSING, Donation.COMPLETED],
            )
            .select_related("received_by", "reviewed_by")
            .prefetch_related("items")
            .order_by("date_received")
        )

        report_data = {
            "year": year,
            "donations": [],
            "summary": {
                "total_donations": 0,
                "total_donors": 0,
                "total_items": 0,
                "total_quantity": 0,
                "total_estimated_value": Decimal("0.00"),
                "total_costs": Decimal("0.00"),
                "total_net_value": Decimal("0.00"),
            },
            "by_donor": {},
        }

        donors = set()
        for donation in donations:
            donors.add(donation.donor_name)
            donation_data = {
                "donation": donation,
                "donor_name": donation.donor_name,
                "date_received": donation.date_received,
                "donation_number": donation.donation_number,
                "total_items": donation.total_items,
                "total_quantity": donation.total_quantity,
                "estimated_value": donation.estimated_value or Decimal("0.00"),
                "associated_costs": donation.associated_costs or Decimal("0.00"),
                "net_value": donation.net_value,
            }
            report_data["donations"].append(donation_data)

            # Update summary
            report_data["summary"]["total_donations"] += 1
            report_data["summary"]["total_items"] += donation.total_items
            report_data["summary"]["total_quantity"] += donation.total_quantity
            report_data["summary"]["total_estimated_value"] += donation.estimated_value or Decimal(
                "0.00"
            )
            report_data["summary"]["total_costs"] += donation.associated_costs or Decimal("0.00")
            report_data["summary"]["total_net_value"] += donation.net_value

            # Update by_donor
            if donation.donor_name not in report_data["by_donor"]:
                report_data["by_donor"][donation.donor_name] = {
                    "donation_count": 0,
                    "total_items": 0,
                    "total_quantity": 0,
                    "total_estimated_value": Decimal("0.00"),
                    "total_costs": Decimal("0.00"),
                    "total_net_value": Decimal("0.00"),
                }

            donor_data = report_data["by_donor"][donation.donor_name]
            donor_data["donation_count"] += 1
            donor_data["total_items"] += donation.total_items
            donor_data["total_quantity"] += donation.total_quantity
            donor_data["total_estimated_value"] += donation.estimated_value or Decimal("0.00")
            donor_data["total_costs"] += donation.associated_costs or Decimal("0.00")
            donor_data["total_net_value"] += donation.net_value

        report_data["summary"]["total_donors"] = len(donors)

        return report_data

    @staticmethod
    def get_donor_summary(
        donor_name: str, start_date: date | None = None, end_date: date | None = None
    ) -> Dict[str, Any]:
        """
        Get summary of donations from a specific donor.

        Args:
            donor_name: Name of the donor
            start_date: Optional start date filter
            end_date: Optional end date filter

        Returns:
            Dictionary with donation summary for the donor
        """
        donations = Donation.objects.filter(donor_name=donor_name).filter(
            status__in=[Donation.REVIEWED, Donation.PROCESSING, Donation.COMPLETED]
        )

        if start_date:
            donations = donations.filter(date_received__gte=start_date)
        if end_date:
            donations = donations.filter(date_received__lte=end_date)

        donations = donations.select_related("received_by", "reviewed_by").prefetch_related("items")

        summary = {
            "donor_name": donor_name,
            "donation_count": 0,
            "total_items": 0,
            "total_quantity": 0,
            "total_estimated_value": Decimal("0.00"),
            "total_costs": Decimal("0.00"),
            "total_net_value": Decimal("0.00"),
            "donations": [],
        }

        for donation in donations:
            summary["donation_count"] += 1
            summary["total_items"] += donation.total_items
            summary["total_quantity"] += donation.total_quantity
            summary["total_estimated_value"] += donation.estimated_value or Decimal("0.00")
            summary["total_costs"] += donation.associated_costs or Decimal("0.00")
            summary["total_net_value"] += donation.net_value

            summary["donations"].append(
                {
                    "donation": donation,
                    "date_received": donation.date_received,
                    "donation_number": donation.donation_number,
                    "total_items": donation.total_items,
                    "total_quantity": donation.total_quantity,
                    "estimated_value": donation.estimated_value or Decimal("0.00"),
                    "associated_costs": donation.associated_costs or Decimal("0.00"),
                    "net_value": donation.net_value,
                }
            )

        return summary
