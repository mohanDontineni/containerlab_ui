import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const { firefox } = createRequire(import.meta.url)("playwright");
const base = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const deployment = process.env.METRICS_DEPLOYMENT_ID || "";
const output = path.resolve("training/81-data-plane-reachability-matrix.png");
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
  const panel = page.locator(".reachability-matrix-panel");
  await page.getByRole("button", { name: /run matrix/i }).click();
  await panel.getByText("All reachable", { exact: false }).waitFor({ timeout: 30000 });
  const text = await panel.innerText();
  for (const label of ["2 / 2 paths reachable", "10.0.12.1 · eth1", "10.0.12.2 · eth1"]) if (!text.includes(label)) throw new Error(`Missing matrix evidence: ${label}`);
  if ((text.match(/✓ Reachable/g) || []).length !== 2) throw new Error(`Expected two reachable ordered paths: ${text}`);
  if ((text.match(/ ms/g) || []).length !== 2) throw new Error(`Expected normalized latency for both paths: ${text}`);
  await panel.screenshot({ path: output });
  console.log(JSON.stringify({ browser: "Firefox", deployment, evidence: text.replaceAll("\n", " · "), screenshot: output }));
} finally {
  await browser.close();
}
