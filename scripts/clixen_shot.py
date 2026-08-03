#!/usr/bin/env python3
"""
Clixen Screenshot Helper Tool
Takes a screenshot (full-screen or interactive) and uploads/sends it to Clixen agent.
"""

import os
import sys
import time
import json
import base64
import hmac
import hashlib
import tempfile
import subprocess
import argparse
from pathlib import Path
import requests

def get_workspace_state() -> tuple[str, str]:
    """Find and read email and session secret from workspace_state.json."""
    # Check environment variable first
    state_path_env = os.environ.get("G4L_WORKSPACE_STATE_PATH")
    if state_path_env:
        paths_to_try = [Path(state_path_env)]
    else:
        # Check standard relative paths
        script_dir = Path(__file__).resolve().parent
        paths_to_try = [
            script_dir.parent / "tools-harness" / "workspace_state.json",
            script_dir.parent / "workspace_state.json",
            Path.cwd() / "tools-harness" / "workspace_state.json",
            Path.cwd() / "workspace_state.json",
        ]

    state_path = None
    for p in paths_to_try:
        if p.exists():
            state_path = p
            break

    if not state_path:
        print(f"Error: Could not locate workspace_state.json in paths: {[str(p) for p in paths_to_try]}")
        print("Please ensure the Clixen server is set up and workspace_state.json exists.")
        sys.exit(1)

    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        account = data.get("account")
        if not account or "email" not in account:
            print(f"Error: No user account found in {state_path}")
            sys.exit(1)
        email = account["email"]
        secret = data.get("session_secret")
        if not secret:
            print(f"Error: session_secret not found in {state_path}")
            sys.exit(1)
        return email, secret
    except Exception as e:
        print(f"Error reading workspace state from {state_path}: {e}")
        sys.exit(1)

def sign_session(email: str, secret: str) -> str:
    """Generate a signed session cookie token using the workspace secret."""
    # Match the signing logic in chat_ui.py
    payload = {
        "email": email,
        "exp": int(time.time()) + 60 * 60 * 24 * 30,  # 30 days
    }
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    
    sig = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")
    return f"{body}.{sig_b64}"

def capture_screen(interactive: bool = False) -> str:
    """Capture screen using macOS native screencapture tool."""
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)

    cmd = ["/usr/sbin/screencapture"]
    if interactive:
        cmd.append("-i")
        print("Select screen area or window to capture (or press ESC to cancel)...")
    else:
        cmd.append("-x")
        print("Capturing full screen...", end="", flush=True)

    res = subprocess.run(cmd)
    if res.returncode != 0:
        print(f"\nCapture failed or was cancelled (exit code: {res.returncode})")
        try:
            os.remove(path)
        except Exception:
            pass
        sys.exit(1)

    if not interactive:
        print(" Done.")
    return path

def upload_screenshot(url: str, token: str, file_path: str, chat_id: str) -> str:
    """Upload the screenshot PNG to the `/upload` API endpoint."""
    upload_url = f"{url.rstrip('/')}/upload"
    cookies = {"g4l_session": token}

    print("Uploading screenshot to agent...", end="", flush=True)
    try:
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f, "image/png")}
            data = {"chat_id": chat_id}
            resp = requests.post(upload_url, cookies=cookies, files=files, data=data, timeout=30)
            
        if resp.status_code != 200:
            print(f"\nUpload failed (HTTP {resp.status_code}): {resp.text}")
            sys.exit(1)
            
        print(" Done.")
        return resp.json()["id"]
    except Exception as e:
        print(f"\nError uploading file: {e}")
        sys.exit(1)

def query_agent(url: str, token: str, params: dict):
    """Call the streaming /chat/stream endpoint and print SSE outputs in real-time."""
    stream_url = f"{url.rstrip('/')}/chat/stream"
    cookies = {"g4l_session": token}

    print(f"Connecting to Clixen stream with chat_id={params.get('chat_id')}...")
    try:
        resp = requests.get(stream_url, params=params, cookies=cookies, stream=True, timeout=300)
        if resp.status_code != 200:
            print(f"Failed to connect to agent stream (HTTP {resp.status_code})")
            print(resp.text)
            return

        print("\n--- Agent Response ---")
        for chunk in resp.iter_lines():
            if not chunk:
                continue
            line = chunk.decode("utf-8", errors="replace").strip()
            if line.startswith("data:"):
                data_str = line[len("data:"):].strip()
                try:
                    data = json.loads(data_str)
                    if "token" in data:
                        sys.stdout.write(data["token"])
                        sys.stdout.flush()
                    if "error" in data:
                        sys.stdout.write(f"\n[Error: {data['error']}]\n")
                        sys.stdout.flush()
                    if data.get("done"):
                        model = data.get("model", "unknown")
                        intent = data.get("intent", "unknown")
                        print(f"\n\n[Done. Model: {model}, Intent: {intent}]")
                        break
                except Exception:
                    pass
    except KeyboardInterrupt:
        print("\n\nStream interrupted by user.")
    except Exception as e:
        print(f"\nStream connection error: {e}")

def main():
    parser = argparse.ArgumentParser(description="Take macOS screenshot and send to Clixen agent.")
    parser.add_argument(
        "-m", "--message", 
        default="Describe what is on my screen and help me with it.",
        help="Text prompt/query to send with the screenshot."
    )
    parser.add_argument(
        "-i", "--interactive",
        action="store_true",
        help="Interactive capture mode (select area or window)."
    )
    parser.add_argument(
        "-u", "--url",
        default="http://localhost:9234",
        help="Clixen API base URL (default: http://localhost:9234)."
    )
    parser.add_argument(
        "-c", "--chat-id",
        default="web_ui",
        help="Target chat/session ID (default: web_ui)."
    )
    parser.add_argument(
        "--model",
        default="",
        help="Optional model override (e.g. gemma4:12b-mlx)."
    )
    parser.add_argument(
        "--web-search",
        action="store_true",
        help="Force web search on the query."
    )
    parser.add_argument(
        "--local-agent",
        action="store_true",
        help="Force local agent (tool use) on the query."
    )
    parser.add_argument(
        "--plan-mode",
        action="store_true",
        help="Force planning mode on the query."
    )

    args = parser.parse_args()

    # 1. Fetch auth info and sign session token
    email, secret = get_workspace_state()
    token = sign_session(email, secret)

    # 2. Capture screen
    path = capture_screen(interactive=args.interactive)

    try:
        # 3. Upload screenshot
        file_id = upload_screenshot(args.url, token, path, args.chat_id)

        # 4. Request stream
        params = {
            "message": args.message,
            "chat_id": args.chat_id,
            "file_ids": file_id,
            "web_search": "1" if args.web_search else "0",
            "local_agent": "1" if args.local_agent else "0",
            "plan_mode": "1" if args.plan_mode else "0",
            "model": args.model
        }
        query_agent(args.url, token, params)
    finally:
        # Clean up temp file
        try:
            os.remove(path)
        except Exception:
            pass

if __name__ == "__main__":
    main()
