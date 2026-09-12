import pytest
from backend.core.fanxiu.remote.frame_geometry import FrameGeometry


@pytest.mark.parametrize("width,height,point", [(900,1600,(74,393)),(720,1280,(59,314)),(540,960,(44,236))])
def test_source_pixels_preserve_normalized_location(width,height,point):
    mapping = FrameGeometry(width,height,900,1600)
    assert mapping.compatible
    assert mapping.original_point(74,393) == point
    assert mapping.original_point(899,1599) == (width-1,height-1)
    assert mapping.original_point(0,0) == (0,0)


@pytest.mark.parametrize("width,height", [(960,540),(540,1000),(300,533),(900,1500)])
def test_unproven_viewports_do_not_authorize_coordinates(width,height):
    mapping=FrameGeometry(width,height,900,1600)
    assert not mapping.compatible
    with pytest.raises(ValueError):
        mapping.original_point(74,393)


def test_reference_coordinates_must_be_in_bounds():
    with pytest.raises(ValueError):
        FrameGeometry(540,960,900,1600).original_point(900,300)
