# Reverse Engineering the Logi Options+ Agent IPC Protocol

**Date**: February 2026
**Goal**: Programmatically switch Logitech multi-host devices between Bluetooth hosts on macOS
**Result**: Documented an undocumented Unix socket IPC protocol for controlling Logitech device host switching without raw HID access

---

## The Problem

On Windows, switching a Logitech multi-host device is straightforward: open the HID device handle, send a 20-byte HID++ 2.0 packet. On macOS, the kernel blocks raw HID access to Bluetooth input devices. No permissions or entitlements bypass this.

The Logi Options+ agent has Apple-signed entitlements (`com.apple.security.device.bluetooth`) granting Bluetooth HID access. The goal became: talk to the agent instead of the device.

---

## Phase 1: Direct HID Access (All Failed)

### hidapitester

```bash
hidapitester --vidpid 046D:B378 --usagePage 0xFF43 --usage 0x0202 --open --length 20 \
  --send-output 0x11,0x00,0x09,0x1E,0x00,...
```

Result: `Error: could not open device`. macOS blocks raw HID access to Bluetooth input devices at the IOKit level.

### Python hid library

```python
import hid
h = hid.device()
h.open(0x046D, 0xB378)
```

Same kernel restriction. `hidapi` calls `IOHIDDeviceOpen()`, which is blocked for Bluetooth input devices.

### ctypes with libhidapi

`hid_enumerate()` lists the device. `hid_open()` returns NULL. The kernel allows enumeration but blocks open.

### sudo

Same error. This restriction is in the Bluetooth HID driver, not in permissions. Root does not help.

### Conclusion

macOS has a kernel-level, non-bypassable restriction on raw HID access to Bluetooth input devices. No combination of permissions (Input Monitoring, Accessibility, Full Disk Access, sudo, SIP disable) changes this.

---

## Phase 2: Finding the IPC Channel

### The Socket

```bash
ls /tmp/ | grep -i logi
```

Found: `/tmp/logitech_kiros_agent-<hash>` (Unix domain socket created by `logioptionsplus_agent`).

### Binary String Analysis

```bash
strings logioptionsplus_agent | grep -i "change_host\|easy_switch\|1814"
```

Found: `Feature1814ChangeHost`, `/change_host/%s/host`, `logi.protocol.devices.ChangeHost`, `SetCurrentHost(short, bool)`. The agent implements HID++ Change Host internally.

---

## Phase 3: Analyzing the Electron App

### Extracting the Source

```bash
npx asar extract /Applications/logioptionsplus.app/Contents/Resources/app.asar /tmp/logi_asar
```

### Module 2824: Wire Protocol

```javascript
o.writeUInt32LE(r.length + t.length + 8, 0)  // LE32: total inner length
u.write(o)       // Write LE32 total
u.write(e)       // Write BE32 proto name length + proto name
u.write(n)       // Write BE32 message length
u.write(t.message)  // Write message bytes
```

Protocol name variable: `c = "json"`. The Electron app uses JSON, not protobuf binary.

### Wire Format

```
LE32(total_inner_len) + BE32(proto_name_len) + proto_name + BE32(msg_len) + msg_data
```

---

## Phase 4: GET Requests Work

Tried JSON over the socket:

```python
msg = json.dumps({'verb': 1, 'path': '/devices/list'}).encode()
proto = b'json'
```

Worked. Got back the full device list. Queried device hosts, easy-switch info, permissions. All readable.

Could not write. Every SET payload format was rejected.

---

## Phase 5: The SET Payload Problem

### Attempt 1: Payload as Separate Binary Block

Appended a third `BE32+data` block after the JSON message.

Result: `"Invalid Payload"`. The wire protocol parser reads exactly two blocks per frame (protocol name + message). A third block is never read and corrupts the socket buffer.

### Attempt 2: Payload Embedded in JSON

```python
{'verb': 'SET', 'path': '/change_host/dev00000001/host', 'payload': {'host': 0}}
```

Result: `INVALID_MESSAGE_RECEIVED`. The agent parses JSON using protobuf's `JsonStringToMessage()`. Missing `@type` annotation on the `google.protobuf.Any` payload field caused a parse error.

### Attempt 3: Protobuf Binary Payloads

Tried varint-encoded payload, `google.protobuf.Any`-wrapped payload, various field numbers (6 through 15). All rejected with either `INVALID_MESSAGE_RECEIVED` or `TIMEOUT`.

### Attempt 4: Different API Paths

- `/devices/<id>/easy_switch/change` -> `INVALID_ARG`
- `/lps/emulate/trigger_easy_switch` -> `"Invalid Payload"`
- `/api/v1/actions/invoke` -> `"Wrong payload format!"`

### Dead End

The handler was receiving requests (responded with specific errors, not timeouts) but could not parse the payload regardless of format. Something fundamental was wrong with the delivery mechanism.

---

## Phase 6: The MITM Breakthrough

### Setting Up the Proxy

