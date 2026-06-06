# AI Software Engineer Agent

Not a chatbot — an **autonomous agent loop** that uses your local **Qwen2.5-Coder-3B**
to plan, write, run, and *self-correct* Python code until it actually works.

```
task ─▶ Planner ─▶ Code Generator ─▶ File Writer ─▶ Runner
                                                       │
                       success ◀── pass? ──────────────┤
                                                       │ fail
                                  Fixer ◀── Error Analyzer
                                    └────────▶ (loop, up to 5 attempts)
```

Each arrow is a real step:
| Stage             | What happens                                                            |
|-------------------|-------------------------------------------------------------------------|
| **Planner**       | Qwen writes a short plan for a single-file solution.                    |
| **Code Generator**| Qwen writes a complete `.py` file with a runnable demo.                 |
| **File Writer**   | The code is saved to `workspace/solution.py`.                          |
| **Runner**        | The file is executed in a subprocess (30s timeout).                    |
| **Error Analyzer / Fixer** | On failure, the code + traceback go back to Qwen, which returns a corrected file. The loop repeats until the run exits cleanly. |

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

The finished program is left in **`workspace/solution.py`** either way.

## Files
| File          | Purpose                                                      |
|---------------|--------------------------------------------------------------|
| `llm.py`      | The Qwen "brain" — loads the model, streams tokens.          |
| `agent.py`    | CLI agent + the shared prompts and run/loop helpers.         |
| `web.py`      | FastAPI server that streams the pipeline as live events.     |
| `web.html`    | The live pipeline web UI.                                    |
| `workspace/`  | Where the generated `solution.py` is written and run.        |

## Notes & tuning (in `agent.py`)
- `MAX_ATTEMPTS` — how many self-correction rounds (default 5).
- `RUN_TIMEOUT` — seconds before a run is killed as a likely infinite loop (default 30).
- Qwen-3B is small, so very hard tasks may not converge — but you'll *see* it
  try, fail, read its own error, and fix itself, which is the whole point.

## Heads-up on GPU memory
This loads Qwen on your GPU. If your **chat website** (`my own gpt`) is still
running, stop it first so both don't fight over VRAM.
