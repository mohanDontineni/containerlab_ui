import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { firefox } = require("playwright");
const baseUrl = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const deploymentId = process.env.TRAINING_DEPLOYMENT_ID || "";
const username = process.env.TRAINING_USERNAME || "";
const password = process.env.TRAINING_PASSWORD || "";
const target = process.env.TRAINING_PING_TARGET || "10.0.12.2";
const output = path.resolve(process.env.TRAINING_OUTPUT || "training");
if (!baseUrl || !deploymentId || !username || !password) throw new Error("Set training base URL, deployment ID, username, and password.");

await mkdir(output, { recursive: true });
const browser = await firefox.launch({ headless: true });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1000 }, colorScheme: "dark" });
try {
  const page = await context.newPage();
  await page.goto(`${baseUrl}/accounts/login/`, { waitUntil: "networkidle" });
  await page.locator("#id_username").fill(username);
  await page.locator("#id_password").fill(password);
  await Promise.all([page.waitForURL((url) => !url.pathname.includes("/accounts/login/")), page.getByRole("button", { name: /sign in|log in/i }).click()]);
  await page.goto(`${baseUrl}/deployments/${deploymentId}/`, { waitUntil: "networkidle" });
  await page.getByText("Link disabled", { exact: true }).waitFor({ timeout: 30_000 });
  const diagnostic = page.locator("section").filter({ has: page.getByRole("heading", { name: "Bounded ping" }) });
  await diagnostic.getByLabel("Source device").selectOption({ label: "r1" });
  await diagnostic.getByLabel("Target IPv4 or IPv6").fill(target);
  await diagnostic.getByLabel("Packets").fill("3");
  await diagnostic.getByRole("button", { name: "Run diagnostic" }).click();
  await page.getByText(/100% packet loss/i).waitFor({ timeout: 45_000 });
  await page.screenshot({ path: path.join(output, "90-saved-link-shutdown-runtime.png"), fullPage: true });
  console.log("Firefox verified the saved link shutdown in the runtime and captured 100% packet loss.");
} finally {
  await context.close();
  await browser.close();
}
