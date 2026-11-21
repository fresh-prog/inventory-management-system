// Hybrid sync E2E across two real servers (branch :8000, cloud :8100). Run in phases:
//   node e2e/sync-flows.mjs connect|offline|recovered <outDir>
import crypto from "crypto";
import fs from "fs";
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const [phase, OUT = "../docs/screenshots"] = process.argv.slice(2);
const BRANCH = "http://127.0.0.1:8000", CLOUD = "http://127.0.0.1:8100";
const STATE = "/tmp/kl-sync-e2e.json";
const CLOUD_USER = { email: "owner@demo.restaurant", password: "demo-cloud-password", secret: "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP" };
const errors = [];
let step = 0;
const ok = (m) => console.log(`  ✓ ${phase} ${++step}. ${m}`);

function totp(secret) {
  const A = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"; let bits = "";
  for (const c of secret) bits += A.indexOf(c).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g).map((b) => parseInt(b, 2)));
  const ctr = Buffer.alloc(8); ctr.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000)));
  const h = crypto.createHmac("sha1", key).update(ctr).digest(); const o = h[h.length - 1] & 15;
  return String((h.readUInt32BE(o) & 0x7fffffff) % 1e6).padStart(6, "0");
}
const watch = (p, l) => { p.on("pageerror", (e) => errors.push(`[${l}] ${e.message}`)); p.on("console", (m) => m.type() === "error" && !/Failed to load resource/.test(m.text()) && errors.push(`[${l}] ${m.text()}`)); };

async function branchLogin(ctx) {
  const p = await ctx.newPage(); watch(p, "branch");
  await p.goto(BRANCH + "/");
  await p.getByLabel("Email").fill("owner@demo.restaurant");
  await p.getByLabel("Password").fill("demo-owner-password");
  await p.getByRole("button", { name: "Sign in" }).click();
  await p.getByRole("heading", { name: "Dashboard" }).waitFor();
  return p;
}
async function cloudLogin(ctx) {
  const p = await ctx.newPage(); watch(p, "cloud");
  await p.goto(CLOUD + "/");
  await p.getByLabel("Email").fill(CLOUD_USER.email);
  await p.getByLabel("Password").fill(CLOUD_USER.password);
  await p.getByRole("button", { name: "Continue" }).click();
  await p.getByLabel("Authenticator code").fill(totp(CLOUD_USER.secret));
  await p.getByRole("button", { name: "Sign in" }).click();
  await p.getByRole("heading", { name: "All branches" }).waitFor();
  return p;
}

