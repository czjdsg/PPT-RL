#!/usr/bin/env python3
"""Read UTF-8 text or parse PDF text; never render or return image pixels."""
import argparse
import json
from pathlib import Path
import re
import sys


def pdf_text(path, pages=None):
    from pypdf import PdfReader
    reader = PdfReader(path if hasattr(path, "read") else str(path))
    selected = list(range(len(reader.pages)))
    if pages:
        selected = []
        for part in pages.split(","):
            bounds = [int(n) for n in part.split("-")]
            if len(bounds) not in {1, 2}:
                raise ValueError("Use --pages 1-3,5")
            first, last = bounds[0], bounds[-1]
            if not 1 <= first <= last <= len(reader.pages):
                raise ValueError("PDF page range is out of bounds")
            selected.extend(range(first - 1, last))
    fallback = None
    output = []
    try:
        for n in selected:
            try:
                text = reader.pages[n].extract_text()
            except Exception as error:
                # Malformed font descriptors can break pypdf extraction even
                # when another text parser can read the same original page.
                import fitz
                if fallback is None:
                    if hasattr(path, "read"):
                        position = path.tell()
                        path.seek(0)
                        data = path.read()
                        path.seek(position)
                        fallback = fitz.open(stream=data, filetype="pdf")
                    else:
                        fallback = fitz.open(str(path))
                text = fallback[n].get_text("text")
                text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
                print(json.dumps({"event": "pdf_text_parser_fallback", "page": n + 1,
                    "parser": "pymupdf_text", "original_error_type": type(error).__name__,
                    "original_error": str(error)[:240], "rasterized": False,
                    "ocr_used": False}), file=sys.stderr)
            output.append(f"--- Page {n + 1} ---\n" + (text or
                "[No extractable text on this page; no image/OCR fallback is enabled.]"))
        return "\n\n".join(output)
    finally:
        if fallback is not None:
            fallback.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--pages", help="PDF text page range, e.g. 1-3,5")
    parser.add_argument("--offset", type=int, default=1, help="First text line, one-based")
    parser.add_argument("--limit", type=int, help="Maximum text lines; default is all")
    args = parser.parse_args()
    try:
        path = args.file.resolve(strict=True)
        with path.open("rb") as stream:
            head = stream.read(1024)
        if b"%PDF-" in head or path.suffix.lower() == ".pdf":
            value = pdf_text(path, args.pages)
        else:
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".ico", ".avif", ".heic"} or b"\x00" in head:
                raise ValueError("Image/binary input disabled. Provide parsed text or structured data.")
            value = path.read_text(encoding="utf-8")
        if args.offset < 1 or args.limit is not None and args.limit < 1:
            raise ValueError("offset and limit must be positive")
        lines = value.splitlines()
        start = args.offset - 1
        print("\n".join(lines[start:None if args.limit is None else start + args.limit]))
        return 0
    except (OSError, ValueError, UnicodeError) as error:
        print(f"Text-only reader: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
