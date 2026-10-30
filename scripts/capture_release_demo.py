"""Deterministic offline capture of recorded stills into a WebM demo.

Source images are actual screenshots/stills (not realtime playback).
Each hash-verified still is rendered with its provenance banner and
caption in a local HTML page, screenshotted once to JPEG (plus a PNG
proof still), and that JPEG is repeated at 1 fps for the scene's
positive-integer duration. The JPEG stream is piped to an explicitly
supplied ffmpeg (image2pipe/MJPEG in, VP9/WebM out), so a 180s scene
list yields exactly 180 frames / 180s with no realtime wait or drift.

Offline is enforced, not assumed: Playwright routes abort every
http/https request; only file:// page/image loads continue. Source
images must be bounded relative PNG/JPEG/WebP files (see
scripts.release_checks.image_bounds_error) with matching SHA-256.

Example (root-run):
    python -m scripts.capture_release_demo --scenes scenes.json \
        --output-dir /tmp/demo-out \
        --ffmpeg /absolute/path/to/ffmpeg
Use --expected-duration 7 with a 7s scene list for a smoke check.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from scripts.release_checks import SCENE_ID_RE, image_bounds_error, safe_path

WIDTH, HEIGHT, FPS = 1280, 720, 1


def load_scenes(
    root: Path, scenes_path: Path, expected_duration: int = 180
) -> list[dict[str, Any]]:
    data = json.loads(scenes_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("scenes"), list):
        raise ValueError("scenes file must be an object with a scenes list")
    scenes: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    expected_start = 0
    for raw in data["scenes"]:
        if not isinstance(raw, dict):
            raise ValueError("each scene must be an object")
        scene_id = raw.get("id")
        if not isinstance(scene_id, str) or not SCENE_ID_RE.fullmatch(scene_id) or scene_id in seen:
            raise ValueError(f"scene id must be a unique safe slug: {scene_id!r}")
        seen.add(scene_id)
        image, caption, provenance = raw.get("image"), raw.get("caption"), raw.get("provenance")
        duration, start = raw.get("duration_seconds"), raw.get("start_seconds")
        if not isinstance(image, str) or not image:
            raise ValueError(f"scene {scene_id}: image path is required")
        if "://" in image or image.startswith("/"):
            raise ValueError(f"scene {scene_id}: image must be a bounded relative path")
        if not isinstance(caption, str) or not caption or "http" in caption:
            raise ValueError(f"scene {scene_id}: caption required, no external URLs")
        if not isinstance(provenance, str) or not provenance:
            raise ValueError(f"scene {scene_id}: continuous provenance label is required")
        if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
            raise ValueError(f"scene {scene_id}: positive integer duration_seconds required")
        if isinstance(start, bool) or not isinstance(start, int) or start != expected_start:
            raise ValueError(
                f"scene {scene_id}: start_seconds must be contiguous ({expected_start})"
            )
        target = safe_path(root, image)
        if target is None or not target.is_file():
            raise ValueError(f"scene {scene_id}: image missing or unsafe: {image}")
        payload = target.read_bytes()
        bounds = image_bounds_error(payload)
        if bounds is not None:
            raise ValueError(f"scene {scene_id}: image {bounds}")
        expected = raw.get("sha256")
        if not isinstance(expected, str) or expected != hashlib.sha256(payload).hexdigest():
            raise ValueError(f"scene {scene_id}: sha256 mismatch for {image}")
        expected_start += duration
        total += duration
        scenes.append(
            {
                "id": scene_id,
                "image_uri": target.as_uri(),
                "duration_seconds": duration,
                "caption": caption,
                "provenance": provenance,
            }
        )
    if total != expected_duration:
        raise ValueError(f"scene durations must sum to {expected_duration}s, got {total}")
    return scenes


def render_html(scenes: list[dict[str, Any]]) -> str:
    sections = "\n".join(
        f'<section class="scene" id="s{i}"><div class="banner">{html.escape(str(s["provenance"]))}'
        " — RECORDED STILLS, NOT REALTIME</div>"
        f'<img src="{html.escape(str(s["image_uri"]))}" alt="">'
        f'<div class="caption">{html.escape(str(s["caption"]))}</div></section>'
        for i, s in enumerate(scenes)
    )
    return (
        "<!doctype html><meta charset='utf-8'><style>"
        "*{box-sizing:border-box}html,body{width:1280px;height:720px;overflow:hidden}"
        "body{margin:0;background:#111;color:#fff;font:28px system-ui}"
        ".scene{display:none;width:1280px;height:720px;position:relative}"
        ".scene.active{display:block}.scene img{width:100%;height:100%;object-fit:contain}"
        ".banner{position:absolute;top:0;left:0;right:0;padding:14px;background:#000;color:#ffd23f;"
        "font-weight:700;text-align:center;z-index:2;overflow-wrap:anywhere}"
        ".caption{position:absolute;bottom:0;left:0;right:0;padding:18px;background:rgba(0,0,0,.85);"
        "text-align:center}</style>"
        f"{sections}<script>window.show=i=>{{document.querySelectorAll('.scene')"
        ".forEach((e,j)=>e.classList.toggle('active',i===j))};</script>"
    )


def render_stills(scenes: list[dict[str, Any]], output_dir: Path) -> list[tuple[bytes, int]]:
    """Render each scene once offline; return (JPEG bytes, repeat count) pairs."""
    html_path = output_dir / "demo.html"
    html_path.write_text(render_html(scenes), encoding="utf-8")
    stills_dir = output_dir / "stills"
    stills_dir.mkdir(parents=True, exist_ok=True)
    frames: list[tuple[bytes, int]] = []
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})

        def offline(route: Any) -> None:
            if route.request.url.startswith(("http://", "https://")):
                route.abort()
            else:
                route.continue_()

        page.route("**/*", offline)
        page.goto(html_path.as_uri())
        for index, scene in enumerate(scenes):
            page.evaluate(f"window.show({index})")
            page.wait_for_selector(f"#s{index}.active img")
            page.locator(f"#s{index} img").evaluate(
                "async image => { await image.decode(); window.scrollTo(0, 0); }"
            )
            proof = stills_dir / f"{index:02d}-{scene['id']}.png"
            proof.write_bytes(page.screenshot(type="png"))
            frames.append(
                (page.screenshot(type="jpeg", quality=85), int(scene["duration_seconds"]))
            )
        browser.close()
    return frames


def encode_webm(frames: list[tuple[bytes, int]], ffmpeg: Path, output: Path) -> int:
    command = [
        str(ffmpeg), "-y", "-f", "image2pipe", "-vcodec", "mjpeg",
        "-framerate", str(FPS), "-i", "pipe:0", "-c:v", "libvpx-vp9",
        "-b:v", "1M", "-pix_fmt", "yuv420p", "-an", str(output),
    ]  # fmt: skip
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    count = 0
    input_closed_early = False
    try:
        for jpeg, repeats in frames:
            for _ in range(repeats):
                proc.stdin.write(jpeg)
                count += 1
        proc.stdin.close()
    except BrokenPipeError:
        input_closed_early = True
    stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed: {stderr[-2000:]}")
    if input_closed_early:
        raise RuntimeError("ffmpeg closed input before all frames were delivered")
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--scenes", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True, help="explicit ffmpeg binary path")
    parser.add_argument("--expected-duration", type=int, default=180)
    args = parser.parse_args(argv)
    if not args.ffmpeg.is_file():
        print(f"error: --ffmpeg not found: {args.ffmpeg}", file=sys.stderr)
        return 2
    root = args.root.resolve()
    try:
        scenes = load_scenes(root, args.scenes, args.expected_duration)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        frames = render_stills(scenes, args.output_dir)
        output = args.output_dir / "demo.webm"
        count = encode_webm(frames, args.ffmpeg, output)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        f"encoded {count} frame(s) at {FPS} fps ({count // FPS}s) from "
        f"{len(scenes)} recorded still(s) to {output}; verify duration/frames before use"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
