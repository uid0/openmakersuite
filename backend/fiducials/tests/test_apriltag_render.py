"""Tests for the AprilTag render helper (op-e9w).

The load-bearing property is generate<->detect symmetry: a tag drawn by
``build_apriltag_image`` must decode back to the same id with cv2.aruco's
detector. Everything else (quiet zone, determinism, range validation) guards
the geometry that makes that round-trip reliable at print scale.
"""

from __future__ import annotations

import numpy as np
import pytest

from fiducials.models import FAMILY_TAG36H10, FAMILY_TAG36H11
from fiducials.services.apriltag_render import build_apriltag_image, decode_apriltag_ids


@pytest.mark.parametrize("tag_id", [0, 1, 42, 586])
def test_round_trips_through_cv2_detector(tag_id):
    img = build_apriltag_image(tag_id, 250, family=FAMILY_TAG36H11)
    assert img.size == (250, 250)
    assert decode_apriltag_ids(img, family=FAMILY_TAG36H11) == [tag_id]


def test_round_trips_at_each_label_footprint():
    # The pixel footprints the two labels actually paste: Epson/Brother
    # (~203-256px) and the maker-box tag at 300dpi (~240px) / 600dpi (~480px).
    for px in (203, 240, 256, 480):
        img = build_apriltag_image(123, px)
        assert decode_apriltag_ids(img) == [123], f"failed to round-trip at {px}px"


def test_36h10_family_round_trips():
    img = build_apriltag_image(2000, 300, family=FAMILY_TAG36H10)
    assert decode_apriltag_ids(img, family=FAMILY_TAG36H10) == [2000]


def test_quiet_zone_edges_are_white():
    # Detectors fail without a white margin around the black border ring.
    arr = np.array(build_apriltag_image(7, 240, quiet_zone_modules=2).convert("L"))
    assert arr[0, :].min() == 255
    assert arr[-1, :].min() == 255
    assert arr[:, 0].min() == 255
    assert arr[:, -1].min() == 255


def test_deterministic_bytes_per_id():
    a = np.array(build_apriltag_image(42, 200))
    b = np.array(build_apriltag_image(42, 200))
    assert np.array_equal(a, b)


def test_distinct_ids_render_differently():
    a = np.array(build_apriltag_image(1, 200))
    b = np.array(build_apriltag_image(2, 200))
    assert not np.array_equal(a, b)


def test_rejects_out_of_range_id():
    with pytest.raises(ValueError):
        build_apriltag_image(587, 200, family=FAMILY_TAG36H11)  # valid is 0..586
    with pytest.raises(ValueError):
        build_apriltag_image(-1, 200)


def test_rejects_unknown_family():
    with pytest.raises(ValueError):
        build_apriltag_image(0, 200, family="tagBogus")


def test_rejects_insufficient_quiet_zone():
    with pytest.raises(ValueError):
        build_apriltag_image(0, 200, quiet_zone_modules=1)


def test_decode_unknown_family_raises():
    img = build_apriltag_image(0, 200)
    with pytest.raises(ValueError):
        decode_apriltag_ids(img, family="tagBogus")
