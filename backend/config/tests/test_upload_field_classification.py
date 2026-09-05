"""An upload field nobody has classified fails the build (op-anonymous-read-posture).

THE UNIT OF THE DERIVATION IS AN UPLOAD FIELD, NOT A URL PREFIX. The first pass
at :data:`~config.protected_media.VENDOR_MEDIA_PREFIXES` enumerated the prefixes
it had already found and stopped, and so left five roots open that hold exactly
the paperwork the captain's decision names — including two fed by the Postmark
inbound webhook, where the contents are whatever a vendor emailed in and cannot
be narrowed by argument. A hand-enumerated list of upload fields is a claim that
goes stale, which is how that happened; this walks the tree instead.

Same shape as ``inventory/tests/test_pack_size_single_owner.py`` and
``test_price_single_owner.py``: every non-test, non-migration module under
``backend/`` is parsed with the AST, every ``upload_to`` is resolved to the
static directory prefix it writes under, and each prefix must be either

* covered by :data:`~config.protected_media.VENDOR_MEDIA_PREFIXES` — nginx and
  ``config.protected_media.serve_media`` both refuse it without a session; or
* an exact key of :data:`OPEN_PREFIXES` below, which carries the written reason
  it stays anonymously readable.

A new upload field anywhere under ``backend/`` matches neither and fails here
until somebody answers the question "can a vendor document be stored there?".

TWO READINGS, AND THE AUTHORITATIVE ONE IS DJANGO'S OWN. The AST walk is a
source reading and so has blind spots; ``test_django_itself_declares_no_
unclassified_upload_root`` asks the app registry instead — every
``FileField``/``ImageField`` Django has actually built, with the ``upload_to``
it will actually use. That is the real consumer, it sees a callable the source
walk cannot resolve, and it sees the POSITIONAL spelling
(``FileField("Invoice", "vendor_invoices/")``) that the keyword-only walk was
blind to. The AST walk is kept because it reaches modules the registry does not
— anything that is not a model field.

THREE HONEST LIMITS, named the way those two modules name theirs:

* **``backend/`` only.** A file written outside a model field — the batch PDFs
  ``IndexCardRenderer.render_batch_to_storage`` persists under ``index_cards/``
  are the live example — has no ``upload_to``, so NEITHER reading sees it.
  ``index_cards/`` is on the protected list because that renderer was read by
  hand, not because this test found it.
* **A callable's prefix has to be resolvable statically, in the AST reading.**
  ``upload_to`` may be a function, and the walk resolves one by reading the
  leading literal of its returned f-string (which is how
  ``maintenance_orders._attachment_upload_path`` is classified). A callable
  whose prefix is computed rather than written down is reported as UNRESOLVED
  and fails, rather than being skipped — a path this test cannot read is a path
  it cannot vouch for. The registry reading calls the callable instead, so the
  two do not share this limit.
* **The registry reading sees FIRST-PARTY MODEL FIELDS ONLY.** A ``FileField``
  on a form or a serializer writes through its own storage call and is
  invisible to ``apps.get_models()``, which is why the AST walk stays rather
  than being replaced; and a field declared by an installed third-party package
  (django-hordak's CSV import) is skipped, because this repo cannot classify
  somebody else's decision. Both readings therefore share the ``backend/``
  boundary.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Optional

import pytest

from config.protected_media import VENDOR_MEDIA_PREFIXES, is_vendor_media

BACKEND = pathlib.Path(__file__).resolve().parents[2]

#: Upload roots that stay anonymously readable, and why. An entry here is a
#: statement that a vendor's identity and a vendor's price cannot be stored
#: there — not that the files are uninteresting.
OPEN_PREFIXES: dict[str, str] = {
    "assets/documents/": (
        "AssetDocument.file. Its Category choices are Manual, CAD Source, "
        "Wiring Diagram, Cut Sheet/Spec, Photo, Other: technical documentation "
        "a member needs in order to work on a machine. RESIDUAL RISK, stated "
        "rather than argued away: the `Other` category means a purchase "
        "document COULD be filed here. Closing the prefix was rejected because "
        "it would take manuals and wiring diagrams away from the people the "
        "space serves."
    ),
    "assets/manuals/": (
        "Asset.manual_pdf — a manufacturer's manual. A manufacturer is not a "
        "vendor relationship, and a manual is not a name, a price, an invoice "
        "or an agreement."
    ),
    "inventory/msds/": (
        "InventorySafetyProfile.msds_file — safety data sheets, on the "
        "anonymous scan path and protected by the brief outright."
    ),
    "donations/tax_receipts/": (
        "TaxReceipt.pdf_file — a receipt this makerspace ISSUES TO A DONOR. A "
        "donor is not a vendor and the document is outbound."
    ),
    "location_problems/paper/": (
        "LocationProblem.paper_form_attachment — a scanned member " "problem-report form."
    ),
    "signatures/": (
        "MembershipAgreement signature images. Member PII rather than vendor "
        "data, and out of this branch's scope by the captain's decision — said "
        "here rather than silently omitted, because an unlisted prefix and a "
        "prefix somebody decided about look identical from outside."
    ),
    "location_problems/": "LocationProblem photo of a reported problem.",
    "asset_problems/": "Asset problem-report photo.",
    "checklist_step_photos/": "Evidence photo for a completed checklist step.",
    "maintenance_log_photos/": "Evidence photo on a maintenance log entry.",
    "maintenance_task_reference/": "Reference photo showing how a task is done.",
    "work_order_photos/": "Evidence photo on a work order.",
    "inventory/images/": "Item photo — the anonymous scan path renders it.",
    "inventory/qrcodes/": "Item QR code — the printed codes point here.",
    "inventory/location_qrcodes/": "Location QR code.",
    "assets/images/": "Asset photo.",
    "assets/qrcodes/": "Asset QR code.",
    "donations/qr_codes/": "Donation QR code.",
    "project_storage/qrcodes/": "Project-storage QR code.",
    "customization/logos/": "This site's own logo.",
    "customization/favicons/": "This site's own favicon.",
    "electrical_circuits/outlets/": "Photo of an outlet in the building.",
    "electrical_circuits/network_drops/": "Photo of a network drop.",
    "electrical_circuits/disconnects/": "Photo of a disconnect.",
    "forgekey/device_photos/": "Photo of a ForgeKey device.",
    "forgekey/device_photos/last/": "Most recent photo from a ForgeKey device.",
    "forgekey/enrollment_photos/": "Photo taken during ForgeKey enrollment.",
    "forgekey/firmware/": "ForgeKey firmware image.",
    "storage_vision/originals/": "Storage-vision camera capture.",
    "storage_vision/crops/": "Storage-vision evidence crop.",
}

#: Directories whose modules are never scanned — the same set the two
#: single-owner gates use, and for the same reasons.
_SKIP_PARTS = {
    "migrations",
    "tests",
    "__pycache__",
    "node_modules",
    "staticfiles",
    "site-packages",
    ".venv",
    "venv",
    ".env",
    "env",
    ".tox",
    ".eggs",
}

#: Reported in place of a prefix when a callable's path cannot be read.
UNRESOLVED = "<unresolved>"


def _is_gated(prefix: str) -> bool:
    """Whether the gate closes a whole DIRECTORY.

    A file name is appended because ``is_vendor_media`` normalises its argument
    first, and ``posixpath.normpath`` strips the trailing slash a bare prefix
    ends in — so asking about ``"supplier_agreements/"`` itself answers ``False``
    while every file under it answers ``True``.
    """
    return is_vendor_media(prefix + "zzqq-probe")


def _modules():
    """Every non-test, non-migration Python module under ``backend/``."""
    for path in sorted(BACKEND.rglob("*.py")):
        if _SKIP_PARTS & set(path.parts):
            continue
        if path.name.startswith("test_") or path.name.endswith("_test.py"):
            continue
        yield path


def _static_prefix(text: str) -> Optional[str]:
    """The directory prefix ``text`` always writes under, or ``None``.

    Everything from the first placeholder on is dropped — ``%Y``/``%m`` in a
    Django ``upload_to`` string, ``{...}`` in an f-string — and what remains is
    cut back to its last ``/``. So ``"work_orders/attachments/%Y/%m/"`` and
    ``"third_party_work_orders/{instance.work_order_id}/{filename}"`` both
    resolve to the directory every file they name lands beneath.
    """
    for marker in ("%", "{"):
        index = text.find(marker)
        if index != -1:
            text = text[:index]
    cut = text.rfind("/")
    if cut == -1:
        return None
    return text[: cut + 1]


def _prefix_from_callable(tree: ast.AST, name: str) -> Optional[str]:
    """The prefix a same-module ``upload_to`` callable writes under.

    Reads the leading literal of what the function returns: a plain string, or
    the first segment of an f-string. Anything else is unreadable and answers
    ``None``, which the test reports as :data:`UNRESOLVED` rather than skipping.
    """
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != name:
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Return) or inner.value is None:
                continue
            value = inner.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return _static_prefix(value.value)
            if isinstance(value, ast.JoinedStr) and value.values:
                head = value.values[0]
                if isinstance(head, ast.Constant) and isinstance(head.value, str):
                    return _static_prefix(head.value)
            return None
    return None


def _is_file_field(node: ast.Call) -> bool:
    """Whether ``node`` constructs a Django file field, by either spelling."""
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    return name.endswith("FileField") or name.endswith("ImageField")


def _upload_prefixes_in(source: str, filename: str = "<snippet>") -> set[str]:
    """Every prefix an ``upload_to`` in ``source`` writes under.

    Both spellings, because the callable one is what the first derivation
    missed: ``upload_to="a/b/"`` and ``upload_to=some_function``.
    """
    tree = ast.parse(source, filename=filename)
    found: set[str] = set()

    def record(value) -> None:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            found.add(_static_prefix(value.value) or UNRESOLVED)
        elif isinstance(value, ast.Name):
            found.add(_prefix_from_callable(tree, value.id) or UNRESOLVED)
        else:
            found.add(UNRESOLVED)

    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "upload_to":
            record(node.value)
            continue
        # `upload_to` is the THIRD positional parameter of FileField/ImageField
        # (verbose_name, name, upload_to, ...), so the keyword branch above is
        # blind to `FileField("Invoice", None, "vendor_invoices/")`. Every other
        # unreadable shape in this module fails CLOSED; this one used to fail
        # open, which is the wrong direction for a gate that exists because a
        # hand-enumerated list goes stale.
        if isinstance(node, ast.Call) and _is_file_field(node) and len(node.args) >= 3:
            record(node.args[2])

    return found


def _upload_prefixes(path: pathlib.Path) -> set[str]:
    """:func:`_upload_prefixes_in` for a file, skipping what Python cannot read.

    An undecodable or unparsable module declares no upload field this test can
    reason about, and failing the gate on one would report a decoding problem
    under a message about vendor media.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return set()
    try:
        return _upload_prefixes_in(source, filename=str(path))
    except SyntaxError:
        return set()


