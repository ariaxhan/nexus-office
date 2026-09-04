import assert from "node:assert/strict";
import test from "node:test";

import {
  inspectPwa, missingManifestFields, verifyInstallability, verifyOfflineShell,
} from "../scripts/pwa-probe.mjs";

function pageFixture({ advance = true, manifest = {} } = {}) {
  let currentTime = 2;
  const media = {
    count: async () => 1,
    evaluate: async (callback) => {
      const element = { currentTime, play: async () => {} };
      const result = await callback(element);
      currentTime = advance ? currentTime + 1 : currentTime;
      return result;
    },
  };
  return {
    url: () => "https://sandbox.example/app",
    locator: (selector) => selector.startsWith("link")
      ? { getAttribute: async () => "/manifest.webmanifest" }
      : { first: () => media },
    request: { get: async () => ({ ok: () => true, json: async () => ({
      name: "Sandbox", short_name: "Sandbox", start_url: "/app", display: "standalone",
      icons: [{ src: "/icon.png" }], ...manifest,
    }) }) },
    evaluate: async () => true,
    waitForTimeout: async () => {},
  };
}

test("repository PWA check covers playback, offline shell, and installability requirements", async () => {
  const playback = await inspectPwa(pageFixture());
  assert.ok(playback.playback.after > playback.playback.before);

  const offline = [];
  const context = { setOffline: async (value) => offline.push(value) };
  const page = { url: () => "https://sandbox.example/app", reload: async () => ({ ok: () => true }) };
  assert.equal(await verifyOfflineShell(context, page), "https://sandbox.example/app");
  assert.deepEqual(offline, [true, false]);

  assert.deepEqual(missingManifestFields({
    name: "Sandbox", short_name: "Sandbox", start_url: "/", display: "standalone", icons: [],
  }), ["icons"]);

  await assert.rejects(
    inspectPwa({ ...pageFixture(), evaluate: async () => false }),
    /service worker inactive/,
  );

  const installabilityPage = {
    context: () => ({ newCDPSession: async () => ({ send: async (method) => method.endsWith("Errors")
      ? { errors: [{ errorId: "no-icon" }] }
      : { errors: [], url: "https://sandbox.example/manifest.webmanifest" } }) }),
  };
  await assert.rejects(verifyInstallability(installabilityPage), /installability failed.*no-icon/);
});
