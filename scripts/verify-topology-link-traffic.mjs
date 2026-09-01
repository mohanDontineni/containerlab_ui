import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const { firefox } = createRequire(import.meta.url)("playwright");
const base = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const deployment = process.env.METRICS_DEPLOYMENT_ID || "";
const output = path.resolve("training/79-topology-wide-link-traffic.png");
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
  await page.getByRole("button", { name: /inspect link traffic/i }).click();
  const summary = page.locator("#topology-traffic-summary");
  await summary.getByText("Complete", { exact: false }).waitFor({ timeout: 25000 });
  const text = await summary.innerText();
  for (const label of ["2 devices", "1 links", "r1:eth1", "r2:eth1", "0 errors", "0 drops"]) if (!text.includes(label)) throw new Error(`Missing live evidence: ${label}`);
  const packetCounts = [...text.matchAll(/(?:RX|TX) ([\d,]+) pkt/g)].map((match) => Number(match[1].replaceAll(",", "")));
  if (packetCounts.length !== 4 || packetCounts.some((value) => value <= 0)) throw new Error(`Expected four nonzero endpoint counters: ${packetCounts}`);
  await page.locator(".runtime-topology-panel").screenshot({ path: output });
  console.log(JSON.stringify({ browser: "Firefox", deployment, packetCounts, evidence: text.replaceAll("\n", " · "), screenshot: output }));
} finally {
  await browser.close();
}
