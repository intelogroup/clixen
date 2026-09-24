"""Convert extracted Blender manual/API html into plain text for semantic_files indexing."""
from pathlib import Path
from bs4 import BeautifulSoup

RAW = Path(__file__).resolve().parent.parent / "blender_docs_raw"
OUT = Path(__file__).resolve().parent.parent / "blender_docs_text"


def clean_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    main = (
        soup.find(attrs={"role": "main"})
        or soup.find("div", class_="document")
        or soup.find("body")
    )
    if not main:
        return ""
    for tag in main(["script", "style", "nav"]):
        tag.decompose()
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    text = main.get_text("\n", strip=True)
    return f"{title}\n\n{text}" if title else text


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    n = 0
    for fp in RAW.rglob("*.html"):
        try:
            html = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        text = clean_text(html)
        if not text.strip():
            continue
        rel = fp.relative_to(RAW).with_suffix(".txt")
        out_fp = OUT / rel
        out_fp.parent.mkdir(parents=True, exist_ok=True)
        out_fp.write_text(text, encoding="utf-8")
        n += 1
        if n % 500 == 0:
            print(f"converted {n}")
    print(f"done: {n} files -> {OUT}")


if __name__ == "__main__":
    main()
