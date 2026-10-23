"""Studio-backdrop compositing.

Every step is deterministic: the segmentation model is fixed, and the only
randomness (backdrop texture and film grain) comes from a seeded generator.
"""

from dataclasses import dataclass
from functools import cache

import numpy as np
from PIL import Image
from pymatting import estimate_foreground_ml
from rembg import new_session, remove
from rembg.sessions import sessions as rembg_sessions
from scipy.ndimage import gaussian_filter

MODEL = "isnet-general-use"
MAX_WALL_CHROMA = 0.35  # warm beige walls are ~0.25; painted colours are well above

Color = tuple[int, int, int]


@dataclass(frozen=True)
class StudioSettings:
    light: Color  # backdrop colour behind the head
    dark: Color  # backdrop colour at the corners
    center: tuple[float, float] | None = None  # light centre (x, y); None = auto from head
    white_balance: float = 0.7  # how much of the original colour cast to remove (0-1)
    gradient: float = 1.0  # backdrop falloff: 0 = flat colour, 1 = default, 2 = dark corners / spotlight
    shadow: float = 0.10  # strength of the soft shadow behind the subject
    light_wrap: float = 0.4  # how much backdrop colour bleeds onto the subject's edges
    texture: float = 0.05  # backdrop mottling
    grain: float = 0.012  # film grain standard deviation
    seed: int = 1
    model: str = MODEL  # cutout model, one of MODELS


def parse_hex(value: str) -> Color:
    h = value.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"expected a colour like #0D4FE4, got {value!r}")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def shades(base: Color) -> tuple[Color, Color]:
    """Derive the light (centre) and dark (edge) backdrop tones from one colour."""
    b = np.array(base, dtype=np.float32)
    light = b * 0.85 + 255 * 0.15
    dark = b * 0.65
    return tuple(int(round(v)) for v in light), tuple(int(round(v)) for v in dark)


def _derived(hex_color: str) -> tuple[Color, Color]:
    return shades(parse_hex(hex_color))


# Named backdrops as (label, light, dark). The first four are hand-tuned pairs.
PRESETS: dict[str, tuple[str, Color, Color]] = {
    "sky-blue": ("Sky blue", (140, 190, 235), (60, 110, 170)),
    "teal": ("Teal", (100, 190, 190), (30, 110, 120)),
    "sage": ("Sage", (175, 205, 150), (95, 130, 80)),
    "amber": ("Amber", (245, 195, 110), (185, 120, 45)),
    "artificialy-blue": ("Artificialy blue", *_derived("#0D4FE4")),
    "slate-blue": ("Slate blue", *_derived("#5E6E80")),
    "lilac": ("Lilac", *_derived("#C8A2C8")),
    "charcoal": ("Charcoal", *_derived("#5A5A5A")),
}


def resolve_color(value: str) -> tuple[Color, Color]:
    """A preset name or a hex colour -> (light, dark)."""
    if value in PRESETS:
        return PRESETS[value][1:]
    try:
        return shades(parse_hex(value))
    except ValueError:
        raise ValueError(f"{value!r} is not a hex colour or a preset ({', '.join(PRESETS)})") from None


@dataclass(frozen=True)
class Model:
    label: str
    tag: str  # one-word role shown in the UI
    size_mb: int  # download size
    good_for: str
    matting: bool  # True: refine edges with pymatting; False: the model's own mask is already fine-grained


MODELS: dict[str, Model] = {
    "isnet-general-use": Model(
        "ISNet general", "Fast", 179, "Fast all-rounder. Plain walls and tidy hair.", matting=True),
    "birefnet-portrait": Model(
        "BiRefNet portrait", "Best quality", 973, "Best for people: curly or loose hair, busy rooms. Slower.", matting=False),
}


def model_downloaded(name: str) -> bool:
    return rembg_sessions[name].resolve_existing(f"{name}.onnx") is not None


@cache
def _session(name: str):
    return new_session(name)  # loads (and on first use downloads) the model once per process


