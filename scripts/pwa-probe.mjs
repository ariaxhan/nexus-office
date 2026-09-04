#!/usr/bin/env node

import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { chromium } from "playwright-core";

const DEFAULT_BROWSER = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const REQUIRED_MANIFEST_FIELDS = ["name", "short_name", "start_url", "display", "icons"];
const SERVICE_WORKER_TIMEOUT_MS = 10_000;

export function missingManifestFields(manifest) {
  return REQUIRED_MANIFEST_FIELDS.filter((field) => {
    const value = manifest?.[field];
    return value == null || value === "" || (Array.isArray(value) && value.length === 0);
  });
}

export async function inspectPwa(page, { mediaSelector = "audio,video", sampleMs = 1500 } = {}) {
  const manifest = await page.locator('link[rel~="manifest"]').getAttribute("href");
  if (!manifest) throw new Error("manifest link absent");
  const manifestResponse = await page.request.get(new URL(manifest, page.url()).href);
  if (!manifestResponse.ok()) throw new Error(`manifest retrieval returned ${manifestResponse.status()}`);
  const fields = missingManifestFields(await manifestResponse.json());
  if (fields.length) throw new Error(`manifest fields absent: ${fields.join(", ")}`);

  const serviceWorker = await page.evaluate(async (timeoutMs) => {
    if (!("serviceWorker" in navigator)) return false;
    const registration = await Promise.race([
      navigator.serviceWorker.ready,
      new Promise(function resolveAfterTimeout(resolve) {
        setTimeout(resolve, timeoutMs, null);
      }),
    ]);
    return Boolean(registration.active);
  }, SERVICE_WORKER_TIMEOUT_MS);
  if (!serviceWorker) throw new Error("service worker inactive");

  const media = page.locator(mediaSelector).first();
  if (!(await media.count())) throw new Error(`media absent: ${mediaSelector}`);
  await media.evaluate((element) => element.play());
  const before = await media.evaluate((element) => element.currentTime);
  await page.waitForTimeout(sampleMs);
  const after = await media.evaluate((element) => element.currentTime);
  if (!(after > before)) throw new Error(`playback did not advance from ${before}`);

  return { manifest: new URL(manifest, page.url()).href, playback: { before, after } };
}

export async function verifyOfflineShell(context, page) {
  await context.setOffline(true);
  try {
    const response = await page.reload({ waitUntil: "domcontentloaded" });
    if (!response?.ok()) throw new Error("offline app shell unavailable");
    return page.url();
  } finally {
    await context.setOffline(false);
  }
}

export async function verifyInstallability(page) {
  const session = await page.context().newCDPSession(page);
  const [{ errors }, manifest] = await Promise.all([
    session.send("Page.getInstallabilityErrors"),
    session.send("Page.getAppManifest"),
  ]);
  if (manifest.errors?.length) throw new Error(`manifest invalid: ${JSON.stringify(manifest.errors)}`);
  if (errors.length) throw new Error(`installability failed: ${JSON.stringify(errors)}`);
  return { manifestUrl: manifest.url };
}

async function captureFailureScreenshot(page, evidenceDir, evidence) {
  if (!page) return;
  try {
    await page.screenshot({ path: path.join(evidenceDir, "failure.png"), fullPage: true });
  } catch (error) {
    evidence.screenshotError = error.message;
  }
}

export async function runProbe({ url, browserPath, evidenceDir, mediaSelector, sampleMs }) {
  const profile = await mkdtemp(path.join(os.tmpdir(), "nexus-pwa-probe-"));
  let browser;
  let context;
  const consoleErrors = [];
  try {
    context = await chromium.launchPersistentContext(profile, {
      executablePath: browserPath,
      headless: true,
    });
    browser = context.browser();
    const page = context.pages()[0] || await context.newPage();
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    page.on("pageerror", (error) => consoleErrors.push(error.message));
    await page.goto(url, { waitUntil: "networkidle" });
    const route = page.url();
    const [pwa, installability] = await Promise.all([
      inspectPwa(page, { mediaSelector, sampleMs }),
      verifyInstallability(page),
    ]);
    const offlineRoute = await verifyOfflineShell(context, page);
    return {
      ok: true,
      browser: await browser.version(),
      route,
      consoleErrors,
      manifest: pwa.manifest,
      playback: pwa.playback,
      installability,
      offlineRoute,
    };
  } catch (error) {
    await mkdir(evidenceDir, { recursive: true });
    const page = browser?.contexts()[0]?.pages()[0];
    const evidence = {
      ok: false,
      browser: browser ? await browser.version() : "launch failed",
      route: page?.url() || url,
      consoleErrors,
      error: error.message,
    };
    await captureFailureScreenshot(page, evidenceDir, evidence);
    await writeFile(path.join(evidenceDir, "failure.json"), `${JSON.stringify(evidence, null, 2)}\n`);
    throw error;
  } finally {
    await context?.close();
    await rm(profile, { recursive: true, force: true });
  }
}

function options(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 2) values[argv[index]] = argv[index + 1];
  if (!values["--url"]) throw new Error("usage: pwa-probe --url URL [--media-selector SELECTOR]");
  const url = new URL(values["--url"]);
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error("URL must use http or https");
  const sampleMs = Number(values["--sample-ms"] || 1500);
  if (!Number.isFinite(sampleMs) || sampleMs <= 0) throw new Error("sample interval must be positive");
  return {
    url: url.href,
    browserPath: values["--browser"] || process.env.PWA_BROWSER || DEFAULT_BROWSER,
    evidenceDir: values["--evidence"] || "pwa-probe-evidence",
    mediaSelector: values["--media-selector"] || "audio,video",
    sampleMs,
  };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  runProbe(options(process.argv.slice(2)))
    .then((result) => process.stdout.write(`${JSON.stringify(result)}\n`))
    .catch((error) => {
      process.stderr.write(`pwa-probe: ${error.message}\n`);
      process.exitCode = 1;
    });
}
