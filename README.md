# logitech-ipc-protocol

Reverse-engineered documentation of the Logi Options+ agent IPC protocol. Enables programmatic control of Logitech multi-host devices (host switching, device queries) without raw HID access, on both macOS and Windows.

The protocol has not been publicly documented before this project.

## Problem

macOS blocks raw HID access to Bluetooth input devices at the kernel level. No permissions, entitlements, or hacks bypass this. The Logi Options+ agent has Apple-signed entitlements (`com.apple.security.device.bluetooth`) that grant it Bluetooth HID access. This project communicates with the agent through its IPC channel instead.

## Files

| File | Description |
|------|-------------|
| `logi-options-ipc-reverse-engineering.md` | Full reverse engineering chronicle |
| `optionsplus-2.7-ipc-security.md` | v2.7 named-pipe security check: analysis + CDP relay bypass |
| `agent_cdp_client.py` | Windows transport for v2.7+: agent IPC via the UI's renderer relay (CDP) — see the doc above |
| `kvm_monitor_daemon_windows.py` | Windows monitor-follow daemon: CDP relay presence polling + ControlMyMonitor DDC/CI; auto-relaunches the UI with the debug flag if needed |
| `kvm_monitor_daemon.py` | Mac monitor-follow daemon: agent IPC presence polling + m1ddc DDC/CI |
| `com.logi.kvm-monitor.plist` | macOS LaunchAgent template for the Mac daemon |
| `api-reference.md` | Agent API reference: working endpoints, protobuf types, device capabilities |
| `switch_to_windows.py` | Mac-side script that switches Logitech devices and monitor input via Unix socket IPC |
| `software-kvm-setup.md` | Two-way software KVM setup guide (pre-2.7 era: Karabiner/AHK hotkeys — superseded by the monitor-follow daemons) |
| `kvm_daemon_windows.py` | *Legacy (≤2.6)*: Windows hotkey daemon via direct named pipe — **broken on 2.7+** (security check) |
| `kvm_config.ini` | Windows configuration (monitor DDC/CI input values; hotkeys for the legacy daemon) |
| `query_feature_index.py` | Discovers HID++ ChangeHost feature index for Logitech devices (direct HID, any version) |
| `query_agent_windows.py` | *Legacy (≤2.6)*: queries the agent on Windows via direct named pipe — **broken on 2.7+** |
| `config.ini` | Legacy UnifiedSwitch configuration |

## Usage

### Mac

```bash
python3 switch_to_windows.py 0        # Switch to host 0 (DisplayPort)
python3 switch_to_windows.py 1        # Switch to host 1 (HDMI)
python3 switch_to_windows.py --dry-run 0  # Show what would happen
```

Requires Logi Options+ running and `m1ddc` installed (`brew install m1ddc`).

### Windows (Options+ 2.7+)

The direct named pipe is gated on client code-signing (see "v2.7 and later" below). Use the CDP relay transport:

```powershell
# UI must be running with the debug flag (the monitor daemon adds/repairs this automatically)
"C:\Program Files\LogiOptionsPlus\logioptionsplus.exe" --remote-debugging-port=9222

python agent_cdp_client.py --list          # devices + current host
python agent_cdp_client.py --switch 1      # one-shot switch to host 1
```

For the one-button loop (Easy-Switch key moves devices, daemon follows with the monitor), see "v2.7 and later" below.

<details>
<summary>Legacy (Options+ ≤2.6): direct named pipe</summary>

```powershell
python kvm_daemon_windows.py --switch 1    # one-shot
python kvm_daemon_windows.py --dry-run     # show devices and hotkeys
python query_agent_windows.py              # raw agent query
```

Requires `pip install pywin32 keyboard`. On ≤2.6 the same scripts also served an
AHK hotkey listener (`Win+1/2/3`, script never committed); the monitor-follow
daemon replaces that flow entirely.

</details>

## Monitor-follow daemon (native Enhanced Easy-Switch + DDC/CI)

With Enhanced Easy-Switch (Logi Options+ v2.3+, MX Keys S firmware 81.2.17+),
pressing the keyboard's physical Easy-Switch key moves keyboard and mouse
between hosts natively. `kvm_monitor_daemon.py` completes the loop: it watches
the lead keyboard's presence via the agent IPC and switches the monitor input
(DDC/CI via m1ddc) — one physical key moves keyboard, mouse, AND monitor.

```bash
python3 kvm_monitor_daemon.py                 # foreground
python3 kvm_monitor_daemon.py --dry-run       # log transitions only
python3 kvm_monitor_daemon.py --here-input 17 --away-input 15
```

### Auto-start on boot (macOS LaunchAgent)

```bash
sed "s|__INSTALL_PATH__|$(pwd)|" com.logi.kvm-monitor.plist > ~/Library/LaunchAgents/com.logi.kvm-monitor.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.logi.kvm-monitor.plist
```

Logs: `/tmp/kvm_monitor.log`. Stop with
`launchctl bootout gui/$(id -u)/com.logi.kvm-monitor`.

A Windows twin (`kvm_monitor_daemon_windows.py`) does the same via the CDP relay
and ControlMyMonitor — see "v2.7 and later" below.

## Protocol

The agent listens on:
- **macOS**: Unix domain socket at `/tmp/logitech_kiros_agent-<hash>`
- **Windows**: Named pipe at `\\.\pipe\logitech_kiros_agent-<hash>`

Same wire protocol on both platforms. Binary frame format:

```
LE32(total_len) + BE32(proto_name_len) + "json" + BE32(msg_len) + JSON_message
```

Switch a device to a different host:

