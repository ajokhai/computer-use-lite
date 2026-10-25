"""Benchmark your own model on the Mac, in one command, then share the result.

    python3 scripts/benchmark.py                 run the tweet test with the model loaded in Model Launcher
    python3 scripts/benchmark.py --task NAME     run one of your own benchmarks instead
    python3 scripts/benchmark.py new             make a benchmark of your own
    python3 scripts/benchmark.py list            the benchmarks you can run
    python3 scripts/benchmark.py report          every run so far, as one table (and summary.md)

Needs only the Python that comes with macOS, Computer Use (for cu-mcp) and a model server: Model
Launcher is found on its own; for LM Studio, Ollama or llama.cpp pass --url and --model. Every run
is saved to ~/Documents/Computer Use Benchmarks: results.jsonl plus a Markdown report you can post
or attach to an issue. Model Launcher's Benchmark page writes to the same folder.
"""


from __future__ import annotations
import argparse
import json
import platform
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_mcp import CU_MCP, run  # noqa: E402

OUT = Path.home() / "Documents" / "Computer Use Benchmarks"
TASKS_DIR = OUT / "tasks"  # one JSON file per benchmark; Model Launcher's Benchmark page uses the same
ML = "http://127.0.0.1:11500"
REPO = "https://github.com/ajokhai/computer-use-lite"


