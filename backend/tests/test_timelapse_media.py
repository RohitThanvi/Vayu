"""Tests for the downloadable timelapse video / PDF frame fetching (services/timelapse_media.py).
Encoding is exercised for real (Pillow GIF, imageio-ffmpeg MP4) with synthetic frames; no network or Earth Engine."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import io

import pytest
from PIL import Image

from app.services import timelapse_media as tm


def _png(i, w=301, h=200):
    img = Image.new("RGB", (w, h), ((i * 40) % 255, 90, 160))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


FRAMES = [{"label": f"2024-{m:02d}", "mean_value": 5.0 + m} for m in range(1, 7)]


@pytest.mark.parametrize("url,ok", [
    ("https://earthengine.googleapis.com/v1/projects/p/thumbnails/x:getPixels", True),
    ("https://sub.earthengine.googleapis.com/x", True),
    ("http://earthengine.googleapis.com/x", False),            # not https
    ("https://earthengine.googleapis.com.evil.com/x", False),  # look-alike host
    ("https://evil.com/earthengine.googleapis.com", False),
    ("https://127.0.0.1/x", False),
    ("file:///etc/passwd", False),
    ("", False),
    (None, False),
])
def test_frame_url_guard(url, ok):
    assert tm.is_allowed_frame_url(url) is ok


def test_disallowed_urls_are_never_fetched():
    out = tm.fetch_frame_images([{"thumb_url": "https://evil.com/x"}, {"thumb_url": None}, {}])
    assert out == [None, None, None]


def test_gif_skips_missing_frame_and_loops():
    images = [_png(i) if i != 2 else None for i in range(6)]
    data, mime, ext = tm.build_video(FRAMES, images, "gif", 4, "AOI-mean radiance", "nW/sr/cm2")
    g = Image.open(io.BytesIO(data))
    assert (mime, ext) == ("image/gif", "gif")
    assert g.n_frames == 5                      # the missing frame is dropped, not blanked
    assert g.info.get("loop") == 0 and g.info.get("duration") == 250
    assert g.size[0] % 2 == 0 and g.size[1] % 2 == 0


def test_mp4_is_a_readable_video():
    pytest.importorskip("imageio_ffmpeg")
    import imageio.v2 as imageio
    import os
    import tempfile
    data, mime, ext = tm.build_video(FRAMES, [_png(i) for i in range(6)], "mp4", 4)
    assert (mime, ext) == ("video/mp4", "mp4") and data[4:8] == b"ftyp"
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as t:
        t.write(data)
    try:
        reader = imageio.get_reader(t.name, format="FFMPEG")
        assert sum(1 for _ in reader) == 6
        reader.close()
    finally:
        os.unlink(t.name)


def test_too_few_usable_frames_gives_a_helpful_error():
    with pytest.raises(tm.VideoBuildError, match="links may have expired"):
        tm.build_video(FRAMES[:3], [None, None, _png(1)], "gif")


def test_unknown_format_rejected():
    with pytest.raises(tm.VideoBuildError, match="Unknown video format"):
        tm.build_video(FRAMES, [_png(i) for i in range(6)], "avi")


def test_fps_is_clamped():
    data, _, _ = tm.build_video(FRAMES, [_png(i) for i in range(6)], "gif", 999)
    # GIF stores delays in 10 ms units, so allow that rounding
    assert abs(Image.open(io.BytesIO(data)).info.get("duration") - int(1000 / tm.MAX_FPS)) <= 10
