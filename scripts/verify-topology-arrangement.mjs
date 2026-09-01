import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { firefox } = require("playwright");

const baseUrl = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const username = process.env.TRAINING_USERNAME || "";
const password = process.env.TRAINING_PASSWORD || "";
const labId = process.env.ARRANGEMENT_LAB_ID || "";
const output = path.resolve(process.env.ARRANGEMENT_SCREENSHOT || "training/39-topology-arrangement.png");
if (!baseUrl || !username || !password || !labId) {
  throw new Error("Set TRAINING_BASE_URL, TRAINING_USERNAME, TRAINING_PASSWORD, and ARRANGEMENT_LAB_ID.");
}

const browser = await firefox.launch({ headless: true });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1000 }, colorScheme: "dark" });
const page = await context.newPage();
try {
  await page.goto(`${baseUrl}/accounts/login/`, { waitUntil: "domcontentloaded" });
  await page.locator("#id_username").fill(username);
  await page.locator("#id_password").fill(password);
  await Promise.all([
    page.waitForURL((url) => !url.pathname.includes("/accounts/login/")),
    page.getByRole("button", { name: /sign in|log in/i }).click(),
  ]);
  await page.goto(`${baseUrl}/labs/${labId}/workspace/`, { waitUntil: "domcontentloaded" });
  const editor = page.frameLocator('iframe[title^="Topology workspace"]');
  await editor.getByText("Draft loaded", { exact: true }).waitFor({ timeout: 20_000 });
  const nodes = editor.locator(".react-flow__node");
  if ((await nodes.count()) < 2) throw new Error("The acceptance lab needs at least two devices.");

  await editor.getByRole("button", { name: /arrange/i }).click();
  await page.waitForTimeout(500);
  const overlaps = await editor.locator(".react-flow").evaluate((root) => {
    const devices = [...root.querySelectorAll(".react-flow__node")].map((item) => item.getBoundingClientRect());
    const objects = [...root.querySelectorAll(".topology-annotation")].map((item) => item.getBoundingClientRect());
    return devices.flatMap((device, deviceIndex) => objects.map((object, objectIndex) => ({
      deviceIndex, objectIndex,
      intersects: device.left < object.right && device.right > object.left && device.top < object.bottom && device.bottom > object.top,
    }))).filter((result) => result.intersects);
  });
  if (overlaps.length) throw new Error(`Arrange left overlapping canvas objects: ${JSON.stringify(overlaps)}`);

  await nodes.nth(0).click();
  await nodes.nth(1).click({ modifiers: ["Meta"] });
  await editor.getByText("2 devices selected", { exact: true }).waitFor();
  await editor.getByRole("button", { name: /align row/i }).click();
  const selectedTops = await editor.locator(".react-flow__node.selected").evaluateAll((items) => items.map((item) => item.getBoundingClientRect().top));
  if (selectedTops.length !== 2 || Math.abs(selectedTops[0] - selectedTops[1]) > 1) throw new Error("Align row did not align both selected devices.");

  const filter = editor.locator('input[placeholder="Filter devices…"]');
  await filter.fill("no-such-template");
  await editor.getByText("No matching devices", { exact: true }).waitFor();
  await filter.fill("routing");
  if ((await editor.locator(".palette-item").count()) < 2) throw new Error("Device palette category filtering failed.");

  const save = editor.getByRole("button", { name: /save draft/i });
  if (await save.isEnabled()) {
    await save.click();
    await editor.getByText("All changes saved", { exact: true }).waitFor({ timeout: 20_000 });
  }
  await page.reload({ waitUntil: "domcontentloaded" });
  const reloaded = page.frameLocator('iframe[title^="Topology workspace"]');
  await reloaded.getByText("Draft loaded", { exact: true }).waitFor({ timeout: 20_000 });
  await reloaded.locator('input[placeholder="Filter devices…"]').fill("routing");
  const reloadedNodes = reloaded.locator(".react-flow__node");
  await reloadedNodes.nth(0).click();
  await reloadedNodes.nth(1).click({ modifiers: ["Meta"] });
  await reloaded.getByText("2 devices selected", { exact: true }).waitFor();
  await page.screenshot({ path: output, fullPage: true });
  console.log(JSON.stringify({ browser: "firefox", overlapCount: 0, alignment: "pass", filter: "pass", persistence: "pass", screenshot: output }));
} finally {
  await browser.close();
}