def _declared_prefixes() -> dict[str, list[str]]:
    """Every upload prefix under ``backend/``, mapped to the modules declaring it."""
    declared: dict[str, list[str]] = {}
    for path in _modules():
        for prefix in _upload_prefixes(path):
            declared.setdefault(prefix, []).append(str(path.relative_to(BACKEND)))
    return declared


def test_every_upload_field_is_classified_for_the_vendor_gate():
    declared = _declared_prefixes()

    assert declared, (
        f"The scan found no upload_to declaration anywhere under {BACKEND}. The "
        "walk itself is broken (a bad _SKIP_PARTS entry, or a moved tree), so "
        "this gate is passing vacuously rather than guarding "
        "config.protected_media.VENDOR_MEDIA_PREFIXES."
    )

    unresolved = declared.pop(UNRESOLVED, None)
    assert not unresolved, (
        f"{sorted(set(unresolved))} declares an upload_to whose directory this "
        "test cannot read statically. A path that cannot be read is a path that "
        "cannot be vouched for: give the field a literal prefix, or a callable "
        "that returns an f-string starting with one, so it can be classified "
        "against config.protected_media.VENDOR_MEDIA_PREFIXES."
    )

    unclassified = {
        prefix: sorted(set(modules))
        for prefix, modules in declared.items()
        if not _is_gated(prefix) and prefix not in OPEN_PREFIXES
    }
    assert not unclassified, (
        f"New upload root(s) nobody has classified: {unclassified}. Ask the "
        "question this gate exists for — CAN A VENDOR DOCUMENT BE STORED THERE? "
        "An invoice, a quote, a receipt, a signed agreement, or anything "
        "arriving down an unfiltered inbound-mail path, means adding the prefix "
        "to config.protected_media.VENDOR_MEDIA_PREFIXES and mirroring a "
        "`location ^~` block in nginx/templates/default.conf.template. "
        "Otherwise add it to OPEN_PREFIXES here WITH THE REASON it stays "
        "anonymously readable."
    )


