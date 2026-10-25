"""Benchmark your own model on the Mac, in one command, then share the result.

    python3 scripts/benchmark.py            run the tweet test with the model loaded in Model Launcher
    python3 scripts/benchmark.py report     every run so far, as one table (and summary.md)

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


def ask(q: str) -> bool:
    return input(f"{q} [y/n] ").strip().lower().startswith("y")


def report_md(r: dict) -> str:
    v = {"pass": "✅ Pass", "fail": "❌ Fail"}.get(r["verdict"], "❔ Not checked")
    mc = r["machine"]
    size = f" · {r['size_gb']:.1f} GB" if r.get("size_gb") else ""
    sees = " · reads images" if r.get("vision") else ""
    lines = [f"# {r['model_name']}: {v}", "",
             f"- **Task:** {r['task']} (post a tweet from Chrome naming the model and linking {REPO})",
             f"- **Result:** {r['seconds']} s, {r['actions']} tool calls. {r['result']}",
             f"- **Model:** {r['model_name']} (`{r['model_id']}`){sees}{size}",
             f"- **Server:** {r['url']}",
             f"- **Mac:** {mc['chip']}, {mc['ram_gb']} GB RAM, macOS {mc['macos']}, {mc['displays']} display(s)",
             f"- **When:** {r['at']}", "", "## Tool calls", "", "```", *[l.strip() for l in r.get("log_tail", [])], "```", "",
             f"Run with [Computer Use]({REPO}) · `python3 scripts/benchmark.py`"]
    return "\n".join(lines) + "\n"


def share(r: dict, path: Path):
    verdict = {"pass": "passed", "fail": "failed"}.get(r["verdict"], "ran")
    tweet = f"{r['model_name']} {verdict} the Computer Use tweet test on my {r['machine']['ram_gb']} GB Mac: {r['seconds']} s, {r['actions']} tool calls. {REPO}"
    title = f"Benchmark: {r['model_name']} {verdict} ({r['machine']['ram_gb']} GB Mac)"
    issue = f"{REPO}/issues/new?" + urllib.parse.urlencode({"title": title, "body": path.read_text()[:6000]})
    print(f"\nReport saved: {path}\n\nShare it:\n  Post on X:  https://x.com/intent/post?{urllib.parse.urlencode({'text': tweet})}\n  Or open an issue (report filled in):  {issue[:120]}…\n")
    if ask("Open the GitHub issue with this report filled in?"):
        subprocess.run(["open", issue])


def cmd_run(a):
    if not Path(CU_MCP).exists():
        sys.exit(f"Computer Use isn't installed ({CU_MCP} is missing). Get it from {REPO}/releases.")
    url, mid, label, info = pick_model(a)
    mc = machine()
    main_only = mc["displays"] > 1
    print(f"\nTesting {info.get('name', label)} on the tweet task.\n"
          "It will drive this Mac for up to 10 minutes: Chrome, x.com, and a tweet from the account you're signed in to.\n"
          + ("It stays on your built-in display, so your other screen is left alone.\n" if main_only else "")
          + "Stop it any time: press Escape three times, or move the mouse hard into a screen corner.")
    if not a.yes and not ask("Start?"):
        return
    OUT.mkdir(parents=True, exist_ok=True)
    r = run(url, mid, label, profile=a.profile, main_display_only=main_only, out=None)
    posted = None if a.yes else ask("\nOpen your X profile: is the tweet there?")
    r.update(verdict={True: "pass", False: "fail", None: "unchecked"}[posted], model_id=mid, model_name=info.get("name", label),
             size_gb=info.get("size_gb"), vision=info.get("vision"), url=url, machine=mc, source="cli")
    with open(OUT / "results.jsonl", "a") as f:
        f.write(json.dumps(r) + "\n")
    path = OUT / f"{time.strftime('%Y-%m-%d %H%M')} {r['model_name']}.md"
    path.write_text(report_md(r))
    share(r, path)


def cmd_report(_a):
    rows = []
    for f in [OUT / "results.jsonl"]:
        if f.exists():
            rows += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    if not rows:
        sys.exit(f"No runs yet in {OUT}. Run: python3 scripts/benchmark.py")
    head = "| When | Model | Mac | Verdict | Time | Calls | How it ended |\n|---|---|---|---|---|---|---|"
    body = [f"| {r['at'][:16]} | {r.get('model_name', r['model'])} | {r.get('machine', {}).get('ram_gb', '?')} GB | "
            f"{ {'pass': '✅ pass', 'fail': '❌ fail'}.get(r.get('verdict'), '❔') } | {r['seconds']} s | {r['actions']} | "
            f"{r['result'][:70].replace('|', '/')} |" for r in rows]
    passed = sum(r.get("verdict") == "pass" for r in rows)
    table = f"# Computer Use benchmarks\n\n{len(rows)} runs, {passed} passed.\n\n{head}\n" + "\n".join(body) + "\n"
    (OUT / "summary.md").write_text(table)
    print(table)
    print(f"Saved {OUT / 'summary.md'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", nargs="?", default="run", choices=["run", "report"])
    ap.add_argument("--model", help="model id (Model Launcher) or name (other servers); default: the loaded one")
    ap.add_argument("--url", help="another OpenAI-compatible server, e.g. http://127.0.0.1:1234/v1 (LM Studio)")
    ap.add_argument("--label", help="the name the model uses for itself in the tweet")
    ap.add_argument("--profile", default="", help="Chrome profile to switch to first; default: whichever is open")
    ap.add_argument("--yes", action="store_true", help="don't ask anything (verdict stays unchecked)")
    a = ap.parse_args()
    (cmd_report if a.command == "report" else cmd_run)(a)


if __name__ == "__main__":
    main()