```json
{
  "msg_id": "1",
  "verb": "SET",
  "path": "/change_host/<device_id>/host",
  "payload": {
    "@type": "type.googleapis.com/logi.protocol.devices.ChangeHost",
    "host": 0
  }
}
```

The payload is a `google.protobuf.Any` field serialized as inline JSON with an `@type` annotation. The agent uses a strict protobuf JSON parser; unknown fields cause `INVALID_MESSAGE_RECEIVED`.

Requests use `msg_id` (snake_case). Responses use `msgId` (camelCase). Verbs are strings: `"GET"`, `"SET"`, `"SUBSCRIBE"`, `"BROADCAST"`.

See `logi-options-ipc-reverse-engineering.md` for the full protocol documentation.

## IPC error handling

| Scenario | What happens | Detection |
|----------|-------------|-----------|
| Agent not running | Socket/pipe doesn't exist | `connect()` raises `FileNotFoundError` or `ConnectionRefusedError` |
| Agent restarts mid-session | Connection breaks | `send()` raises `BrokenPipeError`; `recv()` returns empty |
| Device on another host | `NO_SUCH_PATH` | Check `result.code` |
| Device unreachable | `TIMEOUT` after ~3s | Check `result.code` |
| Malformed payload | `INVALID_MESSAGE_RECEIVED` | Missing `@type` or unknown fields |
| Socket hash changes | Old path gone | Always discover dynamically, never hardcode |
| Stale socket after restart | `ConnectionRefusedError` | Retry after short delay |
| Concurrent clients | Works fine | Agent handles multiple connections |
| **Non-Logitech client (2.7+, Windows)** | Connection accepted then closed instantly, zero bytes, no greeting | Logi-signed-publisher check on the client process — use the CDP relay (`agent_cdp_client.py`) |

For long-running automation, reconnect on `BrokenPipeError` and re-discover the socket/pipe path. macOS behavior on 2.7 is unverified — check before relying on the Unix socket there.

## Windows HID++ gotchas

> These apply when sending HID++ commands directly, not through the agent. On ≤2.6, `kvm_daemon_windows.py` avoided all of them by going through the agent's named pipe; on 2.7+ the agent path is the CDP relay.

<details>
<summary>Legacy HID++ gotchas (for direct HID access)</summary>

**HID++ collection varies per device.** The MX Master 3S exposes HID++ on COL02. The MX Keys S uses COL05. Both use usage page `FF43:0202`. Verify with:
```powershell
Get-PnpDeviceProperty -InstanceId "<instance_id>" -KeyName DEVPKEY_Device_HardwareIds
# Look for UP:FF43_U:0202
```

**Feature indices differ per device.** ChangeHost (0x1814) is at index `0x0A` on MX Keys S but `0x09` on MX Mechanical. Query at runtime via IRoot::GetFeature:
```
Send: {0x11, 0x00, 0x00, 0x0D, 0x18, 0x14, ...}  (20 bytes)
Read: response byte 4 = feature index
```

**Device re-pairing changes HID paths.** Switching from Bolt receiver to direct BT LE changes the path entirely. On ≤2.6, run `query_agent_windows.py` to get current paths from the agent; on 2.7+ use `agent_cdp_client.py --list`.

**BT LE GATT vendor collection goes "Unknown."** Windows occasionally fails to initialize the HID++ GATT service. The device works normally but the vendor command channel is dead. Fix: toggle Bluetooth off/on in Windows Settings. This is a Windows/firmware issue.

</details>

## Tested versions

| Version | Status |
|---------|--------|
| Logi Options+ 2.0.840907 | Working (macOS Tahoe, Windows 11) |
| Logi Options+ 2.6.944893 | Working; coupled Easy-Switch routes live — Enhanced Easy-Switch shipped (see api-reference.md) |
| Logi Options+ 2.7.961922 | **Direct pipe/socket IPC blocked** (client signature check) — works via CDP relay; see "v2.7 and later" |

The wire protocol and core API paths (`/devices/list`, `/change_host/<id>/host`) have been stable. Device re-pairing broke HID paths and collection numbers but the IPC protocol itself was unaffected.

## v2.7 and later (Windows)

The agent now runs a client security check on its named pipe (PID → image path →
Authenticode → Logitech publisher pinning) and closes any non-Logitech client
before the wire protocol even starts. Direct pipe clients (`kvm_daemon_windows.py`,
`query_agent_windows.py`) are **broken on 2.7+**; commands moved to Usage above.

`agent_cdp_client.py` restores full API access by driving the Options+ UI's
built-in agent relay over Chrome DevTools Protocol. Requirement: the UI runs with
`--remote-debugging-port=9222` (launch command in Usage; the monitor daemon
repairs this automatically if it's missing).

The one-button loop (Easy-Switch moves devices natively, daemon follows with
the monitor) and boot autostart:

```powershell
python kvm_monitor_daemon_windows.py            # foreground
python kvm_monitor_daemon_windows.py --dry-run
```

Requires `dependencies\ControlMyMonitor.exe` (NirSoft, see kvm_config.ini for
the download link). Boot persistence: a Startup-folder shortcut launching the
daemon via `pythonw` (single-instance mutex, logs to `%TEMP%\kvm_monitor_windows.log`)
— the daemon is the only boot entry; it brings up the UI with the flag itself.

Full investigation: `optionsplus-2.7-ipc-security.md`. Note: port 9222 lets any
local process drive the UI — loopback-only, acceptable for a personal machine.

## Disclaimer

This project is for **educational and research purposes only**. It documents an undocumented, unsupported protocol that Logitech can change or remove at any time. The authors are not responsible for any damage, data loss, bricked devices, or broken functionality resulting from use of this code or documentation. Use at your own risk.

This project is not affiliated with or endorsed by Logitech.

## License

[MIT](LICENSE)
