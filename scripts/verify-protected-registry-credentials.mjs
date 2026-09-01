import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import { randomBytes } from "node:crypto";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { firefox } = require("playwright");
const baseUrl = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const username = process.env.TRAINING_USERNAME || "";
const password = process.env.TRAINING_PASSWORD || "";
const deploymentId = process.env.CREDENTIAL_DEPLOYMENT_ID || "";
const output = path.resolve(process.env.CREDENTIAL_SCREENSHOT || "training/61-protected-registry-credentials.png");
if (!baseUrl || !username || !password || !deploymentId) throw new Error("Set the training URL, credentials, and CREDENTIAL_DEPLOYMENT_ID.");

await mkdir(path.dirname(output), { recursive: true });
const secret = `temporary-${randomBytes(24).toString("hex")}`;
const rotatedSecret = `rotated-${randomBytes(24).toString("hex")}`;
const credentialName = `Training registry ${Date.now()}`;
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
  await page.goto(`${baseUrl}/images/credentials/new/`, { waitUntil: "domcontentloaded" });
  const projectOptions = await page.locator("#id_project option").evaluateAll(options => options.map(option => ({ value: option.value, label: option.textContent?.trim() || "" })).filter(option => option.value));
  const project = projectOptions.find(option => option.label === "Production Runtime QA") || projectOptions[0];if (!project) throw new Error("No editable project is available for credential acceptance.");
  await page.locator("#id_project").selectOption(project.value);await page.locator("#id_name").fill(credentialName);await page.locator("#id_registry_host").fill("registry.example.invalid");
  await page.locator("#id_credential_type").selectOption("token");await page.locator("#id_secret").fill(secret);
  await Promise.all([page.waitForURL(`${baseUrl}/images/credentials/`),page.getByRole("button",{name:"Save credential"}).click()]);
  const row = page.locator(".credential-row").filter({ hasText: credentialName });await row.waitFor({ timeout: 10000 });
  const firstText = (await row.textContent()) || "";const firstFingerprint = firstText.match(/fingerprint\s+([a-f0-9]{16})/i)?.[1];
  if (!firstFingerprint || firstText.includes(secret) || (await page.content()).includes(secret)) throw new Error("The created credential was not safely fingerprinted and redacted.");
  const editLink = row.getByRole("link", { name: "Edit / rotate" });const credentialId = (await editLink.getAttribute("href"))?.match(/credentials\/([^/]+)\/edit/)?.[1];
  if (!credentialId) throw new Error("Credential identity was not rendered.");
  await editLink.click();await page.locator("#id_secret").fill(rotatedSecret);
  await Promise.all([page.waitForURL(`${baseUrl}/images/credentials/`),page.getByRole("button",{name:"Save credential"}).click()]);
  const rotatedRow = page.locator(".credential-row").filter({ hasText: credentialName });await rotatedRow.waitFor();
  const rotatedText = (await rotatedRow.textContent()) || "";const rotatedFingerprint = rotatedText.match(/fingerprint\s+([a-f0-9]{16})/i)?.[1];
  if (!rotatedFingerprint || rotatedFingerprint === firstFingerprint || rotatedText.includes(rotatedSecret) || (await page.content()).includes(rotatedSecret)) throw new Error("Credential rotation did not replace only the redacted fingerprint.");
  const apiCredential = await (await context.request.get(`${baseUrl}/api/v1/image-credentials/${credentialId}/`, { failOnStatusCode: true })).json();
  const serialized = JSON.stringify(apiCredential);for (const forbidden of [secret,rotatedSecret,"encrypted_secret","secret_name"]) if (serialized.includes(forbidden)) throw new Error(`Credential API exposed ${forbidden}.`);
  if (!apiCredential.credential_present || apiCredential.secret_fingerprint !== rotatedFingerprint) throw new Error("Credential API did not report the rotated protected reference.");
  await rotatedRow.screenshot({ path: output });
  page.once("dialog", dialog => dialog.accept());await rotatedRow.getByRole("button", { name: "Deactivate" }).click();await page.waitForLoadState("domcontentloaded");
  const inactive = page.locator(".credential-row").filter({ hasText: credentialName });if (!((await inactive.textContent()) || "").includes("Inactive")) throw new Error("Credential deactivation was not retained in the GUI.");
  const deactivated = await (await context.request.get(`${baseUrl}/api/v1/image-credentials/${credentialId}/`, { failOnStatusCode: true })).json();if (deactivated.is_active) throw new Error("Credential remained active after deactivation.");
  const runtime = await (await context.request.get(`${baseUrl}/api/v1/deployments/${deploymentId}/runtime/`, { failOnStatusCode: true })).json();
  const ready = runtime.devices.filter(device => ["r1","r2"].includes(device.name) && device.observed_readiness === "ready");if (ready.length !== 2) throw new Error("Production topology health changed during credential lifecycle acceptance.");
  if (errors.length) throw new Error(`Firefox reported errors: ${errors.join(" | ")}`);
  console.log(JSON.stringify({browser:"firefox",credentialId,project:project.label,registryHost:"registry.example.invalid",credentialType:"token",
    fingerprintRotated:firstFingerprint!==rotatedFingerprint,secretRedacted:true,deactivated:true,topologyDevicesReady:ready.map(device=>device.name),screenshot:output}));
} finally {
  await browser.close();
}
