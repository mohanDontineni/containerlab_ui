import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { firefox } = require("playwright");

const baseUrl = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const username = process.env.TRAINING_USERNAME || "";
const password = process.env.TRAINING_PASSWORD || "";
const deploymentId = process.env.RESET_DEPLOYMENT_ID || "";
const deviceName = process.env.RESET_DEVICE_NAME || "r1";
const diagnosticTarget = process.env.RESET_DIAGNOSTIC_TARGET || "";
const output = path.resolve(process.env.RESET_SCREENSHOT || "training/40-guarded-device-reset.png");
if (!baseUrl || !username || !password || !deploymentId) {
  throw new Error("Set TRAINING_BASE_URL, TRAINING_USERNAME, TRAINING_PASSWORD, and RESET_DEPLOYMENT_ID.");
}

const browser = await firefox.launch({ headless: true });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1000 }, colorScheme: "dark" });
const page = await context.newPage();
const runtimeUrl = `${baseUrl}/api/v1/deployments/${deploymentId}/runtime/`;
const runtime = async () => {
  const response = await context.request.get(runtimeUrl, { failOnStatusCode: true });
  return response.json();
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
  const before = await runtime();
  const target = before.devices.find((device) => device.name === deviceName);
  if (!target || target.observed_readiness !== "ready" || !target.runtime_resources.pod_uid) {
    throw new Error(`${deviceName} must be ready with a launcher pod before reset acceptance.`);
  }
  const peerPods = Object.fromEntries(before.devices.filter((device) => device.id !== target.id)
    .map((device) => [device.id, device.runtime_resources.pod_uid]));
  const existingOperations = new Set(before.operations.map((operation) => operation.id));
  const resetButton = page.locator(`button[data-device-reset="${target.id}"]`);
  await resetButton.waitFor({ state: "visible", timeout: 20_000 });
  await resetButton.click();
  const dialog = page.locator("#device-reset-dialog");
  await dialog.waitFor({ state: "visible" });
  await page.getByText(`Reset ${deviceName} to saved revision`, { exact: true }).waitFor();
  if (await page.locator("#confirm-device-reset").isDisabled()) throw new Error("Live reset preview unexpectedly blocked the ready device.");
  await dialog.screenshot({ path: output });
  await page.locator("#confirm-device-reset").click();

  let accepted;
  for (let attempt = 0; attempt < 60; attempt += 1) {
    await page.waitForTimeout(1500);
    const observed = await runtime();
    accepted = observed.operations.find((operation) => operation.operation_type === "reset_device" && !existingOperations.has(operation.id));
    const current = observed.devices.find((device) => device.id === target.id);
    const peersUnchanged = observed.devices.filter((device) => device.id !== target.id)
      .every((device) => device.runtime_resources.pod_uid === peerPods[device.id]);
    if (!peersUnchanged) throw new Error("A peer launcher changed during the single-device reset.");
    if (accepted?.state === "failed") throw new Error(accepted.error_details?.message || "The reset job failed.");
    if (accepted?.state === "succeeded" && current?.observed_readiness === "ready" &&
        current.runtime_resources.pod_uid && current.runtime_resources.pod_uid !== target.runtime_resources.pod_uid) break;
    if (attempt === 59) throw new Error("The reset device did not return ready with a new launcher identity.");
  }

  let diagnostic = null;
  if (diagnosticTarget) {
    const refreshed = await runtime();
    const current = refreshed.devices.find((device) => device.id === target.id);
    await page.waitForTimeout(8000);
    const scheduledJob = await page.evaluate(async ({ deploymentId, nodeId, diagnosticTarget }) => {
      const csrf = document.cookie.split("; ").find((item) => item.startsWith("csrftoken="))?.split("=")[1] || "";
      const response = await fetch(`/api/v1/deployments/${deploymentId}/diagnostics/`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf, "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({ operation: "ping", node_id: nodeId, target: diagnosticTarget, count: 3, timeout: 2 }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error?.details || data.error?.code || "Diagnostic scheduling failed");
      return data;
    }, { deploymentId, nodeId: current.node_id, diagnosticTarget });
    for (let attempt = 0; attempt < 30; attempt += 1) {
      await page.waitForTimeout(1000);
      const observed = await runtime();
      diagnostic = observed.operations.find((operation) => operation.id === scheduledJob.id);
      if (diagnostic?.state === "failed") throw new Error(diagnostic.error_details?.message || "Post-reset ping failed.");
      if (diagnostic?.state === "succeeded") break;
    }
    if (diagnostic?.state !== "succeeded" || !/0% packet loss/.test(diagnostic.result_payload?.output || "")) {
      throw new Error("Post-reset network reachability did not recover.");
    }
  }
  const after = await runtime();
  const current = after.devices.find((device) => device.id === target.id);
  console.log(JSON.stringify({ browser: "firefox", device: deviceName, oldPodUid: target.runtime_resources.pod_uid,
    newPodUid: current.runtime_resources.pod_uid, peersPreserved: true, operation: accepted.result_payload,
    postResetDiagnostic: diagnostic?.result_payload?.output?.trim() || null, screenshot: output }));
} finally {
  await browser.close();
}
