import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { firefox } = require("playwright");
const baseUrl = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const labId = process.env.TRAINING_LAB_ID || "";
const username = process.env.TRAINING_USERNAME || "";
const password = process.env.TRAINING_PASSWORD || "";
const output = path.resolve(process.env.TRAINING_OUTPUT || "training");
if (!baseUrl || !labId || !username || !password) throw new Error("Set training base URL, lab ID, username, and password.");

await mkdir(output, { recursive: true });
const browser = await firefox.launch({ headless: true });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1000 }, colorScheme: "dark" });
try {
  const page = await context.newPage();
  await page.goto(`${baseUrl}/accounts/login/`, { waitUntil: "networkidle" });
  await page.locator("#id_username").fill(username);
  await page.locator("#id_password").fill(password);
  await Promise.all([page.waitForURL((url) => !url.pathname.includes("/accounts/login/")), page.getByRole("button", { name: /sign in|log in/i }).click()]);
  await page.goto(`${baseUrl}/labs/${labId}/workspace/`, { waitUntil: "networkidle" });
  const editor = page.frameLocator('iframe[title^="Topology workspace"]');
  await editor.locator(".workspace-shell").waitFor();
  const edge = editor.locator(".react-flow__edge").first();
  await edge.waitFor();
  await edge.click({ force: true });
  const linkState = editor.getByLabel("Link state");
  await linkState.waitFor();
  await linkState.selectOption("disabled");
  await editor.getByRole("button", { name: /save draft/i }).click();
  await editor.getByRole("button", { name: /saved/i }).waitFor({ timeout: 30_000 });
  await page.screenshot({ path: path.join(output, "89-saved-link-shutdown-design.png") });
  console.log("Firefox saved a topology link shutdown and captured its red dashed design state.");
} finally {
  await context.close();
  await browser.close();
}
