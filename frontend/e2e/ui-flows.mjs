// End-to-end UI check + screenshots against a running server with demo data.
// Usage: node e2e/ui-flows.mjs http://localhost:8000 ../docs/screenshots
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { chromium, devices } = require("playwright");

const BASE = process.argv[2] || "http://localhost:8000";
const OUT = process.argv[3] || "../docs/screenshots";
const errors = [];
let step = 0;
const ok = (msg) => console.log(`  ✓ ${++step}. ${msg}`);

async function login(page) {
  await page.goto(BASE + "/");
  await page.getByLabel("Email").fill("owner@demo.restaurant");
  await page.getByLabel("Password").fill("demo-owner-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("heading", { name: "Dashboard" }).waitFor();
}

function watch(page, label) {
  page.on("console", (m) => m.type() === "error" && errors.push(`[${label}] console: ${m.text()}`));
  page.on("pageerror", (e) => errors.push(`[${label}] pageerror: ${e.message}`));
}

const browser = await chromium.launch();
try {
  // ── desktop ──
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  watch(page, "desktop");
  await page.goto(BASE + "/");
  await page.getByRole("heading", { name: "Sign in" }).waitFor();
  await page.screenshot({ path: `${OUT}/01-login.png` });
  await login(page);
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: `${OUT}/02-dashboard.png`, fullPage: true });
  ok("login + dashboard");

  await page.goto(BASE + "/ingredients");
  await page.getByRole("link", { name: "Beef (stewing)" }).waitFor();
  await page.screenshot({ path: `${OUT}/03-ingredients.png`, fullPage: true });
  await page.getByRole("link", { name: "Beef (stewing)" }).click();
  await page.getByRole("tab", { name: /Stock batches/ }).click();
  await page.getByText("Open batches — used oldest first").waitFor();
  await page.screenshot({ path: `${OUT}/04-ingredient-fifo-batches.png`, fullPage: true });
  await page.getByRole("tab", { name: "Purchase price history" }).click();
  await page.screenshot({ path: `${OUT}/05-ingredient-price-history.png`, fullPage: true });
  ok("ingredient list, FIFO batches, price history");

  await page.goto(BASE + "/ingredients?tab=recipes");
  await page.getByRole("link", { name: "Beef stew with rice" }).click();
  await page.getByText("Ingredient costs").waitFor();
  await page.screenshot({ path: `${OUT}/06-recipe-costing.png`, fullPage: true });
  await page.getByRole("tab", { name: "Cost history" }).click();
  await page.getByText("Cost snapshots").waitFor();
  await page.screenshot({ path: `${OUT}/07-recipe-cost-history.png`, fullPage: true });
  await page.getByRole("link", { name: "Create new version" }).click();
  await page.getByRole("heading", { name: /Edit Beef stew/ }).waitFor();
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/08-recipe-editor-live-cost.png`, fullPage: true });
  ok("recipe costing, cost history, editor");

  // UI flow: record a purchase end-to-end
  await page.goto(BASE + "/purchases/new");
  await page.getByLabel("Supplier").click();
  await page.getByRole("option", { name: "Kampala Quality Meats" }).click();
  await page.getByLabel("Invoice number").fill("E2E-" + Date.now().toString().slice(-6));
  await page.getByPlaceholder("Ingredient…").first().click();
  await page.getByRole("option", { name: /Beef \(stewing\)/ }).click();
  await page.getByLabel("Quantity", { exact: true }).first().fill("12");
  await page.getByLabel("Unit price").first().fill("19000");
  await page.getByRole("button", { name: "+ Add item" }).click();
  await page.getByPlaceholder("Ingredient…").nth(1).click();
  await page.getByRole("option", { name: /Chicken/ }).click();
  await page.getByLabel("Quantity", { exact: true }).nth(1).fill("6");
  await page.getByLabel("Unit price").nth(1).fill("15000");
  await page.getByLabel("Total printed on invoice", { exact: true }).fill("318000");
  await page.screenshot({ path: `${OUT}/09-new-purchase.png`, fullPage: true });
  await page.getByRole("button", { name: "Review & confirm" }).click();
  await page.getByRole("dialog").waitFor();
  await page.screenshot({ path: `${OUT}/10-purchase-confirm.png` });
  await page.getByRole("button", { name: /^Confirm UGX/ }).click();
  await page.getByText(/confirmed — stock added/).waitFor();
  await page.getByRole("heading", { name: /Invoice E2E-/ }).waitFor();
  await page.screenshot({ path: `${OUT}/11-purchase-detail.png`, fullPage: true });
  ok("record purchase via UI (confirm → detail, success toast only after server response)");

  // pay it partially through the UI
  await page.getByRole("button", { name: "Record payment" }).click();
  await page.getByLabel("Amount paid").fill("100000");
  await page.getByLabel("Method").selectOption("mobile_money");
  await page.getByLabel("Reference").fill("MTN-E2E-" + Date.now().toString().slice(-5));
  await page.getByRole("button", { name: "Post payment" }).click();
  await page.getByText(/Payment of UGX 100,000 posted/).waitFor();
  await page.getByText("partially paid").first().waitFor();
  ok("partial payment via UI → invoice shows partially paid");

  await page.goto(BASE + "/suppliers");
  await page.screenshot({ path: `${OUT}/12-suppliers.png`, fullPage: true });
  await page.getByRole("link", { name: "Kampala Quality Meats" }).click();
  await page.getByRole("tab", { name: "Statement" }).click();
  await page.getByText("Account statement").waitFor();
  await page.screenshot({ path: `${OUT}/13-supplier-statement.png`, fullPage: true });
  ok("supplier profile + statement");

  await page.goto(BASE + "/stock");
  await page.getByText("Inventory value").first().waitFor();
  await page.screenshot({ path: `${OUT}/14-stock-levels.png`, fullPage: true });
  // UI flow: record usage
  await page.getByRole("tab", { name: "Record usage" }).click();
  await page.getByPlaceholder("Ingredient…").first().click();
  await page.getByRole("option", { name: "Beef (stewing)" }).click();
  await page.getByLabel("Quantity").first().fill("1500");
  await page.getByRole("button", { name: "Deduct from stock" }).click();
  await page.getByText(/Usage recorded — FIFO cost/).waitFor();
  await page.screenshot({ path: `${OUT}/15-usage-fifo-result.png`, fullPage: true });
  ok("record usage via UI → FIFO allocation shown");
  await page.getByRole("tab", { name: "Production" }).click();
  await page.getByPlaceholder("Choose recipe…").click();
  await page.getByRole("option", { name: /Beef stew/ }).click();
  await page.getByText("Will deduct").waitFor();
  await page.screenshot({ path: `${OUT}/16-production.png`, fullPage: true });
  await page.getByRole("tab", { name: "Stock counts" }).click();
  await page.getByRole("cell", { name: /Weekly dry-store count/ }).click();
  await page.getByText("Net variance value").waitFor();
  await page.screenshot({ path: `${OUT}/17-stock-count.png`, fullPage: true });
  await page.goto(BASE + "/stock?tab=history");
  await page.getByText("Stock movement history").waitFor();
  await page.screenshot({ path: `${OUT}/18-movements.png`, fullPage: true });
  ok("production preview, stock count, movement history");

  await page.goto(BASE + "/reports");
  await page.getByText(/Inventory valuation —/).waitFor();
  await page.screenshot({ path: `${OUT}/19-report-valuation.png`, fullPage: true });
  await page.getByRole("tab", { name: "Data checks" }).click();
  await page.getByText("All checks passed").waitFor();
  await page.screenshot({ path: `${OUT}/20-integrity-checks.png`, fullPage: true });
  ok("reports + integrity checks pass after UI transactions");

  await page.goto(BASE + "/settings");
  await page.getByText("Users").first().waitFor();
  await page.screenshot({ path: `${OUT}/21-settings-users.png`, fullPage: true });
  await ctx.close();

  // ── phone ──
  const m = await browser.newContext({ ...devices["iPhone 13"] });
  const mp = await m.newPage();
  watch(mp, "mobile");
  await login(mp);
  await mp.waitForLoadState("networkidle");
  await mp.screenshot({ path: `${OUT}/m1-dashboard.png`, fullPage: true });
  await mp.goto(BASE + "/purchases/new");
  await mp.getByRole("heading", { name: "Record purchase" }).waitFor();
  await mp.screenshot({ path: `${OUT}/m2-new-purchase.png`, fullPage: true });
  await mp.goto(BASE + "/stock");
  await mp.getByText("Inventory value").first().waitFor();
  await mp.screenshot({ path: `${OUT}/m3-stock.png`, fullPage: true });
  await mp.goto(BASE + "/ingredients?tab=recipes");
  await mp.getByRole("link", { name: "Beef stew with rice" }).click();
  await mp.getByText("Ingredient costs").waitFor();
  await mp.screenshot({ path: `${OUT}/m4-recipe.png`, fullPage: true });
  const overflow = await mp.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  if (overflow > 1) errors.push(`mobile horizontal overflow ${overflow}px`);
  ok(`phone layout (horizontal overflow: ${overflow}px)`);

  // ── tablet ──
  const t = await browser.newContext({ ...devices["iPad (gen 7)"] });
  const tp = await t.newPage();
  watch(tp, "tablet");
  await login(tp);
  await tp.waitForLoadState("networkidle");
  await tp.screenshot({ path: `${OUT}/t1-dashboard-tablet.png` });
  ok("tablet layout");

  // ── server unreachable: indicator + draft kept, nothing posted ──
  const o = await browser.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 2 });
  const op = await o.newPage();
  await login(op);
  await op.goto(BASE + "/purchases/new");
  await op.getByLabel("Invoice number").fill("OFFLINE-TEST");
  await op.waitForTimeout(600);
  await op.route("**/api/**", (r) => r.abort("internetdisconnected"));
  await op.reload();
  await op.getByText("Server unreachable — drafts only").waitFor({ timeout: 20000 });
  await op.getByText("Restored your unsaved purchase from this device").waitFor();
  const kept = await op.getByLabel("Invoice number").inputValue();
  if (kept !== "OFFLINE-TEST") errors.push("draft not restored from IndexedDB");
  await op.screenshot({ path: `${OUT}/22-server-unreachable-draft-kept.png` });
  ok("server unreachable → red indicator, draft restored from IndexedDB");
  await o.close();
} finally {
  await browser.close();
}
const real = errors.filter((e) => !/Failed to load resource|ERR_INTERNET_DISCONNECTED|net::ERR/.test(e));
if (real.length) { console.error("UI errors:\n" + real.join("\n")); process.exit(1); }
console.log(`All ${step} UI checks passed.`);
