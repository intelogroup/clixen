<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.clixen.task_worker</string>

    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/caffeinate</string>
        <string>-i</string>
        <string>__PYTHON_BIN__</string>
        <string>-m</string>
        <string>jobs.worker</string>
        <string>--poll-interval</string>
        <string>30</string>
    </array>

    <key>WorkingDirectory</key>
    <string>__HARNESS_DIR__</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>SoftResourceLimits</key>
    <dict>
        <key>NumberOfFiles</key>
        <integer>65536</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>__HARNESS_DIR__/task_worker.log</string>

    <key>StandardErrorPath</key>
    <string>__HARNESS_DIR__/task_worker.err.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
