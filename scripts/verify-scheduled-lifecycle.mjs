import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { firefox } = require("playwright");

const baseUrl = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const username = process.env.TRAINING_USERNAME || "";
const password = process.env.TRAINING_PASSWORD || "";
const deploymentId = process.env.SCHEDULE_DEPLOYMENT_ID || "";
const output = path.resolve(process.env.SCHEDULE_SCREENSHOT || "training/51-scheduled-lab-lifecycle.png");
if (!baseUrl || !username || !password || !deploymentId) {
  throw new Error("Set TRAINING_BASE_URL, TRAINING_USERNAME, TRAINING_PASSWORD, and SCHEDULE_DEPLOYMENT_ID.");
}

const browser = await firefox.launch({ headless: true });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1000 }, colorScheme: "dark" });
const page = await context.newPage();
const browserErrors = [];
page.on("console", (message) => { if (message.type() === "error") browserErrors.push(`console: ${message.text()}`); });
page.on("pageerror", (error) => browserErrors.push(`page: ${error.message}`));
page.on("response", (response) => { if (response.status() >= 400) browserErrors.push(`http ${response.status()}: ${response.url()}`); });

const runtimeUrl = `${baseUrl}/api/v1/deployments/${deploymentId}/runtime/`;
const runtime = async () => (await context.request.get(runtimeUrl, { failOnStatusCode: true })).json();
const localMinute = (milliseconds) => {
  const date = new Date(milliseconds);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
};
const createSchedule = async (action, milliseconds) => {
  await page.locator("#new-runtime-schedule").click();
  await page.locator("#runtime-schedule-dialog").waitFor({ state: "visible" });
  await page.locator("#schedule-action").selectOption(action);
  await page.locator("#schedule-execute-at").fill(localMinute(milliseconds));
  await page.locator("#confirm-runtime-schedule").click();
  await page.locator("#runtime-schedule-dialog").waitFor({ state: "hidden" });
};

try {
  await page.goto(`${baseUrl}/accounts/login/`, { waitUntil: "domcontentloaded" });
  await page.locator("#id_username").fill(username);
  await page.locator("#id_password").fill(password);
  await Promise.all([
    page.waitForURL((url) => !url.pathname.includes("/accounts/login/")),
    page.getByRole("button", { name: /sign in|log in/i }).click(),
  ]);
  await page.goto(`${baseUrl}/deployments/${deploymentId}/`, { waitUntil: "domcontentloaded" });
  await page.locator("#runtime-schedules").waitFor({ state: "visible" });
  const before = await runtime();
  if (before.deployment.observed_state !== "stopped") throw new Error(`Expected a stopped acceptance runtime, found ${before.deployment.observed_state}.`);
  const existing = new Set(before.schedules.map((schedule) => schedule.id));

  await createSchedule("stop_lab", Date.now() + 10 * 60_000);
  let observed = await runtime();
  const cancellable = observed.schedules.find((schedule) => !existing.has(schedule.id) && schedule.action === "stop_lab");
  if (!cancellable || cancellable.status !== "pending") throw new Error("The cancellable schedule was not persisted as pending.");
  await page.locator(`[data-cancel-schedule="${cancellable.id}"]`).click();

  await createSchedule("start_lab", Date.now() + 100_000);
  observed = await runtime();
  const scheduledStart = observed.schedules.find((schedule) => !existing.has(schedule.id) && schedule.action === "start_lab");
  if (!scheduledStart || scheduledStart.status !== "pending") throw new Error("The start schedule was not persisted as pending.");

  let linkedOperation = null;
  for (let attempt = 0; attempt < 150; attempt += 1) {
    await page.waitForTimeout(1500);
    observed = await runtime();
    const currentSchedule = observed.schedules.find((schedule) => schedule.id === scheduledStart.id);
    linkedOperation = currentSchedule?.operation ? observed.operations.find((operation) => operation.id === currentSchedule.operation) : null;
    if (currentSchedule?.status === "skipped") throw new Error("The scheduled start was skipped by the eligibility recheck.");
    if (linkedOperation?.state === "failed") throw new Error(linkedOperation.error_details?.message || "The scheduled start failed.");
    if (currentSchedule?.status === "dispatched" && linkedOperation?.state === "succeeded" && observed.deployment.observed_state === "running") break;
    if (attempt === 149) throw new Error("The scheduled start did not reach a running state in time.");
  }

  await page.locator("#refresh-runtime").click();
  await page.waitForTimeout(1000);
  const schedulePanel = page.locator(".runtime-schedules");
  await schedulePanel.scrollIntoViewIfNeeded();
  await schedulePanel.screenshot({ path: output });

  page.once("dialog", (dialog) => dialog.accept());
  await page.locator("#stop-runtime").click();
  for (let attempt = 0; attempt < 90; attempt += 1) {
    await page.waitForTimeout(1500);
    observed = await runtime();
    if (observed.deployment.observed_state === "stopped") break;
    const stop = observed.operations.find((operation) => operation.operation_type === "stop_lab");
    if (stop?.state === "failed") throw new Error(stop.error_details?.message || "Runtime restoration failed.");
    if (attempt === 89) throw new Error("The acceptance runtime was not restored to stopped.");
  }
  if (browserErrors.length) throw new Error(`Firefox reported errors: ${browserErrors.join(" | ")}`);
  console.log(JSON.stringify({ browser: "firefox", cancelledSchedule: cancellable.id, dispatchedSchedule: scheduledStart.id,
    linkedOperation: linkedOperation?.id, operationState: linkedOperation?.state, restoredState: observed.deployment.observed_state, screenshot: output }));
} finally {
  await browser.close();
}
