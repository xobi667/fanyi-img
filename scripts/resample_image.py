#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


FORMAT_ALIASES = {
    "png": "PNG",
    "jpg": "JPEG",
    "jpeg": "JPEG",
    "webp": "WEBP",
    "bmp": "BMP",
    "tiff": "TIFF",
}
FORMAT_SUFFIXES = {
    "PNG": {".png"},
    "JPEG": {".jpg", ".jpeg", ".jfif"},
    "WEBP": {".webp"},
    "BMP": {".bmp"},
    "TIFF": {".tif", ".tiff"},
}
ALPHA_FORMATS = {"PNG", "WEBP", "TIFF"}
ICC_FORMATS = {"PNG", "JPEG", "WEBP", "TIFF"}


def parse_size(value: str) -> tuple[int, int]:
    normalized = value.strip().lower().replace("*", "x").replace("×", "x")
    if normalized.count("x") != 1:
        raise argparse.ArgumentTypeError("size must be WIDTHxHEIGHT")
    try:
        width, height = (int(part.strip()) for part in normalized.split("x", 1))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("size must be WIDTHxHEIGHT") from exc
    if width < 1 or height < 1:
        raise argparse.ArgumentTypeError("size dimensions must be positive")
    return width, height


def has_alpha_channel(image: Image.Image) -> bool:
    return "A" in image.getbands() or (image.mode == "P" and "transparency" in image.info)


def has_transparency(image: Image.Image) -> bool:
    return has_alpha_channel(image) and image.convert("RGBA").getchannel("A").getextrema()[0] < 255


def target_format(requested: str, source_format: str) -> str:
    if requested == "source":
        if source_format not in FORMAT_SUFFIXES:
            raise ValueError(f"source encoded format {source_format or 'unknown'} is not supported")
        return source_format
    return FORMAT_ALIASES[requested]


def prepare_mode(image: Image.Image, encoded_format: str) -> tuple[Image.Image, bool]:
    """Return a LANCZOS-capable mode and whether the original ICC remains applicable."""
    alpha = has_alpha_channel(image)
    transparent = has_transparency(image)
    if transparent and encoded_format not in ALPHA_FORMATS:
        raise ValueError(f"output format {encoded_format} cannot preserve transparent pixels")
    if alpha and encoded_format in ALPHA_FORMATS:
        return image.convert("RGBA"), True
    if encoded_format == "JPEG":
        if image.mode in {"L", "RGB", "CMYK"}:
            return image.copy(), True
        return image.convert("RGB"), image.mode != "CMYK"
    if encoded_format == "TIFF" and image.mode in {"L", "LA", "RGB", "RGBA", "CMYK"}:
        return image.copy(), True
    if image.mode == "L":
        return image.copy(), True
    if image.mode == "RGB":
        return image.copy(), True
    return image.convert("RGB"), image.mode != "CMYK"


def save_options(encoded_format: str, icc_profile: bytes | None) -> dict[str, object]:
    options: dict[str, object] = {}
    if icc_profile is not None and encoded_format in ICC_FORMATS:
        options["icc_profile"] = icc_profile
    if encoded_format == "PNG":
        options.update(compress_level=9, optimize=False)
    elif encoded_format == "JPEG":
        options.update(quality=95, subsampling=0, optimize=True, progressive=True)
    elif encoded_format == "WEBP":
        options.update(lossless=True, quality=100, method=6, exact=True)
    elif encoded_format == "TIFF":
        options["compression"] = "tiff_deflate"
    return options


