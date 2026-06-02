"""Add cert_pem + signed_by_ca to FirmwareSigningKey.

The new fields let a generated keypair carry a leaf certificate signed by
the internal CA, so firmware-dispatch payloads can ship the leaf and
devices can verify the chain back to the CA root they already trust
(via the device-cert /enroll/ flow). Both fields are nullable / blank for
back-compat with the keys generated before this change.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("forgekey", "0018_firmware_build"),
    ]

    operations = [
        migrations.AddField(
            model_name="firmwaresigningkey",
            name="cert_pem",
            field=models.TextField(
                blank=True,
                help_text=(
                    "PEM-encoded leaf certificate signed by the internal CA over this "
                    "keypair. When present, the cert is shipped alongside firmware "
                    "signatures so devices can verify the chain back to the CA root "
                    "(rotating the firmware signer no longer requires re-flashing the "
                    "embedded public key). Blank for legacy keys generated before the "
                    "CA-issued path landed."
                ),
            ),
        ),
        migrations.AddField(
            model_name="firmwaresigningkey",
            name="signed_by_ca",
            field=models.ForeignKey(
                blank=True,
                help_text="The CA that signed this keypair's leaf cert (NULL for legacy keys).",
                null=True,
                on_delete=models.deletion.PROTECT,
                related_name="firmware_signing_keys",
                to="forgekey.certificateauthority",
            ),
        ),
    ]
