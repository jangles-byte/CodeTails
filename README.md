<div align="center">

# ✻ CodeTails

### Take Claude With You!

**Your real Claude Code sessions — all of them — on your phone.**

</div>

---

Dispatching a task to a remote agent and waiting for a verdict is not the same as
*working*. You can't see what it's doing. You can't stop it when it goes the wrong way.
And when it's done, that conversation is gone — you can't open it tomorrow and say
"actually, change the query on line 40."

CodeTails is the other thing. It puts **the Claude Code sessions already on your machine**
in your pocket:

- **Every past conversation is right there.** Sidebar lists every project and every
  session Claude Code has ever run on this box. Tap one, read the whole thread, tap
  *resume* — you're back in it with full context, from the couch.
- **You watch it work.** Text streams in token by token. Tool calls, diffs, command
  output and running cost appear as they happen. Wrong turn? Hit stop.
- **It's your actual machine.** Your files, your git repo, your CLAUDE.md, your MCP
  servers, your credentials. Not a sandbox with a copy of your code.
- **Desktop and phone are the same session.** Start something at your desk, keep going on
  the train, finish it at your desk. No handoff, no re-explaining.

Double-click **`CodeTails.command`** and it's running.

```
CodeTails/
├── CodeTails.command     ← double-click this
├── server.py             stdlib-only HTTP + SSE server
├── config.json           created on first run (token, skin, preferences)
├── ct/                   engine, projects, tailnet, QR
├── web/                  the dashboard (no build step, no CDN)
└── tools/make_icons.py   regenerates the app icons
```

## How it works

Each session is a long-lived `claude --print --input-format stream-json` process. The
CLI's own event stream — text deltas, tool calls, permission denials, token usage, cost —
is normalised into a replayable log that any number of browsers can subscribe to over SSE.
Your phone locking its screen doesn't matter: it reconnects and replays whatever it missed.

Change model or permission mode mid-conversation and CodeTails respawns the CLI with
`--resume`, so nothing is lost. If the process dies, your next message brings it back.

## Reaching it from your phone

The launcher prints three URLs and a QR code:

```
local    http://localhost:8790/?t=…
lan      http://192.168.1.x:8790/?t=…
tailnet  http://100.x.x.x:8790/?t=…
```

Scan the QR with your phone's camera while it's on the same tailnet — that's it.
The `?t=` token is stored in `config.json` and set as an `HttpOnly` cookie on first load.

> **A working CodeTails link is a shell on the host.** Claude Code reads files, writes
> files and runs commands as you. Treat the link like a private SSH key, and read
> [SECURITY.md](SECURITY.md) before exposing it to anything.

By default the server answers **only loopback and Tailscale addresses** (`100.64.0.0/10`,
`fd7a:115c:a1e0::/48`) — a hostile LAN can't reach it even though the port is open. Set
`"allow_lan": true` in `config.json` if you want it on your home network too, knowing
there is no TLS on that path.

On iOS: open the link in Safari → Share → *Add to Home Screen*. It launches full-screen
with its own icon.

## Permissions

Headless Claude Code cannot pop its own approval dialog, so it auto-denies anything the
current mode doesn't cover. CodeTails turns that refusal into one tap:

- **allow &lt;Tool&gt; & retry** — adds the tool to `--allowedTools`, resumes the session, retries
- **accept edits** / **bypass all** — switches `--permission-mode` and resumes

Default mode is `acceptEdits` (file edits go through, shell commands ask). Change the
default in Settings, or per-session from the `perm` chip in the header.

## Making it yours

Skins are just CSS variables plus a surface style, so one you build in the tuner is a
first-class citizen. Twelve ship in the box across two surface languages:

- **Messaging** — system type, filled bubbles with tails, iOS-style tool cards.
  *Hologram* (90s foil: a procedurally scattered flake field and iridescent bubbles),
  *Deep Field* (a generated starfield with drifting nebula), *Cobalt*, *Night Sky*,
  *Blurple*, *Fern*, *Daylight*.
- **Terminal** — monospace, hairlines, `⏺` and `⎿`. *Clay*, *Phosphor*, *Amber*,
  *Synth*, *Paper*.

Both backdrops are drawn at runtime on a canvas — no image assets, nothing fetched.
Add live controls for text size, corner radius, glow, density and texture, plus a full
colour editor, and save your own. Export and import as JSON.

## Keyboard

| | |
|---|---|
| `⏎` | send (`⇧⏎` newline) |
| `⌘K` | command palette — app actions + every slash command the CLI reports |
| `⌘N` | new session |
| `⌘J` | skins |
| `⌘/` | toggle sidebar |
| `esc` | interrupt the current turn |

## Running it by hand

```bash
python3 server.py --port 8790 --no-open
```

Needs Python 3.9+ (macOS ships it) and the `claude` CLI on `$PATH`. Nothing else —
no pip installs, no npm, no internet.

`config.json` holds your token and is in `.gitignore`. If you fork this, keep it there.

## License

MIT — see [LICENSE](LICENSE). Not affiliated with Anthropic; "Claude Code" is their CLI,
this is just a front end for it.
