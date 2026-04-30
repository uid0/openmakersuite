from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reorder_queue", "0018_purchaseorder_sales_order_number_and_more"),
        ("reorder_queue", "0007_alter_webhook_event_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="webhook",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("reorder_request_created", "Reorder Request Created"),
                    ("reorder_request_approved", "Reorder Request Approved"),
                    ("reorder_request_ordered", "Reorder Request Ordered"),
                    ("reorder_request_received", "Reorder Request Received"),
                    ("item_low_stock", "Item Low Stock"),
                    ("purchase_order_created", "Purchase Order Created"),
                    ("delivery_received", "Delivery Received"),
                    ("fixture_refill_requested", "Fixture Refill Requested"),
                    ("location_checkin", "Location Check-in"),
                    ("location_feedback", "Location Feedback"),
                    ("security_report", "Security Report"),
                    ("location_problem_reported", "Location Problem Reported"),
                ],
                help_text="Type of event that triggers this webhook",
                max_length=50,
            ),
        ),
    ]