def test_the_open_list_does_not_name_a_prefix_the_gate_already_closes():
    """Two answers to one question is a place for them to disagree."""
    both = sorted(prefix for prefix in OPEN_PREFIXES if _is_gated(prefix))
    assert not both, (
        f"{both} is on OPEN_PREFIXES and also covered by VENDOR_MEDIA_PREFIXES. "
        "The gate wins, so the reason written here is not the rule anyone runs."
    )


def test_the_open_list_does_not_carry_a_prefix_nothing_declares():
    """A reason for a field that no longer exists reads as current policy."""
    declared = _declared_prefixes()
    stale = sorted(prefix for prefix in OPEN_PREFIXES if prefix not in declared)
    assert not stale, (
        f"{stale} is excused in OPEN_PREFIXES but no upload_to under "
        f"{BACKEND} writes there any more. Drop the entry rather than leaving a "
        "classification of something that is gone."
    )


def test_the_five_roots_the_first_derivation_missed_are_closed():
    """REGRESSION (op-anonymous-read-posture).

    Each of these is a real upload root the first pass at the prefix list left
    open, found by re-asking where a vendor document can be STORED. Named one by
    one rather than as a count, so re-opening any single one fails here.
    """
    for prefix in (
        "third_party_work_orders/",
        "inventory/maintenance_records/",
        "work_orders/attachments/",
        "work_orders/submissions/",
        "work_orders/scans/",
    ):
        assert is_vendor_media(prefix + "some-file.pdf"), (
            f"/media/{prefix} is anonymously readable again. It holds vendor "
            "invoices, quotes or receipts."
        )


