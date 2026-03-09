#!/usr/bin/env python3
"""
Mac-side KVM switch script for Logi Options+ IPC.

Communicates with the Logi Options+ agent via its Unix domain socket
to switch Logitech devices between hosts, and uses m1ddc for monitor
input switching on Apple Silicon.

Usage:
    python3 switch_to_windows.py 0            # Switch to host 0 (DisplayPort)
    python3 switch_to_windows.py 1            # Switch to host 1 (HDMI)
    python3 switch_to_windows.py --dry-run 0  # Show what would happen

Requires:
    - Logi Options+ installed and running
    - m1ddc installed: brew install m1ddc

See logi-options-ipc-reverse-engineering.md for protocol details.
"""
import socket, struct, json, subprocess, sys, glob

# --- Configuration ---
# Update these to match your setup.
# Device IDs: query via GET /devices/list to find yours.
DEVICES = {
    'dev00000001': 'Keyboard',
    'dev00000000': 'Mouse',
}
HOST_NAMES = {0: 'Windows', 1: 'Mac', 2: 'iPad'}
# DDC/CI input source values per host. Find yours with ControlMyMonitor or m1ddc.
MONITOR_INPUTS = {0: 15, 1: 18}  # host 0 -> DisplayPort (15), host 1 -> HDMI-2 (18)
M1DDC_PATH = '/opt/homebrew/bin/m1ddc'
# --- End Configuration ---

def find_socket():
    socks = [s for s in glob.glob('/tmp/logitech_kiros_agent-*') if not s.endswith('.real')]
    return socks[0] if socks else None

def send_json(s, obj):
    data = json.dumps(obj).encode()
    proto = b'json'
    inner = struct.pack('>I', len(proto)) + proto + struct.pack('>I', len(data)) + data
    s.send(struct.pack('<I', len(inner)) + inner)

def recv_all(s, timeout=3):
    s.settimeout(timeout)
    chunks = []
    try:
        while True:
            chunk = s.recv(65536)
            if not chunk: break
            chunks.append(chunk)
            s.settimeout(0.5)
    except socket.timeout:
        pass
    return b''.join(chunks)

def parse_responses(data):
    results = []
    pos = 0
    while pos + 4 <= len(data):
        total = struct.unpack_from('<I', data, pos)[0]
        if total > 1000000 or pos + 4 + total > len(data): break
        inner = data[pos+4:pos+4+total]
        pos += 4 + total
        ipos = 0
        if ipos + 4 > len(inner): continue
        plen = struct.unpack_from('>I', inner, ipos)[0]
        ipos += 4
        proto = inner[ipos:ipos+plen]
        ipos += plen
        if ipos + 4 > len(inner): continue
        mlen = struct.unpack_from('>I', inner, ipos)[0]
        ipos += 4
        msg = inner[ipos:ipos+mlen]
        if proto == b'json':
            try: results.append(json.loads(msg))
            except: pass
    return results

def switch_device(s, msg_id, device_id, target_host):
    send_json(s, {
        'msg_id': msg_id,
        'verb': 'SET',
        'path': f'/change_host/{device_id}/host',
        'payload': {
            '@type': 'type.googleapis.com/logi.protocol.devices.ChangeHost',
            'host': target_host
        }
    })
    data = recv_all(s, timeout=3)
    for r in parse_responses(data):
        if isinstance(r, dict) and device_id in r.get('path', ''):
            code = r.get('result', {}).get('code', '')
            return code
    return 'NO_RESPONSE'

def get_current_host(s, msg_id, device_id):
    send_json(s, {'msg_id': msg_id, 'verb': 'GET', 'path': f'/change_host/{device_id}/host'})
    data = recv_all(s, timeout=3)
    for r in parse_responses(data):
        if isinstance(r, dict) and device_id in r.get('path', ''):
            payload = r.get('payload', {})
            if 'host' in payload:
                return payload['host']
    return None

def connect_agent():
    sock_path = find_socket()
    if not sock_path:
        print('ERROR: Logi Options+ agent socket not found')
        return None
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(sock_path)
    send_json(s, {'msg_id': '1', 'verb': 'GET', 'path': '/permissions'})
    recv_all(s)
    return s

def dry_run(target_host=0):
    s = connect_agent()
    if not s:
        return False

    label = HOST_NAMES.get(target_host, f'host {target_host}')
    monitor_input = MONITOR_INPUTS.get(target_host, '?')
    print(f'[dry-run] Target: {label} (host {target_host})')
    print(f'[dry-run] Monitor would switch to input {monitor_input}')
    print()

    for i, (dev_id, name) in enumerate(DEVICES.items()):
        current = get_current_host(s, str(10 + i), dev_id)
        if current is None:
            print(f'  {name} ({dev_id}): unreachable')
        elif current == target_host:
            print(f'  {name} ({dev_id}): already on {HOST_NAMES.get(current, current)}, no action needed')
        else:
            print(f'  {name} ({dev_id}): on {HOST_NAMES.get(current, current)}, would switch to {label}')

    s.close()
    return True

def switch_devices(target_host=0):
    s = connect_agent()
    if not s:
        return False

    for i, (dev_id, name) in enumerate(DEVICES.items()):
        code = switch_device(s, str(10 + i), dev_id, target_host)
        if code == 'SUCCESS':
            print(f'{name}: switched')
        elif code == 'NO_SUCH_PATH':
            print(f'{name}: already on other host')
        else:
            print(f'{name}: {code}')

    s.close()
    return True

def switch_monitor(input_val=15):
    try:
        subprocess.run([M1DDC_PATH, 'set', 'input', str(input_val)],
                      timeout=5, capture_output=True)
        print(f'Monitor: switched to input {input_val}')
    except Exception as e:
        print(f'Monitor: error - {e}')

if __name__ == '__main__':
    args = sys.argv[1:]
    is_dry_run = '--dry-run' in args
    if is_dry_run:
        args.remove('--dry-run')

    target = int(args[0]) if args else 0
    label = HOST_NAMES.get(target, f'host {target}')

    if is_dry_run:
        dry_run(target)
    else:
        monitor_input = MONITOR_INPUTS.get(target, 15)
        print(f'Switching to {label}...')
        switch_devices(target)
        switch_monitor(monitor_input)
        print('Done!')
