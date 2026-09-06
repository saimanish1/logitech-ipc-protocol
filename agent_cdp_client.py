#!/usr/bin/env python3
"""
Logi Options+ agent IPC via the UI's CDP relay (v2.7+ bypass transport).

Logi Options+ 2.7 (961922) added a named-pipe security check: the agent
resolves the client process (GetNamedPipeClientProcessId), verifies its
image is signed by Logitech (WinVerifyTrust + publisher pinning), and
closes the connection instantly otherwise. python.exe — even renamed —
fails the check.

Bypass: the Options+ UI (logioptionsplus.exe, Logitech-signed) already
runs an agent-socket relay for its renderer (preload exposes `electronNet`
over ipc channels CREATE_CONNECTION / CONNECT_AGENT / SOCKET_SEND /
SOCKET_ON_MESSAGE). If the UI runs with --remote-debugging-port, we attach
via Chrome DevTools Protocol, evaluate JS in the renderer, and drive that
relay. The agent sees the UI's main process as the client — check passes.

Usage:
    python agent_cdp_client.py --list              # show devices + current host
    python agent_cdp_client.py --switch 1          # switch devices to host 1
    python agent_cdp_client.py --dry-run 0         # show what would happen

Requires the UI running with the flag:
    "C:\\Program Files\\LogiOptionsPlus\\logioptionsplus.exe" --remote-debugging-port=9222

Standard library only.
"""
import base64
import json
import os
import socket
import struct
import sys
import time
import urllib.request

CDP_PORT = 9222
PAGE_TITLE = "Logi Options+"


class CDPError(Exception):
    pass


