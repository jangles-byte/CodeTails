# Security

**Read this before you expose CodeTails to anything.**

CodeTails runs the Claude Code CLI on your machine with your credentials. Claude Code can
read and write files and run shell commands. Therefore:

> **A working CodeTails link is equivalent to a shell on the host, as your user account.**

Treat the URL the launcher prints the way you would treat a private SSH key.

## What protects you

| Control | What it stops |
|---|---|
| **Peer allowlist** — only loopback, `100.64.0.0/10` and `fd7a:115c:a1e0::/48` (Tailscale) are answered | Anyone on the café wifi who port-scans you. Set `allow_lan: true` in `config.json` to also accept RFC1918 addresses. |
| **Access token** — 144-bit, generated on first run, required on *every* request including loopback | Other local users and local processes; anyone who reaches the port without the link |
| **Host header allowlist** (IP literals, `localhost`, `*.ts.net`, `*.local`, this host's name) | DNS rebinding — an attacker domain that resolves to `127.0.0.1` to become "same-origin" with you |
| **`Origin` / `Sec-Fetch-Site` check on every non-GET** | CSRF — a web page you visit silently POSTing to `localhost:8790` to start a `bypassPermissions` session |
| **`HttpOnly` cookie**, `SameSite=Lax` | Token theft via script; cross-site cookie replay |
| **CSP `default-src 'self'`**, `nosniff`, `no-referrer`, `frame-ancestors 'none'` | XSS blast radius, token leaking through `Referer`, clickjacking |
| **Constant-time token compare** | Timing oracles |
| Session ids validated, static paths contained, no shell used for subprocesses | Path traversal, command injection |

## What does *not* protect you

- **There is no TLS.** Tailscale encrypts tailnet traffic end to end (WireGuard), so the
  tailnet path is fine. If you set `allow_lan: true`, your token and your source code cross
  the LAN in cleartext. Don't do that on a network you don't own. For real TLS, front it
  with `tailscale serve` (see below).
- **The token is in the URL** you scan or click. It therefore lands in browser history and
  in any screenshot or photo of the QR code. `Referrer-Policy: no-referrer` stops it leaking
  outward, but treat screenshots with care. Rotate by deleting `token` from `config.json`
  and restarting.
- **Everyone on your tailnet can reach the port** if they know the token. On a shared
  tailnet, restrict it with a Tailscale ACL.
- **CodeTails does not sandbox Claude Code.** Permission modes are Claude Code's own.
  `bypassPermissions` means exactly that — arbitrary commands, no prompts. The one-tap
  "allow &lt;Tool&gt;" card is a convenience for *you*; it is not a security boundary.
- **Transcripts are shown verbatim.** If Claude reads a `.env`, its contents are on screen
  and in `~/.claude/projects/`.

## Hardening further

```jsonc
// config.json
{
  "host": "100.x.y.z",        // your own tailnet IP — bind that interface only
  "allow_lan": false,         // keep LAN out (default)
  "open_browser": false
}
```

Put it behind Tailscale's own HTTPS + identity layer instead of exposing the port:

```bash
tailscale serve --bg 8790          # https://<host>.<tailnet>.ts.net, TLS terminated by Tailscale
```

Never put CodeTails behind a public reverse proxy or a port-forward. It is not built for
the open internet and no token makes it safe there.

## What CodeTails never touches

Worth stating plainly, since it runs next to your Anthropic account:

- It **does not read your Claude credentials.** Nothing in this repo opens
  `~/.claude/.credentials.json`, reads the macOS keychain, or looks at
  `ANTHROPIC_API_KEY`. The `claude` child process authenticates itself exactly as it
  would if you typed `claude` in a terminal — CodeTails only inherits the environment
  and prepends a few directories to `PATH`.
- It **makes no outbound connections.** There is no HTTP client anywhere in the source.
  The only socket call besides the listener asks the kernel which local address the
  default route uses, aimed at an RFC 5737 test address that sends no packets.
- It **loads nothing from the internet.** No CDN, no fonts, no analytics, no telemetry.
  The QR encoder, the icons and the syntax highlighter are all in-tree, and the page
  runs under `default-src 'self'`.
- It runs exactly **three** subprocesses, all argument-list form with no shell:
  `claude`, the Tailscale CLI (`status --json`), and `git status` in the session folder.

One accepted limit: `/api/boot` returns your tailnet URLs with the token in them, so an
authenticated client can render the QR and "copy link". Same-origin script injection could
therefore read the token despite the `HttpOnly` cookie — though at that point it could
simply use the API directly. `connect-src 'self'` is what keeps such a payload from
shipping anything off the box.

## Reporting

Found something? Open a GitHub issue for low-risk items, or email the maintainer for
anything exploitable. Please don't file a public PoC that hands out shells.
