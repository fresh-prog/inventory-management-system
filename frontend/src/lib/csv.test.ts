import { describe, expect, it } from "vitest";
import { toCsv } from "../pages/Reports";

describe("CSV export", () => {
  it("neutralises spreadsheet formulas but keeps numbers", () => {
    const csv = toCsv([{ a: '=HYPERLINK("http://evil","x")', b: "-12.5", c: "+cmd", d: "@SUM(1)", e: 'say "hi"' }],
      [["a", "A"], ["b", "B"], ["c", "C"], ["d", "D"], ["e", "E"]]);
    const row = csv.split("\n")[1];
    expect(row).toContain(`"'=HYPERLINK(""http://evil"",""x"")"`);
    expect(row).toContain('"-12.5"');
    expect(row).toContain(`"'+cmd"`);
    expect(row).toContain(`"'@SUM(1)"`);
    expect(row).toContain('"say ""hi"""');
  });
});
