# Spire Oracle — real-time strategic advisor for Slay the Spire 2

A read-only advisor for **Slay the Spire 2** (Early Access, 2026). You play every
fight manually. On each non-combat decision screen (card reward, map, shop,
rest site, event, relic/blessing select, card-select grid) a Claude model reads
the live game state and shows a terse recommendation in a small overlay. It
keeps a running "thesis" so advice stays coherent across a full run, and it
**only ever observes the game — it never sends a move.**

Runs on your **Claude Code CLI subscription** (no API key).

## Features

- 🛡️ **Observe-only & safe** — reads game state and shows advice; it never clicks,
  plays a card, or sends any input. The bridge is GET-only by construction.
- 📜 **Grounded, not guessed** — injects the exact card/relic text (base + upgraded),
  keywords, and your owned relics from a local dataset, so numbers are facts.
- 🃏 **Deck-aware** — learns your real deck from combat and reasons about synergies
  (e.g. a Strike-payoff power makes Strike cards better).
- 🗺️ **Full-map routing** — reads the whole act graph and computes which fork
  actually reaches the Elite/Treasure/Shop you want.
- 🧠 **Run "thesis" memory** — keeps a coherent plan (locked facts + live strategy)
  across a whole run.
- ⚡ **Fast & tiered** — warm Claude session, Sonnet for routine screens, Opus for
  run-defining ones (boss relics / Ancient blessings).
- 🏆 **Knows when you win** — announces victory/defeat and resets for the next run.
- 🪟 **Tiny overlay + auto-launch** — appears on decision screens, hides otherwise,
  and starts/stops with the game. One-click Windows installer.

---

## 🚀 Quick start (Windows)

You need four things first (one-time):

