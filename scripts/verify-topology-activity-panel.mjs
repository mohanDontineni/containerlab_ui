import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const { firefox } = createRequire(import.meta.url)("playwright");
const base = (process.env.TRAINING_BASE_URL || "").replace(/\/$/, "");
const output = path.resolve("training/85-topology-jobs-events-panel.png");
if (!base) throw new Error("Set TRAINING_BASE_URL");
const browser = await firefox.launch({ headless:true });
const context = await browser.newContext({ ignoreHTTPSErrors:true,viewport:{width:1600,height:1050},colorScheme:"dark" });
const page = await context.newPage(),errors=[];
page.on("console",message=>{if(message.type()==="error")errors.push(message.text())});page.on("pageerror",error=>errors.push(error.message));
try{
  await page.goto(`${base}/accounts/login/`,{waitUntil:"networkidle"});await page.locator("#id_username").fill(process.env.TRAINING_USERNAME||"");await page.locator("#id_password").fill(process.env.TRAINING_PASSWORD||"");
  await Promise.all([page.waitForURL(url=>!url.pathname.includes("/accounts/login/")),page.getByRole("button",{name:/sign in|log in/i}).click()]);
  await page.goto(`${base}/labs/`,{waitUntil:"networkidle"});const hrefs=await page.locator('a[href*="/workspace/"]').evaluateAll(links=>links.map(link=>link.getAttribute("href")).filter(Boolean));
  let workspace="",activity=null;for(const href of hrefs){const id=href.match(/\/labs\/([0-9a-f-]+)\/workspace\//i)?.[1];if(!id)continue;const candidate=await page.evaluate(async labId=>{const topology=await (await fetch(`/api/v1/labs/${labId}/topology/`,{cache:"no-store"})).json();const response=await fetch(`/api/v1/labs/${labId}/activity/`,{cache:"no-store"});return {topology,activity:response.ok?await response.json():null}},id);if(candidate.topology.nodes?.some(node=>node.name==="r1")&&candidate.activity?.items?.some(item=>item.kind==="job")&&candidate.activity.items.some(item=>item.kind==="event")){workspace=href;activity=candidate.activity;break}}
  if(!workspace||!activity)throw new Error("No BGP workspace with both retained jobs and events was available.");
  const serialized=JSON.stringify(activity);for(const forbidden of ["request_payload","result_payload","metadata","encrypted_content"])if(serialized.includes(forbidden))throw new Error(`Activity leaked ${forbidden}`);
  if(activity.items.length>24||activity.bounds.items!==24)throw new Error("Activity bounds were not enforced.");
  await page.goto(`${base}${workspace}`,{waitUntil:"networkidle"});const editor=page.frameLocator('iframe[title^="Topology workspace"]');await editor.locator(".workspace-shell").waitFor();
  const panel=editor.locator(".topology-activity");await panel.getByText("Jobs & events",{exact:true}).waitFor();await panel.locator("header button").click();await panel.locator("article").first().waitFor();
  const text=await panel.innerText();for(const phrase of ["Current lab only","payloads excluded","Open full job center"]){if(!text.includes(phrase))throw new Error(`Panel missing ${phrase}`)}
  if(!/\d+%/.test(text)||!text.includes("recorded"))throw new Error(`Panel did not render both job progress and audit state: ${text}`);
  await editor.locator(".workspace-shell").screenshot({path:output});if(errors.length)throw new Error(`Firefox reported errors: ${errors.join(" | ")}`);
  console.log(JSON.stringify({browser:"Firefox",workspace,jobs:activity.items.filter(item=>item.kind==="job").length,events:activity.items.filter(item=>item.kind==="event").length,visibleRows:await panel.locator("article").count(),bounds:activity.bounds,screenshot:output}));
}finally{await browser.close()}