def test_every_protected_prefix_is_reachable_as_written():
    """VENDOR_MEDIA_PREFIXES entries must be directory prefixes, not paths.

    ``is_vendor_media`` compares against a normalised path, so an entry with no
    trailing ``/`` would also swallow a sibling directory that merely starts
    with the same letters.
    """
    for prefix in VENDOR_MEDIA_PREFIXES:
        assert prefix.endswith("/"), f"{prefix!r} is not a directory prefix"
        assert not prefix.startswith("/"), f"{prefix!r} is not relative to MEDIA_ROOT"


# ── The detector itself, exercised on constructed sources ────────────────────
#
# A gate that cannot see the declaration it was built to catch passes vacuously,
# so each shape below runs through ``_upload_prefixes_in`` as an ordinary
# function with test-owned input. None of these read the repository's source.


def test_a_literal_upload_to_is_resolved_to_its_directory():
    assert _upload_prefixes_in('f = FileField(upload_to="a/b/")') == {"a/b/"}


def test_a_date_partitioned_upload_to_resolves_to_the_root_above_the_dates():
    assert _upload_prefixes_in('f = FileField(upload_to="a/b/%Y/%m/")') == {"a/b/"}


def test_a_callable_upload_to_is_resolved_through_its_returned_f_string():
    """The shape that defeated the first, string-only derivation."""
    source = (
        "def _path(instance, filename):\n"
        '    return f"third_party_work_orders/{instance.work_order_id}/{filename}"\n'
        "\n"
        "f = FileField(upload_to=_path)\n"
    )
    assert _upload_prefixes_in(source) == {"third_party_work_orders/"}


