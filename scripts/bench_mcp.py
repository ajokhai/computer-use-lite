"""Benchmark a model driving cu-mcp's tools (the no-key MCP path) through any OpenAI-compatible server.

    uv run python scripts/bench_mcp.py --url http://127.0.0.1:8080/v1 --label Llama --task tweet

Same live tasks as scripts/bench.py, so it posts for real. computer_task is left out: it would hand the
task to the app's own agent and the run would no longer be about this model. Results go to bench-results.jsonl."""

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

import openai

from bench import PROJECT, TASKS

CU_MCP = "/Applications/Computer Use.app/Contents/Frameworks/cu-mcp"
HIDDEN = {"computer_task"}
SYSTEM = ("You operate this Mac through the tools. Look (list_screen_elements), do one action, then look again. "
          "Prefer open_url, press_keys and click_element. Call task_done when the task is finished.")


class Mcp:
    """A cu-mcp child over stdio, newline-delimited JSON-RPC."""

    def __init__(self):
        self.p = subprocess.Popen([CU_MCP], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.n = 0
        self.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "bench_mcp", "version": "1"}})
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.p.stdin.flush()

    def call(self, method: str, params: dict) -> dict:
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}) + "\n")
        self.p.stdin.flush()
        while True:
            msg = json.loads(self.p.stdout.readline())
            if msg.get("id") == self.n:
                return msg.get("result") or {"error": msg.get("error")}

    def close(self):
        self.p.terminate()


def text_of(result: dict, limit: int) -> str:
    """Tool output as text only (the model may not take images), cut to fit a small context."""
    parts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    out = "\n".join(parts) or json.dumps(result)[:500]
    return out if len(out) <= limit else out[:limit] + "\n…(cut)"


# The built-in display is the main one, at (0, 0); anything at negative coordinates is on another
# display (here, an LG to its upper left). With --main-display-only the model never sees or touches it.
AT = re.compile(r" at \((-?\d+),(-?\d+)\)")


def on_main(line: str) -> bool:
    m = AT.search(line)
    return not m or (int(m.group(1)) >= 0 and int(m.group(2)) >= 0)


def raise_main_chrome():
    """Brings the Chrome window on the built-in display to the front, so URLs open there."""
    subprocess.run(["osascript", "-e", '''tell application "Google Chrome"
        repeat with w in windows
            if item 1 of (get bounds of w) >= 0 and item 2 of (get bounds of w) >= 0 then
                set index of w to 1
                exit repeat
            end if
        end repeat
    end tell'''], capture_output=True, timeout=10)


def focus_on_main() -> bool:
    """Whether the window that gets keystrokes (the frontmost app's focused window) is on the
    built-in display. Asks for AXFocusedWindow: "window 1" can be another, nameless window of the
    same app (Chrome lists a strip on the other display first). Unknown counts as no."""
    r = subprocess.run(["osascript", "-e", '''tell application "System Events"
        set p to first application process whose frontmost is true
        set w to value of attribute "AXFocusedWindow" of p
        set {x, y} to position of w
        return (x as text) & "," & (y as text)
    end tell'''], capture_output=True, text=True, timeout=10)
    try:
        x, y = (int(v.strip()) for v in r.stdout.split(","))
    except ValueError:
        return False
    return x >= 0 and y >= 0


class MainDisplayGuard:
    """Filters observations to the built-in display and refuses actions aimed anywhere else."""

    def __init__(self):
        self.ids: set[str] = set()

    def before(self, name: str, args: dict) -> str | None:
        """A refusal message, or None to let the call through (args may be adjusted)."""
        if name in ("open_url", "open_app") or (name == "focus_window" and "chrome" in str(args.get("app", "")).lower()):
            raise_main_chrome()
            if name == "focus_window":
                return "Focused the Chrome window on this display."
        if name in ("type_text", "press_keys") and not args.get("element") and not focus_on_main():
            # keystrokes go to whatever window has focus; on another display that can be someone's video
            return "Refused: keyboard focus is on another display. Click a text field on this display first (type_text with its element id)."
        if name == "click_at" and (args.get("x", 0) < 0 or args.get("y", 0) < 0):
            return "Refused: that point is on another display. Work on the main display only."
        el = args.get("element")
        if name in ("click_element", "type_text", "read_element", "scroll") and el:
            if el not in self.ids:
                return "Refused: use an element id (like e12) from your latest list_screen_elements; only elements on this display can be used."
        return None

    def after(self, name: str, out: str) -> str:
        if name in ("list_screen_elements", "list_windows"):
            lines = [ln for ln in out.splitlines() if on_main(ln)]
            if name == "list_screen_elements":
                self.ids = {ln.split()[0] for ln in lines if ln[:1] == "e" and ln.split()[0][1:].isdigit()}
            return "\n".join(lines)
        return out


