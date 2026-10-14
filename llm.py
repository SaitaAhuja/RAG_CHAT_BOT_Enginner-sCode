"""llm.py - one small helper that calls Gemini (main model, then fallback model)."""
import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

MAIN_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")
FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.8-flash")
WAITS = [2, 5]        # seconds to wait before retrying a busy (503) or rate-limited (429) call
TIMEOUT_MS = 40000    # a call that hangs longer than 40 seconds is cancelled

_client = None
_fast_ok = True       # "thinking off" makes answers faster; switched off automatically if the model refuses it


def _get_client():
    global _client
    if _client is None:
        key = os.getenv("GEMINI_API_KEY")
        try:
            _client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=TIMEOUT_MS))
        except Exception as e:  # noqa: BLE001
            print(f"[llm] timeout option not supported, continuing without it ({e})", flush=True)
            _client = genai.Client(api_key=key)
    return _client


def _config(system, temperature, fast):
    kwargs = {"system_instruction": system, "temperature": temperature}
    if fast:
        try:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        except Exception:  # noqa: BLE001  (old library version)
            pass
    return types.GenerateContentConfig(**kwargs)


def call_gemini(prompt, system=None, temperature=0.1, rounds=None):
    """Return the model's text, or None if every try failed (the caller shows a friendly message).
    rounds=1 means: no waiting/retrying on a busy model, just try each model once (used for quick helper calls)."""
    global _fast_ok
    models = [MAIN_MODEL] + ([FALLBACK_MODEL] if FALLBACK_MODEL and FALLBACK_MODEL != MAIN_MODEL else [])
    for model in models:
        max_attempts = 2 if rounds == 1 else len(WAITS) + 2   # (2 = one extra try if 'thinking off' is refused)
        for attempt in range(max_attempts):
            t0 = time.time()
            try:
                res = _get_client().models.generate_content(
                    model=model, contents=prompt, config=_config(system, temperature, _fast_ok))
                text = (res.text or "").strip()
                if text:
                    print(f"[llm] {model}: OK ({time.time() - t0:.1f}s)", flush=True)
                    return text
                print(f"[llm] {model}: EMPTY answer", flush=True)
                break
            except Exception as e:  # noqa: BLE001
                code = getattr(e, "code", None)
                print(f"[llm] {model} attempt {attempt + 1} FAILED after {time.time() - t0:.1f}s: "
                      f"{type(e).__name__} {code}", flush=True)
                if code == 400 and _fast_ok:
                    _fast_ok = False   # this model dislikes the "thinking off" setting -> try normally
                    print("[llm] turning the fast setting off and retrying", flush=True)
                    continue
                if code in (503, 429) and rounds != 1 and attempt < len(WAITS):
                    time.sleep(WAITS[attempt])
                    continue
                break  # 404, timeout or other error -> try the fallback model
    print("[llm] ALL MODELS FAILED -> the user gets the friendly error message", flush=True)
    return None