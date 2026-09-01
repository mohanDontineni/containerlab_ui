import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { firefox } = require("playwright");
const baseUrl = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const labId = process.env.TRAINING_LAB_ID || "";
const output = path.resolve(process.env.TRAINING_OUTPUT || "training");
const accounts = [
  [process.env.TRAINING_OWNER_USERNAME || "", process.env.TRAINING_OWNER_PASSWORD || ""],
  [process.env.TRAINING_EDITOR_USERNAME || "", process.env.TRAINING_EDITOR_PASSWORD || ""],
];
if (!baseUrl || !labId || accounts.some(([username, password]) => !username || !password)) {
  throw new Error("Set the training base URL, lab ID, and both account credentials.");
}

await mkdir(output, { recursive: true });
const browser = await firefox.launch({ headless: true });
const makeContext = () => browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1000 }, colorScheme: "dark" });
const login = async (context, [username, password]) => {
  const page = await context.newPage();
  await page.goto(`${baseUrl}/accounts/login/`, { waitUntil: "networkidle" });
  await page.locator("#id_username").fill(username);
  await page.locator("#id_password").fill(password);
  await Promise.all([page.waitForURL((url) => !url.pathname.includes("/accounts/login/")), page.getByRole("button", { name: /sign in|log in/i }).click()]);
  return page;
};
const workspace = async (page) => {
  await page.goto(`${baseUrl}/labs/${labId}/workspace/`, { waitUntil: "networkidle" });
  const editor = page.frameLocator('iframe[title^="Topology workspace"]');
  await editor.locator(".workspace-shell").waitFor();
  return editor;
};

const ownerContext = await makeContext();
const editorContext = await makeContext();
try {
  const ownerPage = await login(ownerContext, accounts[0]);
  await workspace(ownerPage);
  const editorPage = await login(editorContext, accounts[1]);
  let editor = await workspace(editorPage);
  await editor.locator(".edit-lease-banner.held").waitFor();
  await editor.locator(".react-flow__node").nth(1).waitFor();
  await editorPage.screenshot({ path: path.join(output, "86-protected-read-only-topology.png") });

  await ownerPage.close();
  await editor.locator(".edit-lease-banner.available").waitFor({ timeout: 30_000 });
  await editorPage.screenshot({ path: path.join(output, "87-topology-editing-handoff-available.png") });

  await editor.getByRole("button", { name: "Try editing now" }).click();
  editor = editorPage.frameLocator('iframe[title^="Topology workspace"]');
  await editor.locator(".edit-lease-banner").waitFor({ state: "detached", timeout: 30_000 });
  await editor.locator(".react-flow__node").nth(1).waitFor({ timeout: 30_000 });
  await editor.getByRole("button", { name: /save draft|saved/i }).waitFor();
  await editorPage.screenshot({ path: path.join(output, "88-topology-editing-handoff-complete.png") });
  console.log("Firefox verified protected read-only, available handoff, and refreshed editing access.");
} finally {
  await editorContext.close();
  await ownerContext.close();
  await browser.close();
}
