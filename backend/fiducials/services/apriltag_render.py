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

# Map our family identifiers onto cv2.aruco's predefined dictionaries.
_CV2_DICTIONARY = {
    FAMILY_TAG36H11: cv2.aruco.DICT_APRILTAG_36h11,
    FAMILY_TAG36H10: cv2.aruco.DICT_APRILTAG_36h10,
}

# 36h10 / 36h11 both render as a 6x6 data grid + a 1-module black border.
_CORE_MODULES = 8
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
    capacity = FAMILY_CAPACITY[family]
    if not isinstance(tag_id, int) or not (0 <= tag_id < capacity):
        raise ValueError(
            f"tag_id {tag_id!r} out of range for family {family!r} (0..{capacity - 1})."
        )
    if quiet_zone_modules < 2:
        raise ValueError("quiet_zone_modules must be >= 2 for reliable detection.")
    if target_px <= 0:
        raise ValueError("target_px must be positive.")

    dictionary = cv2.aruco.getPredefinedDictionary(_CV2_DICTIONARY[family])
    base_px = _CORE_MODULES * _BASE_PX_PER_MODULE
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
