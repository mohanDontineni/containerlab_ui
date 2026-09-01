import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { firefox } = require("playwright");
const baseUrl = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const username = process.env.TRAINING_USERNAME || "";
const password = process.env.TRAINING_PASSWORD || "";
const deploymentId = process.env.PCAP_DEPLOYMENT_ID || "";
const output = path.resolve(process.env.PCAP_SCREENSHOT || "training/57-native-packet-analysis.png");
if (!baseUrl || !username || !password || !deploymentId) throw new Error("Set the training URL, credentials, and PCAP_DEPLOYMENT_ID.");

await mkdir(path.dirname(output), { recursive: true });
const browser = await firefox.launch({ headless: true });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1100 }, colorScheme: "dark" });
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
  await page.locator("#capture-device").waitFor({ state: "visible", timeout: 20000 });
  await page.waitForFunction(() => [...document.querySelectorAll("#capture-device option")].some(option => option.textContent === "r1"));
  await page.locator("#capture-device").selectOption({ label: "r1" });
  await page.locator("#capture-interface").selectOption({ label: "eth1" });
  await page.locator("#capture-duration").fill("8");
  await page.locator("#capture-limit").fill("500");
  await page.getByRole("button", { name: "Start capture" }).click();
  await page.locator("#capture-list").getByText(/scheduled|capturing/i).first().waitFor({ timeout: 10000 });
  await page.locator("#ping-node").selectOption({ label: "r1" });
  await page.locator("#ping-target").fill("10.2.2.2");
  await page.locator("#diagnostic-count").fill("5");
  await page.locator("#ping-form").getByRole("button", { name: "Run diagnostic" }).click();
  let capture;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const response = await context.request.get(`${baseUrl}/api/v1/deployments/${deploymentId}/captures/`, { failOnStatusCode: true });
    capture = (await response.json())[0];
    if (capture?.status === "complete") break;
    await page.waitForTimeout(1500);
  }
  if (!capture || capture.status !== "complete") throw new Error("Live packet capture did not complete.");
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator(`[data-analyze-capture="${capture.id}"]`).click();
  const dialog = page.locator("#packet-analysis-dialog");
  await dialog.locator("#packet-rows tr").first().waitFor({ timeout: 15000 });
  const analysisText = (await dialog.textContent()) || "";
  if (!analysisText.includes("ICMP") || !analysisText.includes("10.2.2.2")) throw new Error("Native analysis did not expose the generated ICMP conversation.");
  const analysisResponse = await context.request.get(`${baseUrl}/api/v1/deployments/${deploymentId}/captures/${capture.id}/analysis/`, { failOnStatusCode: true });
  const analysis = await analysisResponse.json();
  if (!analysis.protocols.some(item => item.protocol === "ICMP") || analysis.packets < 1) throw new Error("Analysis API did not decode live ICMP packets.");
  await dialog.screenshot({ path: output });
  if (browserErrors.length) throw new Error(`Firefox reported errors: ${browserErrors.join(" | ")}`);
  console.log(JSON.stringify({ browser: "firefox", capture: capture.id, packets: analysis.packets, protocols: analysis.protocols, screenshot: output }));
} finally {
  await browser.close();
}
