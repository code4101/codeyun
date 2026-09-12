"""Pure interval/sampling contracts, with no simulated game or device I/O."""

import cv2
import numpy as np
import pytest

from backend.core.fanxiu.client.mumu_control import smooth_image_tolerance_bounds


@pytest.mark.parametrize("sigma", [1.25, 900 / 540])
@pytest.mark.parametrize("outside", [0, 255])
def test_filtered_envelope_contains_admissible_pixels_and_unknown_surroundings(sigma, outside):
    rng = np.random.default_rng(17)
    lower = rng.integers(0, 180, (35, 27, 3), dtype=np.uint8)
    upper = (lower.astype(np.uint16) + 75).astype(np.uint8)
    pixels = lower.astype(float) + rng.random(lower.shape) * (upper - lower)
    pixels = pixels.astype(np.uint8)
    frame = np.full((65, 57, 3), outside, dtype=np.uint8)
    frame[15:50, 15:42] = pixels
    actual = cv2.GaussianBlur(frame, (0, 0), sigma)[15:50, 15:42]
    low, high = smooth_image_tolerance_bounds(lower, upper, sigma=sigma)
    assert np.all(actual >= low)
    assert np.all(actual <= high)
    # Swapped channelwise bounds have the same interval meaning.
    swapped = smooth_image_tolerance_bounds(upper, lower, sigma=sigma)
    np.testing.assert_array_equal(swapped[0], low)
    np.testing.assert_array_equal(swapped[1], high)


def test_high_frequency_exact_envelope_is_filtered_without_changing_tolerance():
    pattern = ((np.indices((35, 35)).sum(axis=0) % 2) * 255).astype(np.uint8)
    image = np.repeat(pattern[..., None], 3, axis=2)
    actual = cv2.GaussianBlur(image, (0, 0), 900 / 540)
    assert np.any(actual != image)  # Comparing to the original envelope fails.
    low, high = smooth_image_tolerance_bounds(image, image, sigma=900 / 540)
    np.testing.assert_array_equal(low[10:-10, 10:-10], actual[10:-10, 10:-10])
    np.testing.assert_array_equal(high[10:-10, 10:-10], actual[10:-10, 10:-10])


def test_shape_border_uses_unknown_surroundings_instead_of_reflection():
    bound = np.full((25, 25, 3), 100, dtype=np.uint8)
    low, high = smooth_image_tolerance_bounds(bound, bound, sigma=1.25)
    assert np.all(low[0, 0] < 100)
    assert np.all(high[0, 0] > 100)
    assert np.all(low[12, 12] == 100)
    assert np.all(high[12, 12] == 100)