1. **Slay the Spire 2** installed.
2. **STS2MCP mod** installed + enabled in StS2 → https://github.com/Gennadiyev/STS2MCP
   (drop it in the game's `mods/` folder, enable under Settings ▸ Mods).
3. **Claude Code** installed and signed in (uses your subscription, no API key) →
   https://claude.com/claude-code
4. **Python 3.11+** → https://www.python.org/downloads/ (tick *"Add python.exe to PATH"*).

Then:

1. Download this repo (green **Code** button ▸ *Download ZIP*, or `git clone`), unzip.
2. **Double-click `install.bat`.** It installs everything, downloads the card data,
   checks your setup, and offers to auto-launch with the game. Say yes.
3. Launch StS2 and play. The overlay pops up on each decision screen with a
   recommendation. That's it.

If you skipped auto-launch, double-click **`start.bat`** to run it (and `stop.bat`
to stop). The overlay only appears while StS2 is running.

> It is **observe-only**: it reads game state and shows advice. It never clicks,
> never plays a card, never touches your run.

---

## How it works

```
StS2 + STS2MCP mod ──HTTP(GET)──▶ bridge ──prompt──▶ claude CLI ──advice──▶ overlay
   (game state)                  (this repo)         (warm session)        (window)
```

- **STS2MCP** (a community C# mod) exposes the live game state at
  `http://127.0.0.1:15526/api/v1/singleplayer`. The bridge issues **GET reads
  only** — it has no code path that can POST a game action.
- The bridge detects owned decision screens by `state_type`, treats combat and
  transitions as no-ops, **debounces** and **de-dupes** via a stable
  per-screen signature so the model fires exactly once per decision.
- It injects the **exact card/relic text** (base + upgraded) for whatever's on
  screen, joined from a local dataset by a normalized id key, and feeds back the
  running **thesis** (LOCKED facts + PLAN read).
- Model calls run on a worker thread; a **"thinking…"** placeholder shows
  immediately so stale advice never displays. High-stakes screens route to Opus,
  routine ones to Sonnet (configurable).

---

## Manual setup (the `install.bat` above does all of this for you)

### 1. Install the STS2MCP bridge mod
1. Get it from https://github.com/Gennadiyev/STS2MCP (or its Nexus Mods page).
2. Follow its README to drop the mod (`STS2_MCP.dll` + manifest) into the StS2
   `mods/` folder and enable it in-game (Settings ▸ Mods).
3. Launch StS2, start a run, then confirm the API is live — in a browser or:
   ```powershell
   curl http://127.0.0.1:15526/
   ```
   You should get `{"status":"ok", ...}`. The state endpoint is
   `http://127.0.0.1:15526/api/v1/singleplayer?format=json`.
4. If the mod uses a non-default port, set it in `STS2_MCP.conf` and update
   `bridge.base_url` in `config.yaml`.

> ⚠️ STS2MCP tracks specific game builds. After a StS2 patch, re-check that the
> state endpoint still returns the expected shape; tweak field paths in
> `bridge/screens.py::extract_options` if the mod's layout changed.

### 2. Python environment
```powershell
cd sts2-advisor          # the folder you cloned/unzipped
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
(The overlay uses `tkinter`, which ships with the standard Windows Python
installer — no pip package needed.)

### 3. Fetch the card/relic dataset
```powershell
python data\fetch_data.py
```
Writes `data\cards.json` + `data\relics.json` from the spire-codex datamine
(personal use; do not redistribute game data). Re-run after major patches.

### 4. Validate the Claude Code CLI invocation (important)
Two CLI behaviors are version-dependent: whether a warm `--input-format
stream-json` process survives multiple turns, and the exact stdin message shape.
Check both on your machine:
```powershell
python -m bridge.model_client --selftest
```
Follow its printout. If it reports warm sessions don't persist, the advisor
auto-falls back to one-shot mode — or set `model.cli.warm_session: false`. If a
turn fails, try flipping `model.cli.input_message_format` between `sdk` and
`simple` in `config.yaml`.

---

## Running

Two processes (two terminals):
```powershell
# terminal 1 — the bridge/advisor
python -m bridge.main

# terminal 2 — the overlay window
python overlay\overlay.py
```
The overlay auto-appears when the bridge heartbeat is fresh (game running) and
hides when it goes stale. Drag it by its title bar; position/size/opacity are in
`config.yaml`.

### Test without the game
Set `dev.use_sample_state: true` in `config.yaml` and run `python -m bridge.main`.
It feeds `data/sample_state.json` (a card-reward screen) through the full
pipeline so you can see real advice without StS2 running. Set it back to `false`
for live play.

---

## Auto-launch with the game (recommended)

Instead of starting the two processes by hand, run a supervisor that watches for
StS2 and starts/stops the advisor automatically. Detection uses the STS2MCP
health endpoint, which only answers while the game is running with the mod
loaded — so there's no need to know the game's process name.

Install it to run at every Windows login (no admin needed):
```powershell
powershell -ExecutionPolicy Bypass -File autostart\install_autostart.ps1
```
This drops a `SpireOracle.lnk` shortcut in your Startup folder that runs
`autostart\supervisor.py` under `pythonw` (no console window). From then on:

- Launch StS2 → the supervisor sees `:15526` answer and starts the bridge + overlay.
- The overlay appears on decision screens; stays hidden/idle otherwise.
- Quit StS2 → the supervisor tears down the whole tree (bridge, overlay, and any
  warm `claude` sessions) after a ~0.5s grace period. One light process idles.

Verify the wiring once (launches, checks the heartbeat, then cleans up):
```powershell
python autostart\supervisor.py --test
```
Start it now without rebooting, or remove it:
```powershell
Start-Process pythonw -ArgumentList "autostart\supervisor.py"   # start now
powershell -ExecutionPolicy Bypass -File autostart\uninstall_autostart.ps1  # remove
```
Tuning knobs live under `autostart:` in `config.yaml` (`check_interval_s`,
`grace_misses`, `mutex_port`).

> Alternative: if you'd rather the advisor always run, just autostart
> `bridge.main` + `overlay.py` directly — the overlay already shows/hides itself
> via the heartbeat. The supervisor is preferred because it also stops the
> processes (and frees warm sessions) when you're not playing.

---

## Configuration (`config.yaml`)

| Knob | Meaning |
|------|---------|
| `bridge.base_url` / `poll_interval_s` | STS2MCP location and poll cadence |
| `debounce.settle_s` | how long a screen must hold steady before firing (~0.5s) |
| `model.default` / `model.per_screen` | tiered routing (Opus for boss/shop/event/relic, Sonnet for routine) |
| `model.thinking_budget_tokens` | 0 = extended thinking off (latency) |
| `model.recycle_after_turns` | bound warm-session context growth |
| `model.cli.*` | exact CLI flags, warm vs one-shot, stdin message shape, env |
| `overlay.*` | window size/position/opacity, heartbeat staleness |
| `dev.use_sample_state` / `log_prompts` | offline testing + prompt logging |

The strategist persona, per-screen playbooks, and the strict output contract
live in `prompts/system_prompt.md` + `prompts/screen_playbooks.md` — edit those
to tune advice style. They're concatenated into the model's system prompt.

---

## Project layout

```
sts2-advisor/
├── config.yaml              # all knobs
├── requirements.txt
├── bridge/
│   ├── main.py              # poll loop + orchestration + heartbeat + run detection
│   ├── client.py            # STS2MCP HTTP client — GET-only by construction
│   ├── screens.py           # state_type -> decision/no-op; per-screen option extraction
│   ├── signature.py         # stable per-screen signature + debounce/de-dupe
│   ├── grounding.py         # dataset join (authoritative text) + prompt assembly
│   ├── thesis.py            # LOCKED (append-only) + PLAN (volatile) parse/persist
│   ├── model_client.py      # warm claude stream-json session + one-shot fallback + --selftest
│   ├── worker.py            # worker thread, "thinking…" placeholder, stale-guard
│   └── util.py              # config, paths, logging, executable resolution
├── data/
│   ├── fetch_data.py        # download spire-codex cards/relics
│   └── sample_state.json    # offline test fixture
├── prompts/
│   ├── system_prompt.md     # StS2 strategist persona + grounding rules + output contract
│   └── screen_playbooks.md  # per-screen decision playbooks
├── overlay/overlay.py       # always-on-top window; tails advice; heartbeat show/hide
└── runtime/                 # thesis.json, latest_advice.txt, heartbeat, advisor.log (gitignored)
```

---

## Notes, limits, and known risks

- **Observe-only is structural:** `client.py` exposes only GET. There is no
  method anywhere that POSTs an action.
- **Early Access churn:** StS2 patches ~biweekly. Card text drifts (re-run
  `fetch_data.py`); the STS2MCP state shape can change (adjust `extract_options`).
- **Dataset gaps:** if a card/relic isn't in the local dataset, the prompt says
  so and the model reasons from the in-state description — it's told *not* to
  assume the card is weak just because data is missing.
- **`boss_card_select` routing** is inferred from grid size / boss context
  (`screens.model_kind`); adjust the threshold if needed.
- **Licensing:** the dataset (spire-codex) is PolyForm Noncommercial — fine for
  personal use, not for redistribution. Don't ship game data/text.
