# Zwanki

Review Anki flashcards with the buttons on your **Zwift Ride** handlebars, and make your smart trainer harder or easier from the same buttons.

- Reveal answers and grade cards (Again / Hard / Good / Easy), replay audio, undo.
- Any grade button shows the answer first if the question is still on screen, so a stray press never grades a card you haven't seen.
- Optional smart trainer control (tested on a Wahoo KICKR CORE; any trainer with standard Bluetooth FTMS control should work): resistance, simulated hill, or ERG power.
- Records your rides (power, cadence, speed, heart rate from a chest strap, trainer settings), and lets **Claude** read them for a weekly training review.
- A settings page where every button lights up as you press it and can be reassigned, with live pressure meters for the big buttons.
- Runs on macOS, Windows and Linux, with a menu bar / tray icon.

Not affiliated with Zwift, Wahoo or Anki.

## What you need

- [Anki](https://apps.ankiweb.net) desktop with the [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on (in Anki: Tools → Add-ons → Get Add-ons → code `2055492159`, then restart Anki).
- Zwift Ride controllers (tested on firmware 1.1.0). Zwift Click and Play aren't supported yet.
- Bluetooth LE on your computer.
- Close the Zwift app while using Zwanki: the controllers talk to one app at a time.

## Install

Download the build for your system from the [Releases](../../releases) page.

**macOS:** unzip, move `Zwanki.app` to Applications and open it. The app isn't signed by an Apple developer account yet, so macOS blocks the first launch: open System Settings → Privacy & Security and click **Open Anyway** (or run `xattr -dr com.apple.quarantine /Applications/Zwanki.app`). Allow Bluetooth when asked. Zwanki lives in the menu bar; it has no Dock icon.

**Windows:** unzip and run `zwanki.exe` in the `zwanki` folder. SmartScreen may warn about an unknown publisher: click **More info → Run anyway**. Zwanki lives in the system tray.

**Linux:** extract and run `./zwanki`. Bluetooth goes through BlueZ. The tray icon needs an X11 session or a tray extension; without one, Zwanki still runs and you use the settings page.

## Using it

Zwanki opens Anki (if it isn't running), connects to the controllers, and serves its settings page at **http://127.0.0.1:8770**. It opens the page on first start; later, use **Open settings** in the tray menu, or simply start Zwanki again.

Press a button on the bike and it lights up on the settings page; pick what it should do and the change applies immediately. The default layout:

| Button | Left controller | Right controller |
|---|---|---|
| Big button | Replay audio | Show answer |
| Up | Hard (2) | Again (1) |
| Down | Easy (4) | Good (3) |

The face buttons (A Good, B Hard, Y Easy, Z Again) and the D-pad (right: show answer, up: replay audio) are assigned too. Switch off **Buttons active** to try buttons without anything happening.

### Smart trainer

Turn on **Use my trainer** on the settings page. Assign *Trainer harder* / *Trainer easier* to buttons, and pick what they change:

| Mode | Each press | Starts at |
|---|---|---|
| Resistance | 5% | 0% |
| Hill (simulation) | 0.5% grade | 0% |
| ERG | 10 W | 100 W |

Connecting alone changes nothing on the trainer. Turning the trainer off or quitting Zwanki puts it back to easy.

### Rides and heart rate

While the trainer is connected, Zwanki records a ride as soon as you pedal, and ends it after 5 minutes without pedaling (rides under a minute aren't kept). Every second it stores power, cadence, speed and, with **Use my heart rate strap** on, your heart rate (Polar H10 or any standard Bluetooth strap), plus each trainer setting change.

### Weekly review with Claude

Claude can read your rides through Zwanki's [MCP](https://modelcontextprotocol.io) server, even when Zwanki isn't running. On the settings page under **Rides → Use with Claude**:

- **Claude Desktop:** click *Add to Claude Desktop* (it adds a `zwanki` entry to Claude Desktop's settings and keeps a backup), then restart Claude Desktop.
- **Claude Code:** copy the `claude mcp add zwanki …` command shown there and run it once.

Then ask something like *"Give me my weekly training review from Zwanki and compare it with the last three weeks."* Claude gets three read-only tools:

| Tool | |
|---|---|
| `weekly_summary` | Totals per week: rides, moving time, distance, energy, average power and heart rate. |
| `list_rides` | Rides in a date range with their key numbers, including normalized power and max heart rate. |
| `get_ride` | One ride in detail: trainer settings over time, minutes per setting, and the ride averaged over time blocks. |

The server is the app itself started with `--mcp` (on Windows: `zwanki-mcp.exe --mcp`).

### Files

| | Settings | Log |
|---|---|---|
| macOS | `~/Library/Application Support/Zwanki/config.toml` | `zwanki.log` next to it |
| Windows | `%APPDATA%\Zwanki\config.toml` | `zwanki.log` next to it |
| Linux | `~/.config/zwanki/config.toml` | `zwanki.log` next to it |

Rides are kept in `rides.db` (SQLite) in the same folder.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m zwanki                  # run it (--no-tray, --settings)
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m zwanki.kickr info       # talk to the trainer directly
```

Build the app for the system you're on:

```sh
.venv/bin/pip install -r requirements-build.txt
.venv/bin/python scripts/make_icons.py
.venv/bin/pyinstaller zwanki.spec           # -> dist/Zwanki.app, dist/zwanki/zwanki.exe or dist/zwanki
```

GitHub Actions (`.github/workflows/build.yml`) tests and builds every push for macOS (Apple Silicon and Intel), Windows and Linux. Pushing a tag such as `v0.1.0` publishes the builds as a release.

### How it works

| File | |
|---|---|
| `zwanki/ride.py` | The Zwift Ride's Bluetooth protocol: a `RideOn` handshake, then protobuf button messages. Button bits were mapped by hand on firmware 1.1.0. |
| `zwanki/anki.py` | AnkiConnect calls (`guiShowAnswer`, `guiAnswerCard`, …). |
| `zwanki/kickr.py` | Trainer control over the standard Fitness Machine Service (FTMS). |
| `zwanki/heartrate.py` | Heart rate straps (standard Heart Rate Service). |
| `zwanki/rides.py` | Ride recording (SQLite) and summaries: normalized power, weekly totals. |
| `zwanki/mcp.py`, `claude.py` | The MCP server for Claude, and adding it to Claude Desktop. |
| `zwanki/bridge.py` | Connects it all: presses become actions, state goes to the settings page. |
| `zwanki/server.py`, `settings.html` | The local settings page and its API (local requests only). |
| `zwanki/app.py`, `tray.py` | Startup, the tray icon, single-instance handling. |

The controller protocol builds on community reverse-engineering, notably [BikeControl](https://github.com/jonasbark/swiftcontrol) and [qdomyos-zwift](https://github.com/cagnulein/qdomyos-zwift).
