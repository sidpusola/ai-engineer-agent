# AI Software Engineer Agent

Not a chatbot — an **autonomous agent loop** that uses your local **Qwen2.5-Coder-14B**
to plan, write, run, and *self-correct* a Python **project (one or more files)** until it
actually works.

```
task ─▶ Planner ─▶ Code Generator ─▶ File Writer ─▶ Runner (main.py)
                                                       │
                       success ◀── pass? ──────────────┤
                                                       │ fail
                                  Fixer ◀── Error Analyzer
                                    └────────▶ (loop, up to 5 attempts)
```

Each arrow is a real step:
| Stage             | What happens                                                            |
|-------------------|-------------------------------------------------------------------------|
| **Planner**       | Qwen writes a short plan, including which files the project needs.       |
| **Code Generator**| Qwen emits one or more files, each tagged `FILE: <path>`, with a `main.py` entry point. |
| **File Writer**   | All files are written into `workspace/`.                                |
| **Runner**        | `main.py` is executed in a subprocess (30s timeout).                    |
| **Error Analyzer / Fixer** | On failure, all files + the traceback go back to Qwen, which returns the corrected project. The loop repeats until the run exits cleanly. |

## Two ways to run it

### 1. Web UI — watch the pipeline live (recommended)
```powershell
& "$env:USERPROFILE\miniconda3\envs\tf-2.10\python.exe" web.py
```
Then open **http://127.0.0.1:8100**. Type a task and watch each stage — Planner,
Code Generator, File Writer, Runner, and every Error Analyzer → Fixer retry —
stream into the browser as cards, with stdout/stderr and pass/fail badges.
Or double-click **`run-web.bat`**.

### 2. Command line
```powershell
# one-shot
& "$env:USERPROFILE\miniconda3\envs\tf-2.10\python.exe" agent.py "print the first 20 prime numbers"

# or interactive
& "$env:USERPROFILE\miniconda3\envs\tf-2.10\python.exe" agent.py
```
Or double-click **`run-agent.bat`** (and pass the task in quotes).

The finished project is left in **`workspace/`** either way.

## Files
| File          | Purpose                                                      |
|---------------|--------------------------------------------------------------|
| `llm.py`      | The Qwen "brain" — loads the model, streams tokens.          |
| `agent.py`    | CLI agent + the shared prompts and run/loop helpers.         |
| `web.py`      | FastAPI server that streams the pipeline as live events.     |
| `web.html`    | The live pipeline web UI.                                    |
| `workspace/`  | Where the generated project files are written and `main.py` is run. |

## Notes & tuning (in `agent.py`)
- `MAX_ATTEMPTS` — how many self-correction rounds (default 5).
- `RUN_TIMEOUT` — seconds before a run is killed as a likely infinite loop (default 30).
- Qwen-3B is small, so very hard tasks may not converge — but you'll *see* it
  try, fail, read its own error, and fix itself, which is the whole point.

## Heads-up on GPU memory
This loads Qwen on your GPU. If your **chat website** (`my own gpt`) is still
running, stop it first so both don't fight over VRAM.
