# Logi Options+ 2.7 IPC Security Check — Investigation & Bypass

**Date**: September 2026
**Trigger**: `kvm_daemon_windows.py` silently broke — the agent started refusing all connections from non-Logitech processes
**Affected version**: Logi Options+ **2.7.961922** (Windows, first observed build; 2.6.944893 and earlier unaffected)
**Result**: Direct named-pipe IPC is now gated on client-code-signing. Full API access restored via the UI's own renderer relay (CDP), documented below.

---

## Symptom

Every connection to `\\.\pipe\logitech_kiros_agent-<hash>` is accepted at the OS level
(`CreateFile` succeeds), then **closed within ~100ms with zero bytes sent** — no
connection greeting, no response, nothing. `WriteFile` afterwards fails with
`ERROR_NO_DATA (232)`.

Not a wedged agent: same behavior on a freshly restarted process.

## What changed: the security check

Enabling agent logging (`logioptionsplus_agent.exe --enable_logging`) and connecting
once produces the smoking gun in `%TEMP%\com.logi.optionsplus.agent.logs\*-agent-*.log`:

```
LocalServerImpl::asyncAccept: Connected client: 0x... failed security check
```

Strings embedded in `logioptionsplus_agent.exe` (68MB, Qt/C++, thekogans IPC library)
reveal what the check collects per connection:

```
Remote Info:  Handle: ... Conn: ... PID: ... start_time: ... Path: ... Signed:Yes/No
```

The import table confirms the mechanism:

| API | Role |
|-----|------|
| `GetNamedPipeClientProcessId` | identify the connecting client process |
| `QueryFullProcessImageName` | get its executable path |
| `WinVerifyTrust`, `CryptQueryObject`, `CertGetNameString` | Authenticode verification + publisher extraction |

### Evidence for publisher pinning (not just "is it signed")

