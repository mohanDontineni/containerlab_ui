import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { firefox } = require("playwright");
const baseUrl = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const username = process.env.TRAINING_USERNAME || "";
const password = process.env.TRAINING_PASSWORD || "";
const deploymentId = process.env.EVIDENCE_DEPLOYMENT_ID || "";
const output = path.resolve(process.env.EVIDENCE_SCREENSHOT || "training/59-image-supply-chain-evidence.png");
if (!baseUrl || !username || !password || !deploymentId) throw new Error("Set the training URL, credentials, and EVIDENCE_DEPLOYMENT_ID.");

await mkdir(path.dirname(output), { recursive: true });
const browser = await firefox.launch({ headless: true });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1050 }, colorScheme: "dark" });
const page = await context.newPage();
const errors = [];
page.on("pageerror", error => errors.push(error.message));
page.on("response", response => { if (response.status() >= 500) errors.push(`${response.status()} ${response.url()}`); });
try {
  await page.goto(`${baseUrl}/accounts/login/`, { waitUntil: "domcontentloaded" });
  await page.locator("#id_username").fill(username);
  await page.locator("#id_password").fill(password);
  await Promise.all([page.waitForURL(url => !url.pathname.includes("/accounts/login/")), page.getByRole("button", { name: /sign in|log in/i }).click()]);
  await page.goto(`${baseUrl}/images/`, { waitUntil: "domcontentloaded" });
  await page.locator("#catalog-search").fill("frr");
  await page.locator("#image-status-filter").selectOption("validated");
  await page.locator("#image-architecture-filter").selectOption("amd64");
  const repair = page.getByRole("button", { name: "Repair node copy" }).first();
  await repair.waitFor({ timeout: 10000 });
  const row = repair.locator("xpath=ancestor::article");
  if (await row.isHidden()) throw new Error("Combined image filters hid the validated amd64 FRR artifact.");
  const artifactId = await row.getByRole("button", { name: "Evidence" }).getAttribute("data-image-evidence");
  if (!artifactId) throw new Error("The visible FRR artifact has no evidence identity.");
  await repair.click();
  const evidenceUrl = `${baseUrl}/api/v1/images/${artifactId}/evidence/`;
  let evidence;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    await page.waitForTimeout(1500);
    const response = await context.request.get(evidenceUrl, { failOnStatusCode: true });
    evidence = await response.json();
    if (evidence.builds[0]?.status === "succeeded" && evidence.builds[0]?.log_excerpt) break;
    if (evidence.builds[0]?.status === "failed") throw new Error(`Image repair failed: ${JSON.stringify(evidence.builds[0].failure)}`);
  }
  if (!evidence?.builds[0]?.log_excerpt) throw new Error("The completed image build did not retain bounded output.");
  if (evidence.publications[0]?.status !== "ready" || evidence.publications[0]?.compatibility?.publication_mode !== "node-containerd") throw new Error("Node-local immutable publication evidence is incomplete.");
  await page.goto(`${baseUrl}/images/`, { waitUntil: "domcontentloaded" });
  await page.locator("#catalog-search").fill("frr");
  await page.locator("#image-status-filter").selectOption("validated");
  await page.locator("#image-architecture-filter").selectOption("amd64");
  await page.locator(`[data-image-evidence="${artifactId}"]`).click();
  await page.locator("#image-evidence-title").filter({ hasText: /frr/i }).waitFor({ timeout: 15000 });
  await page.locator("#image-build-evidence details").first().evaluate(element => { element.open = true; });
  const dialogText = (await page.locator("#image-evidence-dialog").textContent()) || "";
  for (const required of ["Validated", "amd64", "docker-archive", "node-containerd", "Retained build output"]) {
    if (!dialogText.toLowerCase().includes(required.toLowerCase())) throw new Error(`Evidence dialog is missing ${required}.`);
  }
  const runtime = await (await context.request.get(`${baseUrl}/api/v1/deployments/${deploymentId}/runtime/`, { failOnStatusCode: true })).json();
  const ready = runtime.devices.filter(device => ["r1", "r2"].includes(device.name) && device.observed_readiness === "ready");
  if (ready.length !== 2) throw new Error("The acceptance topology was not healthy after image republication.");
  await page.locator("#image-evidence-dialog").screenshot({ path: output });
  if (errors.length) throw new Error(`Firefox reported errors: ${errors.join(" | ")}`);
  console.log(JSON.stringify({ browser: "firefox", artifactId, validation: evidence.validation_status,
    architecture: evidence.architecture, publicationMode: evidence.publications[0].compatibility.publication_mode,
    retainedBuildCharacters: evidence.builds[0].log_excerpt.length, topologyDevicesReady: ready.map(device => device.name), screenshot: output }));
} finally {
  await browser.close();
}