def get(url: str, body: dict | None = None, timeout: int = 10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def sysctl(key: str) -> str:
    return subprocess.run(["sysctl", "-n", key], capture_output=True, text=True).stdout.strip()


def machine() -> dict:
    return {"chip": sysctl("machdep.cpu.brand_string") or platform.machine(),
            "ram_gb": round(int(sysctl("hw.memsize") or 0) / 2**30),
            "macos": platform.mac_ver()[0],
            "displays": displays()}


def displays() -> int:
    out = subprocess.run(["system_profiler", "SPDisplaysDataType", "-json"], capture_output=True, text=True).stdout
    try:
        return sum(len(g.get("spdisplays_ndrvs", [])) for g in json.loads(out)["SPDisplaysDataType"])
    except (ValueError, KeyError):
        return 1


def pick_model(a) -> tuple[str, str, str, dict]:
    """(api url, model id, display name, details) for the model to test."""
    if a.url:
        return a.url, a.model or "local", a.label or a.model or "Local model", {}
    try:
        status = get(f"{ML}/api/status")
    except OSError:
        sys.exit("Model Launcher isn't running. Open it and load a chat model, or pass --url and --model for another server.")
    lib = {m["id"]: m for m in get(f"{ML}/api/library") if m["kind"] == "chat"}
    running = status.get("running") or []
    if a.model:
        mid = a.model
    elif len(running) == 1:
        mid = running[0]["id"]
    else:
        ids = [r["id"] for r in running] or list(lib)
        if not ids:
            sys.exit("No chat models yet. Download one in Model Launcher › Discover first.")
        print("Which model?")
        for i, m in enumerate(ids, 1):
            print(f"  {i}. {lib.get(m, {}).get('name', m)}")
        mid = ids[int(input("Number: ").strip() or 1) - 1]
    if mid not in [r["id"] for r in running]:
        print(f"Loading {lib.get(mid, {}).get('name', mid)}…", flush=True)
        try:
            get(f"{ML}/api/launch", {"id": mid}, timeout=300)
        except urllib.error.HTTPError as e:
            if e.code != 507 or input("It needs more RAM than is free. Run it anyway (slower)? [y/N] ").strip().lower() != "y":
                sys.exit("Not run.")
            get(f"{ML}/api/launch", {"id": mid, "force": True}, timeout=300)
    m = lib.get(mid, {})
    name = m.get("name", mid)
    return f"{ML}/v1", mid, a.label or name.split(" ")[0], {"name": name, "size_gb": m.get("size_gb"), "vision": m.get("vision")}


def tweet_task() -> dict:
    return {"id": "tweet", "name": "Post a tweet", "minutes": 10, "check": "Open your X profile. Did the tweet post?",
            "prompt": ("In Google Chrome, go to x.com, and post a tweet saying that you, {model}, are testing computer use with this account, "
                       f"and include the link {REPO}. The wording is up to you; for example: "
                       f'"This is {{model}} testing computer use with my account. {REPO}"')}


def tasks() -> list:
    out = []
    for f in sorted(TASKS_DIR.glob("*.json")):
        try:
            t = json.loads(f.read_text())
        except ValueError:
            continue
        out.append({"id": f.stem, "name": t.get("name", f.stem), "prompt": t.get("prompt", ""),
                    "check": t.get("check") or "Did it work?", "minutes": int(t.get("minutes") or 10)})
    return [tweet_task()] + sorted(out, key=lambda t: t["name"].lower())


def ask(q: str) -> bool:
    return input(f"{q} [y/n] ").strip().lower().startswith("y")


def report_md(r: dict) -> str:
    v = {"pass": "✅ Pass", "fail": "❌ Fail"}.get(r["verdict"], "❔ Not checked")
    mc = r["machine"]
    size = f" · {r['size_gb']:.1f} GB" if r.get("size_gb") else ""
    sees = " · reads images" if r.get("vision") else ""
    lines = [f"# {r['model_name']}: {v}", "",
             f"- **Benchmark:** {r.get('task_name', r['task'])}",
             f"- **Instructions:** {r.get('prompt', '')}",
             f"- **Checked by:** {r.get('check', '')}",
             f"- **Result:** {r['seconds']} s, {r['actions']} tool calls. {r['result']}",
             f"- **Model:** {r['model_name']} (`{r['model_id']}`){sees}{size}",
             f"- **Server:** {r['url']}",
             f"- **Mac:** {mc['chip']}, {mc['ram_gb']} GB RAM, macOS {mc['macos']}, {mc['displays']} display(s)",
             f"- **When:** {r['at']}", "", "## Tool calls", "", "```", *[l.strip() for l in r.get("log_tail", [])], "```", "",
             f"Run with [Computer Use]({REPO}) · `python3 scripts/benchmark.py`"]
    return "\n".join(lines) + "\n"


def share(r: dict, path: Path):
    verdict = {"pass": "passed", "fail": "failed"}.get(r["verdict"], "ran")
    tweet = f"{r['model_name']} {verdict} the \"{r['task_name']}\" computer-use test on my {r['machine']['ram_gb']} GB Mac: {r['seconds']} s, {r['actions']} tool calls. {REPO}"
    title = f"Benchmark: {r['model_name']} {verdict} \"{r['task_name']}\" ({r['machine']['ram_gb']} GB Mac)"
    issue = f"{REPO}/issues/new?" + urllib.parse.urlencode({"title": title, "body": path.read_text()[:6000]})
    print(f"\nReport saved: {path}\n\nShare it:\n  Post on X:  https://x.com/intent/post?{urllib.parse.urlencode({'text': tweet})}\n  Or open an issue (report filled in):  {issue[:120]}…\n")
    if ask("Open the GitHub issue with this report filled in?"):
        subprocess.run(["open", issue])


def cmd_run(a):
    if not Path(CU_MCP).exists():
        sys.exit(f"Computer Use isn't installed ({CU_MCP} is missing). Get it from {REPO}/releases.")
    task = next((t for t in tasks() if t["id"] == a.task), None)
    if not task:
        sys.exit(f"No benchmark called {a.task!r}. See: python3 scripts/benchmark.py list")
    url, mid, label, info = pick_model(a)
    mc = machine()
    main_only = mc["displays"] > 1
    what = ("Chrome, x.com, and a tweet from the account you're signed in to" if task["id"] == "tweet"
            else task["prompt"].replace("{model}", label))
    print(f"\nTesting {info.get('name', label)} on \"{task['name']}\".\n"
          f"It will drive this Mac for up to {task['minutes']} minutes: {what}\n"
          + ("It stays on your built-in display, so your other screen is left alone.\n" if main_only else "")
          + "Stop it any time: press Escape three times, or move the mouse hard into a screen corner.")
    if not a.yes and not ask("Start?"):
        return
    OUT.mkdir(parents=True, exist_ok=True)
    prompt = task["prompt"]
    if task["id"] == "tweet" and a.profile:
        prompt = prompt.replace("In Google Chrome, go to x.com", f'In Google Chrome, switch to the profile named "{a.profile}", go to x.com')
    r = run(url, mid, label, main_display_only=main_only, out=None, prompt=prompt, timeout=task["minutes"] * 60)
    posted = None if a.yes else ask(f"\n{task['check']}")
    r.update(task=task["id"], task_name=task["name"], prompt=prompt.replace("{model}", label), check=task["check"], verdict={True: "pass", False: "fail", None: "unchecked"}[posted], model_id=mid, model_name=info.get("name", label),
             size_gb=info.get("size_gb"), vision=info.get("vision"), url=url, machine=mc, source="cli")
    with open(OUT / "results.jsonl", "a") as f:
        f.write(json.dumps(r) + "\n")
    path = OUT / f"{time.strftime('%Y-%m-%d %H%M')} {r['model_name']}.md"
    path.write_text(report_md(r))
    share(r, path)


def cmd_list(_a):
    for t in tasks():
        print(f"{t['id']:<28} {t['name']}  ({t['minutes']} min)")
    print(f"\nYour benchmarks live in {TASKS_DIR}. Make one: python3 scripts/benchmark.py new")


def cmd_new(_a):
    print("A benchmark is a task for the model and a question you answer afterwards.\n")
    name = input("Name (e.g. Shopping list in Notes): ").strip()
    prompt = input("What the model should do ({model} becomes its name):\n> ").strip()
    check = input("How you'll check it (e.g. Is the note in Notes?): ").strip() or "Did it work?"
    minutes = int(input("Time limit in minutes [10]: ").strip() or 10)
    if not name or not prompt:
        sys.exit("A benchmark needs a name and instructions.")
    slug = "-".join("".join(c if c.isalnum() else " " for c in name.lower()).split()) or "benchmark"
    path, n = TASKS_DIR / f"{slug}.json", 2
    while path.exists() or path.stem == "tweet":
        path, n = TASKS_DIR / f"{slug}-{n}.json", n + 1
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": name, "prompt": prompt, "check": check, "minutes": max(1, min(60, minutes))}, indent=2))
    print(f"\nSaved {path}\nRun it: python3 scripts/benchmark.py --task {path.stem}")


def cmd_report(_a):
    rows = []
    for f in [OUT / "results.jsonl"]:
        if f.exists():
            rows += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    if not rows:
        sys.exit(f"No runs yet in {OUT}. Run: python3 scripts/benchmark.py")
    head = "| When | Benchmark | Model | Mac | Verdict | Time | Calls | How it ended |\n|---|---|---|---|---|---|---|---|"
    body = [f"| {r['at'][:16]} | {r.get('task_name', r.get('task', ''))} | {r.get('model_name', r['model'])} | {r.get('machine', {}).get('ram_gb', '?')} GB | "
            f"{ {'pass': '✅ pass', 'fail': '❌ fail'}.get(r.get('verdict'), '❔') } | {r['seconds']} s | {r['actions']} | "
            f"{r['result'][:70].replace('|', '/')} |" for r in rows]
    passed = sum(r.get("verdict") == "pass" for r in rows)
    table = f"# Computer Use benchmarks\n\n{len(rows)} runs, {passed} passed.\n\n{head}\n" + "\n".join(body) + "\n"
    (OUT / "summary.md").write_text(table)
    print(table)
    print(f"Saved {OUT / 'summary.md'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", nargs="?", default="run", choices=["run", "new", "list", "report"])
    ap.add_argument("--task", default="tweet", help="which benchmark to run (see: list); default: the tweet test")
    ap.add_argument("--model", help="model id (Model Launcher) or name (other servers); default: the loaded one")
    ap.add_argument("--url", help="another OpenAI-compatible server, e.g. http://127.0.0.1:1234/v1 (LM Studio)")
    ap.add_argument("--label", help="the name the model uses for itself in the tweet")
    ap.add_argument("--profile", default="", help="Chrome profile to switch to first; default: whichever is open")
    ap.add_argument("--yes", action="store_true", help="don't ask anything (verdict stays unchecked)")
    a = ap.parse_args()
    {"run": cmd_run, "new": cmd_new, "list": cmd_list, "report": cmd_report}[a.command](a)


if __name__ == "__main__":
    main()
