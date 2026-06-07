"""
AI Software Engineer Agent (real project folders + test-driven self-correction).

An autonomous loop powered by your local Qwen model:

    task -> Plan -> Generate project (named folder, code + UNIT TESTS)
                 -> Write project folder -> Run tests (+ main.py demo)
                                                  |
                  success  <-- tests pass? -------+--- fail --> Analyze error -> Fix --> (loop)

The success signal is "the generated unit tests pass", not just "it ran".

Usage:
    python agent.py "build a fraction class with add/mul and tests"
    python agent.py            # then type your task at the prompt
"""

import os
import re
import sys
import time
import importlib.util
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
WORKDIR = Path(__file__).parent / "workspace"   # holds one folder per project
WORKDIR.mkdir(exist_ok=True)

MAX_ATTEMPTS = 5      # how many fix iterations before giving up
RUN_TIMEOUT = 60      # seconds; guards against infinite loops (tests + demo)

_HAS_PYTEST = importlib.util.find_spec("pytest") is not None

# --------------------------------------------------------------------------
# Terminal colors
# --------------------------------------------------------------------------
os.system("")  # enable ANSI escape codes on Windows terminals
class C:
    RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
    CYAN = "\033[96m"; GREEN = "\033[92m"; YELLOW = "\033[93m"
    RED = "\033[91m"; MAGENTA = "\033[95m"; BLUE = "\033[94m"; GREY = "\033[90m"


def banner(step, title, color):
    line = "─" * max(2, 58 - len(title))
    print(f"\n{color}{C.BOLD}┃ {step}  {title} {C.RESET}{color}{line}{C.RESET}")


def info(msg):
    print(f"{C.GREY}{msg}{C.RESET}")


# --------------------------------------------------------------------------
# Parsing the model output into a named project + files
# --------------------------------------------------------------------------
_FILE_RE = re.compile(
    r"FILE:\s*([^\n`]+?)\s*\n+```[a-zA-Z0-9]*\n(.*?)```",
    re.DOTALL,
)


def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s.strip().lower()).strip("_")
    return s[:40] or "project"


def _safe_path(path: str) -> str | None:
    path = path.strip().strip("`").strip().replace("\\", "/").lstrip("/")
    if not path or ".." in path.split("/") or ":" in path:
        return None
    return path


def extract_files(text: str) -> dict[str, str]:
    """Parse `FILE:`-marked code blocks into {path: content}."""
    files: dict[str, str] = {}
    for m in _FILE_RE.finditer(text):
        path = _safe_path(m.group(1))
        if path:
            files[path] = m.group(2).rstrip() + "\n"
    if not files:  # fallback: a single bare block becomes main.py
        blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL)
        if blocks:
            files["main.py"] = max(blocks, key=len).rstrip() + "\n"
    return files


def extract_project(text: str, fallback: str = "project"):
    """Return (project_name, {path: content})."""
    m = re.search(r"PROJECT:\s*([^\n`]+)", text)
    name = _slug(m.group(1)) if m else fallback
    return name, extract_files(text)


def files_to_text(files: dict[str, str]) -> str:
    return "\n".join(f"FILE: {p}\n```python\n{c}```" for p, c in files.items())


# --------------------------------------------------------------------------
# Workspace I/O + running tests
# --------------------------------------------------------------------------
def write_project(name: str, files: dict[str, str]) -> Path:
    """Write the project into workspace/<name>/, replacing its previous contents."""
    proj = WORKDIR / name
    if proj.exists():
        for f in proj.rglob("*"):
            if f.is_file():
                f.unlink()
    for path, content in files.items():
        dest = proj / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    return proj


def _run(args, cwd) -> tuple[int, str, str]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        p = subprocess.run(
            [sys.executable, *args],
            capture_output=True, text=True, encoding="utf-8",
            timeout=RUN_TIMEOUT, cwd=str(cwd), env=env,
        )
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        return 1, "", f"TimeoutError: exceeded {RUN_TIMEOUT}s (possible infinite loop)."


def run_project(proj: Path):
    """Run the unit tests (the success gate), then the main.py demo. Returns (ok, out, err)."""
    tests = list(proj.glob("test_*.py")) + list(proj.glob("*_test.py"))
    out_parts, err_parts, ok, ran = [], [], True, False

    if tests:
        ran = True
        if _HAS_PYTEST:
            rc, o, e = _run(["-m", "pytest", "-q"], proj)
            ok = ok and rc == 0
            if o.strip():
                out_parts.append("[tests]\n" + o.rstrip())
            if rc != 0 and e.strip():
                err_parts.append(e.rstrip())
        else:
            # unittest writes its report (dots / OK / FAILED) to stderr.
            rc, o, e = _run(["-m", "unittest", "discover", "-p", "test_*.py"], proj)
            ok = ok and rc == 0
            report = (e.strip() or o.strip())
            (out_parts if rc == 0 else err_parts).append("[tests]\n" + report)

    main = proj / "main.py"
    if main.exists():
        ran = True
        rc, o, e = _run(["main.py"], proj)
        ok = ok and rc == 0
        if o.strip():
            out_parts.append("[main.py]\n" + o.rstrip())
        if rc != 0 and e.strip():
            err_parts.append("[main.py]\n" + e.rstrip())

    if not ran:
        return False, "", "No tests or main.py were produced."
    return ok, "\n\n".join(out_parts), "\n\n".join(err_parts)


