"""
retriever.py - finds the chunks that match a question.

Test it by itself (prints the distances so you can tune MAX_DISTANCE):
  venv/Scripts/python.exe retriever.py "What is the admission fee for BS (CT)?"
"""
import re
import sys
import time
import threading

import chromadb
from sentence_transformers import SentenceTransformer

from bm25 import BM25
from llm import call_gemini
from prompts import REWRITE_PROMPT

CHROMA_DIR = "chroma_db"
COLLECTION = "prospectus"
EMBED_MODEL = "intfloat/multilingual-e5-base"
TOP_K = 6          # how many chunks go to the model
CANDIDATES = 20    # how many each search method proposes before they are combined

# Chunks farther than this are treated as "not relevant". This number is a STARTING GUESS for the
# e5 model. Tune it with retriever.py: real questions should pass, off-topic ones should not.
MAX_DISTANCE = 0.26

_model = None
_collection = None
_bm25 = None
_docs = None
_lock = threading.Lock()
FOLLOWUP_RE = re.compile(r"\b(it|its|that|this|they|their|them|those|these|same|also|and|then|about)\b", re.I)


def _load():
    global _model, _collection, _bm25, _docs
    with _lock:
        return _load_locked()


def _load_locked():
    global _model, _collection, _bm25, _docs
    if _model is None:
        t0 = time.time()
        print("[retriever] loading embedding model + database (first question only)...", flush=True)
        _model = SentenceTransformer(EMBED_MODEL)
        _collection = chromadb.PersistentClient(path=CHROMA_DIR).get_collection(COLLECTION)
        everything = _collection.get(include=["documents", "metadatas"])
        _docs = list(zip(everything["documents"], everything["metadatas"]))
        _bm25 = BM25([d for d, _ in _docs])   # keyword index, built in a fraction of a second
        print(f"[retriever] ready in {time.time() - t0:.1f}s, {_collection.count()} chunks", flush=True)
    return _model, _collection


def preload():
    """Call this once when the server starts, so the first question is not slow."""
    _load()


def rewrite_query(question, history, lang):
    """Turn Roman Urdu / Urdu questions and follow-ups into one standalone English search query."""
    # English questions are searched as they are, unless they look like a follow-up
    # ("and its fee?"). Roman Urdu / Urdu always need an English search query.
    if lang == "en" and not (history and (len(question.split()) <= 5 or FOLLOWUP_RE.search(question))):
        return question  # saves one Gemini call (about 2-4 seconds)
    hist = "\n".join(f"{role}: {text}" for role, text in history[-6:]) or "(none)"
    prompt = REWRITE_PROMPT.replace("{HISTORY}", hist).replace("{QUESTION}", question)
    out = call_gemini(prompt, temperature=0.0, rounds=1)
    return out.strip().strip('"') if out else question


def retrieve(search_query):
    """Combine meaning-search (embeddings) and keyword-search (BM25), return the best TOP_K chunks.
    Each result: {text, meta, distance (None if only the keyword search found it), keyword_ok}."""
    model, col = _load()
    vec = model.encode(["query: " + search_query], normalize_embeddings=True).tolist()
    res = col.query(query_embeddings=vec, n_results=CANDIDATES)

    by_text = {}   # chunk text -> info
    for rank, (doc, meta, dist) in enumerate(zip(res["documents"][0], res["metadatas"][0], res["distances"][0])):
        by_text[doc] = {"text": doc, "meta": meta, "distance": dist, "keyword_ok": False, "score": 1 / (60 + rank)}

    for rank, (i, _score, ratio, matched) in enumerate(_bm25.search(search_query, top=CANDIDATES)):
        doc, meta = _docs[i]
        info = by_text.setdefault(doc, {"text": doc, "meta": meta, "distance": None, "keyword_ok": False, "score": 0.0})
        info["score"] += 1 / (60 + rank)
        # keyword match counts as "relevant" only if most of the question's words are in the chunk
        if ratio >= 0.6 and (matched >= 2 or ratio == 1.0):
            info["keyword_ok"] = True

    found = sorted(by_text.values(), key=lambda c: c["score"], reverse=True)[:TOP_K]
    ok = sum(1 for c in found if is_relevant(c))
    best = min((c["distance"] for c in found if c["distance"] is not None), default=None)
    print(f"[retriever] best meaning-distance {best if best is None else round(best, 3)}, "
          f"relevant chunks: {ok}/{len(found)}", flush=True)
    return found


def is_relevant(c):
    return (c["distance"] is not None and c["distance"] <= MAX_DISTANCE) or c["keyword_ok"]


def relevant(found):
    return [c for c in found if is_relevant(c)]


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "What is the application processing fee?"
    results = retrieve(q)
    print(f"MAX_DISTANCE = {MAX_DISTANCE}\n")
    for c in results:
        flag = "PASS" if is_relevant(c) else "drop"
        d = "none (keyword only)" if c["distance"] is None else f"{c['distance']:.3f}"
        print(f"[{flag}] distance {d} keyword_ok={c['keyword_ok']} | {c['text'][:110]!r}")