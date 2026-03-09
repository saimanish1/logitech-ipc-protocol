# Two-Way Software KVM Setup

## Windows PC + Mac (Single Monitor)

One hotkey switches keyboard, mouse, and monitor input between two computers.

- On Windows: `Win+1` = Windows, `Win+2` = Mac
- On Mac: `Cmd+1` = Windows, `Cmd+2` = Mac

---

## Hardware

| Device | Details |
|--------|---------|
| Keyboard | Logitech MX Keys S (PID: B378, Bluetooth LE direct) |
| Mouse | Logitech MX Master 3S (PID: B034, Bluetooth LE) |
| Channel 1 / Host 0 | Windows PC |
| Channel 2 / Host 1 | Mac |
| Channel 3 / Host 2 | iPad |
| Monitor Input - Windows | DisplayPort (DDC/CI value: 15) |
| Monitor Input - Mac | HDMI-2 (DDC/CI value: 18) |

---

## Architecture

```
ON WINDOWS:                              ON MAC:
Press Win+1 or Win+2                     Press Cmd+1 or Cmd+2
    |                                        |
    v                                        v
UnifiedSwitch.exe                        Karabiner-Elements
(low-level keyboard hook)                (intercepts hotkey)
    |                                        |
    v                                        v
LogiSwitch.exe                           switch_to_windows.py
  - Opens HID device handles               - Connects to Logi Options+ agent
  - Sends HID++ 2.0 packets                  via Unix domain socket IPC
  - Direct Bluetooth HID write              - Sends JSON commands over
    |                                          proprietary wire protocol
    v                                        |
ControlMyMonitor.exe                         v
  - DDC/CI via VCP code 0x60            Logi Options+ Agent
  - Sets monitor input source              - Has Apple entitlements for
    |                                        Bluetooth HID access
    v                                      - Sends HID++ to devices
Both devices switch channel                  |
+ Monitor switches input                    v
                                         m1ddc
                                           - DDC/CI on Apple Silicon
                                           - Sets monitor input source
                                             |
                                             v
                                         Both devices switch channel
                                         + Monitor switches input
```

---

## Windows Side

### Components

| Component | Purpose |
|-----------|---------|
| UnifiedSwitch.exe | Hotkey daemon (intercepts Win+1/2/3) |
| LogiSwitch.exe | Sends HID++ channel switch commands |
| ControlMyMonitor.exe | DDC/CI monitor input control |
| config.ini | Configuration file |

### Config (config.ini)

```ini
[PATHS]
clickmon=dependencies\ControlMyMonitor.exe

[INTERFACES]
keyboard_path=\\?\hid#{00001812-...}_dev_vid&02046d_pid&XXXX_rev&XXXX_<bt_address>&colXX#...#{4d1e55b2-f16f-11cf-88cb-001111000030}
mouse_path=\\?\hid#{00001812-...}_dev_vid&02046d_pid&XXXX_rev&XXXX_<bt_address>&colXX#...#{4d1e55b2-f16f-11cf-88cb-001111000030}

[SOURCES]
device1=15
device2=18
device3=

[SETTINGS]
multiMonitor=0
hotkeyMode=1
autoStart=1
```

- `keyboard_path` / `mouse_path`: HID device paths. Must point to the collection with usage page `FF43:0202` (HID++). Use `query_agent_windows.py` or `Configure_debug.exe` to find the correct paths.
- `device1`/`device2`: DDC/CI input source values (15 = DisplayPort, 18 = HDMI-2). Run `ControlMyMonitor.exe` to find your monitor's values.
- `hotkeyMode`: 1 = Win+1/2/3, 2 = Ctrl+Alt, 3 = Ctrl+Shift.

**Important**: The HID++ collection number varies per device. The MX Master 3S uses COL02, the MX Keys S uses COL05. Always verify the usage page is `FF43:0202`.

### How UnifiedSwitch.exe Works

1. Creates an invisible window, installs a global low-level keyboard hook via `SetWindowsHookExA(WH_KEYBOARD_LL, ...)`
2. On hotkey press: calls `ControlMyMonitor.exe /SetValue Primary 60 <value>` for monitor switching, then runs `LogiSwitch.exe <channel>`
3. Single instance via named mutex `UnifiedSwitch_SingleInstance`
4. Auto-start via `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`

### How LogiSwitch.exe Works

1. Loads HID paths from `config.ini`
2. Detects protocol: checks for BT LE UUID `00001812-0000-1000-8000-00805f9b34fb` in path
3. For Bluetooth LE devices (`switch_bluetooth()`):
   - Opens HID device with `CreateFileA()`
   - Looks up PID in `bt_devices[]` cache for known ChangeHost feature index
   - If not cached, queries the device via IRoot::GetFeature for feature 0x1814
   - Sends 20-byte HID++ 2.0 command: `{0x11, 0x00, feature_index, 0x1E, channel}`
   - If feature index cannot be determined, fails with exit code 1
