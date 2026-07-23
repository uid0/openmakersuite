"""Carry a problem report's attachments onto the work order it was promoted to.

Promotion has to carry the evidence forward: the photos (and, for locations, the
scanned paper form) the reporter attached belong on the work order, because the
work order is what the technician or the vendor actually looks at.

Shared by ``AssetProblemViewSet`` and ``LocationProblemViewSet``. The two report
types differ in where their files live — ``AssetProblem.photos`` is a related
table, ``LocationProblem.photo``/``paper_form_attachment`` are single fields —
but not in what copying one means, so the copy itself lives here once.
"""

from __future__ import annotations

from django.core.files.base import ContentFile


def _read(file_field) -> bytes:
    """Read a FileField's bytes, leaving the underlying file closed."""
    file_field.open("rb")
    try:
        return file_field.read()
    finally:
        file_field.close()


def _extension(file_field, default: str = "bin") -> str:
    """Extension of the stored file, so a copied PNG doesn't land named .jpg."""
    name = file_field.name or ""
    return name.rsplit(".", 1)[-1] if "." in name else default


def copy_to_work_order_photo(file_field, work_order, *, caption: str, filename_hint: str):
    """Copy one image onto ``work_order`` as a ``WorkOrderPhoto``."""
    from inventory.models import WorkOrderPhoto

    photo = WorkOrderPhoto(work_order=work_order, caption=caption)
    photo.image.save(
        f"{filename_hint}.{_extension(file_field, default='jpg')}",
        ContentFile(_read(file_field)),
        save=False,
    )
    photo.save()
    return photo


def copy_to_tpwo_attachment(file_field, tpwo, *, kind: str, caption: str, filename_hint: str, user):
    """Copy one file onto ``tpwo`` as a ``ThirdPartyWorkOrderAttachment``."""
    from maintenance_orders.models import ThirdPartyWorkOrderAttachment

    attachment = ThirdPartyWorkOrderAttachment(
        work_order=tpwo,
        kind=kind,
        caption=caption,
        uploaded_by=user,
    )
    attachment.file.save(
        f"{filename_hint}-{tpwo.short_id}.{_extension(file_field)}",
        ContentFile(_read(file_field)),
        save=False,
    )
    attachment.save()
    return attachment
