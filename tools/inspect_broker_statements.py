from __future__ import annotations

import argparse
from pathlib import Path

from pypdf import PdfReader


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--password", required=True)
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--chars", type=int, default=12000)
    args = parser.parse_args()

    for path in args.files:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            reader.decrypt(args.password)
        print(f"\n===== {path.name} | {len(reader.pages)} pages =====")
        for index, page in enumerate(reader.pages[: args.pages], start=1):
            text = page.extract_text() or ""
            print(f"\n--- PAGE {index} ---")
            print(text[: args.chars])


if __name__ == "__main__":
    main()
