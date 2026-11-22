// Accessibility audit (WCAG 2.1 A/AA rules via axe-core) of every main screen, desktop + phone.
// Usage: node e2e/a11y.mjs http://localhost:8000 [out.json]
import fs from "fs";
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { chromium, devices } = require("playwright");
const { AxeBuilder } = require("@axe-core/playwright");

const BASE = process.argv[2] || "http://localhost:8000";
const OUT = process.argv[3];
const PAGES = ["/", "/ingredients", "/ingredients?tab=recipes", "/purchases", "/purchases/new", "/suppliers",
               "/stock", "/stock?tab=use", "/stock?tab=produce", "/stock?tab=waste", "/stock?tab=counts", "/reports",
               "/settings?tab=sync", "/settings?tab=users"];
const browser = await chromium.launch();
const report = [];
for (const [label, opts] of [["desktop", { viewport: { width: 1440, height: 900 } }], ["phone", devices["iPhone 13"]]]) {
  const page = await (await browser.newContext(opts)).newPage();
  await page.goto(BASE + "/");
  await page.getByLabel("Email").fill("owner@demo.restaurant");
  await page.getByLabel("Password").fill("demo-owner-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("heading", { name: "Dashboard" }).waitFor();
  const login = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  for (const path of PAGES) {
    await page.goto(BASE + path);
    await page.waitForLoadState("networkidle");
    const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    for (const v of r.violations) {
      report.push({ device: label, path, id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length,
                    sample: v.nodes[0]?.target?.join(" ") });
    }
  }
  void login;
}
await browser.close();
const key = (v) => `${v.id}`;
const grouped = {};
for (const v of report) (grouped[key(v)] ||= { ...v, paths: new Set() }).paths.add(`${v.device}:${v.path}`);
for (const g of Object.values(grouped)) console.log(`${g.impact.padEnd(9)} ${g.id}: ${g.help} — ${g.paths.size} page(s), e.g. ${g.sample}`);
console.log(`${report.length} violation instances across ${PAGES.length * 2} page views`);
if (OUT) fs.writeFileSync(OUT, JSON.stringify(report, null, 1));
process.exit(report.some((v) => v.impact === "critical" || v.impact === "serious") ? 1 : 0);