def cut_out(img: Image.Image, model: str = MODEL) -> np.ndarray:
    """Return the subject as float RGBA in [0, 1], with clean hair edges."""
    if model not in MODELS:
        raise ValueError(f"unknown model {model!r}; choose one of {', '.join(MODELS)}")
    if MODELS[model].matting:
        cut = remove(
            img,
            session=_session(model),
            alpha_matting=True,
            alpha_matting_foreground_threshold=240,
            alpha_matting_background_threshold=10,
            alpha_matting_erode_size=10,
        )
        return np.asarray(cut.convert("RGBA")).astype(np.float32) / 255
    # Fine-grained mask: keep it as the alpha, and only estimate the true edge colours so the old wall
    # doesn't tint the hair.
    rgb = np.asarray(img.convert("RGB")).astype(np.float64) / 255
    alpha = np.asarray(remove(img, session=_session(model), only_mask=True)).astype(np.float64) / 255
    fg = estimate_foreground_ml(rgb, alpha)
    return np.dstack([fg, alpha]).astype(np.float32)


def head_center(alpha: np.ndarray) -> tuple[float, float]:
    """Place the light behind the face: centred on the head, a fifth of the frame below its top."""
    H = alpha.shape[0]
    rows = np.flatnonzero((alpha > 0.5).any(axis=1))
    if rows.size == 0:
        return alpha.shape[1] / 2, H / 3
    top = rows[0]
    band = alpha[top : top + int(H * 0.1)] > 0.5
    cx = np.nonzero(band)[1].mean()
    return float(cx), float(top + H * 0.2)


def render(img: Image.Image, s: StudioSettings) -> tuple[Image.Image, tuple[float, float]]:
    img = img.convert("RGB")
    orig = np.asarray(img).astype(np.float32) / 255
    cut = cut_out(img, s.model)
    fg, a = cut[..., :3], cut[..., 3:]
    H, W = a.shape[:2]
    cx, cy = s.center or head_center(a[..., 0])
    rng = np.random.default_rng(s.seed)

    # 1. White balance: a near-neutral wall's tint is the light's colour cast; remove part of it.
    #    A genuinely coloured wall (chroma above the limit) is left alone rather than "corrected".
    wall_px = orig[a[..., 0] < 0.02]
    if wall_px.size:
        wall = wall_px.mean(0)
        chroma = (wall.max() - wall.min()) / max(wall.mean(), 1e-6)
        if chroma < MAX_WALL_CHROMA:
            gain = np.clip(1 + s.white_balance * (wall.mean() / wall - 1), 0.75, 1.3)
            fg = np.clip(fg * gain, 0, 1)

    # 2. Backdrop: radial light falloff from behind the head toward the corners
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.sqrt(((xx - cx) / (W * 0.75)) ** 2 + ((yy - cy) / (H * 0.95)) ** 2)
    light, dark = np.array(s.light) / 255, np.array(s.dark) / 255
    if s.gradient != 1:  # scale the light-to-dark gap, and shrink the lit area as the falloff grows
        dark = np.clip(light + (dark - light) * s.gradient**0.7, 0, 1)
        r = r * (0.85 + 0.15 * s.gradient)
    t = (np.clip(r, 0, 1) ** 1.8)[..., None]
    bg = light * (1 - t) + dark * t
    # soft mottling like a painted backdrop
    mott = gaussian_filter(rng.standard_normal((H, W)), 160)
    bg *= 1 + 0.25 * (mott / np.abs(mott).max())[..., None] * s.texture

    # 3. Soft shadow behind the subject, offset down and right
    sh = gaussian_filter(np.roll(np.roll(a[..., 0], 40, 0), 25, 1), 110)
    bg *= (1 - s.shadow * sh)[..., None]

    # 4. Composite, then light wrap: backdrop colour bleeds onto the subject's edges
    comp = fg * a + bg * (1 - a)
    bg_blur = np.stack([gaussian_filter(bg[..., c], 20) for c in range(3)], -1)
    wrap = a * (1 - gaussian_filter(a[..., 0], 14)[..., None])
    comp = comp * (1 - s.light_wrap * wrap) + bg_blur * s.light_wrap * wrap

    # 5. Gentle grade and film grain to unify subject and backdrop
    comp = np.clip((comp - 0.5) * 1.04 + 0.5 + 0.01, 0, 1)
    comp += rng.normal(0, s.grain, comp.shape).astype(np.float32)
    out = Image.fromarray((np.clip(comp, 0, 1) * 255 + 0.5).astype(np.uint8))
    return out, (cx, cy)
