"""
ingest.py - builds the searchable database (run once, and again after any data change).

Text source:
  1. data/pages_vision/page_XXX.md   (Gemini vision text, used when the file exists)
  2. data/prospectus_ocr.json        (OCR text for all other pages)

Run:  venv/Scripts/python.exe ingest.py
"""
import os
import re
import json
import glob

from langchain_text_splitters import RecursiveCharacterTextSplitter

OCR_JSON = "data/prospectus_ocr.json"
VISION_DIR = "data/pages_vision"
CHROMA_DIR = "chroma_db"
COLLECTION = "prospectus"
EMBED_MODEL = "intfloat/multilingual-e5-base"

TARGET = 700      # try to fill chunks up to about this many characters
MAX_CHARS = 950   # never go above this (one long block is split)
MIN_CHARS = 80    # drop tiny chunks (page numbers, photo captions)

# a new clause starts at lines like "4.2.2", "8.1.1.", "R-1(f)", "SF-9(c)"
CLAUSE_RE = re.compile(r"^(\d{1,2}(\.\d{1,2}){1,3}\.?\s|(R|SF)-\d{1,2}(\([a-z]\))?[\s:.])")
HEADING_RE = re.compile(r"^(#{1,6}\s+[A-Za-z0-9][^#]*|\d{1,2}(\.\d{1,2}){1,2}\.?\s+[A-Z][A-Z \-&/,()]{3,})$")


def is_heading(line):
    """A heading must match the pattern AND contain at least 4 letters (rejects OCR junk like '# 3')."""
    return bool(HEADING_RE.match(line)) and len(re.findall(r"[A-Za-z]", line)) >= 4

# running header/footer lines to remove from vision pages
JUNK_RE = re.compile(
    r"^(#+\s*)?(NED UNIVERSITY OF ENGINEERING & TECHNOLOGY|UNDERGRADUATE PROSPECTUS 2025)\s*$"
    r"|^www\.neduet\.edu\.pk\s*$|^NEDUET Official\s*$|^<!--.*-->\s*$|^```.*$",
    re.IGNORECASE,
)


def clean_vision_text(text, printed_page):
    lines = []
    for line in text.split("\n"):
        s = line.strip()
        if JUNK_RE.match(s):
            continue
        if printed_page is not None and s == str(printed_page):  # bare page number
            continue
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def load_pages():
    """Return a list of dicts: pdf_page, printed_page, text, source."""
    with open(OCR_JSON, "r", encoding="utf-8") as f:
        ocr = json.load(f)["pages"]
    pages = []
    for p in ocr:
        pdf_page = p["pdf_page"]
        printed = p.get("printed_page")
        text, source = p["text"], "ocr"
        vpath = os.path.join(VISION_DIR, f"page_{pdf_page:03d}.md")
        if os.path.exists(vpath):
            with open(vpath, "r", encoding="utf-8") as vf:
                vtext = vf.read()
            if "[FAILED" not in vtext and len(vtext) > 80:
                text, source = clean_vision_text(vtext, printed), "vision"
        pages.append({"pdf_page": pdf_page, "printed_page": printed, "text": text, "source": source})
    return pages


def split_blocks(text):
    """Cut the page into blocks: each clause / heading / table is one block."""
    blocks, cur, in_table = [], [], False
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        is_table_line = s.startswith("|")
        starts_new = bool(CLAUSE_RE.match(s) or is_heading(s))
        if is_table_line != in_table:       # table begins or ends
            starts_new = True
        if starts_new and cur:
            blocks.append("\n".join(cur))
            cur = []
        cur.append(s)
        in_table = is_table_line
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def split_long_table(block):
    """A table too long for one chunk is split by rows; header rows repeat in every part."""
    lines = block.split("\n")
    header, rows = lines[:2], lines[2:]
    parts, cur = [], []
    for r in rows:
        if len("\n".join(header + cur + [r])) > MAX_CHARS and cur:
            parts.append("\n".join(header + cur))
            cur = []
        cur.append(r)
    if cur:
        parts.append("\n".join(header + cur))
    return parts


