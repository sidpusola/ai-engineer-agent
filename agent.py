"""
AI Software Engineer Agent (single-file Python tasks).

An autonomous loop powered by your local Qwen model:

    task -> Plan -> Generate code -> Write file -> Run
                                                    |
                              success  <-- pass? ---+--- fail --> Analyze error -> Fix --> (loop)

Usage:
    python agent.py "write a program that prints the first 20 prime numbers"
    python agent.py            # then type your task at the prompt
"""

import os
import re
import sys
import time
import subprocess
from pathlib import Path

# Make sure the Windows console can print emojis / box-drawing characters.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from llm import LLM

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
WORKDIR = Path(__file__).parent / "workspace"
WORKDIR.mkdir(exist_ok=True)
SOLUTION = WORKDIR / "solution.py"

MAX_ATTEMPTS = 5      # how many fix iterations before giving up
RUN_TIMEOUT = 30      # seconds; guards against infinite loops

# --------------------------------------------------------------------------
# Terminal colors
# --------------------------------------------------------------------------
os.system("")  # enable ANSI escape codes on Windows terminals
class C:
    RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
    CYAN = "\033[96m"; GREEN = "\033[92m"; YELLOW = "\033[93m"
    RED = "\033[91m"; MAGENTA = "\033[95m"; BLUE = "\033[94m"; GREY = "\033[90m"


def banner(step, title, color):
    line = "─" * (58 - len(title))
    print(f"\n{color}{C.BOLD}┃ {step}  {title} {C.RESET}{color}{line}{C.RESET}")


def info(msg):
    print(f"{C.GREY}{msg}{C.RESET}")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def extract_code(text: str) -> str:
    """Pull the Python code out of the model's reply."""
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL)
    if blocks:
        # If several blocks, keep the longest (usually the full solution).
        return max(blocks, key=len).strip()
    return text.strip()


def run_solution():
    """Execute the solution file. Returns (ok, stdout, stderr)."""
    try:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.run(
            [sys.executable, str(SOLUTION)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=RUN_TIMEOUT,
            cwd=str(WORKDIR),
            env=env,
        )
        ok = proc.returncode == 0
        return ok, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return False, "", f"TimeoutError: did not finish within {RUN_TIMEOUT}s (possible infinite loop)."


# --------------------------------------------------------------------------
# Agent stages (each one is a Qwen call)
# --------------------------------------------------------------------------
PLANNER_SYS = (
    "You are a senior software engineer. Given a task, write a short, concrete "
    "plan (3-6 bullet points) for solving it in a SINGLE self-contained Python "
    "file. Be brief. Do NOT write any code yet."
)

CODEGEN_SYS = (
    "You are an expert Python developer. Write ONE complete, self-contained "
    "Python file that fully solves the task. Requirements:\n"
    "- All code in a single file, standard library only unless clearly needed.\n"
    "- Include an `if __name__ == \"__main__\":` block that DEMONSTRATES the "
    "solution by printing results, so running the file proves it works.\n"
    "- Output ONLY one Python code block, nothing else."
)

FIXER_SYS = (
    "You are an expert Python debugger. You are given a task, the current code, "
    "and the error it produced when executed. Briefly state the cause in one "
    "line as a comment, then output the COMPLETE corrected single Python file. "
    "Output ONLY one Python code block."
)


def plan(llm, task):
    banner("①", "PLANNER", C.CYAN)
    return llm.generate(PLANNER_SYS, f"Task:\n{task}", max_new_tokens=400, temperature=0.4)


def generate_code(llm, task, plan_text):
    banner("②", "CODE GENERATOR", C.BLUE)
    user = f"Task:\n{task}\n\nPlan:\n{plan_text}\n\nWrite the complete Python file."
    reply = llm.generate(CODEGEN_SYS, user, max_new_tokens=1300, temperature=0.2)
    return extract_code(reply)


def fix_code(llm, task, code, error):
    banner("⟳", "ERROR ANALYZER → FIXER", C.MAGENTA)
    user = (
        f"Task:\n{task}\n\n"
        f"Current code:\n```python\n{code}\n```\n\n"
        f"Error when run:\n{error}\n\n"
        "Fix the bug and return the full corrected file."
    )
    reply = llm.generate(FIXER_SYS, user, max_new_tokens=1300, temperature=0.2)
    return extract_code(reply)


def write_file(code):
    banner("③", "FILE WRITER", C.YELLOW)
    SOLUTION.write_text(code, encoding="utf-8")
    info(f"wrote {len(code)} chars → {SOLUTION}")


def run_stage(attempt):
    banner("④", f"RUNNER  (attempt {attempt})", C.GREEN)
    ok, out, err = run_solution()
    if out.strip():
        print(f"{C.DIM}--- stdout ---{C.RESET}\n{out.rstrip()}")
    if err.strip():
        print(f"{C.RED}--- stderr ---{C.RESET}\n{err.rstrip()}")
    return ok, out, err


# --------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------
def solve(task: str):
    print(f"\n{C.BOLD}🛠  AI Software Engineer Agent{C.RESET}")
    print(f"{C.DIM}task:{C.RESET} {task}")

    started = time.time()
    llm = LLM()

    plan_text = plan(llm, task)
    code = generate_code(llm, task, plan_text)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        write_file(code)
        ok, out, err = run_stage(attempt)

        if ok:
            elapsed = time.time() - started
            banner("✅", "SUCCESS", C.GREEN)
            print(
                f"{C.GREEN}Solved in {attempt} run(s), {elapsed:.1f}s.{C.RESET}\n"
                f"Final file: {C.BOLD}{SOLUTION}{C.RESET}"
            )
            return True

        if attempt < MAX_ATTEMPTS:
            info(f"\nrun failed — sending the error back to Qwen to self-correct "
                 f"({attempt}/{MAX_ATTEMPTS - 1} fixes used)…")
            code = fix_code(llm, task, code, err)

    banner("❌", "GAVE UP", C.RED)
    print(f"{C.RED}Could not get a clean run after {MAX_ATTEMPTS} attempts.{C.RESET}")
    print(f"Last attempt saved at: {SOLUTION}")
    return False


def main():
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
    else:
        task = input("Describe the program you want: ").strip()
    if not task:
        print("No task given. Exiting.")
        return
    solve(task)


if __name__ == "__main__":
    main()
