import { describe, expect, it } from "vitest";

import { centsToInput, formatCents, parseDollars } from "./money";

describe("formatCents", () => {
  it.each([
    [0, "$0.00"],
    [5, "$0.05"],
    [123456, "$1,234.56"],
    [-450, "-$4.50"],
    [100000000, "$1,000,000.00"],
  ])("%i -> %s", (cents, text) => {
    expect(formatCents(cents)).toBe(text);
  });
});

describe("parseDollars", () => {
  it.each([
    ["12.34", 1234],
    ["$1,234.5", 123450],
    ["0.1", 10],
    ["-4.50", -450],
    ["7", 700],
    // 0.1 + 0.2 in floating point is 0.30000000000000004; string parsing is exact.
    ["0.30", 30],
  ])("%s -> %i", (text, cents) => {
    expect(parseDollars(text)).toBe(cents);
  });

  it.each(["", "abc", "1.234", "1.2.3", "12,34.5x"])("rejects %s", (text) => {
    expect(parseDollars(text)).toBeNull();
  });
});

describe("centsToInput", () => {
  it("round-trips with parseDollars", () => {
    for (const cents of [0, 1, 99, 100, 123456, -450]) {
      expect(parseDollars(centsToInput(cents))).toBe(cents);
    }
  });
});