def build_chunks(pages):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=MAX_CHARS, chunk_overlap=120, separators=["\n\n", "\n", ". ", " ", ""]
    )
    chunks = []
    section = "General"
    for pg in pages:
        pdf_p, prt = pg["pdf_page"], pg["printed_page"]
        label = f"Page {prt} (PDF p.{pdf_p})" if prt else f"PDF p.{pdf_p}"
        buf, buf_section, buf_is_table = "", section, False

        def flush():
            nonlocal buf
            if len(buf.strip()) >= MIN_CHARS:
                chunks.append({
                    "text": f"[{label} | {buf_section}]\n{buf.strip()}",
                    "meta": {"pdf_page": pdf_p, "printed_page": prt or 0, "section": buf_section,
                             "type": "table" if buf_is_table else "text", "source": pg["source"]},
                })
            buf = ""

        for block in split_blocks(pg["text"]):
            first = block.split("\n")[0].strip()
            block_is_heading = is_heading(first) and "\n" not in block
            if is_heading(first):
                section = first.lstrip("# ").strip()[:90]
            is_table = block.startswith("|")
            pieces = [block]
            if len(block) > MAX_CHARS:
                pieces = split_long_table(block) if is_table else splitter.split_text(block)
            for piece in pieces:
                only_headings = bool(buf) and all(is_heading(l.strip()) for l in buf.split("\n"))
                if buf and not only_headings:
                    too_big = len(buf) + len(piece) + 1 > TARGET
                    if too_big or is_table != buf_is_table or block_is_heading:
                        flush()
                if not buf:
                    buf_section, buf_is_table = section, is_table
                elif only_headings:
                    buf_section, buf_is_table = section, is_table
                buf = (buf + "\n" + piece).strip() if buf else piece
        flush()
    return chunks


def main():
    pages = load_pages()
    chunks = build_chunks(pages)
    n_vis = sum(1 for p in pages if p["source"] == "vision")
    print(f"Pages: {len(pages)} (vision text on {n_vis} pages) -> chunks: {len(chunks)}")

    with open("chunks_preview.txt", "w", encoding="utf-8") as f:
        for i, c in enumerate(chunks):
            f.write(f"=== CHUNK {i} ({c['meta']['type']}, {c['meta']['source']}) ===\n{c['text']}\n\n")
    print("Saved chunks_preview.txt. First 5 chunks:\n")
    for c in chunks[:5]:
        print(c["text"][:300].replace("\n", " | "), "\n---")

    # embeddings are imported here so build_chunks can be tested without them
    from sentence_transformers import SentenceTransformer
    import chromadb

    model = SentenceTransformer(EMBED_MODEL)
    print("Embedding model max_seq_length (tokens):", model.max_seq_length)
    longest = max(len(c["text"]) for c in chunks)
    print("Longest chunk (characters):", longest, "(about", longest // 4, "tokens)")

    texts = ["passage: " + c["text"] for c in chunks]
    vectors = model.encode(texts, batch_size=16, show_progress_bar=True, normalize_embeddings=True)

    client = chromadb.PersistentClient(path=CHROMA_DIR)
    try:
        client.delete_collection(COLLECTION)  # rebuild from scratch, no duplicates
    except Exception:
        pass
    col = client.create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})
    for i in range(0, len(chunks), 100):
        part = chunks[i:i + 100]
        col.add(
            ids=[f"c{i + j}" for j in range(len(part))],
            documents=[c["text"] for c in part],
            embeddings=vectors[i:i + 100].tolist(),
            metadatas=[c["meta"] for c in part],
        )
    print(f"Done. {col.count()} chunks stored in {CHROMA_DIR}/")


if __name__ == "__main__":
    main()
