# Logi Options+ Agent API Reference

Documented by querying the agent's IPC interface on Windows (named pipe) and macOS (Unix socket). Same wire protocol and API paths on both platforms.

## Wire protocol

Messages use a binary frame:

```
LE32(total_len) + BE32(proto_name_len) + "json" + BE32(msg_len) + JSON_message
```

Three verbs: `GET`, `SET`, `SUBSCRIBE`.

Every request includes `msg_id` (string), `verb`, `path`, and optionally `payload`. Responses echo the `msg_id` and include a `result.code` field.

## Working GET endpoints

| Path | Response `@type` | Description |
|------|-----------------|-------------|
| `/permissions` | `app_permissions.Permissions` | Feature flags: analytics, flow, sso, update, backlight, logivoice, dfu, aipromptbuilder, deviceRecommendation, smartactions, actionsRing |
| `/devices/list` | `devices.Device.Info[]` | All devices with full capabilities, firmware versions, battery caps, slot prefixes, HID paths |
| `/devices/ever_connected/list` | `devices.DevicePermanentDatas` | Historical device list with UDIDs and connection history |
| `/accounts/user_info` | User info | Email, name, profile picture |
| `/accounts/is_authenticated` | Boolean | Authentication status |
| `/offer/retrieve` | `offer_redemption.Offers` | Available offers (e.g. Adobe Creative Cloud) |
| `/updates/depots` | `updates.Depot[]` | Installed component packages with versions |
| `/updates/channel` | Channel info | Update pipeline (e.g. "public") |
| `/lps/endpoint/info` | `lps.Endpoint.Information` | LPS state (ACTIVE) |
| `/lps/status` | `lps.ServiceStatus` | LPS uptime, version |
| `/scarif/info` | `scarif.Info` | Analytics config, system info, server address, app/OS versions |
| `/system/settings` | System settings | Country/region |
| `/device_recommendation_enabled` | Boolean | Whether device recommendations are enabled (true) |

## Working SET endpoints

| Path | Payload `@type` | Description |
|------|-----------------|-------------|
| `/change_host/<device_id>/host` | `type.googleapis.com/logi.protocol.devices.ChangeHost` | Switch device to a different host. Payload: `{"host": N}` where N is 0-indexed host number. This is the only working method for programmatic host switching. |
| `/v2/assignment` | `type.googleapis.com/logi.protocol.profiles_v2.Assignment` | Set device settings (keyboard layout, backlight, pointer speed, etc). Requires `profileId` and `slotId`. |
| `/lps/emulate/trigger_easy_switch` | (none) | **UI notification only.** Accepts `deviceId` + `channel` fields and returns SUCCESS, but does NOT actually switch the device. The `/lps/emulate/` prefix means it only fires an event for the UI overlay and plugin system. The real handler (`OnTriggerEasySwitchEvent`) is only triggered by the physical Easy-Switch button. |

## Endpoints that exist but need correct parameters

| Path | Error | Notes |
|------|-------|-------|
| `/v2/profile` | INVALID_ARG | Profile system exists but the correct query format is unknown |
| `/v2/assignment` (GET) | INVALID_ARG / NOT_FOUND | Needs `slotId`; returns NOT_FOUND when no custom assignment is stored for that slot |

## Path patterns that don't work

Device-specific feature paths like `/<feature>/<device_id>` and `/devices/<device_id>/<feature>` return `NO_SUCH_PATH` for all tested features:

backlight, battery, dpi, sensitivity, scroll, smartshift, thumbwheel, pointer_speed, fn_inversion, easy_switch, illumination, gestures, keyboard_layout, multi_platform, device_info, device_name

SlotPrefix-based paths like `/<slotPrefix>/<setting>` also don't work.

All device settings go through the `/v2/assignment` system, not dedicated per-feature endpoints.

## SUBSCRIBE verb

Returns no response for any path tested (`/devices/list`, `/change_host`, `/permissions`, etc). May need a different message format, or may silently accept and only fire on events over a long-lived connection.

## The `/v2/assignment` system

This is the central settings interface for all device configuration. Settings are identified by a `slotId` that combines the device's slot prefix with a setting name.

### SlotId format

```
<slotPrefix>_<setting_name>
```

Examples:
- `mx-keys-s-2b378_keyboard_settings` -- keyboard layout preferences
- `mx-master-3s-2b034_pointer_speed` -- mouse pointer speed

The `slotPrefix` comes from each device's entry in `/devices/list`.

### Known SET payload for keyboard settings

```json
{
  "verb": "SET",
  "path": "/v2/assignment",
  "payload": {
    "profileId": "<profile-uuid>",
    "assignment": {
      "slotId": "mx-keys-s-2b378_keyboard_settings",
      "card": {
        "attribute": "KEYBOARD_SETTINGS",
        "keyboardSettings": { "keepKeyboardInOsLayout": false }
      }
    }
  }
}
```

## Coupled Easy-Switch (Enhanced Easy-Switch)

The agent has built-in support for linking keyboard + mouse so they switch hosts together from the physical Easy-Switch button.

**This shipped as an official Logitech feature ("Enhanced Easy-Switch")** after the initial investigation concluded it was impossible:

- MX Keys S firmware **81.2.17** (Apr 2026): "prepares your keyboard for the upcoming Enhanced Easy-Switch feature"
- Logi Options+ **v2.3+** (end Apr 2026) unlocks it; verified working on v2.6.944893
- After the one-time link, pressing the keyboard's Easy-Switch key moves keyboard AND follower devices together, natively (keyboard-initiated only; return-sync needs Options+ running on the host you're returning from)

