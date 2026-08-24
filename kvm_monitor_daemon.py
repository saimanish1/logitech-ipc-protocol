#!/usr/bin/env python3
"""
Monitor-follow daemon: native Enhanced Easy-Switch + DDC/CI = one-button KVM.

With Enhanced Easy-Switch linked (keyboard FW 81.2.17+, Options+ v2.3+),
pressing the keyboard's Easy-Switch key moves keyboard AND mouse between
hosts natively. The only thing Logitech doesn't move is the monitor.

This daemon watches the lead keyboard's presence on this Mac via the
Logi Options+ agent IPC and switches the monitor input with m1ddc:

  keyboard leaves  -> someone pressed Easy-Switch away -> monitor to Windows input
  keyboard returns -> Easy-Switch back               -> monitor to Mac input

No hotkeys, no Karabiner, no AHK. One physical key does everything.

Usage:
    python3 kvm_monitor_daemon.py                 # run in foreground
    python3 kvm_monitor_daemon.py --dry-run       # log transitions only
    python3 kvm_monitor_daemon.py --here-input 18 --away-input 15

Requires: Logi Options+ running, m1ddc (brew install m1ddc).
"""
import argparse
import glob
import json
import logging
import socket
import struct
import subprocess
import sys
import time

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("kvm-monitor")


# ---------- agent IPC (frame-exact client) ----------

class Agent:
    def __init__(self):
        self.sock = None
        self.buf = b""
        self.msg_id = 0

    @staticmethod
    def find_socket():
        socks = [s for s in glob.glob("/tmp/logitech_kiros_agent-*")
                 if not s.endswith(".real")]
        return socks[0] if socks else None

    def connect(self):
        path = self.find_socket()
        if not path:
            raise ConnectionError("agent socket not found (is Logi Options+ running?)")
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect(path)
        self.buf = b""

    def close(self):
        try:
            self.sock and self.sock.close()
        finally:
            self.sock = None
            self.buf = b""

    def _read_frame(self, deadline):
        """Read exactly one wire frame. Returns (proto_name, payload_bytes)."""
        while time.time() < deadline:
            if len(self.buf) < 4:
                self.buf += self.sock.recv(65536)
                continue
            total = struct.unpack_from("<I", self.buf, 0)[0]
            if len(self.buf) < 4 + total:
                self.buf += self.sock.recv(65536)
                continue
            inner, self.buf = self.buf[4:4 + total], self.buf[4 + total:]
            plen = struct.unpack_from(">I", inner, 0)[0]
            proto = inner[4:4 + plen]
            ipos = 4 + plen
            mlen = struct.unpack_from(">I", inner, ipos)[0]
            return proto, inner[ipos + 4:ipos + 4 + mlen]
        raise TimeoutError("frame read deadline exceeded")

    def request(self, verb, path, payload=None, timeout=5):
        if self.sock is None:
            self.connect()
        self.msg_id += 1
        mid = str(self.msg_id)
        msg = {"msg_id": mid, "verb": verb, "path": path}
        if payload is not None:
            msg["payload"] = payload
        data = json.dumps(msg).encode()
        inner = struct.pack(">I", 4) + b"json" + struct.pack(">I", len(data)) + data
        try:
            self.sock.send(struct.pack("<I", len(inner)) + inner)
            deadline = time.time() + timeout
            while True:
                proto, raw = self._read_frame(deadline)
                if proto != b"json":
                    continue  # connection greeting / binary noise
                r = json.loads(raw)
                if r.get("msgId") == mid:
                    return r
        except (BrokenPipeError, ConnectionResetError, socket.timeout, TimeoutError):
            self.close()
            raise ConnectionError("agent connection lost")


# ---------- keyboard presence ----------

def lead_keyboard_present(agent):
    """True if the coupled lead keyboard is connected to THIS host."""
    r = agent.request("GET", "/devices/list")
    infos = r.get("payload", {}).get("deviceInfos", [])
    kbs = [d for d in infos if d.get("deviceType") == "KEYBOARD"
           and d.get("connectionType") != "VIRTUAL"]
    if not kbs:
        return None  # no keyboard known at all
    # prefer the coupled lead; fall back to any connected keyboard
    lead = next((d for d in kbs
                 if d.get("capabilities", {}).get("leadCoupledEasySwitch")), kbs[0])
    return bool(lead.get("connected")), lead.get("displayName", lead.get("id"))


