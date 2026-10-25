# Run the benchmark on your own model

The tweet test: your model, driving the Mac through Computer Use, opens Chrome, goes to x.com and posts a tweet that names the model and links this repo. It gets 10 minutes. Afterwards you check X and get a report you can share.

You need two apps:

- **[Model Launcher](https://github.com/ajokhai/model-launcher)** runs the model. (LM Studio, Ollama or llama.cpp's `llama-server` work too, from the command line.)
- **Computer Use** (this repo's [Releases](https://github.com/ajokhai/computer-use-lite/releases)) does the looking and clicking. Open it once and use **Settings › Allow Accessibility…** and **Allow Screen Recording…**.

Be signed in to X in Chrome. The test posts a real tweet from that account.

## The easy way: Model Launcher's Benchmark page

1. In Model Launcher, download a chat model that says **Runs great** or **Fits**.
2. Open **Benchmark**, pick the model and press **Run the test**. Watch the tool calls as they happen.
3. When it ends, open your X profile and answer **Did the tweet post?**
4. Press **Share on X** or **Open an issue** to send the report. Every run is listed below the button, and **Show in Finder** opens the reports.

## From the command line

```sh
git clone https://github.com/ajokhai/computer-use-lite
cd computer-use-lite
python3 scripts/benchmark.py
```

That's it: nothing to install, macOS's own Python is enough. It finds the model loaded in Model Launcher (or asks which one to load), explains what it's about to do, runs the test, asks whether the tweet posted, and saves a report. Other options:

```sh
python3 scripts/benchmark.py report                                   # every run so far, as one table
python3 scripts/benchmark.py --model qwen3-vl-8b                      # a specific Model Launcher model
python3 scripts/benchmark.py --url http://127.0.0.1:1234/v1 --model qwen3-vl-8b   # LM Studio (Ollama: :11434/v1)
python3 scripts/benchmark.py --profile "Work"                         # switch Chrome to that profile first
```

With more than one display, the model is kept on the built-in one, so it can't type into a window on another screen. Stop it any time: press **Escape three times**, or move the mouse hard into a screen corner.

## Where results go

Both ways save to **~/Documents/Computer Use Benchmarks**:

- `results.jsonl`: one line per run (model, Mac, verdict, time, tool calls, log)
- `<date> <model>.md`: a report per run, ready to post or attach to an issue
- `summary.md`: the table from `benchmark.py report`

A model saying "done" isn't a pass. The verdict is yours, after you've looked at your X profile. Share what happened, with your Mac's RAM and the model: post it and tag [@ajokhai](https://x.com/ajokhai), or [open an issue](https://github.com/ajokhai/computer-use-lite/issues) with the report.
