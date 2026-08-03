import sys
import os
from pathlib import Path
import json
import pytest

# Add scripts directory to path to import clixen_shot
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

import clixen_shot
import chat_ui

def test_session_signing():
    email = "test@user.com"
    secret = "my-super-secret-key-12345"
    
    # Generate token using clixen_shot
    token = clixen_shot.sign_session(email, secret)
    
    # Patch chat_ui's workspace state session secret to match
    chat_ui._workspace_state["session_secret"] = secret
    
    # Unsign using chat_ui's function
    payload = chat_ui._unsign_session(token)
    
    assert payload is not None
    assert payload["email"] == email

def test_get_workspace_state(tmp_path, monkeypatch):
    # Create a mock workspace_state.json
    state_file = tmp_path / "workspace_state.json"
    state_data = {
        "account": {
            "email": "agent@clixen.ai"
        },
        "session_secret": "xyz123"
    }
    state_file.write_text(json.dumps(state_data), encoding="utf-8")
    
    # Set env var
    monkeypatch.setenv("G4L_WORKSPACE_STATE_PATH", str(state_file))
    
    email, secret = clixen_shot.get_workspace_state()
    assert email == "agent@clixen.ai"
    assert secret == "xyz123"
