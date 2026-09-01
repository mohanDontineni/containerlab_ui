import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { firefox } = require("playwright");
const baseUrl = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const username = process.env.TRAINING_USERNAME || "";
const password = process.env.TRAINING_PASSWORD || "";
const deploymentId = process.env.CONFIGURATION_DEPLOYMENT_ID || "";
const output = path.resolve(process.env.CONFIGURATION_COLLECTION_SCREENSHOT || "training/73-whole-lab-configuration-collection.png");
if (!baseUrl || !username || !password || !deploymentId) throw new Error("Set training login and CONFIGURATION_DEPLOYMENT_ID values.");

await mkdir(path.dirname(output), { recursive: true });
const browser = await firefox.launch({ headless: true });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1100 }, colorScheme: "dark" });
const page = await context.newPage();
const errors = [];
page.on("pageerror", error => errors.push(error.message));
page.on("response", response => { if (response.status() >= 500) errors.push(`${response.status()} ${response.url()}`); });
const history = async () => await (await context.request.get(`${baseUrl}/api/v1/deployments/${deploymentId}/configurations/`, { failOnStatusCode: true })).json();
const latestVersions = rows => new Map(rows.map(row => row.device).sort().map(device => [device, Math.max(...rows.filter(row => row.device === device).map(row => row.version))]));
try {
  await page.goto(`${baseUrl}/accounts/login/`, { waitUntil: "domcontentloaded" });
  await page.locator("#id_username").fill(username);
  await page.locator("#id_password").fill(password);
  await Promise.all([page.waitForURL(url => !url.pathname.includes("/accounts/login/")), page.getByRole("button", { name: /sign in|log in/i }).click()]);
  const before = latestVersions(await history());
  await page.goto(`${baseUrl}/deployments/${deploymentId}/`, { waitUntil: "domcontentloaded" });
  const button = page.locator("#collect-all-configurations");
  await button.waitFor({ state: "visible", timeout: 20000 });
  await page.waitForFunction(() => !document.querySelector("#collect-all-configurations")?.disabled);
  const responsePromise = page.waitForResponse(response => response.url().endsWith(`/deployments/${deploymentId}/configurations/collect/`) && response.request().method() === "POST");
  await button.click();
  const scheduledResponse = await responsePromise;
  if (scheduledResponse.status() !== 202) throw new Error(`Whole-lab collection returned ${scheduledResponse.status()}: ${await scheduledResponse.text()}`);
  const scheduled = await scheduledResponse.json();
  if (scheduled.count !== 2 || scheduled.jobs.length !== 2) throw new Error(`Expected two scheduled jobs, received ${scheduled.count}.`);
  let rows = [];
  for (let attempt = 0; attempt < 40; attempt += 1) {
    await page.waitForTimeout(750);
    rows = await history();
    const after = latestVersions(rows);
    if (["r1", "r2"].every(device => after.get(device) > (before.get(device) || 0))) break;
  }
  const after = latestVersions(rows);
  if (!["r1", "r2"].every(device => after.get(device) > (before.get(device) || 0))) throw new Error("Both router histories did not advance.");
  await page.waitForFunction(() => document.querySelector("#collect-all-configurations")?.textContent?.includes("Collect all"));
  const section = page.locator("#configuration-list").locator("..");
  await section.screenshot({ path: output });
  if (errors.length) throw new Error(`Firefox reported errors: ${errors.join(" | ")}`);
  console.log(JSON.stringify({ browser: "Firefox", scheduledJobs: scheduled.jobs.map(job => job.id), before: Object.fromEntries(before), after: Object.fromEntries(after), screenshot: output }));
} finally {
  await browser.close();
}
