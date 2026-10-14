"""Run: venv\\Scripts\\python.exe -m uvicorn main:app --port 8000"""
import re
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from chatbot import answer

app = FastAPI(title="NEDi")
app.add_middleware(CORSMiddleware,
                   allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
                   allow_methods=["*"], allow_headers=["*"])


class ChatRequest(BaseModel):
    question: str
    history: list[dict] = []   # [{"role": "user"|"assistant", "content": "..."}]


@app.post("/api/chat")
def chat(req: ChatRequest):
    hist = [("User" if m.get("role") == "user" else "Bot", m.get("content", ""))
            for m in req.history]
    text, cites = answer(req.question, hist)
    pages = []
    for c in cites:                      # "Page 5 (PDF p.7)" -> 7
        m = re.search(r"PDF p\.(\d+)", c)
        if m:
            pages.append(int(m.group(1)))
    return {"answer": text, "sources": [{"page": p} for p in sorted(set(pages))]}


@app.get("/api/health")
def health():
    return {"ok": True}