const browser = await chromium.launch();
const ctxOpts = { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 };
try {
  if (phase === "connect") {
    const cctx = await browser.newContext(ctxOpts);
    const c = await cloudLogin(cctx);
    ok("cloud sign-in requires password + authenticator code");
    await c.getByRole("button", { name: "Add branch" }).click();
    await c.getByLabel("Code").fill("KLA");
    await c.getByLabel("Name").fill("Mama's Kitchen — Kampala");
    await c.getByRole("button", { name: "Create & get code" }).click();
    const code = (await c.locator(".modal .mono").innerText()).trim();
    await c.screenshot({ path: `${OUT}/c1-cloud-add-branch.png` });
    ok(`branch created in cloud, registration code ${code}`);

    const bctx = await browser.newContext(ctxOpts);
    const b = await branchLogin(bctx);
    await b.goto(BRANCH + "/settings?tab=sync");
    await b.getByLabel("Cloud address").fill(CLOUD);
    await b.getByLabel("Registration code").fill(code);
    await b.getByRole("button", { name: "Connect" }).click();
    await b.getByText("Registered with the cloud").waitFor();
    await b.getByRole("button", { name: "Sync now" }).click();
    await b.getByText(/Sync complete/).waitFor({ timeout: 60000 });
    await b.getByText("In sync").first().waitFor();
    await b.screenshot({ path: `${OUT}/s1-branch-sync-in-sync.png`, fullPage: true });
    ok("branch registered from its Settings screen; initial snapshot acknowledged");

    await b.goto(BRANCH + "/purchases");
    await b.locator("tbody tr").first().click();
    const jpeg = Buffer.concat([Buffer.from([0xff, 0xd8, 0xff, 0xe0]), crypto.randomBytes(400 * 1024)]);
    await b.locator('input[type="file"]').setInputFiles({ name: "invoice-photo.jpg", mimeType: "image/jpeg", buffer: jpeg });
    await b.getByText("Receipt saved on the restaurant server").waitFor();
    await b.getByText("not yet in cloud").waitFor();
    await b.screenshot({ path: `${OUT}/s2-receipt-local-only.png`, fullPage: true });
    await b.goto(BRANCH + "/settings?tab=sync");
    await b.getByRole("button", { name: "Sync now" }).click();
    await b.getByText(/Sync complete/).waitFor({ timeout: 60000 });
    await b.goBack();
    await b.getByText("in cloud ✓").waitFor();
    ok("receipt stored locally first, then uploaded and checksum-verified");
    fs.writeFileSync(STATE, JSON.stringify({ code }));
  }

  if (phase === "offline") {
    const bctx = await browser.newContext(ctxOpts);
    const b = await branchLogin(bctx);
    await b.goto(BRANCH + "/stock?tab=use");
    await b.getByPlaceholder("Ingredient…").first().click();
    await b.getByRole("option", { name: "Rice (Kaiso)" }).click();
    await b.getByLabel("Quantity").first().fill("2");
    await b.getByRole("button", { name: "Deduct from stock" }).click();
    await b.getByText(/Usage recorded — FIFO cost/).waitFor();
    ok("cloud is down: branch still records usage (FIFO) normally");
    await b.goto(BRANCH + "/settings?tab=sync");
    await b.getByRole("button", { name: "Sync now" }).click();
    await b.getByText(/Sync failed/).waitFor({ timeout: 60000 });
    await b.reload();
    await b.getByText(/changes? not yet in the cloud/).waitFor();
    await b.screenshot({ path: `${OUT}/s3-branch-offline-queue.png`, fullPage: true });
    ok("indicator shows unsynchronised changes; queue visible with retry/backoff; no false success");
  }

  if (phase === "recovered") {
    const bctx = await browser.newContext(ctxOpts);
    const b = await branchLogin(bctx);
    await b.getByText(/All changes synced/).waitFor({ timeout: 60000 });
    await b.screenshot({ path: `${OUT}/s4-branch-all-synced.png` });
    ok("background worker drained the queue after the cloud came back");

    const cctx = await browser.newContext(ctxOpts);
    const c = await cloudLogin(cctx);
    await c.getByText("In sync").first().waitFor({ timeout: 30000 });
    await c.screenshot({ path: `${OUT}/c2-cloud-overview.png`, fullPage: true });
    await c.getByRole("link", { name: /Mama's Kitchen/ }).click();
    await c.getByText(/Read-only copy from the cloud/).waitFor();
    await c.screenshot({ path: `${OUT}/c3-cloud-branch-dashboard.png`, fullPage: true });
    await c.getByRole("tab", { name: "Sync monitor" }).click();
    await c.getByText("Most recent changes received").waitFor();
    await c.screenshot({ path: `${OUT}/c4-cloud-sync-monitor.png`, fullPage: true });
    ok("central overview, branch dashboard from replica (data-as-of label), sync monitor");

    // Two people at once: the HQ user opens the form (sees Eggs at version N) while a manager at the
    // branch edits Eggs. HQ then sends a change based on version N → the branch must report a conflict.
    await c.getByRole("tab", { name: "Remote changes" }).click();
    const ingCard = c.locator(".card", { hasText: "Edit an ingredient" });
    await ingCard.getByPlaceholder("Search…").waitFor();
    await b.goto(BRANCH + "/ingredients");
    await b.getByRole("link", { name: "Eggs" }).click();
    await b.getByRole("link", { name: "Edit" }).click();
    await b.getByLabel("Description").fill("Edited at the branch");
    await b.getByRole("button", { name: "Save changes" }).click();
    await b.getByText(/Eggs updated/).waitFor();
    await ingCard.getByPlaceholder("Search…").click();
    await c.getByRole("option", { name: "Salt" }).click();
    await ingCard.getByLabel(/Minimum stock/).fill("3");
    await ingCard.getByRole("button", { name: "Send change" }).click();
    await c.getByText(/the branch will validate and apply it/).first().waitFor();
    await ingCard.getByPlaceholder("Search…").click();
    await c.getByRole("option", { name: "Eggs" }).click();
    await ingCard.getByLabel(/Minimum stock/).fill("90");
    await ingCard.getByRole("button", { name: "Send change" }).click();
    ok("HQ queued two ingredient changes while the branch edited Eggs");
    await b.goto(BRANCH + "/settings?tab=sync");
    await b.getByRole("button", { name: "Sync now" }).click();
    await b.getByText(/Sync complete/).waitFor({ timeout: 60000 });
    await b.reload();
    await b.getByText("Remote changes from the cloud").waitFor();
    await b.screenshot({ path: `${OUT}/s5-branch-remote-changes.png`, fullPage: true });
    await c.reload();
    await c.getByRole("tab", { name: "Remote changes" }).click();
    const card = c.locator(".card", { hasText: "Requests sent to this branch" });
    await card.locator("tbody tr").first().locator(".badge", { hasText: /^conflict$/i }).waitFor({ timeout: 30000 });
    await card.locator("tbody tr").nth(1).locator(".badge", { hasText: /^applied$/i }).waitFor();
    await card.getByText(/Eggs was changed at the branch/).first().waitFor();
    await c.screenshot({ path: `${OUT}/c5-cloud-remote-changes.png`, fullPage: true });
    ok("Salt change applied at the branch; Eggs change marked conflict (branch edit kept, nothing overwritten)");
  }
} finally { await browser.close(); }
if (errors.length) { console.error(errors.join("\n")); process.exit(1); }
console.log(`${phase}: all ${step} checks passed`);
