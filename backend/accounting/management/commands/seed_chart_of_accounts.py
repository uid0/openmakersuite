from django.core.management.base import BaseCommand

from accounting.chart import seed_chart_of_accounts


class Command(BaseCommand):
    help = "Idempotently seed the Phase-1 accounting chart of accounts."

    def handle(self, *args, **options):
        result = seed_chart_of_accounts()
        self.stdout.write(
            self.style.SUCCESS(
                "Chart of accounts seeded: "
                f"{result['created']} created, {result['existing']} existing "
                f"({result['total']} total)."
            )
        )
