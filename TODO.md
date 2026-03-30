# TODO

## Script Improvements

- [x] ~~Auto-discover device IDs by querying `/devices/list` instead of hardcoding~~ -- Done in `kvm_daemon_windows.py`
- [ ] Filter devices that support ChangeHost (Easy-Switch capable only)
- [x] ~~Make monitor input values configurable (or skip monitor switching)~~ -- Done in `kvm_config.ini`
- [ ] Support m1ddc on Intel Macs (different install path)
- [ ] Add config file for Mac side (device IDs, monitor values, m1ddc path)
- [ ] Add `--list-devices` flag to show connected devices and current host
- [ ] Add `--status` flag to show current host without switching

## Windows Side

- [x] Confirmed same wire protocol works on Windows via named pipe (GET and SET both work)
- [x] Dynamic IRoot::GetFeature query instead of hardcoded feature index fallback
- [x] ~~Auto-detect HID device paths from the Logi Options+ agent instead of hardcoding in config.ini~~ -- Done: `kvm_daemon_windows.py` uses named pipe IPC to discover devices and switch hosts. No HID paths or feature indices needed.
- [x] ~~Replace compiled C programs with Python~~ -- Done: `kvm_daemon_windows.py` replaces UnifiedSwitch.exe + LogiSwitch.exe with a single Python daemon

## Protocol Exploration

- [x] ~~Document more API paths~~ -- Done: see `api-reference.md`. Queried ~200 path patterns, found 12+ working GET endpoints.
- [x] ~~Explore `/lps/emulate/trigger_easy_switch` with correct payload format~~ -- Accepts `deviceId` + `channel` fields and returns SUCCESS, but does NOT actually switch the device. The `/lps/emulate/` prefix means it only fires an event for the UI overlay and plugin system. `/change_host` remains the only working method for programmatic host switching.
- [ ] Explore `/api/v1/actions/invoke` for macro/action triggering
- [ ] Map out SUBSCRIBE endpoints for real-time device status monitoring (all tested paths return no response)
- [ ] Investigate the WebSocket server on port 59869
- [x] ~~Extract protobuf types from agent binary~~ -- Found 920 protobuf type names. Covers devices, mouse, keyboard, macros, flow, haptics, presentation, webcam, audio, lighting, integrations, and more. Full list in `api-reference.md`.
- [ ] Crack the `/v2/profile` query format (returns INVALID_ARG for all payload shapes tried)
- [ ] Find the correct path pattern for device battery status
- [ ] Try SET on `/v2/assignment` for pointer speed, DPI, backlight, smartshift
- [ ] Probe `LogiPluginService` and `logitech_kiros_updater` pipes (different protocol from agent)
- [ ] Test SUBSCRIBE with a long-lived connection to see if events arrive asynchronously

## Coupled Easy-Switch

**Status: NOT POSSIBLE on current hardware.**

Investigated native coupled Easy-Switch -- the agent's built-in feature for linking keyboard + mouse so they switch hosts together from the physical Easy-Switch button.

- [x] ~~Find coupled Easy-Switch API paths~~ -- Found 5 paths: `/coupled_easy_switch/<id>/compatible_devices`, `coupled_switch_link_device`, `follow_cookies`, `follow_change_host`, `add_pending_device`
- [x] ~~Find protobuf types~~ -- `CoupledSwitchCompatibleDevices` (toggle, devices), `LinkDeviceInfo` (follow_device_id, lead_serial_number), `FollowDeviceCookieInfo` (coupled_switch_capable, lead_hashed_serial_number)
- [x] ~~Test the endpoints~~ -- All return NO_SUCH_PATH. Routes only register when device capabilities have `leadCoupledEasySwitch: true` (keyboard) or `followCoupledEasySwitch: true` (mouse). MX Keys S and MX Master 3S both have these set to `false`.
- [x] ~~Check if it can be enabled~~ -- No. This is a firmware/depot capability, not user-configurable.
- [x] ~~Listen for Easy-Switch events on the agent pipe~~ -- Passive listener receives no events when the button is pressed. The agent does not broadcast Easy-Switch events to connected clients.
- [x] ~~Detect Easy-Switch via AutoHotkey keyboard hook~~ -- The Easy-Switch button does not send a standard keyboard scancode. It's a HID++ command handled entirely by the Logitech firmware/receiver, invisible to the OS keyboard input stack.
- [ ] Test on newer devices that might support it (MX Keys S Combo, future products)

**Conclusion:** Easy-Switch button presses cannot be detected through the agent IPC or OS keyboard hooks. The only way is at the HID level via HID++ through the Bolt receiver.

The `kvm.ahk` + `kvm_daemon_windows.py --switch` approach (AHK hotkeys calling one-shot Python switching) is the workaround for devices that lack native coupled support.

## Packaging

- [ ] Proper CLI arg parsing (argparse)
- [ ] Brew formula or installer for Mac
- [ ] LaunchAgent plist for auto-start on boot
