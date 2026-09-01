import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { firefox } = require("playwright");

const baseUrl = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const username = process.env.TRAINING_USERNAME || "";
const password = process.env.TRAINING_PASSWORD || "";
const deploymentId = process.env.STAGED_DEPLOYMENT_ID || "";
const output = path.resolve(process.env.STAGED_SCREENSHOT || "training/52-staged-device-start.png");
if (!baseUrl || !username || !password || !deploymentId) {
  throw new Error("Set TRAINING_BASE_URL, TRAINING_USERNAME, TRAINING_PASSWORD, and STAGED_DEPLOYMENT_ID.");
}

const browser = await firefox.launch({ headless: true });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1000 }, colorScheme: "dark" });
const page = await context.newPage();
const browserErrors = [];
page.on("console", (message) => { if (message.type() === "error") browserErrors.push(`console: ${message.text()}`); });
page.on("pageerror", (error) => browserErrors.push(`page: ${error.message}`));
page.on("response", (response) => { if (response.status() >= 400) browserErrors.push(`http ${response.status()}: ${response.url()}`); });
const runtimeUrl = `${baseUrl}/api/v1/deployments/${deploymentId}/runtime/`;
const runtime = async () => (await context.request.get(runtimeUrl, { failOnStatusCode: true })).json();
const selectDevices = async (devices) => {
  for (const device of devices) await page.locator(`[data-device-select][value="${device.id}"]`).check();
};