def test_a_callable_whose_path_is_computed_is_reported_unresolved_not_skipped():
    source = (
        "def _path(instance, filename):\n"
        "    return os.path.join(instance.root, filename)\n"
        "\n"
        "f = FileField(upload_to=_path)\n"
    )
    assert _upload_prefixes_in(source) == {UNRESOLVED}


def test_an_upload_to_that_is_neither_a_literal_nor_a_name_is_unresolved():
    assert _upload_prefixes_in("f = FileField(upload_to=settings.SOMETHING)") == {UNRESOLVED}


def test_a_bare_filename_upload_to_is_unresolved_rather_than_root():
    """``upload_to="x.pdf"`` names no directory, so it cannot be classified."""
    assert _upload_prefixes_in('f = FileField(upload_to="x.pdf")') == {UNRESOLVED}


def test_a_field_with_no_upload_to_contributes_nothing():
    assert _upload_prefixes_in('name = CharField(max_length=10, help_text="upload_to")') == set()


def test_a_positional_upload_to_is_caught_too():
    """The spelling the keyword-only walk was blind to.

    ``upload_to`` is the third positional parameter of ``FileField``, so this
    declared a whole upload root that produced no prefix, no ``UNRESOLVED`` and
    no failure — the one shape in this module that failed OPEN.
    """
    assert _upload_prefixes_in('f = models.FileField("Invoice", None, "vendor_invoices/")') == {
        "vendor_invoices/"
    }
    assert _upload_prefixes_in('f = ImageField("Photo", None, "assets/images/")') == {
        "assets/images/"
    }


def test_a_positional_argument_on_something_that_is_not_a_file_field_is_ignored():
    """CONTROL: the positional branch must not invent prefixes elsewhere."""
    assert _upload_prefixes_in('f = CharField("Label", None, "not/a/path/")') == set()


@pytest.mark.django_db
def test_django_itself_declares_no_unclassified_upload_root():
    """THE AUTHORITATIVE READING: ask the app registry, not the source.

    ``apps.get_models()`` gives every ``FileField``/``ImageField`` Django
    actually built, with the ``upload_to`` it will actually use — so a callable
    the AST cannot resolve is resolved here by CALLING it, and a positional
    spelling is invisible to neither. Whatever the source walk's blind spots,
    a model field that writes somewhere unclassified fails here.
    """
    from django.apps import apps
    from django.db.models import FileField

    unclassified: dict[str, list[str]] = {}
    for model in apps.get_models():
        # FIRST-PARTY MODELS ONLY, the same scope the AST walk states. A
        # third-party package's upload field (django-hordak's CSV import writes
        # straight to MEDIA_ROOT) is not something this repo can classify or
        # gate, and failing here on one would report somebody else's decision
        # under a message about our vendor prefixes.
        app_path = pathlib.Path(apps.get_app_config(model._meta.app_label).path)
        if BACKEND not in app_path.parents and app_path != BACKEND:
            continue
        for field in model._meta.get_fields():
            if not isinstance(field, FileField):
                continue
            upload_to = field.upload_to
            if callable(upload_to):
                # Resolved the way storage resolves it, with a filename that
                # cannot itself introduce a directory.
                try:
                    raw = upload_to(model(), "zzqq-probe.bin")
                except Exception:
                    raw = None
                prefix = _static_prefix(raw) if isinstance(raw, str) else None
            else:
                prefix = _static_prefix(str(upload_to))

            where = f"{model._meta.label}.{field.name}"
            if prefix is None:
                unclassified.setdefault(UNRESOLVED, []).append(where)
            elif not _is_gated(prefix) and prefix not in OPEN_PREFIXES:
                unclassified.setdefault(prefix, []).append(where)

    assert not unclassified, (
        f"Model upload root(s) nobody has classified: {unclassified}. Ask the "
        "question this gate exists for — CAN A VENDOR DOCUMENT BE STORED THERE? "
        "Gate it in config.protected_media.VENDOR_MEDIA_PREFIXES with a "
        "mirrored nginx `location ^~` block, or add it to OPEN_PREFIXES here "
        "WITH THE REASON it stays anonymously readable."
    )
