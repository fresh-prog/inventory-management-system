import { describe, expect, it } from "vitest";
import { configureMoney, money, pct, smartQty, unitCost } from "./format";

describe("format", () => {
  it("formats money with currency and sign", () => {
    configureMoney("USD", 2);
    expect(money("145.0000")).toContain("145.00");
    expect(money("-13.5")).toMatch(/^−USD 13\.50$/);
    expect(money(null)).toBe("—");
  });
  it("gives unit costs enough precision", () => {
    configureMoney("USD", 2);
    expect(unitCost("0.009", "g")).toContain("0.0090");
  });
  it("shows friendly quantities", () => {
    expect(smartQty("15000", "g")).toContain("15");
    expect(smartQty("15000", "g")).toContain("kg");
    expect(smartQty("250", "ml")).toContain("ml");
  });
  it("formats percentages", () => expect(pct("0.306667")).toBe("30.7%"));
});
