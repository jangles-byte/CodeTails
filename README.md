# ✻ CodeTails

A self-hosted dashboard for the **Claude Code CLI**. Black terminal skin, iMessage-style
thread, reachable from your phone over Tailscale.

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

## What it does

- **Drives the real `claude` CLI.** Each session is a long-lived
  `claude --print --input-format stream-json` process. Streaming text, tool calls,
  diffs, todos, cost and context all come straight from the CLI's own event stream.
- **Sessions survive.** Change model or permissions mid-conversation and CodeTails
  respawns the CLI with `--resume`, so nothing is lost. If the process dies, your next
  message brings it back.
- **Your history is there.** Every past Claude Code session on this machine is browsable
  in the sidebar and renders through the same view. Tap *resume* to pick one up.
- **Phone-first.** Bubbles, swipe-in drawer, safe-area padding, add-to-home-screen.
  Reconnects and replays whatever it missed while your screen was off.
- **Skinnable.** Eight built-in skins, a live tuner (text size, radius, glow, density,
  texture) and a full colour editor. Save your own, export/import as JSON.

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

Tailscale is found automatically (`/Applications/Tailscale.app`, Homebrew, or `$PATH`).
If it isn't running you still get the LAN URL.

On iOS: open the link in Safari → Share → *Add to Home Screen*. It launches full-screen
with its own icon.

## Permissions

Headless Claude Code cannot pop its own approval dialog, so it auto-denies anything the
current mode doesn't cover. CodeTails turns that refusal into one tap:

- **allow &lt;Tool&gt; & retry** — adds the tool to `--allowedTools`, resumes the session, retries
- **accept edits** / **bypass all** — switches `--permission-mode` and resumes

Default mode is `acceptEdits` (file edits go through, shell commands ask). Change the
default in Settings, or per-session from the `perm` chip in the header.

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
