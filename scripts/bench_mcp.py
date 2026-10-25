"""Benchmark a model driving cu-mcp's tools (the no-key MCP path) through any OpenAI-compatible server.

    uv run python scripts/bench_mcp.py --url http://127.0.0.1:8080/v1 --label Llama --task tweet

Same live tasks as scripts/bench.py, so it posts for real. computer_task is left out: it would hand the
task to the app's own agent and the run would no longer be about this model. Results go to bench-results.jsonl."""

import argparse
import json
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


def fit(msgs: list, keep: int) -> list:
    """The task plus the most recent turns, so a long run fits a small model's context. Starts the
    recent part at an assistant turn so no tool result loses the call it answers."""
    if len(msgs) <= keep + 2:
        return msgs
    tail = msgs[-keep:]
    while tail and tail[0].get("role") != "assistant":
        tail = tail[1:]
    return msgs[:2] + tail


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", required=True)
    ap.add_argument("--model", default="local")
    ap.add_argument("--label", required=True, help="the name the model gives itself in the task")
    ap.add_argument("--task", default="tweet", choices=TASKS)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--result-chars", type=int, default=6000)
    ap.add_argument("--keep", type=int, default=16, help="recent messages sent each turn, besides the task")
    ap.add_argument("--out", default=str(PROJECT.parent / "bench-results.jsonl"))
    a = ap.parse_args()

    stamp = time.strftime("%H%M%S")
    task = TASKS[a.task].format(label=a.label, stamp=stamp)
    client = openai.OpenAI(base_url=a.url, api_key="not-needed", max_retries=1, timeout=300)
    mcp = Mcp()
    tools = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                               "parameters": t.get("inputSchema") or {"type": "object"}}}
             for t in mcp.call("tools/list", {}).get("tools", []) if t["name"] not in HIDDEN]
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
                out = text_of(mcp.call("tools/call", {"name": tc.function.name, "arguments": args}), a.result_chars)
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
