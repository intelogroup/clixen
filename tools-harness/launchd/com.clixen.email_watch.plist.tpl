<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.clixen.email_watch___SENDER_ID__</string>

    <key>ProgramArguments</key>
    <array>
        <string>__PYTHON_BIN__</string>
        <string>__HARNESS_DIR__/scripts/email_watch.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>__HARNESS_DIR__</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
        <key>EMAIL_WATCH_LOOP</key>
        <string>1</string>
        <key>EMAIL_WATCH_POLL_SECONDS</key>
        <string>60</string>
        <key>EMAIL_WATCH_SENDER</key>
        <string>__SENDER__</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>__HARNESS_DIR__/email_watch___SENDER_ID__.log</string>
    <key>StandardErrorPath</key>
    <string>__HARNESS_DIR__/email_watch___SENDER_ID__.err.log</string>
</dict>
</plist>
