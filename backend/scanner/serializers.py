from rest_framework import serializers


class ScanDispatchRequestSerializer(serializers.Serializer):
    payload = serializers.CharField(
        required=True,
        allow_blank=False,
        max_length=2048,
        help_text="The raw string emitted by a barcode/QR scanner.",
    )
