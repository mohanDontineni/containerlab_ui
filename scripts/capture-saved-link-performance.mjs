import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { firefox } = require("playwright");
const baseUrl = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const username = process.env.TRAINING_USERNAME || "";
const password = process.env.TRAINING_PASSWORD || "";
const labId = process.env.TRAINING_LAB_ID || "";
const stoppedDeploymentId = process.env.TRAINING_STOP_DEPLOYMENT_ID || "";
const existingDeploymentId = process.env.TRAINING_EXISTING_DEPLOYMENT_ID || "";
const output = path.resolve(process.env.TRAINING_OUTPUT || "training");
if (!baseUrl || !username || !password || !labId || !stoppedDeploymentId) {
  throw new Error("Set the training URL, credentials, lab ID, and deployment to stop.");
}

await mkdir(output, { recursive: true });
const browser = await firefox.launch({ headless: true });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1100 }, colorScheme: "dark" });
const page = await context.newPage();
const api = async (pathname) => {
  const response = await context.request.get(`${baseUrl}${pathname}`, { failOnStatusCode: true });
  return response.json();
};
const waitRuntime = async (deploymentId, predicate, attempts = 180) => {
  let runtime;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    runtime = await api(`/api/v1/deployments/${deploymentId}/runtime/`);
    if (predicate(runtime)) return runtime;
    await page.waitForTimeout(1000);
  }
  throw new Error(`Runtime ${deploymentId} did not converge; last state was ${runtime?.deployment?.observed_state}.`);
};

try {
  await page.goto(`${baseUrl}/accounts/login/`, { waitUntil: "networkidle" });
  await page.locator("#id_username").fill(username);
  await page.locator("#id_password").fill(password);
  await Promise.all([
    page.waitForURL((url) => !url.pathname.includes("/accounts/login/")),
    page.getByRole("button", { name: /sign in|log in/i }).click(),
  ]);

  let deploymentId = existingDeploymentId;
  if (!deploymentId) {
  await page.goto(`${baseUrl}/deployments/${stoppedDeploymentId}/`, { waitUntil: "domcontentloaded" });
  const oldRuntime = await api(`/api/v1/deployments/${stoppedDeploymentId}/runtime/`);
  if (oldRuntime.deployment.observed_state !== "stopped") {
    page.once("dialog", (dialog) => dialog.accept());
    await page.locator("#stop-runtime").click();
    await waitRuntime(stoppedDeploymentId, (runtime) => runtime.deployment.observed_state === "stopped", 120);
  }

  await page.goto(`${baseUrl}/labs/${labId}/workspace/`, { waitUntil: "networkidle" });
  const editor = page.frameLocator('iframe[title^="Topology workspace"]');
  await editor.locator(".workspace-shell").waitFor();
  if (!(await editor.locator(".react-flow__edge").count())) {
    await editor.getByRole("button", { name: "History" }).click();
    const history = editor.getByRole("dialog", { name: "Topology revision history" });
    await history.waitFor();
    page.once("dialog", (dialog) => dialog.accept());
    await Promise.all([
      page.waitForEvent("framenavigated", { predicate: (frame) => frame !== page.mainFrame() && frame.url().includes("/editor/") }),
      history.getByRole("button", { name: "Restore" }).first().click(),
    ]);
    await editor.locator(".workspace-shell").waitFor();
  }
  await editor.locator(".react-flow__edge").first().click({ force: true });
  await editor.getByLabel("Link state").selectOption("enabled");
  await editor.getByLabel("Latency ms").fill("120");
  await editor.getByLabel("Jitter ms").fill("10");
  await editor.getByLabel("Loss %").fill("0");
  await editor.getByLabel("Corrupt %").fill("0");
  await editor.getByLabel("Rate Kbit/s").fill("10000");
  await editor.getByRole("button", { name: /save draft/i }).click();
  await editor.getByRole("button", { name: /saved/i }).waitFor({ timeout: 30_000 });
  await page.screenshot({ path: path.join(output, "93-saved-link-performance-profile.png") });

  await editor.getByRole("button", { name: /Deploy$/ }).click();
  const plan = editor.getByRole("dialog", { name: "Review deployment plan" });
  await plan.waitFor();
  const planText = await plan.innerText();
  if (!/capacity after/i.test(planText) || !/no yaml is required/i.test(planText)) throw new Error("The guarded no-YAML deployment plan was incomplete.");
  const acknowledgement = plan.getByRole("checkbox");
  if (await acknowledgement.count()) await acknowledgement.check();
  const runtimeFrame = page.waitForEvent("framenavigated", { predicate: (frame) => frame !== page.mainFrame() && /\/deployments\/[0-9a-f-]+\/$/.test(frame.url()) });
  await plan.getByRole("button", { name: "Publish and create new runtime" }).click();
  deploymentId = (await runtimeFrame).url().match(/\/deployments\/([0-9a-f-]+)\/$/)?.[1] || "";
  if (!deploymentId) throw new Error("The GUI did not open the newly created runtime.");
  }

  let runtime = await waitRuntime(deploymentId, (value) => value.deployment.observed_state === "running" && value.devices.length === 2 && value.devices.every((device) => device.observed_readiness === "ready"));
  await page.goto(`${baseUrl}/deployments/${deploymentId}/`, { waitUntil: "domcontentloaded" });
  await page.locator("#link-control-list article").first().waitFor({ state: "visible", timeout: 30_000 });
  await page.waitForFunction(() => {
    const article = document.querySelector("#link-control-list article");
    return article?.querySelector('[data-field="latency_ms"]')?.value === "120" && article?.querySelector('[data-field="jitter_ms"]')?.value === "10" && article?.querySelector('[data-field="rate_kbps"]')?.value === "10000";
  }, null, { timeout: 45_000 });

  const r1 = runtime.devices.find((device) => device.name === "r1");
  await page.locator("#ping-node").selectOption(r1.node_id);
  await page.locator("#ping-target").fill("10.0.12.2");
  await page.locator("#diagnostic-count").fill("3");
  await page.locator("#ping-form button").click();
  let ping;
  for (let attempt = 0; attempt < 45; attempt += 1) {
    await page.waitForTimeout(1000);
    runtime = await api(`/api/v1/deployments/${deploymentId}/runtime/`);
    ping = runtime.operations.find((operation) => operation.operation_type === "ping" && operation.state === "succeeded");
    if (ping) break;
  }
  if (!ping) throw new Error("The GUI ping did not complete.");
  const pingOutput = ping.result_payload.output || "";
  const average = Number(pingOutput.match(/= [\d.]+\/([\d.]+)\//)?.[1]);
  if (!/0% packet loss/.test(pingOutput) || !(average > 200)) throw new Error(`Saved latency was not observable in the data plane: ${pingOutput}`);
  await page.locator("#refresh-runtime").click();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(output, "94-applied-saved-link-performance.png"), fullPage: true });

  const condition = runtime.links[0]?.condition || {};
  if (condition.latency_ms !== 120 || condition.jitter_ms !== 10 || condition.rate_kbps !== 10000 || !condition.active) {
    throw new Error(`Runtime API did not preserve the saved condition: ${JSON.stringify(condition)}`);
  }
  console.log(JSON.stringify({ browser: "firefox", deploymentId, namespace: runtime.deployment.namespace, revision: runtime.deployment.revision_number, condition, pingAverageMs: average, screenshots: ["93-saved-link-performance-profile.png", "94-applied-saved-link-performance.png"] }));
} finally {
  await context.close();
  await browser.close();
}