# ---------- monitor ----------

def set_monitor(m1ddc, input_value, dry_run):
    if dry_run:
        log.info("[dry-run] would set monitor input %s", input_value)
        return
    try:
        r = subprocess.run([m1ddc, "set", "input", str(input_value)],
                           timeout=5, capture_output=True, text=True)
        if r.returncode != 0 or r.stderr.strip():
            log.warning("m1ddc -> rc=%d out=%r err=%r", r.returncode,
                        r.stdout.strip(), r.stderr.strip())
        else:
            log.info("monitor -> input %s", input_value)
    except subprocess.TimeoutExpired:
        log.warning("m1ddc timed out setting input %s — monitor busy or link down", input_value)


# ---------- main loop ----------

def main():
    ap = argparse.ArgumentParser(description="Follow lead keyboard, switch monitor via DDC/CI")
    ap.add_argument("--here-input", type=int, default=17,
                    help="monitor input when keyboard is on this Mac (default 17 = HDMI-1)")
    ap.add_argument("--away-input", type=int, default=15,
                    help="monitor input when keyboard left (default 15 = DisplayPort)")
    ap.add_argument("--poll", type=float, default=1.0, help="poll interval seconds")
    ap.add_argument("--debounce", type=int, default=2,
                    help="consecutive polls required to confirm a state change")
    ap.add_argument("--cooldown", type=float, default=8.0,
                    help="seconds after a switch during which further changes are "
                         "deferred and re-verified (absorbs keyboard flapping)")
    ap.add_argument("--m1ddc", default="/opt/homebrew/bin/m1ddc")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    def fire(here, name):
        if here:
            log.info("%s returned -> Mac", name)
            set_monitor(args.m1ddc, args.here_input, args.dry_run)
        else:
            log.info("%s left -> away host", name)
            set_monitor(args.m1ddc, args.away_input, args.dry_run)

    agent = Agent()
    state = None            # debounced presence: True=keyboard here, False=away
    pending = None          # candidate new state during debounce
    pending_count = 0
    last_fired = None       # state the monitor was last set to
    cooldown_until = 0.0    # monitor locked until this time
    deferred = None         # confirmed change waiting for the lock to expire

    log.info("watching for lead keyboard (poll %.1fs, cooldown %.0fs, %s)",
             args.poll, args.cooldown, "DRY RUN" if args.dry_run else "live")
    while True:
        try:
            res = lead_keyboard_present(agent)
        except ConnectionError as e:
            log.warning("agent unreachable: %s — retrying", e)
            time.sleep(args.poll)
            continue

        if res is None:
            time.sleep(args.poll)
            continue
        here, name = res
        now = time.time()

        if state is None:  # first reading: adopt, assume monitor already matches
            state = last_fired = here
            log.info("initial state: %s (%s)", "keyboard HERE" if here else "keyboard AWAY", name)
        elif here == state:
            pending, pending_count = None, 0
        else:  # candidate change
            if pending != here:
                pending, pending_count = here, 1
            else:
                pending_count += 1
            if pending_count >= args.debounce:
                state = here
                pending, pending_count = None, 0
                if here == last_fired:
                    deferred = None        # settled back to what monitor shows
                elif now >= cooldown_until:
                    fire(here, name)       # fast path: switch immediately
                    last_fired = here
                    cooldown_until = now + args.cooldown
                    deferred = None
                else:
                    log.info("change to %s during cooldown — deferred",
                             "HERE" if here else "AWAY")
                    deferred = here

        # deferred re-fire once the monitor is free and state still matches
        if deferred is not None and now >= cooldown_until:
            if state == deferred:
                fire(deferred, name)
                last_fired = deferred
                cooldown_until = now + args.cooldown
            deferred = None

        time.sleep(args.poll)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
