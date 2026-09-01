import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const { firefox } = createRequire(import.meta.url)("playwright");
const base = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const deployment = process.env.METRICS_DEPLOYMENT_ID || "";
const output = path.resolve("training/82-durable-runtime-observation-recovery.png");
if (!base || !deployment) throw new Error("Set TRAINING_BASE_URL and METRICS_DEPLOYMENT_ID");
await mkdir(path.dirname(output), { recursive: true });
const browser = await firefox.launch({ headless: true });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 950 }, colorScheme: "dark" });
const page = await context.newPage();
try {
  await page.goto(`${base}/accounts/login/`);
  await page.locator("#id_username").fill(process.env.TRAINING_USERNAME || "");
  await page.locator("#id_password").fill(process.env.TRAINING_PASSWORD || "");
  await Promise.all([page.waitForURL((url) => !url.pathname.includes("/accounts/login/")), page.getByRole("button", { name: /sign in|log in/i }).click()]);
  await page.goto(`${base}/deployments/${deployment}/`);
  await page.getByText("recovered after reload", { exact: false }).first().waitFor({ timeout: 20000 });
  await page.reload();
  await page.getByText("recovered after reload", { exact: false }).first().waitFor({ timeout: 20000 });
  const traffic = await page.locator("#topology-traffic-summary").innerText();
  const matrix = await page.locator("#reachability-matrix").innerText();
  if (!traffic.includes("recovered after reload") || !traffic.includes("Refresh snapshot to calculate rate")) throw new Error(`Traffic recovery or safe baseline missing: ${traffic}`);
  if (!matrix.includes("recovered after reload") || !matrix.includes("2 / 2 paths reachable")) throw new Error(`Matrix recovery missing: ${matrix}`);
  await page.locator(".reachability-matrix-panel").screenshot({ path: output });
  console.log(JSON.stringify({ browser: "Firefox", deployment, traffic: traffic.replaceAll("\n", " · "), matrix: matrix.replaceAll("\n", " · "), screenshot: output }));
} finally {
  await browser.close();
}
