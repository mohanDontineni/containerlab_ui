import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const { firefox } = createRequire(import.meta.url)("playwright");
const base = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const deployment = process.env.METRICS_DEPLOYMENT_ID || "";
const screenshot = path.resolve("training/83-network-health-evidence-export.png");
const archivePath = path.resolve("training/83-network-health-evidence.zip");
if (!base || !deployment) throw new Error("Set TRAINING_BASE_URL and METRICS_DEPLOYMENT_ID");
await mkdir(path.dirname(screenshot), { recursive: true });
const browser = await firefox.launch({ headless: true });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 950 }, colorScheme: "dark", acceptDownloads: true });
const page = await context.newPage();
const browserErrors = [];
page.on("console", message => { if (message.type() === "error") browserErrors.push(message.text()); });
page.on("pageerror", error => browserErrors.push(error.message));
try {
  await page.goto(`${base}/accounts/login/`);
  await page.locator("#id_username").fill(process.env.TRAINING_USERNAME || "");
  await page.locator("#id_password").fill(process.env.TRAINING_PASSWORD || "");
  await Promise.all([page.waitForURL(url => !url.pathname.includes("/accounts/login/")), page.getByRole("button", { name: /sign in|log in/i }).click()]);
  await page.goto(`${base}/deployments/${deployment}/`);
  const button = page.getByRole("button", { name: /export network health/i });
  await button.waitFor({ state: "visible" });
  await page.waitForFunction(() => !document.querySelector("#export-network-health")?.disabled, null, { timeout: 20000 });
  const [download] = await Promise.all([page.waitForEvent("download"), button.click()]);
  await download.saveAs(archivePath);
  if (!download.suggestedFilename().endsWith("-network-health.zip")) throw new Error(`Unexpected filename: ${download.suggestedFilename()}`);
  const members = execFileSync("unzip", ["-Z1", archivePath], { encoding: "utf8" }).trim().split("\n").sort();
  if (members.join(",") !== "manifest.json,reachability.csv,traffic.csv") throw new Error(`Unexpected archive members: ${members.join(", ")}`);
  const manifest = JSON.parse(execFileSync("unzip", ["-p", archivePath, "manifest.json"], { encoding: "utf8" }));
  for (const name of ["traffic.csv", "reachability.csv"]) {
    const content = execFileSync("unzip", ["-p", archivePath, name]);
    const checksum = createHash("sha256").update(content).digest("hex");
    if (manifest.files[name].sha256 !== checksum || manifest.files[name].bytes !== content.length) throw new Error(`Integrity mismatch for ${name}`);
  }
  const archiveChecksum = createHash("sha256").update(await readFile(archivePath)).digest("hex");
  const panel = page.locator(".reachability-matrix-panel");
  await panel.screenshot({ path: screenshot });
  if (browserErrors.length) throw new Error(`Firefox reported errors: ${browserErrors.join(" | ")}`);
  console.log(JSON.stringify({ browser: "Firefox", deployment, filename: download.suggestedFilename(), members,
    trafficRows: manifest.observations.traffic?.rows, reachabilityRows: manifest.observations.reachability?.rows,
    archiveSha256: archiveChecksum, screenshot, archive: archivePath }));
} finally {
  await browser.close();
}
