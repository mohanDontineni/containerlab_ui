import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const { firefox } = createRequire(import.meta.url)("playwright");
const base = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const deployment = process.env.METRICS_DEPLOYMENT_ID || "";
const output = path.resolve("training/84-multi-device-split-console.png");
if (!base || !deployment) throw new Error("Set TRAINING_BASE_URL and METRICS_DEPLOYMENT_ID");
await mkdir(path.dirname(output), { recursive: true });
const browser = await firefox.launch({ headless: true });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1000 }, colorScheme: "dark" });
const page = await context.newPage(), browserErrors = [], authorized = [], revoked = [];
async function waitCount(locator,count){for(let attempt=0;attempt<100;attempt++){if(await locator.count()===count)return;await new Promise(resolve=>setTimeout(resolve,50))}throw new Error(`Expected ${count} console tabs.`)}
page.on("console", message => { if (message.type() === "error") browserErrors.push(message.text()); });
page.on("pageerror", error => browserErrors.push(error.message));
page.on("response", async response => {
  if (response.request().method() === "POST" && response.url().endsWith(`/deployments/${deployment}/consoles/`) && response.status() === 201) authorized.push((await response.json()).id);
  if (response.request().method() === "DELETE" && response.url().includes(`/deployments/${deployment}/consoles/`) && response.status() === 204) revoked.push(response.url());
});
try {
  await page.goto(`${base}/accounts/login/`);
  await page.locator("#id_username").fill(process.env.TRAINING_USERNAME || "");
  await page.locator("#id_password").fill(process.env.TRAINING_PASSWORD || "");
  await Promise.all([page.waitForURL(url => !url.pathname.includes("/accounts/login/")), page.getByRole("button", { name: /sign in|log in/i }).click()]);
  await page.goto(`${base}/deployments/${deployment}/`);
  const frame = page.frameLocator('iframe[title="Device consoles"]');
  await frame.getByText("Multi-device console workspace", { exact: true }).waitFor({ timeout: 20000 });
  await frame.locator('nav [data-device]').filter({ hasText: "r1" }).click();
  await frame.locator('#open-console-tabs [data-console-tab]').filter({ hasText: "r1" }).getByText("connected", { exact: true }).waitFor({ timeout: 20000 });
  await frame.locator('nav [data-device]').filter({ hasText: "r2" }).click();
  await frame.locator('#open-console-tabs [data-console-tab]').filter({ hasText: "r2" }).getByText("connected", { exact: true }).waitFor({ timeout: 20000 });
  await frame.getByRole("button", { name: /split/i }).click();
  await frame.locator(".console-pane").nth(1).waitFor();
  if (await frame.locator(".console-pane").count() !== 2) throw new Error("Split layout did not render two console panes.");
  for (const name of ["r1", "r2"]) {
    const pane = frame.locator(".console-pane").filter({ has: frame.locator(`.console-pane-heading strong:text-is("${name}")`) });
    await pane.locator(".xterm-helper-textarea").focus();
    await page.keyboard.type("hostname");await page.keyboard.press("Enter");
    await pane.locator(".xterm-rows").getByText(name, { exact: true }).last().waitFor({ timeout: 10000 });
  }
  await page.locator(".runtime-console").screenshot({ path: output });
  const tabs = frame.locator('#open-console-tabs [data-console-tab]');
  await tabs.first().locator("[data-close-console]").click();await waitCount(tabs,1);
  await tabs.first().locator("[data-close-console]").click();await waitCount(tabs,0);
  for (let attempt=0;attempt<20&&revoked.length<2;attempt++)await new Promise(resolve=>setTimeout(resolve,100));
  if (authorized.length !== 2 || revoked.length !== 2) throw new Error(`Expected two authorized and revoked sessions; got ${authorized.length}/${revoked.length}.`);
  if (browserErrors.length) throw new Error(`Firefox reported errors: ${browserErrors.join(" | ")}`);
  console.log(JSON.stringify({ browser:"Firefox",deployment,devices:["r1","r2"],layout:"split",commands:["hostname","hostname"],authorized:authorized.length,revoked:revoked.length,screenshot:output }));
} finally { await browser.close(); }
