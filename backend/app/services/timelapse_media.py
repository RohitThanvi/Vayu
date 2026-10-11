"""Downloadable timelapse media: fetches the per-frame images a timelapse result already carries, burns the period
label in, and encodes an animated GIF or an MP4; also supplies the frame images for the PDF contact sheet.

Why server-side: the frame URLs are Earth Engine thumbnail links, which a browser cannot stitch into a video itself
(cross-origin images taint a canvas), and building it here lets the period labels be burned into every frame.

Security: the frame URLs come from a stored job result, but they are still only fetched if they are https links on
Earth Engine's own host, so this can never be turned into a general-purpose URL fetcher (SSRF).
"""
import io
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

ALLOWED_FRAME_HOST = "earthengine.googleapis.com"
MAX_FRAME_BYTES = 8 * 1024 * 1024  # a 512 px PNG is far smaller; this only stops a runaway response
FETCH_TIMEOUT_S = 90.0  # Earth Engine renders a thumbnail on first request, which can take a while
MIN_FPS, MAX_FPS, DEFAULT_FPS = 1, 12, 4
VIDEO_FORMATS = {"gif": ("image/gif", "gif"), "mp4": ("video/mp4", "mp4")}


class VideoBuildError(Exception):
    """Raised with a user-readable message when a video cannot be built."""


def is_allowed_frame_url(url: Optional[str]) -> bool:
    if not url or not isinstance(url, str):
        return False
    p = urlparse(url)
    host = (p.hostname or "").lower()
    return p.scheme == "https" and (host == ALLOWED_FRAME_HOST or host.endswith("." + ALLOWED_FRAME_HOST))


def _fetch_one(url: Optional[str]) -> Optional[bytes]:
    if not is_allowed_frame_url(url):
        return None
    try:
        with httpx.Client(timeout=FETCH_TIMEOUT_S, follow_redirects=False) as client:
            with client.stream("GET", url) as r:
                if r.status_code != 200:
                    logger.warning(f"timelapse_media: frame fetch returned HTTP {r.status_code}")
                    return None
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > MAX_FRAME_BYTES:
                        logger.warning("timelapse_media: frame exceeded size cap, dropped")
                        return None
                return bytes(buf)
    except Exception as e:
        logger.warning(f"timelapse_media: frame fetch failed: {type(e).__name__}: {e}")
        return None


def fetch_frame_images(frames: List[Dict[str, Any]], max_workers: int = 6) -> List[Optional[bytes]]:
    """Image bytes for each frame's thumb_url, in order; None where a link is missing, not allowed, expired or failed."""
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(_fetch_one, [f.get("thumb_url") for f in frames]))


def _font(size: int):
    for name in ("DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def _caption(frame: Dict[str, Any], value_label: Optional[str], units: Optional[str]) -> str:
    v = frame.get("mean_value")
    if v is None:
        return ""
    return f"{value_label or 'value'}: {v:g}{(' ' + units) if units else ''}"


def _stamp(img: Image.Image, title: str, sub: str) -> Image.Image:
    """Burn the period label (and a smaller value line) onto the frame."""
    img = img.convert("RGB")
    w, h = img.size
    big, small = _font(max(14, h // 16)), _font(max(10, h // 30))
    draw = ImageDraw.Draw(img, "RGBA")
    pad = max(4, h // 90)
    tb = draw.textbbox((0, 0), title, font=big)
    box_w, box_h = tb[2] - tb[0], tb[3] - tb[1]
    draw.rectangle([0, 0, box_w + 2 * pad + 2, box_h + 2 * pad + 2], fill=(0, 0, 0, 150))
    draw.text((pad, pad - tb[1]), title, font=big, fill=(255, 255, 255, 255))
    if sub:
        sb = draw.textbbox((0, 0), sub, font=small)
        sw, sh = sb[2] - sb[0], sb[3] - sb[1]
        draw.rectangle([0, h - sh - 2 * pad - 2, sw + 2 * pad + 2, h], fill=(0, 0, 0, 150))
        draw.text((pad, h - sh - pad - sb[1] - 1), sub, font=small, fill=(255, 255, 255, 255))
    return img


def build_video(frames: List[Dict[str, Any]], images: List[Optional[bytes]], fmt: str = "gif", fps: int = DEFAULT_FPS,
                value_label: Optional[str] = None, units: Optional[str] = None) -> Tuple[bytes, str, str]:
    """(bytes, mime, extension) of an animation of the frames that have an image. Needs at least 2 usable frames."""
    fmt = (fmt or "gif").lower()
    if fmt not in VIDEO_FORMATS:
        raise VideoBuildError(f"Unknown video format '{fmt}'. Use one of: {', '.join(VIDEO_FORMATS)}.")
    fps = max(MIN_FPS, min(MAX_FPS, int(fps or DEFAULT_FPS)))
    stamped: List[Image.Image] = []
    size = None
    for f, raw in zip(frames, images):
        if not raw:
            continue
        try:
            im = Image.open(io.BytesIO(raw))
            im.load()
        except Exception as e:
            logger.warning(f"timelapse_media: undecodable frame {f.get('label')}: {e}")
            continue
        if size is None:
            size = (im.width - im.width % 2, im.height - im.height % 2)  # even dimensions: required by H.264 yuv420p
        im = im.convert("RGB").resize(size) if im.size != size else im.convert("RGB")
        stamped.append(_stamp(im, str(f.get("label", "")), _caption(f, value_label, units)))
    if len(stamped) < 2:
        raise VideoBuildError(
            f"Only {len(stamped)} of {len(frames)} frame images could be fetched, so no video can be built. "
            "The Earth Engine frame links may have expired — re-run the timelapse and download again."
        )
    mime, ext = VIDEO_FORMATS[fmt]
    if fmt == "gif":
        out = io.BytesIO()
        stamped[0].save(out, format="GIF", save_all=True, append_images=stamped[1:], duration=int(1000 / fps), loop=0, optimize=False)
        return out.getvalue(), mime, ext
    try:
        import imageio.v2 as imageio
        import numpy as np
    except Exception:
        raise VideoBuildError("MP4 encoding is not available on this server; download the GIF instead.")
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()
    try:
        with imageio.get_writer(tmp.name, format="FFMPEG", fps=fps, codec="libx264", pixelformat="yuv420p", macro_block_size=2, quality=7) as w:
            for im in stamped:
                w.append_data(np.asarray(im))
        with open(tmp.name, "rb") as fh:
            return fh.read(), mime, ext
    except Exception as e:
        logger.error(f"timelapse_media: MP4 encode failed: {type(e).__name__}: {e}")
        raise VideoBuildError("MP4 encoding failed on this server; download the GIF instead.")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
