import path from "node:path";

// E2E drives a *debug* bundle, not the release one: the embedded WebDriver
// (tauri-plugin-wdio-webdriver) is registered under cfg(debug_assertions) so a
// shipped build can never open a local server that evaluates JS in the orb.
// `tauri build --debug` still produces a real .app, so Info.plist (and thus the
// microphone usage string) is present and getUserMedia works.
const APP = path.resolve(
  import.meta.dirname,
  "src-tauri/target/debug/bundle/macos/Clixen Orb.app/Contents/MacOS/clixen-orb",
);

export const config = {
  runner: "local",
  specs: ["./e2e/specs/**/*.spec.js"],
  maxInstances: 1,
  logLevel: "warn",
  bail: 0,
  waitforTimeout: 10000,
  connectionRetryTimeout: 120000,
  connectionRetryCount: 1,

  capabilities: [
    {
      browserName: "tauri",
      "tauri:options": { application: APP },
    },
  ],

  services: [
    [
      "@wdio/tauri-service",
      {
        appBinaryPath: APP,
        driverProvider: "embedded",
        embeddedPort: 4445,
      },
    ],
  ],

  framework: "mocha",
  reporters: ["spec"],
  mochaOpts: { ui: "bdd", timeout: 60000 },
};
