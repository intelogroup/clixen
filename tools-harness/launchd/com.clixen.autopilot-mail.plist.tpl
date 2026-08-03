<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.clixen.autopilot-mail</string>

    <key>ProgramArguments</key>
    <array>
        <string>__AUTOPILOT_BIN__</string>
    </array>

    <key>WorkingDirectory</key>
    <string>__HARNESS_DIR__/data/autopilot</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>__HARNESS_DIR__/data/autopilot/autopilot_stdout.log</string>

    <key>StandardErrorPath</key>
    <string>__HARNESS_DIR__/data/autopilot/autopilot_stderr.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
        <key>SQLITE_PATH</key>
        <string>__HARNESS_DIR__/data/autopilot/autopilot.db</string>
        <key>API_KEYS</key>
        <string>clixen-test-key</string>
        <key>DOMAIN</key>
        <string>resend.dev</string>
        <key>SMTP_HOST</key>
        <string>smtp.resend.com</string>
        <key>SMTP_PORT</key>
        <string>465</string>
        <key>SMTP_USER</key>
        <string>resend</string>
        <key>SMTP_PASS</key>
        <string>SET_VIA_ENV_FILE</string>
    </dict>
</dict>
</plist>
