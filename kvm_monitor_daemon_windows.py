#!/usr/bin/env python3
"""
Monitor-follow daemon for Windows: native Enhanced Easy-Switch + DDC/CI = one-button KVM.

Windows port of kvm_monitor_daemon.py. With Enhanced Easy-Switch linked, the
keyboard's physical Easy-Switch key moves keyboard AND mouse between hosts
natively. This daemon watches the lead keyboard's presence on THIS host and
switches the monitor input with ControlMyMonitor (DDC/CI):

  keyboard leaves  -> monitor to away input (the other computer)
  keyboard returns -> monitor to here input (this computer)

Presence detection uses the agent IPC through the Options+ UI's renderer relay
(CDP) -- Logi Options+ 2.7+ rejects direct pipe clients that are not Logitech-
signed. See optionsplus-2.7-ipc-security.md and agent_cdp_client.py.

Requires: Logi Options+ UI running with --remote-debugging-port=9222
(the daemon auto-relaunches the UI with the flag if it is missing).

Usage:
    python kvm_monitor_daemon_windows.py                # foreground
    python kvm_monitor_daemon_windows.py --dry-run      # log transitions only
    python kvm_monitor_daemon_windows.py --here-input 15 --away-input 17
"""
import argparse
import logging
import subprocess
import sys
import time

from agent_cdp_client import AgentViaUI, CDPError

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("kvm-monitor-win")

UI_EXE = r"C:\Program Files\LogiOptionsPlus\logioptionsplus.exe"
UI_ARGS = ["--remote-debugging-port=9222"]


def lead_keyboard_present(agent):
    """True if the lead keyboard is connected to THIS host, else (name)."""
    r = agent.request("GET", "/devices/list", msg_id="kvm-presence")
    if not r:
        return None
    infos = (r.get("payload") or {}).get("deviceInfos", [])
    kbs = [d for d in infos if d.get("deviceType") == "KEYBOARD"
           and (d.get("connectionType") or "") != "VIRTUAL"]
    if not kbs:
        return None
    lead = next((d for d in kbs
                 if (d.get("capabilities") or {}).get("leadCoupledEasySwitch")), kbs[0])
    return bool(lead.get("connected")), lead.get("displayName", lead.get("id"))


def set_monitor(clickmon, input_value, dry_run):
    if dry_run:
        log.info("[dry-run] would set monitor input %s", input_value)
        return
    try:
        r = subprocess.run([clickmon, "/SetValue", "Primary", "60", str(input_value)],
                           timeout=5, capture_output=True, text=True)
        if r.returncode != 0 or r.stderr.strip():
            log.warning("ControlMyMonitor rc=%d out=%r err=%r", r.returncode,
                        r.stdout.strip(), r.stderr.strip())
        else:
            log.info("monitor -> input %s", input_value)
    except subprocess.TimeoutExpired:
        log.warning("ControlMyMonitor timed out setting input %s", input_value)
    except FileNotFoundError:
        log.error("ControlMyMonitor not found at %s", clickmon)


def relaunch_ui_with_flag():
    import os
    log.warning("relaunching Options+ UI with %s", UI_ARGS)
    subprocess.run(["taskkill", "/F", "/IM", "logioptionsplus.exe"],
                   capture_output=True)
    time.sleep(2)
    subprocess.Popen([UI_EXE, *UI_ARGS],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=0x00000008)  # DETACHED_PROCESS
    time.sleep(12)


def connect_agent(auto_relaunch, failures=0):
    try:
        agent = AgentViaUI()
        agent.connect()
        return agent
    except CDPError as e:
        if auto_relaunch and failures >= 2:
            relaunch_ui_with_flag()
            failures = 0
        raise


def main():
    ap = argparse.ArgumentParser(description="Follow lead keyboard, switch monitor via DDC/CI (Windows)")
    ap.add_argument("--here-input", type=int, default=15,
                    help="monitor input when keyboard is on this host (default 15 = DisplayPort)")
    ap.add_argument("--away-input", type=int, default=17,
                    help="monitor input when keyboard left (default 17 = HDMI-1)")
    ap.add_argument("--poll", type=float, default=1.0, help="poll interval seconds")
    ap.add_argument("--debounce", type=int, default=2,
                    help="consecutive polls required to confirm a state change")
    ap.add_argument("--cooldown", type=float, default=8.0,
                    help="seconds after a switch during which further changes are deferred")
    ap.add_argument("--clickmon", default=r"dependencies\ControlMyMonitor.exe")
    ap.add_argument("--no-auto-relaunch", action="store_true",
                    help="do not relaunch the UI with the debugging flag when the relay is down")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    def fire(here, name):
        if here:
            log.info("%s returned -> this host", name)
            set_monitor(args.clickmon, args.here_input, args.dry_run)
        else:
            log.info("%s left -> away host", name)
            set_monitor(args.clickmon, args.away_input, args.dry_run)

    agent = None
    state = None
    pending = None
    pending_count = 0
    last_fired = None
    cooldown_until = 0.0
    deferred = None
    failures = 0

    log.info("watching for lead keyboard (poll %.1fs, cooldown %.0fs, %s)",
             args.poll, args.cooldown, "DRY RUN" if args.dry_run else "live")
    while True:
        try:
            if agent is None:
                agent = connect_agent(not args.no_auto_relaunch, failures)
            res = lead_keyboard_present(agent)
            failures = 0
        except (CDPError, OSError) as e:
            failures += 1
            agent = None
            log.warning("relay unreachable (%s) - retry %d", e, failures)
            time.sleep(min(args.poll * 2, 5))
            continue

        if res is None:
            time.sleep(args.poll)
            continue
        here, name = res
        now = time.time()

        if state is None:
            state = last_fired = here
            log.info("initial state: %s (%s)", "keyboard HERE" if here else "keyboard AWAY", name)
        elif here == state:
            pending, pending_count = None, 0
        else:
            if pending != here:
                pending, pending_count = here, 1
            else:
                pending_count += 1
            if pending_count >= args.debounce:
                state = here
                pending, pending_count = None, 0
                if here == last_fired:
                    deferred = None
                elif now >= cooldown_until:
                    fire(here, name)
                    last_fired = here
                    cooldown_until = now + args.cooldown
                    deferred = None
                else:
                    log.info("change to %s during cooldown - deferred",
                             "HERE" if here else "AWAY")
                    deferred = here

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
