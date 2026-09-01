import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { firefox } = require("playwright");

const baseUrl = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const username = process.env.TRAINING_USERNAME || "";
const password = process.env.TRAINING_PASSWORD || "";
const deploymentId = process.env.MAP_DEPLOYMENT_ID || "";
const output = path.resolve(process.env.MAP_SCREENSHOT || "training/53-live-operational-topology.png");
if (!baseUrl || !username || !password || !deploymentId) {
  throw new Error("Set TRAINING_BASE_URL, TRAINING_USERNAME, TRAINING_PASSWORD, and MAP_DEPLOYMENT_ID.");
}

const browser = await firefox.launch({ headless: true });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1100 }, colorScheme: "dark" });
const page = await context.newPage();
const browserErrors = [];
page.on("console", (message) => { if (message.type() === "error") browserErrors.push(`console: ${message.text()}`); });
page.on("pageerror", (error) => browserErrors.push(`page: ${error.message}`));
page.on("response", (response) => { if (response.status() >= 400) browserErrors.push(`http ${response.status()}: ${response.url()}`); });

try {
  await page.goto(`${baseUrl}/accounts/login/`, { waitUntil: "domcontentloaded" });
  await page.locator("#id_username").fill(username);
  await page.locator("#id_password").fill(password);
  await Promise.all([
    page.waitForURL((url) => !url.pathname.includes("/accounts/login/")),
    page.getByRole("button", { name: /sign in|log in/i }).click(),
  ]);
  await page.goto(`${baseUrl}/deployments/${deploymentId}/`, { waitUntil: "domcontentloaded" });
  const nodes = page.locator("[data-runtime-map-device]");
  await nodes.first().waitFor({ state: "visible", timeout: 20_000 });
  if (await nodes.count() !== 2) throw new Error(`Expected two topology devices, rendered ${await nodes.count()}.`);
  const runtime = await (await context.request.get(`${baseUrl}/api/v1/deployments/${deploymentId}/runtime/`, { failOnStatusCode: true })).json();
  if (runtime.links.length !== 1 || runtime.devices.some((device) => device.observed_readiness !== "ready")) {
    throw new Error("The BGP acceptance topology must expose one link and two ready devices.");
  }
  if (runtime.devices.some((device) => !Number.isFinite(Number(device.position?.x)) || !Number.isFinite(Number(device.position?.y)))) {
    throw new Error("Runtime devices did not expose saved topology positions.");
  }
  const r1 = runtime.devices.find((device) => device.name === "r1");
  const r2 = runtime.devices.find((device) => device.name === "r2");
  const r1Card = page.locator(`[data-runtime-map-device="${r1.id}"]`);
  const r2Card = page.locator(`[data-runtime-map-device="${r2.id}"]`);
  const r1Box = await r1Card.boundingBox();
  const r2Box = await r2Card.boundingBox();
  if (!r1Box || !r2Box || Math.hypot(r1Box.x - r2Box.x, r1Box.y - r2Box.y) < 160) {
    throw new Error("Saved topology positions did not produce distinct map placement.");
  }
  const link = page.locator(".runtime-map-link");
  if (await link.count() !== 1 || !(await link.getAttribute("class"))?.includes("healthy")) {
    throw new Error("The live link did not render in its healthy state.");
  }

  await r1Card.click();
  const inspector = page.locator("#runtime-map-inspector");
  await inspector.getByRole("heading", { name: "r1" }).waitFor();
  const inspectorText = await inspector.textContent();
  if (!inspectorText.includes(r1.worker_placement) || !inspectorText.includes(String(r1.interfaces.length))) {
    throw new Error("The topology inspector omitted live worker or interface data.");
  }
  await page.locator(".runtime-topology-panel").screenshot({ path: output });

  await link.click();
  await page.waitForTimeout(500);
  if (!(await link.getAttribute("class"))?.includes("selected")) throw new Error("Link selection did not persist on the live map.");
  await r1Card.click();
  await inspector.locator('[data-runtime-map-action="console"]').click();
  const frame = page.frameLocator('.runtime-console iframe[title="Device consoles"]');
  await frame.locator(`[data-device="${r1.id}"].active`).waitFor({ state: "visible", timeout: 15_000 });
  await frame.locator("#connection-state").waitFor({ state: "visible" });
  let connectionState = "";
  for (let attempt = 0; attempt < 30; attempt += 1) {
    connectionState = (await frame.locator("#connection-state").textContent())?.trim() || "";
    if (connectionState !== "Select a device") break;
    await page.waitForTimeout(250);
  }
  if (connectionState === "Select a device") throw new Error("The map did not request the selected device console.");
  if (browserErrors.length) throw new Error(`Firefox reported errors: ${browserErrors.join(" | ")}`);
  console.log(JSON.stringify({ browser: "firefox", devices: runtime.devices.map((device) => ({ name: device.name, state: device.observed_readiness,
    position: device.position, worker: device.worker_placement })), link: `${runtime.links[0].endpoint_a.node}:${runtime.links[0].endpoint_a.interface} ↔ ${runtime.links[0].endpoint_b.node}:${runtime.links[0].endpoint_b.interface}`,
    linkState: "healthy", inspector: "r1", consoleState: connectionState, screenshot: output }));
} finally {
  await browser.close();
}