def fit(msgs: list, keep: int) -> list:
    """Keeps a long run inside a small model's context without breaking its prompt cache. The
    history is only ever appended to; when it outgrows `keep` messages, the older half is dropped in
    one go (one cache miss), cutting at an assistant turn so no tool result loses its call."""
    if len(msgs) <= keep + 2:
        return msgs
    tail = msgs[-(keep // 2):]
    while tail and tail[0].get("role") != "assistant":
        tail = tail[1:]
    msgs[2:] = tail  # trim in place, so the next turns share this new prefix
    return msgs


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", required=True)
    ap.add_argument("--model", default="local")
    ap.add_argument("--label", required=True, help="the name the model gives itself in the task")
    ap.add_argument("--task", default="tweet", choices=TASKS)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--result-chars", type=int, default=6000)
    ap.add_argument("--keep", type=int, default=24, help="most messages kept besides the task; past it the older half is dropped")
    ap.add_argument("--element-limit", type=int, default=250, help="default limit for list_screen_elements (before filtering)")
    ap.add_argument("--profile", default="familymake", help='Chrome profile for the tweet task; "" uses whichever is open')
    ap.add_argument("--main-display-only", action="store_true", help="hide and refuse everything off the built-in display")
    ap.add_argument("--out", default=str(PROJECT.parent / "bench-results.jsonl"))
    a = ap.parse_args()

    stamp = time.strftime("%H%M%S")
    task = TASKS[a.task].format(label=a.label, stamp=stamp)
    if a.profile != "familymake":
        swap = f'switch to the profile named "{a.profile}", ' if a.profile else ""
        task = task.replace('switch to the profile named "familymake", ', swap)
    client = openai.OpenAI(base_url=a.url, api_key="not-needed", max_retries=1, timeout=300)
    mcp = Mcp()
    tools = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                               "parameters": t.get("inputSchema") or {"type": "object"}}}
             for t in mcp.call("tools/list", {}).get("tools", []) if t["name"] not in HIDDEN]
    guard = MainDisplayGuard() if a.main_display_only else None
    if guard:
        raise_main_chrome()
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]
    log, calls, result, t0 = [], 0, "stopped: ran out of turns", time.monotonic()
    print(f"▶ {a.label} via cu-mcp ({len(tools)} tools) [stamp {stamp}]: {task}", flush=True)
    try:
        for _ in range(a.max_turns):
            if time.monotonic() - t0 > a.timeout:
                result = f"stopped: TIMED OUT after {a.timeout}s"
                break
            m = client.chat.completions.create(model=a.model, messages=fit(msgs, a.keep), tools=tools).choices[0].message
            msgs.append(m.model_dump(exclude_none=True))
            if not m.tool_calls:
                result = f"stopped: replied without a tool call: {(m.content or '')[:200]!r}"
                break
            finished = False
            for tc in m.tool_calls:
                calls += 1
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except ValueError:
                    args = {}
                line = f"  {tc.function.name} {json.dumps(args)[:160]}"
                print(line, flush=True)
                log.append(line)
                if tc.function.name == "list_screen_elements":
                    args.setdefault("limit", a.element_limit)  # a whole list the model can read beats a cut-off one
                refusal = guard.before(tc.function.name, args) if guard else None
                if refusal:
                    out = refusal
                else:
                    raw = mcp.call("tools/call", {"name": tc.function.name, "arguments": args})
                    out = text_of(raw, 10**7)
                    out = guard.after(tc.function.name, out) if guard else out
                    out = out if len(out) <= a.result_chars else out[:a.result_chars] + "\n…(cut)"
                msgs.append({"role": "tool", "tool_call_id": tc.id, "content": out})
                finished |= tc.function.name == "task_done"
            if finished:
                result = "done (model called task_done; check the post separately)"
                break
    except Exception as e:  # a model error is a result too
        result = f"stopped: {type(e).__name__}: {str(e)[:300]}"
    finally:
        mcp.close()
    r = {"model": f"{a.label.lower()}-mcp", "mode": f"{a.label} at {a.url} → cu-mcp tools (no key)", "task": a.task,
         "seconds": round(time.monotonic() - t0, 1), "result": result, "actions": calls, "log_tail": log[-40:],
         "void": False, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "stamp": stamp}
    print(f"  {r['seconds']}s, {calls} tool calls: {result}", flush=True)
    with open(a.out, "a") as f:
        f.write(json.dumps(r) + "\n")


if __name__ == "__main__":
    main()
