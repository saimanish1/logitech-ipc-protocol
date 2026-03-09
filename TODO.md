# TODO

## Script Improvements

- [ ] Auto-discover device IDs by querying `/devices/list` instead of hardcoding
- [ ] Filter devices that support ChangeHost (Easy-Switch capable only)
- [ ] Make monitor input values configurable (or skip monitor switching)
- [ ] Support m1ddc on Intel Macs (different install path)
- [ ] Add config file for Mac side (device IDs, monitor values, m1ddc path)
- [ ] Add `--list-devices` flag to show connected devices and current host
- [ ] Add `--status` flag to show current host without switching

## Windows Side

- [x] Confirmed same wire protocol works on Windows via named pipe
- [x] Dynamic IRoot::GetFeature query instead of hardcoded feature index fallback
- [ ] Auto-detect HID device paths from the Logi Options+ agent instead of hardcoding in config.ini
  - Agent's `/devices/list` returns full HID path, connection type, device type, host channel
  - Agent listens on named pipe `\\.\pipe\logitech_kiros_agent-<hash>`
  - Would eliminate config.ini path management entirely

## Protocol Exploration

- [ ] Document more API paths
- [ ] Explore `/lps/emulate/trigger_easy_switch` with correct payload format
- [ ] Explore `/api/v1/actions/invoke` for macro/action triggering
- [ ] Map out SUBSCRIBE endpoints for real-time device status monitoring
- [ ] Investigate the WebSocket server on port 59869

## Packaging

- [ ] Proper CLI arg parsing (argparse)
- [ ] Brew formula or installer for Mac
- [ ] LaunchAgent plist for auto-starting karabiner_console_user_server on boot