### What changed vs the initial findings

The capability gate flipped: both devices now report the required flags once firmware/Options+ are current.

| Device | Role | Capability flag | Status |
|--------|------|-----------------|--------|
| MX Keys S | lead | `leadCoupledEasySwitch: true` | live |
| MX Master 3S | follow | `followCoupledEasySwitch: true` | live |

### API paths and scoping (verified live)

Route registration is asymmetric — check which side of the link each path expects:

| Path | Side | Verb | Notes |
|------|------|------|-------|
| `/coupled_easy_switch/<lead_id>/compatible_devices` | lead | GET | **Requires a payload** (empty `{}` works) — payload-less GETs return `INVALID_ARG` |
| `/coupled_easy_switch/<follower_id>/follow_cookies` | follower | GET | Link state: `{link, coupledSwitchCapable, leadHashedSerialNumber}` |
| `/coupled_easy_switch/<follower_id>/follow_change_host` | follower | SET | Payload type: `logi.protocol.devices.FollowChangeHost` |
| `/coupled_easy_switch/<follower_id>/coupled_switch_link_device` | follower | SET | Payload: `LinkDeviceInfo {link, follow_device_id, lead_serial_number}` |
| `/coupled_easy_switch/add_pending_device` | global | — | **No device id in path** (the `/<id>/add_pending_device` form returns `NO_SUCH_PATH`); route not registered in current build |

Protobuf namespace: `logi.protocol.coupled_easy_switch_assist` (see `coupled_easy_switch_assist.proto` in the agent binary).

### Protobuf types

- `CoupledSwitchCompatibleDevices` -- toggle, lead_need_fw_update, devices[] {device_id, device_serial_number, linked, need_fw_update}, lead_serial_number
- `LinkDeviceInfo` -- link, follow_device_id, lead_serial_number
- `FollowDeviceCookieInfo` -- link, coupled_switch_capable, lead_hashed_serial_number

### Firmware gate on linking

While devices report `need_fw_update` (lead or follower), SET link attempts return SUCCESS and echo the `LinkDeviceInfo` payload, but the link does not take effect -- `follow_cookies` keeps returning `link: false`. After updating firmware and linking once (via the Options+ UI Easy-Switch tab or the SET endpoints), `follow_cookies` flips to `link: true` and physical Easy-Switch moves both devices in both directions.

Verified live: MX Keys S FW 81.2.17 (lead) linked to MX Master 3S FW 22.2.9 (follower) on Logi Options+ 2.6.944893.

## Other named pipes

| Pipe | Protocol |
|------|----------|
| `\\.\pipe\LogiPluginService` | Different protocol -- no response to JSON wire format |
| `\\.\pipe\logitech_kiros_updater` | Different protocol -- no response to JSON wire format |

## Device capabilities (from `/devices/list`)

### MX Master 3S (mouse)

- pointerSpeed: true
- hostInfos: true (Easy-Switch capable)
- hasBatteryStatus: true, unifiedBattery: true
- smartshift: enabled, sensitivity 83
- thumbwheel: smooth scroll, standard direction
- flow: hostCount 3
- highResolutionSensor: true (200-8000 DPI range)
- isActionRingSupportedDevice: true
- leadCoupledEasySwitch: false
- followCoupledEasySwitch: false
- Programmable CIDs: 82, 83, 86, 195, 196

### MX Keys S (keyboard)

- fnInversion: true
- hostInfos: true (Easy-Switch capable)
- hasBatteryStatus: true, unifiedBattery: true
- backlightVersion: 3
- keepKeyboardInOsLayout: true (from capabilities override)
- disableKeys: true
- isActionRingSupportedDevice: true
- leadCoupledEasySwitch: false
- followCoupledEasySwitch: false
- Programmable CIDs: 199, 200, 226, 227, 259, 264, 284, 228, 229, 230, 231, 232, 233, 10, 266, 234, 111

## Protobuf types (920 found in agent binary)

Extracted via regex from `logioptionsplus_agent.exe`. These are the `@type` identifiers used in JSON payloads.

### devices
ChangeHost, FnInversion, BatteryStatus, HostInfo, DivertState, DeviceBrightness, DeviceInfo, DevicePermanentDatas, DeviceConnectionType

### mouse
Dpi, PointerSpeed, SmartShiftSettings, ScrollMode, ThumbWheelSettings, ReportRate, AngleSnapping, PrecisionMode

### keyboard
KeyboardSettings

### backlight
Settings, BacklightEffect, BacklightMode, BacklightLevel

### macros
Macro (subtypes: Keystroke, Mouse, Media, System, ScreenCapture, OpenWebPage, AI-related actions)

### flow
Config, EdgeHit, PeerStatus

### haptics
PlayWaveFormRequest, HapticSettings

### highlights (presentation tools)
Laser, Magnifier, Vignette, Annotation

### presentation_timers
Timer, TimerSettings

### webcam
Full camera control -- video, crop, focus, microphone, broadcast settings

### audio
DTS, Dolby, Equalizer, SurroundSound, Volume

### loupedeck
Separate message format for Loupedeck devices

### integrations
Plugin system, OBS integration

### unified_profiles
Activity-based profile switching

### firmware_lighting
RGB effects -- breathing, color cycle, color wave, ripple, and more

### other
offer_redemption, updates (Depot), app_permissions, scarif (analytics), lps (service status), coupled_easy_switch, device_recommendation
