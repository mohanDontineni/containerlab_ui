import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { firefox } = require("playwright");
const baseUrl = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const output = path.resolve(process.env.TRAINING_OUTPUT || "training");
const projectId = process.env.TRAINING_PROJECT_ID || "";
const labId = process.env.TRAINING_LAB_ID || "";
const deploymentId = process.env.TRAINING_DEPLOYMENT_ID || "";
const admin = [process.env.TRAINING_ADMIN_USERNAME || "", process.env.TRAINING_ADMIN_PASSWORD || ""];
const editor = [process.env.TRAINING_EDITOR_USERNAME || "", process.env.TRAINING_EDITOR_PASSWORD || ""];
if (!baseUrl || !projectId || !labId || !deploymentId || [...admin, ...editor].some((value) => !value)) {
  throw new Error("Set the base URL, project/lab/deployment IDs, and both account credentials.");
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

const adminContext = await makeContext();
const editorContext = await makeContext();
try {
  const editorWorkspace = await login(editorContext, editor);
  await editorWorkspace.goto(`${baseUrl}/labs/${labId}/workspace/`, { waitUntil: "networkidle" });
  const topology = editorWorkspace.frameLocator('iframe[title^="Topology workspace"]');
  await topology.locator(".workspace-shell").waitFor();
  await topology.getByRole("button", { name: /save draft|saved/i }).waitFor();

  const editorRuntime = await editorContext.newPage();
  await editorRuntime.goto(`${baseUrl}/deployments/${deploymentId}/`, { waitUntil: "networkidle" });
  const consoleFrame = editorRuntime.frameLocator('iframe[title="Device consoles"]');
  const r1 = consoleFrame.locator("#device-tabs button").filter({ hasText: /^r1/ });
  await r1.waitFor({ timeout: 30_000 });
  await r1.click();
  await consoleFrame.locator('[data-console-tab]').filter({ hasText: "r1" }).waitFor({ timeout: 30_000 });
  await consoleFrame.locator(".xterm").waitFor({ timeout: 30_000 });
  await editorRuntime.waitForTimeout(1_000);

  const adminPage = await login(adminContext, admin);
  await adminPage.goto(`${baseUrl}/projects/${projectId}/`, { waitUntil: "networkidle" });
  const memberRow = adminPage.locator("[data-membership]").filter({ hasText: editor[0] });
  await memberRow.waitFor();
  await memberRow.locator(".member-role").selectOption("viewer");
  const dialog = adminPage.locator("#access-revoke-dialog");
  await dialog.waitFor();
  await dialog.getByText("1", { exact: true }).first().waitFor();
  await adminPage.screenshot({ path: path.join(output, "91-live-membership-revocation-preview.png") });

  await dialog.locator("#confirm-access-revoke").click();
  await adminPage.getByText(/Closed 1 console\(s\) and released 1 editing lease\(s\)/).waitFor({ timeout: 30_000 });
  await consoleFrame.locator('[data-console-tab] small').filter({ hasText: /access-revoked|disconnected/ }).waitFor({ timeout: 30_000 });
  await editorRuntime.locator(".runtime-console").scrollIntoViewIfNeeded();
  await editorRuntime.screenshot({ path: path.join(output, "92-live-console-access-revoked.png"), fullPage: true });

  const impact = await adminPage.evaluate(async (membershipId) => {
    const response = await fetch(`/api/v1/memberships/${membershipId}/access-impact/`, { cache: "no-store" });
    return response.json();
  }, await memberRow.getAttribute("data-membership"));
  if (impact.current_role !== "viewer" || impact.active_consoles !== 0 || impact.editing_leases !== 0) {
    throw new Error(`Unexpected post-revocation state: ${JSON.stringify(impact)}`);
  }
  console.log("Firefox verified one live console and one editing lease were atomically revoked by editor-to-viewer demotion.");
} finally {
  await editorContext.close();
  await adminContext.close();
  await browser.close();
}
