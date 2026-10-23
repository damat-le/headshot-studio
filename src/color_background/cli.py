import argparse
from pathlib import Path

from PIL import Image

from color_background.studio import MODEL, MODELS, PRESETS, StudioSettings, parse_hex, render, resolve_color


def main() -> None:
    p = argparse.ArgumentParser(
        description="Replace a portrait's background with a studio-style coloured backdrop."
    )
    p.add_argument("input", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--color", default="#C8A2C8", help=f"preset ({', '.join(PRESETS)}) or hex colour (default: lilac)")
    p.add_argument("--light", help="override the colour behind the head, e.g. #3773F0")
    p.add_argument("--dark", help="override the colour at the corners, e.g. #0030A0")
    p.add_argument("--center", nargs=2, type=float, metavar=("X", "Y"),
                   help="light centre in pixels (default: detected from the head)")
    p.add_argument("--gradient", type=float, default=1.0,
                   help="backdrop falloff: 0 = flat, 1 = default, 2 = dark corners")
    p.add_argument("--shadow", type=float, default=0.10)
    p.add_argument("--white-balance", type=float, default=0.7)
    p.add_argument("--model", default=MODEL, choices=list(MODELS),
                   help="cutout model: isnet-general-use (fast, default) or birefnet-portrait (best for hair)")
    p.add_argument("--seed", type=int, default=1)
    args = p.parse_args()

    light, dark = resolve_color(args.color)
    settings = StudioSettings(
        light=parse_hex(args.light) if args.light else light,
        dark=parse_hex(args.dark) if args.dark else dark,
        center=tuple(args.center) if args.center else None,
        gradient=args.gradient,
        shadow=args.shadow,
        white_balance=args.white_balance,
        seed=args.seed,
        model=args.model,
    )
    out, (cx, cy) = render(Image.open(args.input), settings)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    out.save(args.output, quality=95)
    print(f"{args.output}  light={settings.light} dark={settings.dark} center=({cx:.0f}, {cy:.0f})")


if __name__ == "__main__":
    main()