# --------------------------------------------------------------------------
# Agent stages (each one is a Qwen call)
# --------------------------------------------------------------------------
PLANNER_SYS = (
    "You are a senior software engineer. Given a task, write a short, concrete "
    "plan (3-6 bullet points) for solving it as a small Python project WITH unit "
    "tests. Say which files the project needs (modules, tests, main demo). Be "
    "brief. Do NOT write any code yet."
)

CODEGEN_SYS = (
    "You are an expert Python developer who practices test-driven development. "
    "Build the task as a REAL project in a named folder. Output format:\n"
    "- First line: `PROJECT: <short_snake_case_name>`\n"
    "- Then each file as a line `FILE: <relative/path>` immediately followed by a "
    "fenced code block.\n"
    "Your project MUST contain:\n"
    "1. The implementation module(s), e.g. `FILE: <module>.py`.\n"
    "2. UNIT TESTS using Python's built-in `unittest` in `FILE: test_<module>.py` "
    "that import the module and assert correct behavior, including edge cases.\n"
    "3. `FILE: main.py` — a short demo that imports the module and prints results.\n"
    "Keep ALL files at the project root (no sub-packages) so `import <module>` "
    "works directly. Standard library only. Output ONLY the PROJECT line and FILE "
    "blocks, nothing else."
)

FIXER_SYS = (
    "You are an expert Python debugger. You are given a task, the current project "
    "files, and the output from running its unit tests (and demo). Briefly state "
    "the cause in one comment line, then output the COMPLETE corrected project. "
    "Fix the implementation so the tests pass — do NOT weaken or delete tests "
    "unless a test is genuinely wrong. Output every file in the same `FILE: <path>` "
    "+ code-block format, and nothing else."
)


def plan(llm, task):
    banner("①", "PLANNER", C.CYAN)
    return llm.generate(PLANNER_SYS, f"Task:\n{task}", max_new_tokens=400, temperature=0.4)


def generate_code(llm, task, plan_text):
    banner("②", "CODE GENERATOR  (+ tests)", C.BLUE)
    user = f"Task:\n{task}\n\nPlan:\n{plan_text}\n\nWrite the complete project with tests."
    reply = llm.generate(CODEGEN_SYS, user, max_new_tokens=1800, temperature=0.2)
    return extract_project(reply, fallback=_slug(task))


def fix_code(llm, task, files, error):
    banner("⟳", "ERROR ANALYZER → FIXER", C.MAGENTA)
    user = (
        f"Task:\n{task}\n\n"
        f"Current project:\n{files_to_text(files)}\n\n"
        f"Test/run output:\n{error}\n\n"
        "Fix the code so the tests pass and return the full corrected project."
    )
    reply = llm.generate(FIXER_SYS, user, max_new_tokens=1800, temperature=0.2)
    return extract_files(reply) or files


def write_stage(name, files):
    banner("③", "FILE WRITER", C.YELLOW)
    proj = write_project(name, files)
    info(f"  project: workspace/{name}/")
    for p, c in files.items():
        info(f"    {p}  ({len(c)} chars)")
    return proj


def run_stage(attempt, proj):
    banner("④", f"RUNNER — unittest (attempt {attempt})", C.GREEN)
    ok, out, err = run_project(proj)
    if out.strip():
        print(f"{C.DIM}--- output ---{C.RESET}\n{out.rstrip()}")
    if err.strip():
        print(f"{C.RED}--- failures ---{C.RESET}\n{err.rstrip()}")
    return ok, out, err


# --------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------
def solve(task: str):
    print(f"\n{C.BOLD}🛠  AI Software Engineer Agent{C.RESET}")
    print(f"{C.DIM}task:{C.RESET} {task}")
    print(f"{C.DIM}test runner:{C.RESET} {'pytest' if _HAS_PYTEST else 'unittest'}")

    started = time.time()
    llm = LLM()

    plan_text = plan(llm, task)
    name, files = generate_code(llm, task, plan_text)

    proj = WORKDIR / name
    for attempt in range(1, MAX_ATTEMPTS + 1):
        proj = write_stage(name, files)
        ok, out, err = run_stage(attempt, proj)

        if ok:
            elapsed = time.time() - started
            banner("✅", "SUCCESS — tests passed", C.GREEN)
            print(
                f"{C.GREEN}Solved in {attempt} run(s), {elapsed:.1f}s "
                f"({len(files)} file(s)).{C.RESET}\n"
                f"Project: {C.BOLD}{proj}{C.RESET}"
            )
            return True

        if attempt < MAX_ATTEMPTS:
            info(f"\ntests failed — sending the output back to Qwen to self-correct "
                 f"({attempt}/{MAX_ATTEMPTS - 1} fixes used)…")
            files = fix_code(llm, task, files, err)

    banner("❌", "GAVE UP", C.RED)
    print(f"{C.RED}Tests still failing after {MAX_ATTEMPTS} attempts.{C.RESET}")
    print(f"Last attempt saved in: {proj}")
    return False


def main():
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
    else:
        task = input("Describe the project you want: ").strip()
    if not task:
        print("No task given. Exiting.")
        return
    solve(task)


if __name__ == "__main__":
    main()
