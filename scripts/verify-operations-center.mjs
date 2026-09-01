import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require=createRequire(import.meta.url);
const {firefox}=require("playwright");
const baseUrl=(process.env.TRAINING_BASE_URL||"").replace(/\/$/,"");
const username=process.env.TRAINING_USERNAME||"";
const password=process.env.TRAINING_PASSWORD||"";
const output=path.resolve(process.env.OPERATIONS_CENTER_SCREENSHOT||"training/64-operations-job-center.png");
if(!baseUrl||!username||!password) throw new Error("Set TRAINING_BASE_URL, TRAINING_USERNAME, and TRAINING_PASSWORD.");

const browser=await firefox.launch({headless:true});
const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1600,height:1000},colorScheme:"dark"});
const page=await context.newPage();
try {
  await page.goto(`${baseUrl}/accounts/login/`,{waitUntil:"networkidle"});
  await page.locator("#id_username").fill(username);await page.locator("#id_password").fill(password);
  await Promise.all([page.waitForURL(url=>!url.pathname.includes("/accounts/login/")),page.getByRole("button",{name:/sign in|log in/i}).click()]);
  await page.goto(`${baseUrl}/operations/?state=failed&type=capture_packets&q=BGP`,{waitUntil:"networkidle"});
  const matchingJobs=await page.locator('.operation-center-rows article').count();
  if(matchingJobs<1) throw new Error("Expected at least one production BGP capture failure");
  const row=page.locator('.operation-center-rows article').first();
  await row.locator("details").evaluate(element=>element.open=true);
  const evidence=await row.locator("details").innerText();
  if(!evidence.includes("CapabilityError")||!evidence.includes("capture stream")) throw new Error("Bounded failure evidence was not rendered");
  const html=await page.locator("body").innerText();
  if(/request_payload|result_payload|authorization/i.test(html)) throw new Error("Private operation fields leaked into the job center");
  const action=row.getByRole("link",{name:/Open deployment/i});const href=await action.getAttribute("href");
  await page.screenshot({path:output,fullPage:true});
  await Promise.all([page.waitForURL(url=>url.pathname===href),action.click()]);
  const recoveryTitle=await page.locator("h1").innerText();
  console.log(JSON.stringify({matchingJobs,evidence:evidence.replaceAll("\n"," · "),recoveryHref:href,recoveryTitle,screenshot:output}));
} finally { await browser.close(); }
