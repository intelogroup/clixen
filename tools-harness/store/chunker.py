# Ported directly from zl_master_board/polo-ingest/parsers/base.py
# Paragraph-aware overlapping chunker — proven in production.

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """
    Split text into overlapping chunks.
    Prefers paragraph boundaries (double newline) so table rows and sentence
    groups stay intact. Falls back to hard char-count split only when a single
    paragraph exceeds chunk_size.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para)
        if para_len > chunk_size:
            if current:
                chunks.append("\n\n".join(current))
                current = [current[-1]] if current else []
                current_len = len(current[0]) if current else 0
            start = 0
            while start < para_len:
                end = start + chunk_size
                chunks.append(para[start:end])
                start += chunk_size - overlap
            tail = para[max(0, para_len - overlap):]
            current = [tail]
            current_len = len(tail)
        elif current_len + para_len + 2 > chunk_size:
            chunks.append("\n\n".join(current))
            overlap_seed = current[-1] if current else ""
            current = [overlap_seed, para] if overlap_seed else [para]
            current_len = sum(len(p) for p in current) + 2 * max(0, len(current) - 1)
        else:
            current.append(para)
            current_len += para_len + 2

    if current:
        chunks.append("\n\n".join(current))

    return [c for c in chunks if c.strip()]
