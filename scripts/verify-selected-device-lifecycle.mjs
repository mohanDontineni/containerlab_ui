import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { firefox } = require("playwright");
const baseUrl = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const username = process.env.TRAINING_USERNAME || "";
const password = process.env.TRAINING_PASSWORD || "";
const deploymentId = process.env.BULK_DEPLOYMENT_ID || "";
const diagnosticTarget = process.env.BULK_DIAGNOSTIC_TARGET || "";
const output = path.resolve(process.env.BULK_SCREENSHOT || "training/41-selected-device-lifecycle.png");
if (!baseUrl || !username || !password || !deploymentId) throw new Error("Set login and BULK_DEPLOYMENT_ID environment values.");

const browser = await firefox.launch({ headless: true });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1000 }, colorScheme: "dark" });
const page = await context.newPage();
const runtimeUrl = `${baseUrl}/api/v1/deployments/${deploymentId}/runtime/`;
const runtime = async () => (await context.request.get(runtimeUrl, { failOnStatusCode: true })).json();
const waitForReadiness = async (ids, readiness, previousOperationIds) => {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    await page.waitForTimeout(1000);
    const data = await runtime();
    const selected = data.devices.filter((device) => ids.includes(device.id));
    const jobs = data.operations.filter((job) => !previousOperationIds.has(job.id) && ids.includes(job.target_id));
    if (jobs.some((job) => job.state === "failed")) throw new Error(jobs.find((job) => job.state === "failed")?.error_details?.message || "Selected device job failed.");
    if (selected.length === ids.length && selected.every((device) => device.observed_readiness === readiness) &&
        jobs.filter((job) => job.state === "succeeded").length >= ids.length) return { data, jobs };
  }
  throw new Error(`Selected devices did not reach ${readiness}.`);
};
const selectDevices = async (ids) => {
  for (const id of ids) await page.locator(`input[data-device-select][value="${id}"]`).check();
  await page.getByText(`${ids.length} selected`, { exact: true }).waitFor();
};
const previewAndConfirm = async (operation, screenshot = false) => {
  await page.locator(`button[data-bulk-device-operation="${operation}"]`).click();
  const dialog = page.locator("#bulk-device-dialog");
  await dialog.waitFor({ state: "visible" });
  await page.getByText("Every selected device passed the current-state preflight.", { exact: true }).waitFor();
  if (screenshot) await dialog.screenshot({ path: output });
  await page.locator("#confirm-bulk-device").click();
  await dialog.waitFor({ state: "hidden" });
};

try {
  await page.goto(`${baseUrl}/accounts/login/`, { waitUntil: "domcontentloaded" });
  await page.locator("#id_username").fill(username);await page.locator("#id_password").fill(password);
  await Promise.all([page.waitForURL((url) => !url.pathname.includes("/accounts/login/")),page.getByRole("button", { name: /sign in|log in/i }).click()]);
  await page.goto(`${baseUrl}/deployments/${deploymentId}/`, { waitUntil: "domcontentloaded" });
  const before = await runtime();
  const targets = before.devices.filter((device) => device.observed_readiness === "ready" && device.runtime_resources.pod_uid).slice(0, 2);
  if (targets.length !== 2) throw new Error("Two ready devices are required for selected lifecycle acceptance.");
  const ids = targets.map((device) => device.id), podUids = Object.fromEntries(targets.map((device) => [device.id, device.runtime_resources.pod_uid]));
  let previous = new Set(before.operations.map((job) => job.id));
  await page.locator(`input[data-device-select][value="${ids[0]}"]`).waitFor({ timeout: 20_000 });
  await selectDevices(ids);await previewAndConfirm("suspend_device", true);
  const suspended = await waitForReadiness(ids, "suspended", previous);
  if (!suspended.data.devices.filter((device) => ids.includes(device.id)).every((device) => device.runtime_resources.pod_uid === podUids[device.id])) {
    throw new Error("Bulk suspend replaced a launcher instead of preserving compute.");
  }
  previous = new Set(suspended.data.operations.map((job) => job.id));
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator(`input[data-device-select][value="${ids[0]}"]`).waitFor({ timeout: 20_000 });
  await selectDevices(ids);await previewAndConfirm("resume_device");
  const resumed = await waitForReadiness(ids, "ready", previous);
  if (!resumed.data.devices.filter((device) => ids.includes(device.id)).every((device) => device.runtime_resources.pod_uid === podUids[device.id])) {
    throw new Error("Bulk resume changed a preserved launcher identity.");
  }

  let diagnostic = null;
  if (diagnosticTarget) {
    const source = resumed.data.devices.find((device) => device.id === ids[0]);
    await page.waitForTimeout(2500);
    for (let convergenceAttempt = 0; convergenceAttempt < 4; convergenceAttempt += 1) {
      const job = await page.evaluate(async ({ deploymentId, nodeId, diagnosticTarget }) => {
        const csrf = document.cookie.split("; ").find((item) => item.startsWith("csrftoken="))?.split("=")[1] || "";
        const response = await fetch(`/api/v1/deployments/${deploymentId}/diagnostics/`, { method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrf, "Idempotency-Key": crypto.randomUUID() },
          body: JSON.stringify({ operation: "ping", node_id: nodeId, target: diagnosticTarget, count: 3, timeout: 2 }) });
        const data = await response.json();if (!response.ok) throw new Error(data.error?.details || data.error?.code);return data;
      }, { deploymentId, nodeId: source.node_id, diagnosticTarget });
      for (let attempt = 0; attempt < 30; attempt += 1) {
        await page.waitForTimeout(1000);const observed = await runtime();diagnostic = observed.operations.find((operation) => operation.id === job.id);
        if (diagnostic?.state === "failed") throw new Error(diagnostic.error_details?.message || "Post-resume diagnostic failed.");
        if (diagnostic?.state === "succeeded") break;
      }
      if (/3 packets received, 0% packet loss/.test(diagnostic?.result_payload?.output || "")) break;
      if (convergenceAttempt < 3) await page.waitForTimeout(5000);
    }
    if (diagnostic?.state !== "succeeded" || !/3 packets received, 0% packet loss/.test(diagnostic.result_payload?.output || "")) throw new Error("Post-resume reachability failed after bounded convergence retries.");
  }
  console.log(JSON.stringify({ browser: "firefox", devices: targets.map((device) => device.name), launcherUidsPreserved: true,
    suspendJobs: suspended.jobs.map((job) => job.id), resumeJobs: resumed.jobs.map((job) => job.id),
    postResumeDiagnostic: diagnostic?.result_payload?.output?.trim() || null, screenshot: output }));
} finally { await browser.close(); }