- `python.exe` from python.org is **validly signed** (CN=Python Software Foundation) — still rejected.
- A copy of `python.exe` renamed to `logioptionsplus.exe` — rejected (so it's not a basename allowlist either).
- The check also records the client's process **start time** (PID-reuse hardening) and full image **path**.

Conclusion: the agent requires the client image to be **signed by Logitech** (likely
publisher name match via `CertGetNameString`, possibly also path checks). Source path
reference: `C:\builds\kiros\kiros\logi\api\src\server_named_pipes\server_impl.cpp` (PDB
path in binary). The one "failed security check" string appears **only** in the
named-pipes server — the marconi listener (below) has its own, different protocol.

## Dead ends mapped

| Avenue | Outcome |
|--------|---------|
| Spoof process name (`logioptionsplus.exe` rename) | Rejected — signature-based |
| Validly-signed third-party binary (python.org PSF) | Rejected — publisher pinned to Logitech |
| WebSocket port **59869** | Not the API. It's `logitech_marconi` — the Flow feature's P2P tunnel endpoint (TLS-encrypted tunnels between Flow peers, ClientHello/ServerHello packets). Silent to HTTP/WS probes; logged as `Stream disconnect, peer: ... host: ... 59869` |
| `ELECTRON_RUN_AS_NODE=1` on the UI exe | Neutralized — the full app boots anyway (custom Electron build; no fuse sentinel `dL7pKGdn...SmashHeader` in the binary, so the check was compiled out, not fused) |
| Other Logitech exes (appbroker, updater, PlugInInstaller) | None are Electron; no scriptable signed host |
| `LogiPluginService` / `logitech_kiros_updater` pipes | Different protocols (LogiConn), don't proxy the agent API |
| `C:\ProgramData\LogiOptionsPlus` config tampering | Not writable by user; keys.json is Flow pairing keys, unrelated |

## The bypass: the UI's own agent relay over CDP

The Options+ **UI is Electron** (Chrome 142 / Electron 39.1.2), and its main process
(Logitech-signed — passes the security check) already maintains an agent socket relay
for its renderer. Found in `resources/app.asar`:

- **main.js** registers ipc channels (`server_named_pipes`-equivalent in JS):
  `CREATE_CONNECTION` (build socket wrapper, pipe path auto-discovered),
  `CONNECT_AGENT` (connect), `SOCKET_SEND` (frame + write — the same
  `LE32 + BE32 + "json" + BE32 + msg` wire format, module 2824), and broadcasts
  socket events (`SOCKET_OPEN/CLOSE/ERROR/ON_MESSAGE`) to the renderer.
- **preload.bundle.js** exposes this to the page as `window.electronNet`
  (`createConnection`, `connect`, `send`, `onMessage`, ...).
- The renderer can send **arbitrary agent messages** through it — the UI's own
  analytics code does exactly that (`SET /scarif/event` built by hand in main.js).

And the release UI honors `--remote-debugging-port`:

```powershell
"C:\Program Files\LogiOptionsPlus\logioptionsplus.exe" --remote-debugging-port=9222
```

With Chrome DevTools Protocol we attach to the page target and drive `electronNet`
from the page context. The agent sees the UI's main process as the client. **Full
documented API restored**: `GET /devices/list`, `SET /change_host/<id>/host`,
everything in `api-reference.md`.

### Verified live (2.7.961922)

| Operation | Result |
|-----------|--------|
| `GET /devices/list` | `SUCCESS` — both devices returned |
| `SET /updates/check_now` (harmless write test) | `SUCCESS` |
| `SET /change_host/dev00000000/host {host:1}` (MX Master 3S) | `SUCCESS` — device physically left for the Mac |
| `SET /change_host/dev00000001/host {host:1}` (MX Keys S) | `SUCCESS` — device physically left for the Mac |

Client implementation: [`agent_cdp_client.py`](agent_cdp_client.py) (stdlib only).

```powershell
python agent_cdp_client.py --list          # devices + current host
python agent_cdp_client.py --switch 1      # everyone to host 1
```

### Quirks

- Responses are broadcast to **all** renderer listeners — match by `msgId` (the
  relay sees the UI's own traffic too, e.g. `/device_recommendation_enabled`).
- The relay connection must be primed once per page load:
  `createConnection()` → `connect()` before the first `send` (handled by the client).
- Requests use `msg_id`, responses `msgId` (unchanged).

## Operational caveats

1. **The UI must run with `--remote-debugging-port=9222`.** Without the flag there is
   no way in (as far as this investigation found). If the UI restarts (update,
  crash, reboot), relaunch it with the flag.
2. **Port 9222 is an open control surface**: any local process can drive the UI via
   CDP. That is strictly more powerful than the pipe access Logitech just revoked.
   Acceptable trade-off for a personal machine; do not expose it beyond loopback
   (it binds 127.0.0.1 only).
3. The CDP attach can race the UI's boot; attach after the page target exists.
4. macOS 2.7 not yet tested — if the same check shipped there, the same relay trick
   applies (`--remote-debugging-port` + the Mac UI's own agent bridge), but the Unix
   socket may also still be ungated. Verify before migrating.

### Hibernate / fast-startup resume wedges the agent (observed once)

A "restart" that is actually a Windows fast-startup shutdown or hibernate-resume
does NOT restart user processes — they come back with their original start times.
The stale agent survives but is wedged: its device connections are dead, and a
freshly launched UI hangs forever on its splash at **"Getting resources"**
("Getting resources error" + TROUBLESHOOT button).

Diagnosis: `Get-Process logioptionsplus_agent` shows a `StartTime` older than the
resume → stale process. The monitor daemon cannot fix this class of failure — its
self-healing covers the UI/relay only; the agent pipe itself is up but comatose.

Fix (clean Logi stack restart, in order):

```powershell
Get-Process logioptionsplus* | Where-Object Name -ne 'logioptionsplus_updater' | Stop-Process -Force
Start-Process "C:\Program Files\LogiOptionsPlus\logioptionsplus_agent.exe"
Start-Sleep 8   # wait for the pipe
Start-Process "C:\Program Files\LogiOptionsPlus\logioptionsplus.exe" --remote-debugging-port=9222
```

The updater runs elevated and cannot (and need not) be killed.

## Why this works (threat-model view)

Logi hardened the **transport** (who may hold a pipe handle) but left the UI's
**in-process API bridge** in place, and left Chromium's remote debugging available in
the release build. Any path that reaches the renderer's JS context gets agent access
by construction — because that's how the UI itself works.

## Timeline

| Time (local) | Event |
|---|---|
| 03:21 | `kvm_daemon_windows.py --dry-run` fails: pipe closes instantly, error 232 |
| 03:24 | Agent restart — same behavior (not wedged) |
| 03:27 | Binary scan: `GetNamedPipeClientProcessId` + `WinVerifyTrust` imports, source paths |
| 03:29 | `--enable_logging` → log shows `failed security check`; name spoof fails |
| 03:33 | 59869 identified as marconi (Flow) — dead end |
| 03:35 | `ELECTRON_RUN_AS_NODE` neutralized |
| 03:41 | asar extracted: `electronNet` relay discovered in preload + main |
| 03:45 | `--remote-debugging-port=9222` responds; CDP attach works |
| 03:47 | First round-trip: `GET /devices/list` → `SUCCESS` |
| 03:49 | Harmless `SET /updates/check_now` → `SUCCESS` |
| 03:52 | Real KVM switch: both devices to host 1 — `SUCCESS` |
