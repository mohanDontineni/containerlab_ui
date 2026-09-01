import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const { firefox } = createRequire(import.meta.url)("playwright");
const base = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const deployment = process.env.METRICS_DEPLOYMENT_ID || "";
const output = path.resolve("training/80-topology-link-traffic-rates.png");
if (!base || !deployment) throw new Error("Set TRAINING_BASE_URL and METRICS_DEPLOYMENT_ID");
await mkdir(path.dirname(output), { recursive: true });
const browser = await firefox.launch({ headless: true });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1150 }, colorScheme: "dark" });
const page = await context.newPage();
try {
  await page.goto(`${base}/accounts/login/`);
  await page.locator("#id_username").fill(process.env.TRAINING_USERNAME || "");
  await page.locator("#id_password").fill(process.env.TRAINING_PASSWORD || "");
  await Promise.all([page.waitForURL((url) => !url.pathname.includes("/accounts/login/")), page.getByRole("button", { name: /sign in|log in/i }).click()]);
  await page.goto(`${base}/deployments/${deployment}/`);
  const panel = page.locator(".runtime-topology-panel");
  await page.getByRole("button", { name: /inspect link traffic/i }).click();
  await panel.getByText("Refresh snapshot to calculate rate", { exact: false }).first().waitFor({ timeout: 25000 });

  const runtime = await (await context.request.get(`${base}/api/v1/deployments/${deployment}/runtime/`, { failOnStatusCode: true })).json();
  const r1 = runtime.devices.find((device) => device.name === "r1");
  const diagnostic = await page.evaluate(async ({ deployment, nodeId }) => {
    const csrf = document.cookie.split("; ").find((item) => item.startsWith("csrftoken="))?.split("=")[1] || "";
    const response = await fetch(`/api/v1/deployments/${deployment}/diagnostics/`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf, "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ operation: "ping", node_id: nodeId, target: "10.2.2.2", count: 5, timeout: 1 }),
    });
    return { status: response.status, data: await response.json() };
  }, { deployment, nodeId: r1.node_id });
  if (diagnostic.status !== 202) throw new Error(JSON.stringify(diagnostic));
  for (let attempt = 0; attempt < 30; attempt += 1) {
    await page.waitForTimeout(400);
    const data = await (await context.request.get(`${base}/api/v1/deployments/${deployment}/runtime/`)).json();
    const job = data.operations.find((operation) => operation.id === diagnostic.data.id);
    if (job?.state === "succeeded") break;
    if (job?.state === "failed") throw new Error(JSON.stringify(job.error_details));
  }
  await page.getByRole("button", { name: /refresh link traffic/i }).click();
  await panel.getByText(/Rate RX (?!0(?:\.0)? pkt\/s)/).first().waitFor({ timeout: 25000 });
  const text = await page.locator("#topology-traffic-summary").innerText();
  const rates = [...text.matchAll(/Rate RX ([\d.]+) pkt\/s \/ ([\d.]+) B\/s · TX ([\d.]+) pkt\/s \/ ([\d.]+) B\/s/g)].map((match) => match.slice(1).map(Number));
  if (rates.length !== 2 || !rates.some((values) => values.some((value) => value > 0))) throw new Error(`Expected two endpoint rate rows with live traffic: ${JSON.stringify(rates)}`);
  if (text.includes("counter reset detected")) throw new Error("Unexpected counter reset during acceptance");
  await panel.screenshot({ path: output });
  console.log(JSON.stringify({ browser: "Firefox", deployment, generatedPingPackets: 5, rates, screenshot: output }));
} finally {
  await browser.close();
}
