"""Copy Bootstrap vendor assets from node_modules into ditaknet/static/vendor."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "ditaknet" / "static" / "vendor"
NODE = ROOT / "node_modules"

COPY_MAP = {
    NODE / "bootstrap" / "dist" / "css" / "bootstrap.min.css": VENDOR / "bootstrap" / "bootstrap.min.css",
    NODE / "bootstrap" / "dist" / "js" / "bootstrap.bundle.min.js": VENDOR / "bootstrap" / "bootstrap.bundle.min.js",
    NODE / "bootstrap-icons" / "font" / "bootstrap-icons.min.css": VENDOR / "bootstrap-icons" / "bootstrap-icons.min.css",
}


def strip_source_maps(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if "sourceMappingURL" not in line]
    path.write_text("\n".join(lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8")


def main() -> None:
    if not NODE.exists():
        raise SystemExit("node_modules missing. Run: npm install bootstrap@5.3.3 bootstrap-icons@1.11.3 --no-save")

    for src, dest in COPY_MAP.items():
        if not src.exists():
            raise SystemExit(f"Missing source asset: {src}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        if dest.suffix in {".css", ".js"}:
            strip_source_maps(dest)

    fonts_src = NODE / "bootstrap-icons" / "font" / "fonts"
    fonts_dest = VENDOR / "bootstrap-icons" / "fonts"
    fonts_dest.mkdir(parents=True, exist_ok=True)
    for font in fonts_src.glob("*"):
        if font.is_file():
            (fonts_dest / font.name).write_bytes(font.read_bytes())

    print(f"Synced vendor assets to {VENDOR}")


if __name__ == "__main__":
    main()
