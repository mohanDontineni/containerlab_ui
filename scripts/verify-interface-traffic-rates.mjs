import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const { firefox } = createRequire(import.meta.url)("playwright");
const base = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const id = process.env.METRICS_DEPLOYMENT_ID || "";
const output = path.resolve("training/77-live-interface-traffic-rates.png");
if (!base || !id) throw new Error("Set URL and deployment");
await mkdir(path.dirname(output), { recursive: true });
const browser = await firefox.launch({ headless: true });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1100 }, colorScheme: "dark" });
const page = await context.newPage();

try {
  await page.goto(`${base}/accounts/login/`);
  await page.locator("#id_username").fill(process.env.TRAINING_USERNAME || "");
  await page.locator("#id_password").fill(process.env.TRAINING_PASSWORD || "");
  await Promise.all([page.waitForURL((url) => !url.pathname.includes("/accounts/login/")), page.getByRole("button", { name: /sign in|log in/i }).click()]);
  await page.goto(`${base}/deployments/${id}/`);
  const stateButton = page.locator('[data-device-state][data-device-name="r1"]');
  await stateButton.waitFor();
  await stateButton.click();
  const dialog = page.locator("#device-state-dialog");
  await dialog.getByText("Refresh to calculate live rate", { exact: false }).first().waitFor({ timeout: 15000 });
  const runtime = await (await context.request.get(`${base}/api/v1/deployments/${id}/runtime/`, { failOnStatusCode: true })).json();
  const r1 = runtime.devices.find((device) => device.name === "r1");
  const diagnostic = await page.evaluate(async ({ id, nodeId }) => {
    const csrf = document.cookie.split("; ").find((item) => item.startsWith("csrftoken="))?.split("=")[1] || "";
    const response = await fetch(`/api/v1/deployments/${id}/diagnostics/`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf, "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ operation: "ping", node_id: nodeId, target: "10.2.2.2", count: 5, timeout: 1 }),
    });
    return { status: response.status, data: await response.json() };
  }, { id, nodeId: r1.node_id });
  if (diagnostic.status !== 202) throw new Error(JSON.stringify(diagnostic));
  for (let attempt = 0; attempt < 30; attempt += 1) {
    await page.waitForTimeout(400);
    const data = await (await context.request.get(`${base}/api/v1/deployments/${id}/runtime/`)).json();
    const job = data.operations.find((operation) => operation.id === diagnostic.data.id);
    if (job?.state === "succeeded") break;
    if (job?.state === "failed") throw new Error(JSON.stringify(job.error_details));
  }
  await dialog.locator("#refresh-device-state").click();
  await dialog.getByText(/Rate RX (?!0(?:\.0)? pkt\/s)/).first().waitFor({ timeout: 15000 });
  const text = await dialog.innerText();
  const rates = [...text.matchAll(/Rate RX ([\d.]+) pkt\/s \/ ([\d.]+) B\/s · TX ([\d.]+) pkt\/s \/ ([\d.]+) B\/s/g)].map((match) => match.slice(1).map(Number));
  if (!rates.some((values) => values.some((value) => value > 0))) throw new Error(`Nonzero rates missing: ${text}`);
  await dialog.screenshot({ path: output });
  console.log(JSON.stringify({ browser: "Firefox", device: "r1", generatedPingPackets: 5, rates, screenshot: output }));
} finally {
  await browser.close();
}
