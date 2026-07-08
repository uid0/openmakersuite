"""Render an AprilTag marker as a PIL image, ready to paste onto a label.

Generation goes through ``cv2.aruco`` — no new dependency, since
``opencv-python-headless`` is already pinned (it backs the QR detection in
``storage_vision``). Using the same library for *generation* that the
downstream vision system uses for *detection* gives generate<->detect
symmetry, which is the single most important correctness property of a
fiducial: a tag we draw with ``cv2.aruco.generateImageMarker`` decodes
reliably with ``cv2.aruco.ArucoDetector`` using the same dictionary.

The shape of :func:`build_apriltag_image` deliberately mirrors the
``_build_qr_image`` helpers in the label services: render crisp at a clean
module multiple, add a white quiet zone, then ``NEAREST``-resize to the
target pixel size so modules stay sharp at print scale.

Quiet zone: tag36h11 is a 6x6 data grid wrapped in a 1-module black border
= 8x8 modules. AprilTag detectors need a *white* margin around that black
border or they fail to find the tag, so we add ``quiet_zone_modules`` (>=2)
of white on every side.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageOps

from fiducials.models import FAMILY_CAPACITY, FAMILY_TAG36H10, FAMILY_TAG36H11

# A plain ArUco family reserved for Work Order OMR form fiducials — kept
# ISOLATED from the object-label AprilTag families above so a form scan can
# never be confused with an ambient tag36h11 object label. Its 4 corner ids
# (0..3) are fixed TEMPLATE CONSTANTS printed on every form, never drawn from
# the fiducials allocator pool (which hands out globally-unique ids for
# physical labels). DICT_4X4_50 encodes 50 ids; we only ever use 0..3.
FAMILY_ARUCO_4X4_50 = "aruco_4x4_50"

# Map our family identifiers onto cv2.aruco's predefined dictionaries.
_CV2_DICTIONARY = {
    FAMILY_TAG36H11: cv2.aruco.DICT_APRILTAG_36h11,
    FAMILY_TAG36H10: cv2.aruco.DICT_APRILTAG_36h10,
    FAMILY_ARUCO_4X4_50: cv2.aruco.DICT_4X4_50,
}

# Distinct ids each family encodes. AprilTag capacities come from the fiducials
# registry; the isolated 4x4 form-fiducial dict adds its own 50.
_FAMILY_CAPACITY = {**FAMILY_CAPACITY, FAMILY_ARUCO_4X4_50: 50}

# Total module span = data grid + a 1-module black border on every side.
# tag36h10 / tag36h11 = 6x6 data + border = 8; DICT_4X4_50 = 4x4 data +
# border = 6. Drives base_px so the marker renders at an integer number of
# pixels per module (crisp NEAREST resize).
_FAMILY_CORE_MODULES = {
    FAMILY_TAG36H11: 8,
    FAMILY_TAG36H10: 8,
    FAMILY_ARUCO_4X4_50: 6,
}
# Pixels per module before the final NEAREST resize. 10 keeps the marker
# crisp and far above the ~6-8 px/module detection floor at every DPI we
# print at (203 / 300 / 600).
_BASE_PX_PER_MODULE = 10


def build_apriltag_image(
    tag_id: int,
    target_px: int,
    *,
    family: str = FAMILY_TAG36H11,
    quiet_zone_modules: int = 2,
) -> Image.Image:
    """Return an RGB :class:`PIL.Image.Image` of the tag, ``target_px`` square.

    ``target_px`` is the side length of the *whole* returned image, white
    quiet zone included — so callers size it to the footprint they reserve
    on the label, the same way they size the QR.
    """
    if family not in _CV2_DICTIONARY:
        raise ValueError(f"Unsupported AprilTag family {family!r}.")
    capacity = _FAMILY_CAPACITY[family]
    if not isinstance(tag_id, int) or not (0 <= tag_id < capacity):
        raise ValueError(
            f"tag_id {tag_id!r} out of range for family {family!r} (0..{capacity - 1})."
        )
    if quiet_zone_modules < 2:
        raise ValueError("quiet_zone_modules must be >= 2 for reliable detection.")
    if target_px <= 0:
        raise ValueError("target_px must be positive.")

    dictionary = cv2.aruco.getPredefinedDictionary(_CV2_DICTIONARY[family])
    base_px = _FAMILY_CORE_MODULES[family] * _BASE_PX_PER_MODULE
    # uint8 HxW array: 0 = black module, 255 = white. Includes the marker's
    # own 1-module black border (borderBits defaults to 1).
    grid = cv2.aruco.generateImageMarker(dictionary, tag_id, base_px)
    core = Image.fromarray(grid).convert("RGB")

    quiet_px = quiet_zone_modules * _BASE_PX_PER_MODULE
    bordered = ImageOps.expand(core, border=quiet_px, fill="white")
    return bordered.resize((target_px, target_px), Image.Resampling.NEAREST)


def decode_apriltag_ids(image: Image.Image, *, family: str = FAMILY_TAG36H11) -> list[int]:
    """Detect AprilTag IDs in ``image`` — the inverse of build_apriltag_image.

    Exposed for the renderer contract tests (render a label, assert the
    embedded tag decodes back to the allocated ID) and usable by callers who
    want to sanity-check a generated label before printing.
    """
    if family not in _CV2_DICTIONARY:
        raise ValueError(f"Unsupported AprilTag family {family!r}.")
    dictionary = cv2.aruco.getPredefinedDictionary(_CV2_DICTIONARY[family])
    detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())
    gray = np.array(image.convert("L"))
    _corners, ids, _rejected = detector.detectMarkers(gray)
    if ids is None:
        return []
    return [int(v) for v in ids.ravel().tolist()]


# The 4 corner fiducial ids for a Work Order OMR form, keyed by page corner.
# These are TEMPLATE CONSTANTS — the same 4 ids on every form — so the reader
# always knows which detected marker anchors which corner and can recover the
# page homography before thresholding marks. Drawn from FAMILY_ARUCO_4X4_50,
# never allocated from the AprilTagAssignment pool.
FORM_FIDUCIAL_IDS = {
    "tl": 0,
    "tr": 1,
    "br": 2,
    "bl": 3,
}


def build_form_fiducials(target_px: int) -> dict[str, Image.Image]:
    """Render the 4 corner fiducial markers for a Work Order OMR form.

    Returns a dict keyed by page corner (``"tl"``/``"tr"``/``"br"``/``"bl"``)
    → the marker image, each ``target_px`` square in ``FAMILY_ARUCO_4X4_50``.
    bead-2's reader detects these 4, matches ids 0..3 to the known corners,
    and warps the scan into template space before reading each region.
    """
    return {
        corner: build_apriltag_image(tag_id, target_px, family=FAMILY_ARUCO_4X4_50)
        for corner, tag_id in FORM_FIDUCIAL_IDS.items()
    }
