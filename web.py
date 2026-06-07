"""
Web UI for the AI Software Engineer Agent (multi-file projects).

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
    extract_project,
    extract_files,
    files_to_text,
    write_project,
    run_project,
    MAX_ATTEMPTS,
    _slug,
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

    # ---- Code generator (+ tests) ----
    yield sse({"t": "stage", "kind": "codegen", "label": "Code Generator (+ tests)"})
    cg_user = f"Task:\n{task}\n\nPlan:\n{plan}\n\nWrite the complete project with tests."
    reply = ""
    for tok in llm.stream(CODEGEN_SYS, cg_user, max_new_tokens=1800, temperature=0.2):
        reply += tok
        yield sse({"t": "token", "text": tok})
    name, files = extract_project(reply, fallback=_slug(task))

    # ---- Build / run-tests / fix loop ----
    for attempt in range(1, MAX_ATTEMPTS + 1):
        proj = write_project(name, files)
        yield sse({"t": "files", "project": name,
                   "files": [{"path": p, "size": len(c)} for p, c in files.items()]})

        yield sse({"t": "run", "attempt": attempt})
        ok, out, err = run_project(proj)
        if out.strip():
            yield sse({"t": "stdout", "text": out.rstrip()})
        if err.strip():
            yield sse({"t": "stderr", "text": err.rstrip()})
        yield sse({"t": "verdict", "ok": ok})

        if ok:
            yield sse({
                "t": "done", "success": True, "attempts": attempt,
                "elapsed": round(time.time() - started, 1), "files": len(files),
            })
            return

        if attempt < MAX_ATTEMPTS:
            yield sse({"t": "stage", "kind": "fixer",
                       "label": "Error Analyzer → Fixer", "attempt": attempt})
            fx_user = (
                f"Task:\n{task}\n\nCurrent project:\n{files_to_text(files)}\n\n"
                f"Test/run output:\n{err}\n\nFix the code so the tests pass and return the full corrected project."
            )
            reply = ""
            for tok in llm.stream(FIXER_SYS, fx_user, max_new_tokens=1800, temperature=0.2):
                reply += tok
                yield sse({"t": "token", "text": tok})
            files = extract_files(reply) or files

    yield sse({
        "t": "done", "success": False, "attempts": MAX_ATTEMPTS,
        "elapsed": round(time.time() - started, 1), "files": len(files),
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
                           "elapsed": 0, "files": 0})
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
