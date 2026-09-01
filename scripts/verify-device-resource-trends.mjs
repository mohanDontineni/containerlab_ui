import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const { firefox } = createRequire(import.meta.url)("playwright");
const base = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const deployment = process.env.METRICS_DEPLOYMENT_ID || "";
const output = path.resolve("training/78-live-device-resource-trends.png");
if (!base || !deployment) throw new Error("Set TRAINING_BASE_URL and METRICS_DEPLOYMENT_ID");
await mkdir(path.dirname(output), { recursive: true });

const browser = await firefox.launch({ headless: true });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1000 }, colorScheme: "dark" });
const page = await context.newPage();
try {
  await page.goto(`${base}/accounts/login/`);
  await page.locator("#id_username").fill(process.env.TRAINING_USERNAME || "");
  await page.locator("#id_password").fill(process.env.TRAINING_PASSWORD || "");
  await Promise.all([
    page.waitForURL((url) => !url.pathname.includes("/accounts/login/")),
    page.getByRole("button", { name: /sign in|log in/i }).click(),
  ]);
  await page.goto(`${base}/deployments/${deployment}/`);
  const panel = page.locator(".resource-telemetry-panel");
  await panel.getByText("browser-local history", { exact: false }).first().waitFor({ timeout: 20000 });
  for (let attempt = 0; attempt < 12; attempt += 1) {
    const text = await panel.innerText();
    if (/Recent utilization · ([2-9]|[12][0-9]|30)\/30 samples/.test(text)) break;
    await page.evaluate(async (deploymentId) => {
      const csrf = document.cookie.split("; ").find((item) => item.startsWith("csrftoken="))?.split("=")[1] || "";
      await fetch(`/api/v1/deployments/${deploymentId}/refresh/`, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: "{}" });
    }, deployment);
    await page.waitForTimeout(5000);
  }
  const text = await panel.innerText();
  if (!/Recent utilization · ([2-9]|[12][0-9]|30)\/30 samples/.test(text)) throw new Error(`Trend did not collect two samples: ${text}`);
  if ((await panel.locator("svg polyline.cpu").count()) < 2 || (await panel.locator("svg polyline.memory").count()) < 2) throw new Error("Both routers must render CPU and memory trends");
  if (!/normal|elevated|critical/i.test(text)) throw new Error("Pressure classification is missing");
  await panel.screenshot({ path: output });
  console.log(JSON.stringify({ browser: "Firefox", deployment, evidence: text.replaceAll("\n", " · "), screenshot: output }));
} finally {
  await browser.close();
}
