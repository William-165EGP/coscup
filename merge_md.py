from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = BASE_DIR / "2026.md"


def main():
    md_files = []

    for folder in sorted(p for p in BASE_DIR.iterdir() if p.is_dir()):
        for md in sorted(folder.glob("*.md")):
            md_files.append(md)

    with OUTPUT_FILE.open("w", encoding="utf-8") as out:
        for i, md in enumerate(md_files):
            content = md.read_text(encoding="utf-8")

            out.write(content.rstrip())
            out.write("\n")

           
            if i != len(md_files) - 1:
                out.write("\n")

    print(f"Done. Merged {len(md_files)} markdown file(s) into {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