4. For Bolt receivers (`switch_bolt()`): queries each paired device slot (1-3) via IRoot::GetFeature, sends ChangeHost to the first one that responds
5. 80ms delay between keyboard and mouse switches
6. Mouse wake: wiggles cursor with `mouse_event(MOUSEEVENTF_MOVE, ...)`

### Device Feature Indices

| Device | PID | Feature Index | Notes |
|--------|-----|---------------|-------|
| MX Keys S | B378 | 0x0A | Tested via IRoot::GetFeature query |
| MX Master 3S | B034 | 0x0A | Tested |
| MX Mechanical | B366 | 0x09 | Tested |
| MX Keys | B365 | 0x09 | Same family as MX Mechanical |
| MX Keys Mini | B35C | 0x09 | Same family |
| MX Master 3 | B033 | 0x0A | Same family as 3S |

Feature indices are device-specific and can differ even between models in the same product line. Use `query_feature_index.py` to discover yours.

---

## Mac Side

### The macOS Problem

macOS blocks all raw HID access to Bluetooth input devices at the kernel level. The Logi Options+ agent has Apple entitlements allowing Bluetooth HID access. Commands are sent to the agent via its Unix domain socket, and the agent relays them to the devices.

### Components

| Component | Purpose |
|-----------|---------|
| `switch_to_windows.py` | Main switch script (Python) |
| Karabiner-Elements | Hotkey binding (Cmd+1/Cmd+2) |
| m1ddc | DDC/CI monitor switching (Apple Silicon) |
| Logi Options+ Agent | Background agent with Bluetooth entitlements |

### How switch_to_windows.py Works

1. Discovers the agent socket via `glob('/tmp/logitech_kiros_agent-*')`
2. Connects and sends a GET `/permissions` to trigger registration handshake
3. Discards the protobuf registration frame + GET response
4. For each device, sends a SET to `/change_host/<device_id>/host` with `ChangeHost` payload
5. Calls `m1ddc set input <value>` for monitor switching

### Karabiner Configuration

Add complex modification rules to `~/.config/karabiner/karabiner.json`:

```json
[
  {
    "description": "KVM: Cmd+1 = Switch to Windows",
    "manipulators": [{
      "type": "basic",
      "from": {"key_code": "1", "modifiers": {"mandatory": ["command"], "optional": ["any"]}},
      "to": [{"shell_command": "/usr/bin/python3 /path/to/switch_to_windows.py 0 >> /tmp/kvm_switch.log 2>&1"}]
    }]
  },
  {
    "description": "KVM: Cmd+2 = Switch to Mac",
    "manipulators": [{
      "type": "basic",
      "from": {"key_code": "2", "modifiers": {"mandatory": ["command"], "optional": ["any"]}},
      "to": [{"shell_command": "/usr/bin/python3 /path/to/switch_to_windows.py 1 >> /tmp/kvm_switch.log 2>&1"}]
    }]
  }
]
```

The `karabiner_console_user_server` process must be running for shell commands to execute. If hotkeys do not work:

```bash
nohup '/Library/Application Support/org.pqrs/Karabiner-Elements/bin/karabiner_console_user_server' > /tmp/karabiner_console.log 2>&1 &
```

---

## IPC Protocol Reference

### Socket / Pipe Location

- **macOS**: `/tmp/logitech_kiros_agent-<md5_hash>` (Unix domain socket)
- **Windows**: `\\.\pipe\logitech_kiros_agent-<md5_hash>` (named pipe)

Discover dynamically. Never hardcode the hash.

### Wire Protocol

```
+-------------------+-------------------+------------------+-------------------+------------------+
| LE32: total_len   | BE32: proto_len   | proto_name       | BE32: msg_len     | msg_data         |
| (4 bytes)         | (4 bytes)         | (variable)       | (4 bytes)         | (variable)       |
+-------------------+-------------------+------------------+-------------------+------------------+
```

- `total_len`: little-endian uint32, length of everything after this field
- `proto_len`: big-endian uint32, length of protocol name
- `proto_name`: `"json"` or `"protobuf"`
- `msg_len`: big-endian uint32, length of message
- `msg_data`: JSON string or protobuf binary

### Connection Handshake

```python
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect('/tmp/logitech_kiros_agent-<hash>')

# Send any GET to trigger registration
send_json(s, {'msg_id': '1', 'verb': 'GET', 'path': '/permissions'})
recv_all(s)  # Discard protobuf registration + GET response

# Connection ready for subsequent requests
```

### JSON Message Format

Request:
```json
{"msg_id": "1", "verb": "GET", "path": "/devices/list"}
```

Request with payload:
```json
{
  "msg_id": "10",
  "verb": "SET",
  "path": "/change_host/<device_id>/host",
  "payload": {
    "@type": "type.googleapis.com/logi.protocol.devices.ChangeHost",
    "host": 0
  }
}
```

Response:
```json
{
  "msgId": "10",
  "verb": "SET",
  "path": "/change_host/<device_id>/host",
  "origin": "backend",
  "result": {"code": "SUCCESS", "what": ""}
}
```