```python
# Rename real socket, create proxy at original path
os.rename(REAL_SOCK, REAL_SOCK + '.real')
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(REAL_SOCK)
server.listen(10)

# For each client connection, relay + log both directions
def handle_client(client_sock):
    agent_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    agent_sock.connect(REAL_SOCK + '.real')
```

Restarted the Electron UI so it reconnected through the proxy. Captured ~15MB of traffic.

### The Finding

Extracted SET requests from the captured traffic:

```python
{"msg_id":"198","verb":"SET","path":"/lps/endpoint/control_plugin_service",
 "payload":{"type":"START","delay":2,"force":false}}
```

The `payload` field is inline in the JSON. Not a separate binary block. Not a separate wire frame.

### Why Previous Attempts Failed

The `payload` field in the protobuf schema is `google.protobuf.Any`. In protobuf JSON format, `Any` types require an `@type` annotation. Our earlier embedded payload attempt used a numeric verb and no `@type`, which tripped the strict protobuf JSON parser.

### The Working Format

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

Requirements:
- `msg_id`: snake_case (responses use `msgId` camelCase)
- `verb`: string `"SET"` (not numeric)
- `payload`: inline JSON with `@type` annotation (protobuf Any JSON format)
- `host`: integer

### First Successful SET

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

The device physically switched hosts.

---

## Post-Mortem: Why Each Attempt Failed

| Attempt | Failure Mode |
|---------|-------------|
| Separate payload block | Wire parser reads exactly 2 blocks. Third block left in socket buffer, corrupting subsequent messages |
| Embedded payload, numeric verb | Strict protobuf JSON parser rejects untyped payload with numeric enum verb |
| Missing `@type` | `google.protobuf.Any` requires `@type` in JSON serialization. Parser cannot deserialize without it |
| `hostIndex` instead of `host` | Wrong field name. GET response showed the correct name but we were also fighting the delivery mechanism |

The payload is a `google.protobuf.Any` field in the request protobuf, serialized as inline JSON with an `@type` annotation. Standard protobuf JSON format, but impossible to guess without seeing a real message.

---

## Discovered API Paths

| Path | Verb | Description |
|------|------|-------------|
| `/permissions` | GET | App permissions |
| `/devices/list` | GET | All connected devices |
| `/devices/<id>/easy_switch` | GET | Easy-Switch host info |
| `/change_host/<id>/host` | GET | Current host index |
| `/change_host/<id>/host` | SET | Switch device to a different host |
| `/macros/predefined` | GET | Predefined macros |
| `/macros/presets` | GET | Macro presets |
| `/macros/custom_categories/storage` | GET | Custom macro categories |
| `/macro_assignments/all` | GET | All macro assignments |
| `/lps/endpoint/info` | GET | LPS endpoint info |
| `/lps/start_library_package_update_check` | SET | Trigger update check |
| `/lps/endpoint/control_plugin_service` | SET | Start/stop plugin service |
| `/applications/scan/installed` | SET | Scan installed applications |
| `/updates/check_now` | SET | Check for updates |
| `/backups/device/list` | GET | Device backup list |
| `/resources/list_device_notifications` | GET | Device notification resources |
| `/device_recommendation_enabled` | GET | Device recommendation flag |
| `/secure_input_enabled` | BROADCAST | Secure input state change |
| `/webcams/in_conference` | SUBSCRIBE | Webcam conference status |
| `/rightsight/status` | SUBSCRIBE | RightSight status |
| `/applications/foreground_event` | SUBSCRIBE | App focus changes |

## Protobuf Types

| Type | Purpose |
|------|---------|
| `logi.protocol.devices.ChangeHost` | Change Host command (field: `host` int) |
| `logi.protocol.devices.HostInfo.Hosts` | Host info list |
| `logi.protocol.lps.TriggerEasySwitch` | Easy-Switch trigger (alternative) |
| `logi.protocol.app_permissions.Permissions` | App permissions |
| `google.protobuf.StringValue` | Wrapper for string values |

## Result Codes

`SUCCESS`, `NO_SUCH_PATH`, `INVALID_ARG`, `INVALID_MESSAGE_RECEIVED`, `TIMEOUT`

---

## Tools Used

| Tool | Purpose |
|------|---------|
| `strings` | Binary analysis of the agent |
| `npx asar extract` | Extracting Electron app JavaScript |
| Python `socket` | Unix socket communication |
| Python `struct` | Binary protocol encoding/decoding |
| Python MITM proxy | Intercepting UI-to-agent traffic |
| `lsof` | Finding listening ports |

---

## Takeaways

1. MITM is the fastest way to reverse-engineer IPC protocols. Set it up early instead of guessing.
2. Minified Electron app JS is readable enough to extract wire formats.
3. `google.protobuf.Any` requires `@type` annotations in JSON. Without the schema, this is non-obvious.
4. "Invalid Payload" was returned for both "malformed payload" and "no payload at all". Misleading error messages cost hours.
5. Isolate variables. We were fighting wire format, verb encoding, payload delivery, and field names simultaneously. The MITM capture isolated all of them at once.
