# logitech-ipc-protocol

Reverse-engineered documentation of the Logi Options+ agent IPC protocol. Enables programmatic control of Logitech multi-host devices (host switching, device queries) without raw HID access.

The protocol has not been publicly documented before this project.

## Problem

macOS blocks raw HID access to Bluetooth input devices at the kernel level. No permissions, entitlements, or hacks bypass this. The Logi Options+ agent has Apple-signed entitlements (`com.apple.security.device.bluetooth`) that grant it Bluetooth HID access. This project communicates with the agent through its IPC channel instead.

## Files

| File | Description |
|------|-------------|
| `logi-options-ipc-reverse-engineering.md` | Full reverse engineering chronicle |
| `software-kvm-setup.md` | Two-way software KVM setup guide (Windows + Mac) |
| `switch_to_windows.py` | Switches Logitech devices and monitor input via the IPC protocol |
| `query_feature_index.py` | Discovers HID++ ChangeHost feature index for Logitech devices (Windows) |
| `query_agent_windows.py` | Queries the agent on Windows via named pipe |
| `config.ini` | Example UnifiedSwitch configuration |

## Usage (Mac)

```bash
python3 switch_to_windows.py 0        # Switch to host 0 (DisplayPort)
python3 switch_to_windows.py 1        # Switch to host 1 (HDMI)
python3 switch_to_windows.py --dry-run 0  # Show what would happen
```

Requires Logi Options+ running and `m1ddc` installed (`brew install m1ddc`).

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

For long-running automation, reconnect on `BrokenPipeError` and re-discover the socket/pipe path.

## Windows HID++ gotchas

These apply when sending HID++ commands directly (not through the agent):

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

**Device re-pairing changes HID paths.** Switching from Bolt receiver to direct BT LE changes the path entirely. Run `query_agent_windows.py` to get current paths from the agent.

**BT LE GATT vendor collection goes "Unknown."** Windows occasionally fails to initialize the HID++ GATT service. The device works normally but the vendor command channel is dead. Fix: toggle Bluetooth off/on in Windows Settings. This is a Windows/firmware issue.

## Tested versions

| Version | Status |
|---------|--------|
| Logi Options+ 2.0.840907 | Working (macOS Tahoe, Windows 11) |

The wire protocol and core API paths (`/devices/list`, `/change_host/<id>/host`) have been stable. Device re-pairing broke HID paths and collection numbers but the IPC protocol itself was unaffected.
