"""Fetch the GDPR (CELEX 32016R0679) and split it into citable chunks."""

import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

# EUR-Lex answers scripts with a bot challenge; the Publications Office serves the same OJ text.
URL = "http://publications.europa.eu/resource/celex/32016R0679"
H = "{http://www.w3.org/1999/xhtml}"
MAX_WORDS = 350  # bge-small truncates at 512 tokens


@dataclass
class Chunk:
    label: str  # "Art. 6(1)(f)", "Recital 47"
    ref: str  # element id in the source document, e.g. "006.001"
    heading: str  # "Chapter II Principles > Article 6 Lawfulness of processing"
    text: str


def fetch(dest: Path) -> bytes:
    if not dest.exists():
        req = urllib.request.Request(
            URL, headers={"Accept": "application/xhtml+xml", "Accept-Language": "eng"}
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return dest.read_bytes()


def _text(el: ET.Element) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _split(label: str, ref: str, heading: str, el: ET.Element, max_words: int) -> list[Chunk]:
    parts = [c for c in el if c.get("class") not in ("oj-ti-art", "eli-title")]
    whole = Chunk(label, ref, heading, " ".join(_text(c) for c in parts))
    tables = [c for c in parts if c.tag == H + "table"]
    # shortcut: long text without lettered points stays one chunk (embedding truncates), fine for GDPR
    if len(whole.text.split()) <= max_words or not tables:
        return [whole]
    first = parts.index(tables[0])
    lead = " ".join(_text(c) for c in parts[:first])
    out = []
    for c in parts[first:]:
        if c.tag == H + "table":
            point = _text(c.find(f".//{H}td"))
            sub = point.strip("()") if re.fullmatch(r"\(\w+\)", point) else point
            out.append(Chunk(f"{label}({sub})", ref, heading, f"{lead} {_text(c)}".strip()))
        else:  # closing sentence after the points: keep the lead-in so it reads in context
            out.append(Chunk(label, ref, heading, f"{lead} [...] {_text(c)}".strip()))
    return out


def parse(xhtml: bytes, max_words: int = MAX_WORDS) -> list[Chunk]:
    root = ET.fromstring(xhtml)
    divs = {d.get("id"): d for d in root.iter(H + "div") if d.get("id")}
    chunks = [
        Chunk(f"Recital {m[1]}", id_, "Recitals", re.sub(r"^\(\d+\)\s*", "", _text(d)))
        for id_, d in divs.items()
        if (m := re.fullmatch(r"rct_(\d+)", id_))
    ]
    for cid, chapter in divs.items():
        if not re.fullmatch(r"cpt_[IVX]+", cid):
            continue
        title = divs.get(f"{cid}.tit_1")
        chap = f"Chapter {cid[4:]} {_text(title) if title is not None else ''}".strip()
        for art in chapter.iter(H + "div"):
            aid = art.get("id", "")
            if not re.fullmatch(r"art_\d+", aid):
                continue
            n = int(aid[4:])
            heading = f"{chap} > Article {n} {_text(divs[aid + '.tit_1'])}"
            paras = [c for c in art if re.fullmatch(r"\d{3}\.\d{3}", c.get("id", ""))]
            if not paras:
                chunks += _split(f"Art. {n}", aid, heading, art, max_words)
            for p in paras:
                label = f"Art. {n}({int(p.get('id')[-3:])})"
                chunks += _split(label, p.get("id"), heading, p, max_words)
    return chunks
