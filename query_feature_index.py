"""
Query the ChangeHost (0x1814) feature index from a Logitech HID++ device on Windows.

Sends an IRoot::GetFeature query to discover the feature index for ChangeHost,
which varies per device model.

Usage:
    python query_feature_index.py              # Uses default VID/PID (MX Keys S)
    python query_feature_index.py 046D B034    # Specify VID PID (MX Master 3S)

Requires:
    - hidapi: pip install hidapi
    - Device must be connected via Bluetooth LE
"""
import hid, sys, time

VID = int(sys.argv[1], 16) if len(sys.argv) > 2 else 0x046D
PID = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0xB378
USAGE_PAGE = 0xFF43
USAGE = 0x0202

print(f"Looking for VID={VID:#06x} PID={PID:#06x} UP={USAGE_PAGE:#06x} U={USAGE:#06x}")

device_info = None
for d in hid.enumerate(VID, PID):
    if d['usage_page'] == USAGE_PAGE and d['usage'] == USAGE:
        device_info = d
        break

if not device_info:
    print("Device not found. Available collections:")
    for d in hid.enumerate(VID, PID):
        print(f"  UP={d['usage_page']:#06x} U={d['usage']:#06x} path={d['path']}")
    sys.exit(1)

print(f"Found: {device_info['path']}")

h = hid.device()
h.open_path(device_info['path'])
h.set_nonblocking(0)

# IRoot::GetFeature (feature 0x00, function 0)
# Bytes 4-5: feature ID 0x1814 (ChangeHost)
query = [0x11, 0x00, 0x00, 0x0D, 0x18, 0x14] + [0] * 14
print(f"Query: {bytes(query).hex()}")
h.write(query)

time.sleep(0.2)

resp = h.read(64, timeout_ms=2000)
if resp:
    print(f"Response: {bytes(resp).hex()}")
    if len(resp) >= 5:
        feat_idx = resp[4]
        print(f"\nChangeHost feature index = 0x{feat_idx:02X}")
        print(f"HID++ command: {{0x11, 0x00, 0x{feat_idx:02X}, 0x1E, channel}}")
else:
    print("No response.")

h.close()
