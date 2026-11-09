"""User uploads: PDF / DOCX / MD / TXT -> Markdown -> heading-aware chunks."""

import io
import re
from pathlib import PurePath

from .gdpr import MAX_WORDS, Chunk

EXTENSIONS = {".pdf", ".docx", ".md", ".txt"}
MAX_BYTES = 20 * 1024 * 1024
MIN_CHARS_PER_PAGE = 100  # less than this on average means a scanned PDF without a text layer


class UploadError(ValueError):
    """The file can't be used; the message is shown to the user."""


def to_markdown(filename: str, data: bytes) -> str:
    ext = PurePath(filename).suffix.lower()
    if ext not in EXTENSIONS:
        raise UploadError(f"Unsupported file type {ext or '(none)'}: use PDF, DOCX, MD or TXT.")
    if len(data) > MAX_BYTES:
        raise UploadError("File is larger than 20 MB.")
    if ext in (".md", ".txt"):
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise UploadError("Text file is not UTF-8.") from None
    if ext == ".docx":
        import docx

        try:
            doc = docx.Document(io.BytesIO(data))
        except Exception:  # noqa: BLE001 - python-docx raises many types for corrupt files
            raise UploadError("Could not read this DOCX file.") from None
        lines = []
        for p in doc.paragraphs:
            level = re.fullmatch(r"Heading (\d)", p.style.name or "")
            lines.append(f"{'#' * int(level[1])} {p.text}" if level else p.text)
        return "\n\n".join(lines)
    import pymupdf
    import pymupdf4llm

    try:
        pdf = pymupdf.open(stream=data, filetype="pdf")
    except Exception:  # noqa: BLE001 - pymupdf raises its own error types for corrupt files
        raise UploadError("Could not read this PDF file.") from None
    md = pymupdf4llm.to_markdown(pdf, show_progress=False)
    if len(re.sub(r"\s", "", md)) < MIN_CHARS_PER_PAGE * max(pdf.page_count, 1):
        raise UploadError("This PDF has almost no text (a scanned document?). OCR isn't supported.")
    return md


def chunk_markdown(md: str, title: str, max_words: int = MAX_WORDS) -> list[Chunk]:
    """One chunk per section, split into ~max_words pieces at paragraph boundaries."""
    chunks: list[Chunk] = []
    path: list[str] = []
    paras: list[str] = []

    def flush():
        section = " > ".join(path)
        heading = f"{title} > {section}" if section else title
        label = f"{title}: {path[-1]}" if path else title
        piece: list[str] = []
        for p in paras + [""]:  # sentinel flushes the last piece
            words = p.split()
            if piece and (not p or sum(len(x.split()) for x in piece) + len(words) > max_words):
                chunks.append(Chunk(label[:80], f"sec{len(chunks) + 1}", heading, "\n\n".join(piece)))
                piece = []
            # shortcut: a single paragraph longer than max_words stays whole (embedding truncates)
            if p:
                piece.append(p)
        paras.clear()

    for block in re.split(r"\n\s*\n", md):
        block = block.strip()
        if m := re.fullmatch(r"(#{1,6})\s+(.+)", block.split("\n")[0]):
            flush()
            level = len(m[1])
            path[:] = path[: level - 1] + [re.sub(r"[*_`]", "", m[2]).strip()]
            block = block.split("\n", 1)[1].strip() if "\n" in block else ""
        if block:
            paras.append(block)
    flush()
    return chunks
