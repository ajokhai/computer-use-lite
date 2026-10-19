# Computer Use (lite)

Cowork-style control of the whole Mac, not just a browser. It runs from the menu bar, and every API key is optional.

## Install
1. Open `Computer Use <version>.dmg` (next to this Project folder) and drag **Computer Use** into Applications.
2. Open it. A cursor icon appears in the menu bar; there's no Dock icon and no window.
3. Go to **Settings › Grant Accessibility…** and **Grant Screen Recording…**, then add Computer Use.
4. Optional: **Settings › API Keys…** (they're stored in the macOS Keychain) and **Settings › Open at Login**.
5. Optional: **Settings › Connect to** › tick Antigravity, VS Code, Cursor, Windsurf or Claude, then restart that app.

## Ways to use it

| How | Keys needed | Who thinks | Who clicks |
|---|---|---|---|
| Ask the agent in **Antigravity, VS Code, Cursor, Windsurf or Claude** | none | whatever model you've picked there (Gemini, Claude, GPT…) | the app's tools |
| Menu bar, mode **Jev only** | Jev | Jev | Jev |
| Menu bar, mode **Hybrid** | both | Gemini plans a few steps at a time | Jev, with Gemini vision when Jev is unsure |
| Menu bar, mode **Gemini only** | Gemini | Gemini vision | Gemini vision |

**Auto** picks the best mode your keys and remaining quota allow. When an editor's agent drives the Mac, that editor needs Accessibility and Screen Recording permission too, because it's the app that starts the tools.

**Jev only:** Jev is text-only and doesn't generate text. Each turn the app reads the front app's accessibility tree. In one request, Jev picks the most likely next action, which element it applies to, which phrase from your task to type, and which installed app to open. Put text you want typed in quotes, or after words like *saying*, *titled* or *search for*.

## Voice
Everything is under **Voice** in the menu.

- **Talk (⌃⌥Space):** press it, say what you want, then pause. The words show in the menu as you speak, and the task starts when you stop. Press it again to finish early.
- **Hands-free — say “Computer, …”:** always listening, and acts only on speech that starts with "Computer" or "Hey computer".
- **"Stop"** halts the running task. A new instruction mid-task replaces the current one.
- **Speak replies:** says "On it", reads out any confirmation questions, and gives a short summary at the end.
- **Narrate while working:** talks you through each step ("Opening Notes", "Clicking New Note"), whichever model is acting: Jev, Gemini (in its own words), or Claude or another agent through the MCP tools. Agents can also call `speak` to talk to you directly. The mic ignores the app's own voice.

### Listen With (speech to text, all on your Mac)
| Model | Size | Notes |
|---|---|---|
| Apple | built in | The default; nothing to download. |
| Moonshine Tiny / Base | 110 / 250 MB | Light and fast. Needs the Voice Pack. |
| Parakeet v2 (English) / v3 (25 languages) | 2.5 GB | The most accurate; runs on the Mac's GPU. Needs the Voice Pack. |

**Add a custom model:** **Voice › Listen With › Add Custom Model…**, then paste either:
- **A Hugging Face model name.** Parakeet models converted for MLX work, e.g. `mlx-community/parakeet-tdt_ctc-0.6b-ja`, and so do Moonshine ONNX models.
- **A folder on your Mac.** Parakeet needs `config.json` and `model.safetensors`; Moonshine needs `encoder_model.onnx` and `decoder_model_merged.onnx`.

If the name doesn't say which kind it is, put `parakeet:` or `moonshine:` in front. Models download the first time you use them. Whisper models aren't supported, because their MLX package needs PyTorch.

### Speaking Voice
- **Mac:** any voice installed on your Mac. **Get More Voices…** opens System Settings, where you can download the Premium and Enhanced ones.
- **Kokoro:** natural-sounding local voices (Heart, Bella, Michael, Emma, George and more). Needs the Voice Pack; the model (about 120 MB) downloads on first use.
- **Gemini:** Google's 30 voices (Kore, Puck, Charon…). Needs a Gemini key, runs in the cloud, and counts toward your Gemini usage.

Picking a voice plays a short preview.

### Voice Pack
`Computer Use Voice Pack <version>.zip` sits next to the DMG and keeps the app download small. Install it from **Settings › Install Voice Pack…**; the app also looks for it in Downloads. Choosing a model or voice that needs the pack offers to install it.

## Usage and switching
- **Usage** in the menu shows each model's use in the current window, what's left, and when it resets. Gemini resets at midnight Pacific; Jev resets on your billing day.
- Neither provider has an API that reports your remaining quota, so the app counts its own requests. Click a line to enter that model's limit from AI Studio or your TypeSafe dashboard. Usage from outside this app isn't counted.
- When a provider replies "out of quota", the model is marked as out until it resets.
- **Switch models automatically when one runs out:** steps down the Gemini models (3.8 Flash → 3.5 Flash-Lite → 3 Flash preview), and moves between Jev and Gemini, carrying on from the current screen.

## Safety
Clicks on send/buy/delete-style buttons ask first, unless you untick that. To abort, move the mouse hard into any screen corner, or use Stop. The log is at `~/Library/Logs/computer-use.log`.

## Development
- `uv run python -m cu "task"` (CLI), `uv run python -m cu --doctor`, `uv run python -m cu --elements`
- `./packaging/build.sh` builds the app and writes `Computer Use <version>.dmg` next to the Project folder, replacing the previous one.
