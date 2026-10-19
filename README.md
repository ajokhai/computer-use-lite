# Computer Use (lite)

Cowork-style control of the whole Mac, not just a browser. It runs from the menu bar, and every API key is optional.

## Install
1. Open `dist/Computer Use.dmg` and drag **Computer Use** into Applications.
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

## Usage and switching
- **Usage** in the menu shows each model's use in the current window, what's left, and when it resets. Gemini resets at midnight Pacific; Jev resets on your billing day.
- Neither provider has an API that reports your remaining quota, so the app counts its own requests. Click a line to enter that model's limit from AI Studio or your TypeSafe dashboard. Usage from outside this app isn't counted.
- When a provider replies "out of quota", the model is marked as out until it resets.
- **Switch models automatically when one runs out:** steps down the Gemini models (3.8 Flash → 3.5 Flash-Lite → 3 Flash preview), and moves between Jev and Gemini, carrying on from the current screen.

## Safety
Clicks on send/buy/delete-style buttons ask first, unless you untick that. To abort, move the mouse hard into any screen corner, or use Stop. The log is at `~/Library/Logs/computer-use.log`.

## Development
- `uv run python -m cu "task"` (CLI), `uv run python -m cu --doctor`, `uv run python -m cu --elements`
- `./packaging/build.sh` builds the app and the DMG into `dist/`.
