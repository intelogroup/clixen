<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.clixen.daily_email_summary</string>

    <key>ProgramArguments</key>
    <array>
        <string>__PYTHON_BIN__</string>
        <string>__HARNESS_DIR__/scripts/daily_email_summary.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>__HARNESS_DIR__</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
        <key>EMAIL_SUMMARY_EMAIL_LIMIT</key>
        <string>3</string>
    </dict>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>6</integer>
        <key>Minute</key>
        <integer>30</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>__HARNESS_DIR__/email_summary.log</string>
    <key>StandardErrorPath</key>
    <string>__HARNESS_DIR__/email_summary.err.log</string>
</dict>
</plist>
