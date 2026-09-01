import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { firefox } = require("playwright");
const baseUrl = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const username = process.env.TRAINING_USERNAME || "";
const password = process.env.TRAINING_PASSWORD || "";
const deploymentId = process.env.EVENTS_DEPLOYMENT_ID || "";
const skipRestart = process.env.EVENTS_SKIP_RESTART === "true";
const output = path.resolve(process.env.EVENTS_SCREENSHOT || "training/58-kubernetes-device-events.png");
if (!baseUrl || !username || !password || !deploymentId) throw new Error("Set the training URL, credentials, and EVENTS_DEPLOYMENT_ID.");

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
  await page.goto(`${baseUrl}/deployments/${deploymentId}/`, { waitUntil: "domcontentloaded" });
  const runtimeUrl = `${baseUrl}/api/v1/deployments/${deploymentId}/runtime/`;
  const initial = await (await context.request.get(runtimeUrl, { failOnStatusCode: true })).json();
  const r1 = initial.devices.find(device => device.name === "r1");
  if (!r1?.runtime_resources?.pod_uid || r1.observed_readiness !== "ready") throw new Error("r1 is not ready for event acceptance.");
  const oldUid = r1.runtime_resources.pod_uid;
  let replacement = r1;
  if (!skipRestart) {
    const row = page.locator("#device-list article").filter({ hasText: "r1" }).first();
    await row.getByTitle("Restart device").click();
    for (let attempt = 0; attempt < 70; attempt += 1) {
      await page.waitForTimeout(1500);
      const runtime = await (await context.request.get(runtimeUrl, { failOnStatusCode: true })).json();
      replacement = runtime.devices.find(device => device.name === "r1");
      if (replacement?.observed_readiness === "ready" && replacement.runtime_resources?.pod_uid && replacement.runtime_resources.pod_uid !== oldUid) break;
    }
    if (!replacement || replacement.observed_readiness !== "ready" || replacement.runtime_resources.pod_uid === oldUid) throw new Error("Replacement launcher did not become ready.");
  }
  await page.reload({ waitUntil: "domcontentloaded" });
  const refreshedRow = page.locator("#device-list article").filter({ hasText: "r1" }).first();
  await refreshedRow.getByTitle("Inspect runtime logs").click();
  await page.locator("#device-log-meta").filter({ hasText: /appliance/i }).waitFor({ timeout: 15000 });
  await page.locator("#device-log-source").selectOption("events");
  const eventCards = page.locator("#device-event-output article");
  await eventCards.first().waitFor({ timeout: 15000 });
  const text = (await page.locator("#device-event-output").textContent()) || "";
  if (!/Scheduled|Pulled|Created|Started/.test(text)) throw new Error("Launcher lifecycle events were not rendered.");
  const jobs = await (await context.request.get(runtimeUrl, { failOnStatusCode: true })).json();
  const eventJob = jobs.operations.find(job => job.operation_type === "get_device_logs" && job.result_payload?.source === "events");
  if (!eventJob || eventJob.state !== "succeeded" || !eventJob.result_payload.events.length) throw new Error("Bounded event job did not succeed.");
  await page.locator("#device-log-dialog").screenshot({ path: output });
  await page.locator("#device-log-dialog").evaluate(dialog => dialog.close());
  const existingOperationIds = new Set(jobs.operations.map(job => job.id));
  await page.locator("#ping-node").selectOption({ label: "r1" });
  await page.locator("#ping-target").fill("10.2.2.2");
  await page.locator("#diagnostic-count").fill("3");
  await page.locator("#ping-form").getByRole("button", { name: "Run diagnostic" }).click();
  let pingJob;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    await page.waitForTimeout(750);
    const runtime = await (await context.request.get(runtimeUrl, { failOnStatusCode: true })).json();
    pingJob = runtime.operations.find(job => job.operation_type === "ping" && !existingOperationIds.has(job.id));
    if (pingJob?.state === "succeeded" || pingJob?.state === "failed") break;
  }
  if (pingJob?.state !== "succeeded" || !pingJob.result_payload?.output?.includes("0% packet loss")) throw new Error("Post-restart routed reachability did not recover.");
  if (errors.length) throw new Error(`Firefox reported errors: ${errors.join(" | ")}`);
  console.log(JSON.stringify({ browser: "firefox", oldUid, newUid: replacement.runtime_resources.pod_uid,
    events: eventJob.result_payload.events.map(event => ({ type: event.type, reason: event.reason, count: event.count })), ping: "3/3, 0% loss", screenshot: output }));
} finally {
  await browser.close();
}
