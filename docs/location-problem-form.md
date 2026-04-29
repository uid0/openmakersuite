# Location Problem Report — Paper Form

Standard PDF template for reporting non-asset problems at a `Location` —
leaks, broken doors, lighting, HVAC complaints, etc. The reporter prints
the form (or fills it in any PDF reader), drops it in the building's
inbox, and Logistics scans / emails it back. The Postmark inbound
webhook picks up the attachment and
`inventory.services.work_order_ingest._apply_location_problem_submission`
materializes a `LocationProblem` row.

## Routing

`detect_submission_kind(pdf_bytes, subject)` classifies inbound PDFs as
PM completion, third-party WO, or **location problem**. The location
path is taken when any of the following hold:

1. Email subject prefix `[LOCPROB]` (strongest signal — printed on the
   form for the reporter to use).
2. AcroForm field `location_problem_report` (pre-populated marker) or
   `location_id` present.
3. Header text matches `Location Problem Report` (text fallback for
   scanned paper).

## Template fields

The form is a single-page US-Letter PDF. The literal header line
`Location Problem Report` must appear at the top so the text-fallback
detector triggers on scanned paper copies.

### Hidden / machine fields

| AcroForm field name         | Purpose                                                |
| --------------------------- | ------------------------------------------------------ |
| `location_problem_report`   | Marker — value `"1"`. Disambiguates from PM forms.     |
| `location_id`               | Numeric primary key of the target `Location`.          |

A QR code with payload `location:<id>` is placed on the page as a
secondary recovery path for image-only scans where the AcroForm fields
were stripped.

### Reporter-fillable fields

| AcroForm field name | Expected content                                    | Maps to                       |
| ------------------- | --------------------------------------------------- | ----------------------------- |
| `severity`          | One of `low`, `medium`, `high`, `urgent`            | `LocationProblem.severity`    |
| `description`       | Free-form description of the problem                | `LocationProblem.description` |
| `reported_by`       | Reporter's name or email (optional)                 | `LocationProblem.reported_by` |

A `Severity:` and `Description:` text block fallback is used when the
form has been printed and rescanned (no AcroForm round-trip).

## Behaviour

On a successful parse the ingest service:

- Creates a `LocationProblem` with `status = reported`.
- Attaches the original PDF to `paper_form_attachment`.
- Sets `severity` (defaults to `medium` if missing/invalid).
- Sets `reported_by` to the inbound email's `from_email` when known.
- Marks the `WorkOrderSubmission` row `applied` and links it back via
  the `location_problem` FK so the same submission is idempotent on
  reprocessing.

If the location id can't be resolved the submission is marked `failed`
with a `parse_error` describing which extraction routes were tried, and
no `LocationProblem` row is created. The PDF stays attached to the
submission for staff review.
