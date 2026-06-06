"""
Web UI for the AI Software Engineer Agent.

Runs the same autonomous loop as agent.py, but streams every stage
(Planner → CodeGen → FileWriter → Runner → Error Analyzer → Fixer → Success)
to the browser live as newline-delimited JSON events.

Run:  python web.py   →   http://127.0.0.1:8100
"""

import json
import time
import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel

from llm import LLM
# Reuse the exact prompts + helpers the CLI agent uses, so behaviour matches.
from agent import (
    PLANNER_SYS,
    CODEGEN_SYS,
    FIXER_SYS,
    extract_code,
    run_solution,
    SOLUTION,
    MAX_ATTEMPTS,
    RUN_TIMEOUT,
)

llm = LLM()                 # loads Qwen once at startup
run_lock = threading.Lock() # only one generation at a time (single GPU)

app = FastAPI(title="AI Software Engineer Agent")


class RunRequest(BaseModel):
    task: str


def sse(event: dict) -> str:
    return json.dumps(event) + "\n"


def agent_events(task: str):
    """Generator that runs the agent loop and yields UI events as it goes."""
    started = time.time()

    # ---- Planner ----
    yield sse({"t": "stage", "kind": "planner", "label": "Planner"})
    plan = ""
    for tok in llm.stream(PLANNER_SYS, f"Task:\n{task}", max_new_tokens=400, temperature=0.4):
        plan += tok
        yield sse({"t": "token", "text": tok})

    # ---- Code generator ----
    yield sse({"t": "stage", "kind": "codegen", "label": "Code Generator"})
    cg_user = f"Task:\n{task}\n\nPlan:\n{plan}\n\nWrite the complete Python file."
    reply = ""
    for tok in llm.stream(CODEGEN_SYS, cg_user, max_new_tokens=1300, temperature=0.2):
        reply += tok
        yield sse({"t": "token", "text": tok})
    code = extract_code(reply)

    # ---- Build / run / fix loop ----
    for attempt in range(1, MAX_ATTEMPTS + 1):
        SOLUTION.write_text(code, encoding="utf-8")
        yield sse({"t": "file", "path": str(SOLUTION), "size": len(code), "code": code})

        yield sse({"t": "run", "attempt": attempt})
        ok, out, err = run_solution()
        if out.strip():
            yield sse({"t": "stdout", "text": out.rstrip()})
        if err.strip():
            yield sse({"t": "stderr", "text": err.rstrip()})
        yield sse({"t": "verdict", "ok": ok})

        if ok:
            yield sse({
                "t": "done", "success": True, "attempts": attempt,
                "elapsed": round(time.time() - started, 1), "file": str(SOLUTION),
            })
            return

        if attempt < MAX_ATTEMPTS:
            yield sse({"t": "stage", "kind": "fixer",
                       "label": "Error Analyzer → Fixer", "attempt": attempt})
            fx_user = (
                f"Task:\n{task}\n\nCurrent code:\n```python\n{code}\n```\n\n"
                f"Error when run:\n{err}\n\nFix the bug and return the full corrected file."
            )
            reply = ""
            for tok in llm.stream(FIXER_SYS, fx_user, max_new_tokens=1300, temperature=0.2):
                reply += tok
                yield sse({"t": "token", "text": tok})
            code = extract_code(reply)

    yield sse({
        "t": "done", "success": False, "attempts": MAX_ATTEMPTS,
        "elapsed": round(time.time() - started, 1), "file": str(SOLUTION),
    })


@app.post("/api/run")
def run(req: RunRequest):
    task = req.task.strip()

    def stream():
        if not run_lock.acquire(blocking=False):
            yield sse({"t": "busy"})
            return
        try:
            if not task:
                yield sse({"t": "done", "success": False, "attempts": 0,
                           "elapsed": 0, "file": ""})
                return
            yield from agent_events(task)
        finally:
            run_lock.release()

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "web.html")


if __name__ == "__main__":
    import uvicorn
    print("\nOpen http://127.0.0.1:8100 in your browser\n")
    uvicorn.run(app, host="127.0.0.1", port=8100)
