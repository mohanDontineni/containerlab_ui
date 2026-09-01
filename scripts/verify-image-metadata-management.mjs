import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { firefox } = require("playwright");
const baseUrl = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const username = process.env.TRAINING_USERNAME || "";
const password = process.env.TRAINING_PASSWORD || "";
const deploymentId = process.env.METADATA_DEPLOYMENT_ID || "";
const output = path.resolve(process.env.METADATA_SCREENSHOT || "training/60-image-metadata-management.png");
if (!baseUrl || !username || !password || !deploymentId) throw new Error("Set the training URL, credentials, and METADATA_DEPLOYMENT_ID.");

await mkdir(path.dirname(output), { recursive: true });
const browser = await firefox.launch({ headless: true });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1000 }, colorScheme: "dark" });
const page = await context.newPage();
const errors = [];
page.on("pageerror", error => errors.push(error.message));
page.on("response", response => { if (response.status() >= 500) errors.push(`${response.status()} ${response.url()}`); });
try {
  await page.goto(`${baseUrl}/accounts/login/`, { waitUntil: "domcontentloaded" });
  await page.locator("#id_username").fill(username);await page.locator("#id_password").fill(password);
  await Promise.all([page.waitForURL(url => !url.pathname.includes("/accounts/login/")),page.getByRole("button",{name:/sign in|log in/i}).click()]);
  await page.goto(`${baseUrl}/images/`, { waitUntil: "domcontentloaded" });
  await page.locator("#catalog-search").fill("frr");
  const edit = page.getByRole("button", { name: "Edit details" }).first();await edit.waitFor({ timeout: 10000 });
  const artifactId = await edit.getAttribute("data-edit-image");if (!artifactId) throw new Error("The FRR image has no metadata identity.");
  await edit.click();await page.locator("#image-metadata-dialog[open]").waitFor();
  await page.locator("#image-metadata-vendor").fill("FRRouting");await page.locator("#image-metadata-category").selectOption("router");await page.locator("#image-metadata-version").fill("10.4.1");
  await Promise.all([page.waitForURL(`${baseUrl}/images/`),page.locator("#save-image-metadata").click()]);
  await page.locator("#catalog-search").fill("FRRouting 10.4.1");
  const persistedEdit = page.locator(`[data-edit-image="${artifactId}"]`);await persistedEdit.waitFor({ timeout: 10000 });
  if (await persistedEdit.locator("xpath=ancestor::article").isHidden()) throw new Error("Saved vendor/version are not searchable in the image library.");
  await persistedEdit.click();await page.locator("#image-metadata-dialog[open]").waitFor();
  if (await page.locator("#image-metadata-vendor").inputValue() !== "FRRouting" || await page.locator("#image-metadata-category").inputValue() !== "router" || await page.locator("#image-metadata-version").inputValue() !== "10.4.1") throw new Error("The metadata dialog did not reload the persisted identity.");
  const evidence = await (await context.request.get(`${baseUrl}/api/v1/images/${artifactId}/evidence/`, { failOnStatusCode: true })).json();
  if (evidence.vendor !== "FRRouting" || evidence.category !== "router" || evidence.version !== "10.4.1") throw new Error("Supply-chain evidence did not reflect the catalog identity.");
  const artifact = await (await context.request.get(`${baseUrl}/api/v1/images/${artifactId}/`, { failOnStatusCode: true })).json();
  const imageList = await (await context.request.get(`${baseUrl}/api/v1/images/`, { failOnStatusCode: true })).json();
  if ("storage_reference" in artifact || JSON.stringify(imageList).includes("storage_reference")) throw new Error("An internal artifact storage path escaped the API boundary.");
  const runtime = await (await context.request.get(`${baseUrl}/api/v1/deployments/${deploymentId}/runtime/`, { failOnStatusCode: true })).json();
  const ready = runtime.devices.filter(device => ["r1","r2"].includes(device.name) && device.observed_readiness === "ready");
  if (ready.length !== 2) throw new Error("The production topology was not healthy after catalog metadata editing.");
  await page.locator("#image-metadata-dialog").screenshot({ path: output });
  if (errors.length) throw new Error(`Firefox reported errors: ${errors.join(" | ")}`);
  console.log(JSON.stringify({ browser:"firefox",artifactId,vendor:evidence.vendor,category:evidence.category,version:evidence.version,
    internalStorageHidden:true,topologyDevicesReady:ready.map(device=>device.name),screenshot:output }));
} finally {
  await browser.close();
}
