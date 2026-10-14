"""bm25.py - small keyword search (no extra install). Used together with the meaning-based search."""
import math
import re
from collections import Counter

STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are", "was", "be", "do", "does",
    "what", "which", "who", "how", "when", "where", "can", "i", "me", "my", "we", "you", "your", "it",
    "its", "this", "that", "with", "at", "by", "from", "as", "about", "tell", "please", "much", "many",
    "there", "any", "have", "has", "get", "need", "mention", "mentioned", "required", "ka", "ki", "ke",
}


def tokenize(text):
    """lowercase words, no stopwords, tiny stemmer (fees -> fee, programmes -> programme)."""
    out = []
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if w in STOPWORDS:
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


class BM25:
    def __init__(self, documents, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.docs = [Counter(tokenize(d)) for d in documents]
        self.lens = [sum(c.values()) for c in self.docs]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 1.0
        df = Counter()
        for c in self.docs:
            df.update(c.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query, top=20):
        """Return [(doc_index, score, matched_ratio, matched_count)] best first."""
        q = list(dict.fromkeys(tokenize(query)))
        if not q:
            return []
        results = []
        for i, c in enumerate(self.docs):
            score, matched = 0.0, 0
            for t in q:
                f = c.get(t, 0)
                if f:
                    matched += 1
                    score += self.idf.get(t, 0) * f * (self.k1 + 1) / (
                        f + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avg))
            if score > 0:
                results.append((i, score, matched / len(q), matched))
        results.sort(key=lambda r: r[1], reverse=True)
        return results[:top]