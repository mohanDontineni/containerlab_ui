import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { firefox } = require("playwright");

const baseUrl = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const username = process.env.TRAINING_USERNAME || "";
const password = process.env.TRAINING_PASSWORD || "";
const output = path.resolve(process.env.TRAINING_OUTPUT || "training");

if (!baseUrl || !username || !password) {
  throw new Error("Set TRAINING_BASE_URL, TRAINING_USERNAME, and TRAINING_PASSWORD.");
}

await mkdir(output, { recursive: true });
const browser = await firefox.launch({ headless: true });
const context = await browser.newContext({
  ignoreHTTPSErrors: true,
  viewport: { width: 1600, height: 1000 },
  colorScheme: "dark",
});
const page = await context.newPage();

const capture = async (name, url, prepare) => {
  await page.goto(`${baseUrl}${url}`, { waitUntil: "networkidle" });
  const target = prepare ? await prepare(page) : null;
  if (target) await target.screenshot({ path: path.join(output, name) });
  else await page.screenshot({ path: path.join(output, name), fullPage: true });
  console.log(`${name} <- ${page.url()}`);
};

const firstHref = async (selector, pattern) => {
  const values = await page.locator(selector).evaluateAll((items) =>
    items.map((item) => item.getAttribute("href")).filter(Boolean),
  );
  return values.find((value) => pattern.test(value)) || null;
};

const matchingHrefs = async (selector, pattern) => {
  const values = await page.locator(selector).evaluateAll((items) =>
    items.map((item) => item.getAttribute("href")).filter(Boolean),
  );
  return [...new Set(values.filter((value) => pattern.test(value)))];
};

try {
  await page.goto(`${baseUrl}/accounts/login/`, { waitUntil: "networkidle" });
  await page.locator("#id_username").fill(username);
  await page.locator("#id_password").fill(password);
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
  const workspaceHrefs = await matchingHrefs('a[href*="/workspace/"]', /^\/labs\/[0-9a-f-]+\/workspace\/$/i);
  let workspaceHref = workspaceHrefs[0] || null;
  for (const candidate of workspaceHrefs) {
    await page.goto(`${baseUrl}${candidate}`, { waitUntil: "networkidle" });
    const editor = page.frameLocator('iframe[title^="Topology workspace"]');
    await editor.locator(".workspace-shell").waitFor();
    const summary = (await editor.locator(".canvas-hint").textContent()) || "";
    if (/\b[1-9]\d* links?\b/i.test(summary)) {
      workspaceHref = candidate;
      break;
    }
  }
  if (workspaceHref) {
    await capture("07-topology-workspace.png", workspaceHref, async (p) => {
      await p.frameLocator('iframe[title^="Topology workspace"]').locator(".workspace-shell").waitFor();
    });
    await capture("08-save-as-dialog.png", workspaceHref, async (p) => {
      const editor = p.frameLocator('iframe[title^="Topology workspace"]');
      await editor.getByRole("button", { name: /save as/i }).click();
      await editor.getByRole("dialog").waitFor();
    });
    await capture("09-revision-history.png", workspaceHref, async (p) => {
      const editor = p.frameLocator('iframe[title^="Topology workspace"]');
      await editor.getByRole("button", { name: /history/i }).click();
      await editor.getByRole("dialog").waitFor();
      await editor
        .locator(".revision-list article")
        .or(editor.locator(".history-empty").filter({ hasNotText: "Loading revision history" }))
        .first()
        .waitFor();
    });
  }

  await capture("10-image-library.png", "/images/");
  await capture("11-upload-image.png", "/images/upload/");
  await capture("12-register-image.png", "/images/register/");
  await capture("13-deployments.png", "/deployments/");

  await page.goto(`${baseUrl}/deployments/`, { waitUntil: "networkidle" });
  const deploymentHrefs = await matchingHrefs('a[href^="/deployments/"]', /^\/deployments\/[0-9a-f-]+\/$/i);
  let deploymentHref = deploymentHrefs[0] || null;
  for (const candidate of deploymentHrefs) {
    await page.goto(`${baseUrl}${candidate}`, { waitUntil: "networkidle" });
    await page.locator("#device-list article").first().waitFor({ timeout: 20_000 });
    if ((await page.locator("#link-control-list article").count()) > 0) {
      deploymentHref = candidate;
      break;
    }
  }
  let configurationHref = deploymentHref;
  for (const candidate of deploymentHrefs) {
    await page.goto(`${baseUrl}${candidate}`, { waitUntil: "networkidle" });
    await page.waitForFunction(() => {
      const list = document.querySelector("#configuration-list");
      return list && !list.textContent?.includes("Loading collected versions");
    });
    if ((await page.locator("#configuration-list article").count()) > 0) {
      configurationHref = candidate;
      break;
    }
  }
  if (deploymentHref) {
    await capture("14-runtime-overview.png", deploymentHref, async (p) => {
      await p.locator("#device-list article").first().waitFor({ timeout: 20_000 });
    });
    await capture("15-device-lifecycle.png", deploymentHref, async (p) => {
      await p.locator(".device-actions").first().waitFor({ timeout: 20_000 });
      return p.locator(".runtime-devices");
    });
    await capture("16-live-link-controls.png", deploymentHref, async (p) => {
      const panel = p.locator(".link-control-panel");
      await panel.scrollIntoViewIfNeeded();
      return panel;
    });
    await capture("17-ping-diagnostic.png", deploymentHref, async (p) => {
      const panel = p.locator(".diagnostic-panel");
      await panel.scrollIntoViewIfNeeded();
      return panel;
    });
    await capture("18-packet-capture.png", deploymentHref, async (p) => {
      const panel = p.locator(".capture-panel");
      await panel.scrollIntoViewIfNeeded();
      return panel;
    });
    await capture("19-device-console.png", deploymentHref, async (p) => {
      const panel = p.locator(".runtime-console");
      await panel.scrollIntoViewIfNeeded();
      const consoleFrame = p.frameLocator('iframe[title="Device consoles"]');
      await consoleFrame.locator("nav button:not([disabled])").first().click();
      await consoleFrame.locator(".xterm").waitFor({ timeout: 20_000 });
      await p.waitForTimeout(750);
      return panel;
    });
    await capture("20-configuration-history.png", configurationHref, async (p) => {
      const panel = p.locator("#configuration-list").locator("xpath=ancestor::section[1]");
      await panel.scrollIntoViewIfNeeded();
      return panel;
    });
  }

  await capture("21-device-templates.png", "/device-templates/");
  await capture("22-jobs-events.png", "/operations/");
  await capture("23-account-security.png", "/settings/");
  await capture("24-api-explorer.png", "/api/v1/docs/");
} finally {
  await browser.close();
}
