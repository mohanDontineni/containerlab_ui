import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require=createRequire(import.meta.url);
const {firefox}=require("playwright");
const baseUrl=(process.env.TRAINING_BASE_URL||"").replace(/\/$/,"");
const username=process.env.TRAINING_USERNAME||"";
const password=process.env.TRAINING_PASSWORD||"";
const output=path.resolve(process.env.DASHBOARD_CAPACITY_SCREENSHOT||"training/63-dashboard-failure-capacity.png");
if(!baseUrl||!username||!password) throw new Error("Set TRAINING_BASE_URL, TRAINING_USERNAME, and TRAINING_PASSWORD.");

const browser=await firefox.launch({headless:true});
const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1600,height:1200},colorScheme:"dark"});
const page=await context.newPage();
try {
  await page.goto(`${baseUrl}/accounts/login/`,{waitUntil:"networkidle"});
  await page.locator("#id_username").fill(username);await page.locator("#id_password").fill(password);
  await Promise.all([page.waitForURL(url=>!url.pathname.includes("/accounts/login/")),page.getByRole("button",{name:/sign in|log in/i}).click()]);
  await page.goto(`${baseUrl}/`,{waitUntil:"networkidle"});
  const states=(await page.locator(".state-summary").innerText()).replaceAll("\n"," · ");
  for(const label of ["running","pending or deploying","stopped or removed","degraded","failed"])
    if(!states.includes(label)) throw new Error(`Missing deployment state: ${label}`);
  const quota=await page.locator(".quota-panel").innerText();
  for(const label of ["Labs","Active runtimes","Members","Images","Upload reservations","Largest draft"])
    if(!quota.includes(label)) throw new Error(`Missing capacity evidence: ${label}`);
  if(!/Live CPU and memory usage: (Available|Unavailable)/.test(quota)) throw new Error("Telemetry availability is not explicit");
  const failure=page.locator(".failures-panel article").first();await failure.waitFor();
  const href=await failure.getByRole("link").getAttribute("href");
  if(!href?.match(/^\/deployments\/[0-9a-f-]+\/$/i)) throw new Error("Latest failure does not provide a deployment recovery action");
  await page.screenshot({path:output,fullPage:true});
  await Promise.all([page.waitForURL(url=>url.pathname===href),failure.getByRole("link").click()]);
  const recoveryTitle=await page.locator("h1").innerText();
  console.log(JSON.stringify({states,recoveryHref:href,recoveryTitle,screenshot:output}));
} finally { await browser.close(); }
