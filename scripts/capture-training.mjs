import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const baseUrl = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const username = process.env.TRAINING_USERNAME || "";
const password = process.env.TRAINING_PASSWORD || "";
const output = path.resolve(process.env.TRAINING_OUTPUT || "training");

if (!baseUrl || !username || !password) {
  throw new Error("Set TRAINING_BASE_URL, TRAINING_USERNAME, and TRAINING_PASSWORD.");
}

await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  ignoreHTTPSErrors: true,
  viewport: { width: 1600, height: 1000 },
  colorScheme: "dark",
});
const page = await context.newPage();

const capture = async (name, url, prepare) => {
  await page.goto(`${baseUrl}${url}`, { waitUntil: "networkidle" });
  if (prepare) await prepare(page);
  await page.screenshot({ path: path.join(output, name), fullPage: true });
  console.log(`${name} <- ${page.url()}`);
};

const firstHref = async (selector, pattern) => {
  const values = await page.locator(selector).evaluateAll((items) =>
    items.map((item) => item.getAttribute("href")).filter(Boolean),
  );
  return values.find((value) => pattern.test(value)) || null;
};

try {
  await page.goto(`${baseUrl}/accounts/login/`, { waitUntil: "networkidle" });
  await page.getByLabel(/username/i).fill(username);
  await page.getByLabel(/password/i).fill(password);
  await Promise.all([
    page.waitForURL((url) => !url.pathname.includes("/accounts/login/")),
    page.getByRole("button", { name: /sign in|log in/i }).click(),
  ]);

  await capture("01-overview.png", "/");
  await capture("02-projects.png", "/projects/");
  await capture("03-create-project.png", "/projects/new/");

  await page.goto(`${baseUrl}/projects/`, { waitUntil: "networkidle" });
  const projectHref = await firstHref('a[href^="/projects/"]', /^\/projects\/[0-9a-f-]+\/$/i);
  if (projectHref) await capture("04-project-access.png", projectHref);

  await capture("05-lab-library.png", "/labs/");
  await capture("06-create-lab.png", "/labs/new/");
  await page.goto(`${baseUrl}/labs/`, { waitUntil: "networkidle" });
  const workspaceHref = await firstHref('a[href*="/workspace/"]', /^\/labs\/[0-9a-f-]+\/workspace\/$/i);
  if (workspaceHref) {
    await capture("07-topology-workspace.png", workspaceHref, async (p) => {
      await p.locator(".workspace-shell").waitFor();
    });
    await capture("08-save-as-dialog.png", workspaceHref, async (p) => {
      await p.getByRole("button", { name: /save as/i }).click();
      await p.getByRole("dialog").waitFor();
    });
    await capture("09-revision-history.png", workspaceHref, async (p) => {
      await p.getByRole("button", { name: /history/i }).click();
      await p.getByRole("dialog").waitFor();
    });
  }

  await capture("10-image-library.png", "/images/");
  await capture("11-upload-image.png", "/images/upload/");
  await capture("12-register-image.png", "/images/register/");
  await capture("13-deployments.png", "/deployments/");

  await page.goto(`${baseUrl}/deployments/`, { waitUntil: "networkidle" });
  const deploymentHref = await firstHref('a[href^="/deployments/"]', /^\/deployments\/[0-9a-f-]+\/$/i);
  if (deploymentHref) {
    await capture("14-runtime-overview.png", deploymentHref, async (p) => {
      await p.locator("#device-list article").first().waitFor({ timeout: 20_000 });
    });
    await capture("15-device-lifecycle.png", deploymentHref, async (p) => {
      await p.locator(".device-actions").first().waitFor({ timeout: 20_000 });
    });
    await capture("16-live-link-controls.png", deploymentHref, async (p) => {
      await p.locator(".link-control-panel").scrollIntoViewIfNeeded();
    });
    await capture("17-ping-diagnostic.png", deploymentHref, async (p) => {
      await p.locator(".diagnostic-panel").scrollIntoViewIfNeeded();
    });
    await capture("18-packet-capture.png", deploymentHref, async (p) => {
      await p.locator(".capture-panel").scrollIntoViewIfNeeded();
    });
    await capture("19-device-console.png", deploymentHref, async (p) => {
      await p.locator(".runtime-console").scrollIntoViewIfNeeded();
    });
    await capture("20-configuration-history.png", deploymentHref, async (p) => {
      await p.locator("#configuration-list").scrollIntoViewIfNeeded();
    });
  }

  await capture("21-device-templates.png", "/device-templates/");
  await capture("22-jobs-events.png", "/operations/");
  await capture("23-account-security.png", "/settings/");
  await capture("24-api-explorer.png", "/api/v1/docs/");
} finally {
  await browser.close();
}
