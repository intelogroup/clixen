import json
import socket
import sys


def send(cmd, params=None, timeout=120):
    s = socket.create_connection(("localhost", 9876), timeout=timeout)
    s.sendall((json.dumps({"type": cmd, "params": params or {}}) + "\n").encode())
    s.settimeout(timeout)
    data = b""
    while True:
        try:
            chunk = s.recv(65536)
        except socket.timeout:
            break
        if not chunk:
            break
        data += chunk
        try:
            json.loads(data.decode())
            break
        except ValueError:
            continue
    s.close()
    return json.loads(data.decode(errors="replace"))


def run_code(path):
    code = open(path).read()
    r = send("execute_code", {"code": code})
    if r.get("status") != "success":
        print("ERROR:", r.get("message"))
        sys.exit(1)
    print(r["result"].get("result", ""))


if __name__ == "__main__":
    if sys.argv[1] == "code":
        run_code(sys.argv[2])
    else:
        params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
        print(json.dumps(send(sys.argv[1], params), indent=1)[:4000])