try {
  await page.goto(`${baseUrl}/accounts/login/`, { waitUntil: "domcontentloaded" });
  await page.locator("#id_username").fill(username);
  await page.locator("#id_password").fill(password);
  await Promise.all([
    page.waitForURL((url) => !url.pathname.includes("/accounts/login/")),
    page.getByRole("button", { name: /sign in|log in/i }).click(),
  ]);
  await page.goto(`${baseUrl}/deployments/${deploymentId}/`, { waitUntil: "domcontentloaded" });
  await page.locator("#device-list [data-device-select]").first().waitFor({ state: "visible", timeout: 20_000 });
  let before = await runtime();
  let devices = ["r1", "r2"].map((name) => before.devices.find((device) => device.name === name));
  if (devices.every((device) => device?.runtime_resources.manual_desired_state === "stopped")) {
    await selectDevices(devices);
    await page.locator('[data-bulk-device-operation="start_device"]').click();
    await page.locator("#bulk-device-dialog").waitFor({ state: "visible" });
    await page.locator("#confirm-bulk-device").click();
    for (let attempt = 0; attempt < 120; attempt += 1) {
      await page.waitForTimeout(1000);before = await runtime();
      devices = ["r1", "r2"].map((name) => before.devices.find((device) => device.name === name));
      if (devices.every((device) => device?.observed_readiness === "ready" && device.runtime_resources.pod_uid)) break;
      if (attempt === 119) throw new Error("The stopped acceptance routers could not be restored before retry.");
    }
    await page.locator("#refresh-runtime").click();await page.waitForTimeout(800);
  }
  if (devices.some((device) => !device || device.observed_readiness !== "ready" || !device.runtime_resources.pod_uid)) {
    throw new Error("The BGP acceptance routers must both be ready before staged-start verification.");
  }
  const originalUids = Object.fromEntries(devices.map((device) => [device.name, device.runtime_resources.pod_uid]));
  const existingOperations = new Set(before.operations.map((operation) => operation.id));

  await selectDevices(devices);
  await page.locator('[data-bulk-device-operation="stop_device"]').click();
  await page.locator("#bulk-device-dialog").waitFor({ state: "visible" });
  await page.locator("#confirm-bulk-device").click();
  for (let attempt = 0; attempt < 90; attempt += 1) {
    await page.waitForTimeout(1000);
    const observed = await runtime();
    const stopped = devices.every((device) => {
      const current = observed.devices.find((item) => item.id === device.id);
      return current?.observed_readiness === "stopped" && current.runtime_resources.manual_desired_state === "stopped";
    });
    const failures = observed.operations.filter((operation) => !existingOperations.has(operation.id) && operation.state === "failed");
    if (failures.length) throw new Error(failures[0].error_details?.message || "Selected-device stop failed.");
    if (stopped) break;
    if (attempt === 89) throw new Error("The selected routers did not stop in time.");
  }

  await page.locator("#refresh-runtime").click();
  await page.waitForTimeout(800);
  const r1 = (await runtime()).devices.find((device) => device.name === "r1");
  const r2 = (await runtime()).devices.find((device) => device.name === "r2");
  await selectDevices([r1, r2]);
  await page.locator("#staged-start-selected").click();
  const stagedDialog = page.locator("#staged-start-dialog");
  await stagedDialog.waitFor({ state: "visible" });
  await page.locator("#staged-start-interval").fill("8");
  await page.locator(`[data-stage-move="down"][data-device-id="${r1.id}"]`).click();
  const displayedOrder = await page.locator("#staged-start-list strong").allTextContents();
  if (displayedOrder.join(",") !== "r2,r1") throw new Error(`Unexpected staged order: ${displayedOrder.join(",")}`);
  await stagedDialog.screenshot({ path: output });
  await page.locator("#confirm-staged-start").click();

  let stagedJob = null;
  let after = null;
  for (let attempt = 0; attempt < 150; attempt += 1) {
    await page.waitForTimeout(1000);
    after = await runtime();
    stagedJob = after.operations.find((operation) => !existingOperations.has(operation.id) && operation.operation_type === "staged_start_devices");
    if (stagedJob?.state === "failed") throw new Error(stagedJob.error_details?.message || "Staged start failed.");
    const ready = ["r1", "r2"].every((name) => {
      const current = after.devices.find((device) => device.name === name);
      return current?.observed_readiness === "ready" && current.runtime_resources.pod_uid && current.runtime_resources.pod_uid !== originalUids[name];
    });
    if (stagedJob?.state === "succeeded" && ready) break;
    if (attempt === 149) throw new Error("The staged sequence did not return both routers ready in time.");
  }
  const resultOrder = stagedJob.result_payload.devices;
  if (resultOrder.map((row) => row.device).join(",") !== "r2,r1") throw new Error("The worker did not preserve the displayed order.");
  const separation = (new Date(resultOrder[1].started_at) - new Date(resultOrder[0].started_at)) / 1000;
  if (separation < 7.5) throw new Error(`Launcher starts were separated by only ${separation} seconds.`);

  await page.waitForTimeout(12_000);
  await page.locator("#ping-node").selectOption(after.devices.find((device) => device.name === "r1").node_id);
  await page.locator("#ping-target").fill("10.2.2.2");
  await page.locator("#ping-form button").click();
  let diagnostic = null;
  for (let attempt = 0; attempt < 30; attempt += 1) {
    await page.waitForTimeout(1000);
    const observed = await runtime();
    diagnostic = observed.operations.find((operation) => !existingOperations.has(operation.id) && operation.operation_type === "ping");
    if (diagnostic?.state === "failed") throw new Error(diagnostic.error_details?.message || "Post-stage ping failed.");
    if (diagnostic?.state === "succeeded") break;
  }
  if (diagnostic?.state !== "succeeded" || !/0% packet loss/.test(diagnostic.result_payload?.output || "")) {
    throw new Error("Routed reachability did not recover after staged startup.");
  }
  if (browserErrors.length) throw new Error(`Firefox reported errors: ${browserErrors.join(" | ")}`);
  console.log(JSON.stringify({ browser: "firefox", order: resultOrder.map((row) => row.device), intervalSeconds: 8,
    measuredSeparationSeconds: separation, operation: stagedJob.id, finalReadiness: after.devices.filter((device) => ["r1", "r2"].includes(device.name)).map((device) => device.observed_readiness),
    reachability: diagnostic.result_payload.output.trim(), screenshot: output }));
} finally {
  await browser.close();
}