def validate_staged_output(
    path: Path,
    encoded_format: str,
    target_size: tuple[int, int],
    expected_transparency: bool,
    expected_icc: bytes | None,
) -> None:
    with Image.open(path) as raw:
        actual_format = (raw.format or "").upper()
        actual_icc = raw.info.get("icc_profile")
        image = ImageOps.exif_transpose(raw)
        image.load()
    if actual_format != encoded_format:
        raise ValueError(f"staged output encoded as {actual_format or 'unknown'}, expected {encoded_format}")
    if image.size != target_size:
        raise ValueError(
            f"staged output size {image.width}x{image.height} does not match "
            f"{target_size[0]}x{target_size[1]}"
        )
    if has_transparency(image) != expected_transparency:
        raise ValueError("staged output did not preserve the source transparency contract")
    if expected_icc is not None and actual_icc != expected_icc:
        raise ValueError("staged output did not preserve the source ICC profile")


def resample_image(
    source: Path,
    output: Path,
    size: tuple[int, int],
    output_format: str,
    overwrite: bool = False,
) -> tuple[str, bool, bool]:
    source = source.expanduser().resolve()
    output_absolute = output.expanduser().absolute()
    output_resolved = output_absolute.resolve()
    if not source.is_file():
        raise ValueError(f"input image not found: {source}")
    if output_absolute.exists() and output_absolute.is_symlink():
        raise ValueError("output must not be a symlink")
    if source == output_resolved:
        raise ValueError("output must differ from input; source images are read-only")
    if output_absolute.exists() and not output_absolute.is_file():
        raise ValueError("output must be a file path")
    if output_absolute.exists() and not overwrite:
        raise ValueError("output exists; use --overwrite")

    try:
        with Image.open(source) as raw:
            source_format = (raw.format or "").upper()
            icc_value = raw.info.get("icc_profile")
            icc_profile = bytes(icc_value) if isinstance(icc_value, (bytes, bytearray)) else None
            image = ImageOps.exif_transpose(raw)
            image.load()
            image = image.copy()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError(f"input is not a readable image: {exc}") from exc

    encoded_format = target_format(output_format, source_format)
    if output_absolute.suffix.lower() not in FORMAT_SUFFIXES[encoded_format]:
        allowed = ", ".join(sorted(FORMAT_SUFFIXES[encoded_format]))
        raise ValueError(f"output suffix must match {encoded_format}: {allowed}")
    if image.width * size[1] != image.height * size[0]:
        raise ValueError(
            f"target size {size[0]}x{size[1]} changes source ratio "
            f"{image.width}:{image.height}; only same-ratio resampling is allowed"
        )

    source_transparency = has_transparency(image)
    prepared, icc_compatible = prepare_mode(image, encoded_format)
    resized = prepared.resize(size, Image.Resampling.LANCZOS)
    preserved_icc = icc_profile if icc_compatible and encoded_format in ICC_FORMATS else None

    output_absolute.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_absolute.with_name(
        f".{output_absolute.stem}.tmp-{os.getpid()}-{uuid.uuid4().hex}{output_absolute.suffix}"
    )
    try:
        resized.save(
            temporary,
            format=encoded_format,
            **save_options(encoded_format, preserved_icc),
        )
        validate_staged_output(
            temporary,
            encoded_format,
            size,
            source_transparency,
            preserved_icc,
        )
        os.replace(temporary, output_absolute)
    except (OSError, ValueError) as exc:
        raise ValueError(f"could not write resampled image: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return encoded_format, source_transparency, icc_profile is not None and preserved_icc is not None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministically resample one complete image to an exact same-ratio size."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--size", required=True, type=parse_size, help="Exact WIDTHxHEIGHT target size.")
    parser.add_argument(
        "--output-format",
        required=True,
        choices=[*FORMAT_ALIASES, "source"],
        help="Encode as the manifest expected format, or preserve the source encoded format.",
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    try:
        encoded_format, transparency, icc_preserved = resample_image(
            args.input,
            args.output,
            args.size,
            args.output_format,
            args.overwrite,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(
        f"written={args.output.expanduser().absolute()} size={args.size[0]}x{args.size[1]} "
        f"format={encoded_format} transparency={str(transparency).lower()} "
        f"icc_preserved={str(icc_preserved).lower()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
