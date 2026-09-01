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
  await page.goto(`${baseUrl}/projects/`, { waitUntil: "networkidle" });
  const retirementRow = page.locator(".catalog-row").filter({ hasText: /Project Retirement Acceptance/i }).first();
  if (await retirementRow.count()) {
    const retirementHref = await retirementRow.locator('a[href^="/projects/"]').getAttribute("href");
    if (retirementHref) await capture("33-guarded-project-retirement.png", retirementHref, async (p) => {
      await p.locator("#retire-project").click();
      const dialog = p.locator("#project-retire-dialog");
      await dialog.waitFor();
      return dialog;
    });
  }

  await capture("05-lab-library.png", "/labs/");
  await capture("32-guarded-lab-deletion.png", "/labs/", async (p) => {
    const row = p.locator(".catalog-row").filter({ hasText: /Lab Deletion Acceptance/i }).first();
    await row.waitFor();
    await row.locator("button[data-delete-lab]").click();
    const dialog = p.locator("#lab-delete-dialog");
    await dialog.waitFor();
    return dialog;
  });
  await capture("06-create-lab.png", "/labs/new/");
  await page.goto(`${baseUrl}/labs/`, { waitUntil: "networkidle" });
  const workspaceHrefs = await matchingHrefs('a[href*="/workspace/"]', /^\/labs\/[0-9a-f-]+\/workspace\/$/i);
  const acceptanceRow = page.locator(".catalog-row").filter({ hasText: /Backup Restore Acceptance/i }).first();
  const acceptanceHref = (await acceptanceRow.count()) ? await acceptanceRow.locator('a[href*="/workspace/"]').first().getAttribute("href") : null;
  const canvasObjectsRow = page.locator(".catalog-row").filter({ hasText: /Canvas Objects Acceptance/i }).first();
  const canvasObjectsHref = (await canvasObjectsRow.count()) ? await canvasObjectsRow.locator('a[href*="/workspace/"]').first().getAttribute("href") : null;
  const subgraphRow = page.locator(".catalog-row").filter({ hasText: /Subgraph Duplication Acceptance/i }).first();
  const subgraphHref = (await subgraphRow.count()) ? await subgraphRow.locator('a[href*="/workspace/"]').first().getAttribute("href") : null;
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
    const restoreHref = acceptanceHref || workspaceHref;
    const restoreLabId = restoreHref.match(/^\/labs\/([0-9a-f-]+)\/workspace\/$/i)?.[1];
    if (restoreLabId) {
      const backupResponse = await context.request.get(`${baseUrl}/api/v1/labs/${restoreLabId}/export/`);
      if (!backupResponse.ok()) throw new Error(`Backup download failed with ${backupResponse.status()}`);
      const backupBuffer = await backupResponse.body();
      await capture("27-backup-restore-preview.png", restoreHref, async (p) => {
        const editor = p.frameLocator('iframe[title^="Topology workspace"]');
        await editor.locator('input[type="file"]').setInputFiles({ name: "verified-lab.clabstudio.json", mimeType: "application/vnd.containerlab.studio.lab+json", buffer: backupBuffer });
        await editor.getByRole("dialog", { name: /restore topology backup/i }).waitFor();
      });
    }
  }
  if (canvasObjectsHref) {
    await capture("36-topology-canvas-objects.png", canvasObjectsHref, async (p) => {
      const editor = p.frameLocator('iframe[title^="Topology workspace"]');
      await editor.getByText("Draft loaded", { exact: true }).waitFor();
      await editor.locator(".topology-annotation.note").first().click();
    });
  } else console.warn("36-topology-canvas-objects.png retained: Canvas Objects Acceptance fixture is unavailable");
  if (subgraphHref) {
    await capture("38-subgraph-duplication.png", subgraphHref, async (p) => {
      const editor = p.frameLocator('iframe[title^="Topology workspace"]');
      await editor.getByText("Draft loaded", { exact: true }).waitFor();
      const nodes = editor.locator(".react-flow__node");
      await nodes.nth(3).waitFor();
      await nodes.nth(2).click();
      await nodes.nth(3).click({ modifiers: ["Meta"] });
      await editor.getByText("2 devices selected", { exact: true }).waitFor();
    });
  } else console.warn("38-subgraph-duplication.png retained: Subgraph Duplication Acceptance fixture is unavailable");
  if (subgraphHref) {
    await capture("39-topology-arrangement.png", subgraphHref, async (p) => {
      const editor = p.frameLocator('iframe[title^="Topology workspace"]');
      await editor.getByText("Draft loaded", { exact: true }).waitFor();
      await editor.locator('input[placeholder="Filter devices…"]').fill("routing");
      const nodes = editor.locator(".react-flow__node");
      await nodes.nth(1).waitFor();
      await nodes.nth(0).click();
      await nodes.nth(1).click({ modifiers: ["Meta"] });
      await editor.getByText("2 devices selected", { exact: true }).waitFor();
    });
  } else console.warn("39-topology-arrangement.png retained: Subgraph Duplication Acceptance fixture is unavailable");

  await capture("10-image-library.png", "/images/");
  await page.goto(`${baseUrl}/images/`, { waitUntil: "networkidle" });
  const imageDeletionRow = page.locator(".catalog-row").filter({ hasText: /Deletion Acceptance Artifact\.bin/i }).first();
  if (await imageDeletionRow.count()) {
    await capture("31-guarded-image-deletion.png", "/images/", async (p) => {
      const row = p.locator(".catalog-row").filter({ hasText: /Deletion Acceptance Artifact\.bin/i }).first();
      await row.locator("button[data-delete-image]").click();
      const dialog = p.locator("#image-delete-dialog");
      await dialog.waitFor();
      return dialog;
    });
  } else console.warn("31-guarded-image-deletion.png retained: disposable specimen has already been deleted");
  await capture("11-upload-image.png", "/images/upload/");
  await capture("12-register-image.png", "/images/register/");
  await capture("13-deployments.png", "/deployments/");

  await page.goto(`${baseUrl}/deployments/`, { waitUntil: "networkidle" });
  const deploymentHrefs = await matchingHrefs('a[href^="/deployments/"]', /^\/deployments\/[0-9a-f-]+\/$/i);
  const resourceRow = page.locator(".catalog-row").filter({ hasText: /Runtime Resource Acceptance/i }).first();
  if (await resourceRow.count()) {
    const resourceHref = await resourceRow.locator('a[href^="/deployments/"]').getAttribute("href");
    if (resourceHref) await capture("37-enforced-device-resources.png", resourceHref, async (p) => {
      await p.getByText(/CPU \/ .* RAM/).waitFor({ timeout: 20_000 });
    });
  } else console.warn("37-enforced-device-resources.png retained: Runtime Resource Acceptance fixture is unavailable");
  const removalRow = page.locator(".catalog-row").filter({ hasText: /Runtime Removal Acceptance/i }).first();
  if (await removalRow.count()) {
    const removalHref = await removalRow.locator('a[href^="/deployments/"]').getAttribute("href");
    if (removalHref) {
      await page.goto(`${baseUrl}${removalHref}`, { waitUntil: "networkidle" });
      const removalButton = page.locator("#remove-runtime");
      if (await removalButton.count()) {
        await page.locator("#device-list article").first().waitFor({ timeout: 20_000 });
        await removalButton.click();
        const dialog = page.locator("#runtime-removal-dialog");
        await dialog.waitFor();
        await dialog.screenshot({ path: path.join(output, "34-guarded-runtime-removal.png") });
        console.log(`34-guarded-runtime-removal.png <- ${page.url()}`);
      } else console.warn("34-guarded-runtime-removal.png retained: specimen is already terminal");
    }
  }
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
  let tracerouteHref = deploymentHref;
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
  for (const candidate of deploymentHrefs) {
    await page.goto(`${baseUrl}${candidate}`, { waitUntil: "networkidle" });
    await page.locator("#device-list article").first().waitFor({ timeout: 20_000 });
    const deviceNames = await page.locator("#device-list article strong").allTextContents();
    if (deviceNames.includes("r1") && deviceNames.includes("r2")) {
      tracerouteHref = candidate;
      break;
    }
  }
  if (tracerouteHref) {
    await capture("40-guarded-device-reset.png", tracerouteHref, async (p) => {
      const reset = p.locator("button[data-device-reset]:not([disabled])").first();
      await reset.waitFor({ timeout: 20_000 });
      await reset.click();
      const dialog = p.locator("#device-reset-dialog");
      await dialog.waitFor();
      return dialog;
    });
  } else console.warn("40-guarded-device-reset.png retained: a ready multi-device runtime is unavailable");
  if (tracerouteHref) {
    await capture("41-selected-device-lifecycle.png", tracerouteHref, async (p) => {
      const selectors = p.locator("input[data-device-select]");
      await selectors.nth(1).waitFor({ timeout: 20_000 });
      await selectors.nth(0).check();await selectors.nth(1).check();
      await p.locator('button[data-bulk-device-operation="suspend_device"]').click();
      const dialog = p.locator("#bulk-device-dialog");
      await dialog.waitFor();
      return dialog;
    });
  } else console.warn("41-selected-device-lifecycle.png retained: a ready multi-device runtime is unavailable");
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
    await capture("30-traceroute-diagnostic.png", tracerouteHref, async (p) => {
      const panel = p.locator(".diagnostic-panel");
      await panel.scrollIntoViewIfNeeded();
      await p.locator("#diagnostic-kind").selectOption("traceroute");
      await p.locator("#ping-node").selectOption({ index: 1 });
      await p.locator("#ping-target").fill("10.2.2.2");
      await p.locator("#ping-form button").click();
      await p.waitForFunction(() => (document.querySelector("#diagnostic-output")?.textContent || "").includes("traceroute to"), null, { timeout: 20_000 });
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
    await capture("28-configuration-compare.png", configurationHref, async (p) => {
      const versions = p.locator("#configuration-list article").filter({ hasText: /firewall/i });
      if ((await versions.count()) < 2) throw new Error("Two collected versions for one device are required");
      await versions.nth(0).locator('input[type="checkbox"]').check();
      await versions.nth(1).locator('input[type="checkbox"]').check();
      await p.locator("#compare-configurations").click();
      const dialog = p.locator("#configuration-compare-dialog");
      await dialog.waitFor();
      return dialog;
    });
    await capture("29-configuration-restore-preview.png", configurationHref, async (p) => {
      await p.locator("button[data-restore-configuration]:not([disabled])").first().click();
      const dialog = p.locator("#configuration-restore-dialog");
      await dialog.waitFor();
      return dialog;
    });
    await capture("25-redeploy-preview.png", deploymentHref, async (p) => {
      await p.locator("#redeploy-runtime").click();
      const dialog = p.locator("#redeploy-dialog");
      await dialog.waitFor();
      return dialog;
    });
    await capture("26-device-runtime-logs.png", deploymentHref, async (p) => {
      await p.locator("button[data-device-log]:not([disabled])").first().click();
      const dialog = p.locator("#device-log-dialog");
      await dialog.waitFor();
      await p.waitForFunction(() => {
        const output = document.querySelector("#device-log-output")?.textContent || "";
        return !output.includes("Reading bounded runtime logs") && output.length > 20;
      });
      return dialog;
    });
  }

  await capture("21-device-templates.png", "/device-templates/");
  await page.goto(`${baseUrl}/device-templates/`, { waitUntil: "networkidle" });
  const managedTemplate = page.locator(".catalog-row").filter({ hasText: /GUI Template Acceptance/i }).first();
  if (await managedTemplate.count()) {
    const managedTemplateHref = await managedTemplate.locator('a[href^="/device-templates/"]').getAttribute("href");
    if (managedTemplateHref) await capture("35-versioned-device-template.png", managedTemplateHref);
  }
  await capture("22-jobs-events.png", "/operations/");
  await capture("23-account-security.png", "/settings/");
  await capture("24-api-explorer.png", "/api/v1/docs/");
} finally {
  await browser.close();
}