Key details:
- Requests use `msg_id` (snake_case), responses use `msgId` (camelCase)
- Verbs are strings: `"GET"`, `"SET"`, `"SUBSCRIBE"`, `"BROADCAST"`
- Payload is inline JSON with `@type` annotation (protobuf Any JSON format)
- The agent uses a strict protobuf JSON parser. Unknown fields cause `INVALID_MESSAGE_RECEIVED`
- Result codes: `SUCCESS`, `NO_SUCH_PATH`, `INVALID_ARG`, `INVALID_MESSAGE_RECEIVED`, `TIMEOUT`

### Change Host Command

```json
{
  "msg_id": "<unique_id>",
  "verb": "SET",
  "path": "/change_host/<device_id>/host",
  "payload": {
    "@type": "type.googleapis.com/logi.protocol.devices.ChangeHost",
    "host": <0|1|2>
  }
}
```

- `host` is a 0-indexed integer (0 = Channel 1, 1 = Channel 2, 2 = Channel 3)

---

## DDC/CI Monitor Control

VCP code `0x60` controls the input source.

**Windows** (ControlMyMonitor.exe):
```
ControlMyMonitor.exe /SetValue Primary 60 15   # DisplayPort
ControlMyMonitor.exe /SetValue Primary 60 18   # HDMI-2
```

**Mac** (m1ddc):
```bash
/opt/homebrew/bin/m1ddc set input 15   # DisplayPort
/opt/homebrew/bin/m1ddc set input 18   # HDMI-2
```

---

## HID++ Protocol Reference

### Feature 0x1814: Change Host

20-byte HID++ 2.0 packet:

```
Byte 0:  0x11            Long HID++ report header
Byte 1:  0x00            Device index (0x00 for direct BT LE)
Byte 2:  feature_index   Discovered via IRoot::GetFeature
Byte 3:  0x1E            Function 7 (SetCurrentHost), shifted left
Byte 4:  channel         0x00=Ch1, 0x01=Ch2, 0x02=Ch3
Bytes 5-19: 0x00         Padding
```

### Discovering the Feature Index

Query IRoot (feature 0x00, function 0) for feature ID 0x1814:

```
Send:  {0x11, device_idx, 0x00, 0x0D, 0x18, 0x14, 0x00...}  (20 bytes)
Response byte 4 = feature index for ChangeHost
```

For direct Bluetooth LE, `device_idx` = 0x00. For Bolt receivers, try slots 1-3.

---

## Troubleshooting

### Mac: Hotkey does not work

1. Verify `karabiner_console_user_server` is running: `ps aux | grep karabiner_console_user_server`
2. Validate config: `karabiner_cli --lint-complex-modifications ~/.config/karabiner/karabiner.json`

### Mac: Keyboard/mouse do not switch

Check `/tmp/kvm_switch.log`:
- `"already on other host"`: device is already on target host or disconnected
- `"ERROR: Logi Options+ agent socket not found"`: agent not running
- `"NO_RESPONSE"`: agent did not respond, restart Logi Options+

### Windows: Devices do not switch but monitor does

LogiSwitch.exe is failing silently. Possible causes:
- HID device path in config.ini is stale (device re-paired, Bluetooth address changed)
- Wrong HID collection (not the `FF43:0202` collection)
- BT LE GATT vendor collection has "Unknown" status (toggle Bluetooth off/on)

Run `query_agent_windows.py` to verify current device paths.

### Windows: BT LE GATT "Unknown" status

A Bluetooth LE device's HID++ vendor collection can enter "Unknown" status in Device Manager. The device works normally but LogiSwitch cannot open the vendor command channel.

This is a Windows/firmware interaction, not a LogiSwitch bug. Fix: toggle Bluetooth off/on in Windows Settings.

```powershell
Get-PnpDevice -Class "HIDClass" | Where-Object {
    $_.InstanceId -match "<bt_address>" -and $_.FriendlyName -match "vendor|Download"
} | Select-Object Status, FriendlyName
```

### Split-brain state

Keyboard on one host, mouse on another. Manually press the channel button on the stuck device, then use the hotkey.

### After Mac reboot

Logi Options+ agent starts automatically. Karabiner starts automatically. `karabiner_console_user_server` may not start. Open Karabiner-Elements Settings app to re-enable it.

---

## Installation

### Windows

1. Place UnifiedSwitch.exe, LogiSwitch.exe, config.ini, and dependencies/ in a directory
2. Run `Configure_debug.exe` or `query_agent_windows.py` to find HID device paths
3. Update `config.ini` with your device paths (must be the `FF43:0202` collections)
4. Run `UnifiedSwitch.exe` (requires Administrator for the keyboard hook)

### Mac

1. Install Logi Options+ (provides the agent)
2. Install m1ddc: `brew install m1ddc`
3. Copy `switch_to_windows.py` somewhere and `chmod +x` it
4. Update the device IDs in the script if needed (query via `/devices/list`)
5. Configure Karabiner-Elements with the hotkey rules above
6. Test: `python3 switch_to_windows.py 0` / `python3 switch_to_windows.py 1`
