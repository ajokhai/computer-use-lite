# Run the benchmark on your own model

The tweet test: a model, driving the Mac through `cu-mcp`, opens Chrome, goes to x.com and posts a tweet that names the model and links this repo. It gets 10 minutes. Two projects are involved:

- **[Model Launcher](https://github.com/ajokhai/model-launcher)** runs the model and serves it as an OpenAI-compatible API on `http://127.0.0.1:11500/v1`. (Any OpenAI-compatible server works too: LM Studio, Ollama, llama.cpp's `llama-server`.)
- **Computer Use** (this repo) provides `cu-mcp`, the tools that see and operate the Mac, and `scripts/bench_mcp.py`, which connects the two and runs the test.

## 1. Run a model

1. Install Model Launcher from its [Releases](https://github.com/ajokhai/model-launcher/releases) page.
2. In **Discover**, download a chat model that shows **Runs great** or **Fits**. Models that read images (Qwen3-VL, Gemma 3, InternVL3) are the interesting ones here.
3. In **Library**, press **Chat** once to load it. Note its id: it's the folder name in `~/Models/Chat/` (for example `qwen3-vl-8b`).

## 2. Install Computer Use

1. Install **Computer Use** from this repo's [Releases](https://github.com/ajokhai/computer-use-lite/releases) and drag it to Applications. `cu-mcp` ships inside it.
2. Open it (it lives in the menu bar), then use **Settings › Allow Accessibility…** and **Allow Screen Recording…** to give it access.

## 3. Run the test

```sh
git clone https://github.com/ajokhai/computer-use-lite
cd computer-use-lite
uv sync                      # or: python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/python scripts/bench_mcp.py \
  --url http://127.0.0.1:11500/v1 \
  --model qwen3-vl-8b \
  --label "Qwen3-VL" \
  --profile "" \
  --main-display-only
```

- `--model` is the id from step 1, and `--label` is the name the model uses for itself in the tweet.
- `--profile ""` uses whichever Chrome profile is open. Give a profile name to have the model switch to it first. Be signed in to X in that profile.
- `--main-display-only` keeps the model on your built-in display, so it can't type into a window on another screen.
- Stop it any time: press **Escape three times**, or move the mouse hard into a screen corner.

The run prints every tool call and ends with the time, the number of calls and why it stopped. A line is added to `bench-results.jsonl` next to the repo folder.

## 4. Check and share it

A model saying "done" isn't a pass: open your X profile and look for the tweet. Then share what happened, the result plus your Mac's RAM and the model: post it and tag [@ajokhai](https://x.com/ajokhai), or [open an issue](https://github.com/ajokhai/computer-use-lite/issues) with the printed log.
