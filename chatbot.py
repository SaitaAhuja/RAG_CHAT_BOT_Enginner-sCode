"""chatbot.py - answer(question, history) -> (text, citations). The UI only calls this function."""
import re
import time
import threading

from llm import call_gemini
from prompts import (BOT_NAME, SYSTEM_PROMPT, NOT_FOUND, GREETING, THANKS, FRIENDLY_ERROR, DISCLAIMER)

FEE_WORDS = re.compile(r"\b(fee|fees|charge|charges|deposit|rs\.?|deadline|last date|date|dates|kitni|kitna|tareekh)\b", re.I)
ROMAN_URDU_WORDS = {
    "kya", "kitni", "kitna", "kitne", "hai", "hain", "ka", "ki", "ke", "mein", "me", "kaise", "kese",
    "nahi", "nahin", "aap", "mujhe", "batao", "bataye", "bataiye", "karna", "karni", "chahiye", "kab",
    "kahan", "kon", "kaun", "tum", "mera", "meri", "hoga", "hogi", "sakta", "sakti", "wala", "wali",
    "shukriya", "shukria", "salam", "salaam", "assalam", "alaikum", "hafiz",
}
HELLO_RE = re.compile(r"^(hi|hello|hey|salam|salaam|assalam o alaikum|assalamualaikum|aoa|asalam o alaikum)\W*$", re.I)
THANKS_RE = re.compile(r"^(thanks|thank you|thankyou|shukriya|shukria|jazakallah|ok thanks|bye|khuda hafiz|allah hafiz)\W*$", re.I)


def detect_lang(text):
    """'ur' = Urdu script, 'ru' = Roman Urdu, 'en' = English."""
    if re.search(r"[\u0600-\u06FF]", text):
        return "ur"
    words = set(re.findall(r"[a-z]+", text.lower()))
    return "ru" if len(words & ROMAN_URDU_WORDS) >= 1 else "en"


def _citations(chunks):
    seen = []
    for c in chunks:
        m = c["meta"]
        label = f"Page {m['printed_page']} (PDF p.{m['pdf_page']})" if m.get("printed_page") else f"PDF p.{m['pdf_page']}"
        if label not in seen:
            seen.append(label)
    return seen


def answer(question, history):
    """history = list of (role, text), role is 'User' or 'Bot'. Returns (text, list of page labels)."""
    question = question.strip()
    lang = detect_lang(question)

    # greetings / thanks: no search needed
    if HELLO_RE.match(question):
        return GREETING[lang].replace("{BOT_NAME}", BOT_NAME), []
    if THANKS_RE.match(question):
        return THANKS[lang], []

    # imported here so greetings and tests work even before the database is built
    from retriever import rewrite_query, retrieve, relevant

    t0 = time.time()
    # greetings and thanks are not real context, so they should not trigger an extra rewrite call
    fixed = {m.replace("{BOT_NAME}", BOT_NAME) for d in (GREETING, THANKS) for m in d.values()}
    real_history = [(r, t) for r, t in history
                    if t not in fixed and not (r == "User" and (HELLO_RE.match(t) or THANKS_RE.match(t)))]
    print(f"[bot] question={question!r} lang={lang}", flush=True)
    search_query = rewrite_query(question, real_history, lang)
    t_rewrite = time.time() - t0
    print(f"[bot] search query: {search_query!r} (rewrite {t_rewrite:.1f}s)", flush=True)
    t1 = time.time()
    chunks = relevant(retrieve(search_query))
    t_search = time.time() - t1

    # GATE: nothing relevant -> do NOT call the model, return the fixed "not found" message
    if not chunks:
        print(f"[bot] NO relevant chunks -> NOT_FOUND ({time.time() - t0:.1f}s)", flush=True)
        return NOT_FOUND[lang], []

    context = "\n\n---\n\n".join(c["text"] for c in chunks)
    system = SYSTEM_PROMPT.replace("{BOT_NAME}", BOT_NAME).replace("{CONTEXT}", context)
    hist = "\n".join(f"{role}: {text}" for role, text in history[-6:])
    prompt = (f"Conversation so far:\n{hist}\n\n" if hist else "") + f"User question: {question}"

    t2 = time.time()
    text = call_gemini(prompt, system=system, temperature=0.1)
    if text is None:
        return FRIENDLY_ERROR[lang], []

    print(f"[bot] TOTAL {time.time() - t0:.1f}s = rewrite {t_rewrite:.1f}s + search {t_search:.1f}s "
          f"+ answer {time.time() - t2:.1f}s ({len(chunks)} chunks)", flush=True)
    if FEE_WORDS.search(question + " " + text):
        text += "\n\n" + DISCLAIMER[lang]
    return text, _citations(chunks)


def _warm_up():
    """Loads the search model in the background when the server starts, so the first question is fast."""
    try:
        import retriever
        retriever.preload()
    except Exception as e:  # noqa: BLE001  (e.g. database not built yet: not a problem)
        print(f"[bot] warm-up skipped: {e}", flush=True)


threading.Thread(target=_warm_up, daemon=True).start()