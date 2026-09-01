import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { firefox } = require("playwright");
const baseUrl = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const username = process.env.TRAINING_USERNAME || "";
const password = process.env.TRAINING_PASSWORD || "";
const deploymentId = process.env.CONFIGURATION_DEPLOYMENT_ID || "";
const output = path.resolve(process.env.CONFIGURATION_SCREENSHOT || "training/72-whole-lab-configuration-export.png");
const downloadPath = path.resolve(process.env.CONFIGURATION_ARCHIVE || "/tmp/containerlab-studio-live-configurations.zip");
if (!baseUrl || !username || !password || !deploymentId) throw new Error("Set training login and CONFIGURATION_DEPLOYMENT_ID values.");

await mkdir(path.dirname(output), { recursive: true });
const browser = await firefox.launch({ headless: true });
const context = await browser.newContext({ ignoreHTTPSErrors: true, acceptDownloads: true, viewport: { width: 1600, height: 1100 }, colorScheme: "dark" });
const page = await context.newPage();
const browserErrors = [];
page.on("pageerror", error => browserErrors.push(error.message));
page.on("response", response => { if (response.status() >= 500) browserErrors.push(`${response.status()} ${response.url()}`); });
try {
  await page.goto(`${baseUrl}/accounts/login/`, { waitUntil: "domcontentloaded" });
  await page.locator("#id_username").fill(username);
  await page.locator("#id_password").fill(password);
  await Promise.all([page.waitForURL(url => !url.pathname.includes("/accounts/login/")), page.getByRole("button", { name: /sign in|log in/i }).click()]);
  await page.goto(`${baseUrl}/deployments/${deploymentId}/`, { waitUntil: "domcontentloaded" });
  let history = await (await context.request.get(`${baseUrl}/api/v1/deployments/${deploymentId}/configurations/`, { failOnStatusCode: true })).json();
  if (new Set(history.map(item => item.device)).size < 2) {
    const collectionButtons = page.locator('[data-operation="collect_configuration"]');
    await collectionButtons.first().waitFor({ state: "visible", timeout: 20000 });
    const count = await collectionButtons.count();
    if (count < 2) throw new Error(`Expected at least two GUI configuration collection controls, found ${count}.`);
    for (let index = 0; index < count; index += 1) {
      await collectionButtons.nth(index).click();
      await page.waitForTimeout(500);
    }
    for (let attempt = 0; attempt < 30; attempt += 1) {
      await page.waitForTimeout(1000);
      history = await (await context.request.get(`${baseUrl}/api/v1/deployments/${deploymentId}/configurations/`, { failOnStatusCode: true })).json();
      if (new Set(history.map(item => item.device)).size >= 2) break;
    }
    await page.reload({ waitUntil: "domcontentloaded" });
  }
  const devices = [...new Set(history.map(item => item.device))].sort();
  if (devices.length < 2) throw new Error(`Expected collected configurations for at least two devices, found ${devices.join(", ") || "none"}.`);
  const section = page.locator("#configuration-list").locator("..");
  const exportButton = page.locator("#export-configurations");
  await exportButton.waitFor({ state: "visible", timeout: 20000 });
  await page.waitForFunction(() => !document.querySelector("#export-configurations")?.disabled);
  const downloadPromise = page.waitForEvent("download");
  await exportButton.click();
  const download = await downloadPromise;
  await download.saveAs(downloadPath);
  const archiveBytes = await readFile(downloadPath);
  if (archiveBytes[0] !== 0x50 || archiveBytes[1] !== 0x4b) throw new Error("Downloaded file is not a ZIP archive.");
  const manifest = JSON.parse(execFileSync("/usr/bin/unzip", ["-p", downloadPath, "manifest.json"], { encoding: "utf8" }));
  const exportedDevices = manifest.configurations.map(item => item.device).sort();
  if (JSON.stringify(exportedDevices) !== JSON.stringify(devices)) throw new Error(`Archive devices ${exportedDevices} did not match history devices ${devices}.`);
  for (const item of manifest.configurations) {
    const content = execFileSync("/usr/bin/unzip", ["-p", downloadPath, item.filename]);
    const checksum = createHash("sha256").update(content).digest("hex");
    if (checksum !== item.checksum || content.length !== item.bytes) throw new Error(`Manifest integrity failed for ${item.device}.`);
  }
  await section.screenshot({ path: output });
  if (browserErrors.length) throw new Error(`Firefox reported errors: ${browserErrors.join(" | ")}`);
  console.log(JSON.stringify({ browser: "Firefox", devices: exportedDevices, entries: manifest.configurations.length, archive: downloadPath, screenshot: output }));
} finally {
  await browser.close();
}
