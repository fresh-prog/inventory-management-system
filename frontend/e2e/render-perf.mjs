// Frontend rendering on a large restaurant (3,000 ingredients, 10,000+ invoices), phone emulation
// with 4x CPU slowdown. Usage: node e2e/render-perf.mjs http://127.0.0.1:8202
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { chromium, devices } = require("playwright");
const BASE = process.argv[2];
const b = await chromium.launch();
const ctx = await b.newContext({ ...devices["Pixel 7"] });
const page = await ctx.newPage();
const cdp = await ctx.newCDPSession(page);
await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
await page.goto(BASE + "/");
await page.getByLabel("Email").fill("bench@example.com");
await page.getByLabel("Password").fill("bench-password-1");
let t = Date.now();
await page.getByRole("button", { name: "Sign in" }).click();
await page.getByText("Inventory value").first().waitFor({ timeout: 60000 });
const results = { "sign-in → dashboard usable": Date.now() - t };
const measure = async (label, path, ready) => {
  const t0 = Date.now(); await page.goto(BASE + path); await ready(); results[label] = Date.now() - t0;
};
await measure("ingredients (3,000) list", "/ingredients", () => page.locator("tbody tr").nth(50).waitFor({ timeout: 60000 }));
const t1 = Date.now(); await page.getByLabel("Search ingredients").fill("item 0123"); await page.locator("tbody tr").first().waitFor();
results["instant search keystroke → result"] = Date.now() - t1;
await measure("purchases page", "/purchases", () => page.locator("tbody tr").nth(10).waitFor({ timeout: 60000 }));
await measure("stock levels (3,000)", "/stock", () => page.locator("tbody tr").nth(50).waitFor({ timeout: 60000 }));
await measure("recipes (300)", "/ingredients?tab=recipes", () => page.locator("tbody tr").nth(50).waitFor({ timeout: 60000 }));
await measure("new purchase form", "/purchases/new", () => page.getByRole("heading", { name: "Record purchase" }).waitFor());
const heap = await page.evaluate(() => performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1e6) : null);
results["JS heap after tour (MB)"] = heap;
const domRows = await page.evaluate(() => document.querySelectorAll("tr").length);
await b.close();
for (const [k, v] of Object.entries(results)) console.log(`${k.padEnd(38)} ${v}${k.includes("MB") ? "" : " ms"}`);