class WS:
    """Minimal websocket client for CDP."""

    def __init__(self, url):
        hostport, path = url.split("//", 1)[1].split("/", 1)
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            (f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
             f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
             f"Sec-WebSocket-Version: 13\r\n\r\n").encode())
        hdr = b""
        while b"\r\n\r\n" not in hdr:
            hdr += self.sock.recv(4096)
        if b"101" not in hdr.split(b"\r\n")[0]:
            raise CDPError("websocket upgrade failed: " + hdr[:80].decode(errors="replace"))
        self.buf = b""
        self.msgid = 0

    def _send_frame(self, payload: bytes):
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        n = len(payload)
        hdr = bytes([0x81])
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.sock.sendall(hdr + mask + masked)

    def _recv_message(self):
        while True:
            if len(self.buf) >= 2:
                op = self.buf[0] & 0x0F
                n = self.buf[1] & 0x7F
                off = 2
                if n == 126 and len(self.buf) >= 4:
                    n = struct.unpack(">H", self.buf[2:4])[0]
                    off = 4
                elif n == 127 and len(self.buf) >= 10:
                    n = struct.unpack(">Q", self.buf[2:10])[0]
                    off = 10
                if len(self.buf) >= off + n:
                    data = self.buf[off:off + n]
                    self.buf = self.buf[off + n:]
                    if op == 1:
                        return json.loads(data.decode())
                    if op == 8:
                        raise CDPError("websocket closed by server")
            chunk = self.sock.recv(65536)
            if not chunk:
                raise CDPError("websocket closed by peer")
            self.buf += chunk

    def call(self, method, params=None, session_id=None):
        self.msgid += 1
        msg = {"id": self.msgid, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        self._send_frame(json.dumps(msg).encode())
        while True:
            m = self._recv_message()
            if m.get("id") == self.msgid:
                if "error" in m:
                    raise CDPError(f"{method}: {m['error'].get('message')}")
                return m


class AgentViaUI:
    """Speaks the agent JSON API through the UI renderer's electronNet relay."""

    BOOTSTRAP = """
    (async () => {
      window.__kvm = [];
      if (!window.__kvmReady) {
        window.__kvmReady = true;
        try { electronNet.onMessage((m) => { try { window.__kvm.push(m); } catch(e){} }); } catch(e) {}
        electronNet.createConnection();
        await new Promise(r => setTimeout(r, 400));
        electronNet.connect();
        await new Promise(r => setTimeout(r, 1200));
      }
      return 'ok';
    })()
    """

    def __init__(self, port=CDP_PORT):
        self.port = port

    def _http(self, path):
        return json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5))

    def connect(self):
        try:
            ver = self._http("/json/version")
        except Exception as e:
            raise CDPError(
                f"no DevTools endpoint on port {self.port} ({e}). Start the UI with:\n"
                f'  "C:\\Program Files\\LogiOptionsPlus\\logioptionsplus.exe" '
                f"--remote-debugging-port={self.port}") from e
        self.ws = WS(ver["webSocketDebuggerUrl"])
        targets = self._http("/json/list")
        page = next((t for t in targets if t["type"] == "page" and PAGE_TITLE in t.get("title", "")), None)
        if not page:
            raise CDPError("Logi Options+ page target not found")
        # attach via the browser endpoint
        tgt = self.ws.call("Target.getTargets")
        info = next(t for t in tgt["result"]["targetInfos"]
                    if t["type"] == "page" and PAGE_TITLE in t.get("title", ""))
        r = self.ws.call("Target.attachToTarget", {"targetId": info["targetId"], "flatten": True})
        self.sid = r["result"]["sessionId"]
        self._eval(self.BOOTSTRAP)

    def _eval(self, js, timeout_note=""):
        r = self.ws.call("Runtime.evaluate",
                         {"expression": js, "awaitPromise": True, "returnByValue": True},
                         session_id=self.sid)
        res = r.get("result", {}).get("result", {})
        if res.get("type") == "string":
            return res.get("value")
        if "exceptionDetails" in r:
            raise CDPError(f"eval failed: {json.dumps(r['exceptionDetails'])[:300]}")
        return json.dumps(res)

    def request(self, verb, path, payload=None, msg_id=None, wait=4.0):
        """One agent request through the relay; returns the response dict or None."""
        mid = msg_id or f"kvm{int(time.time() * 1000) % 100000}"
        msg = {"msg_id": mid, "verb": verb, "path": path}
        if payload is not None:
            msg["payload"] = payload
        js = f"""
        (async () => {{
          const before = window.__kvm.length;
          electronNet.send({json.dumps(json.dumps(msg))});
          const deadline = Date.now() + {int(wait * 1000)};
          while (Date.now() < deadline) {{
            const r = window.__kvm.slice(before).filter(m => m && m.msgId === {json.dumps(mid)}).pop();
            if (r) return JSON.stringify(r);
            await new Promise(r => setTimeout(r, 100));
          }}
          return null;
        }})()
        """
        out = self._eval(js)
        return json.loads(out) if out else None

    def devices(self):
        r = self.request("GET", "/devices/list", msg_id="kvm-devices")
        if not r:
            raise CDPError("no response to /devices/list (bridge not connected?)")
        infos = (r.get("payload") or {}).get("deviceInfos", [])
        return [d for d in infos
                if d.get("deviceType") in ("KEYBOARD", "MOUSE")
                and d.get("connected") is not False
                and (d.get("connectionType") or "") != "VIRTUAL"]

    def current_host(self, dev_id):
        r = self.request("GET", f"/change_host/{dev_id}/host", msg_id=f"kvm-host-{dev_id}")
        if r and "host" in (r.get("payload") or {}):
            return r["payload"]["host"]
        return None

    def switch_host(self, dev_id, host):
        return self.request(
            "SET", f"/change_host/{dev_id}/host",
            payload={"@type": "type.googleapis.com/logi.protocol.devices.ChangeHost",
                     "host": host},
            msg_id=f"kvm-sw-{dev_id}")


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    if dry:
        args.remove("--dry-run")
    target = None
    if "--switch" in args:
        i = args.index("--switch")
        target = int(args[i + 1])
    elif "--list" in args:
        target = None
    elif args:
        target = int(args[0])

    agent = AgentViaUI()
    try:
        agent.connect()
    except CDPError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    devices = agent.devices()
    if not devices:
        print("ERROR: no connected switchable devices found")
        sys.exit(1)

    if target is None or dry:
        print("Devices:")
        for d in devices:
            host = agent.current_host(d["id"])
            print(f"  {d.get('displayName', d['id'])} ({d['id']}, {d.get('deviceType')}): "
                  f"host {host}" + (f" -> would switch to {target}" if (dry and target is not None) else ""))
        if dry and target is not None:
            print(f"[dry-run] would SET /change_host/<id>/host to {target} for all of the above")
        return

    print(f"Switching to host {target}...")
    for d in devices:
        r = agent.switch_host(d["id"], target)
        code = (r or {}).get("result", {}).get("code", "NO_RESPONSE")
        print(f"  {d.get('displayName', d['id'])}: {code}")


if __name__ == "__main__":
    main()
