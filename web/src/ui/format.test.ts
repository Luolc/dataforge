import { describe, expect, it } from "vitest";

import { bytes, clock, cores, epoch, instant } from "./format";

describe("the formatters the Monitor page reads", () => {
  it("write bytes in binary units", () => {
    expect(bytes(512)).toBe("512 B");
    expect(bytes(1536 * 1024 ** 2)).toBe("1.5 GiB");
    expect(bytes(128 * 1024 ** 3)).toBe("128 GiB");
  });

  it("write cores with a decimal only where it says something", () => {
    expect(cores(0)).toBe("0 cores");
    expect(cores(1.54)).toBe("1.5 cores");
    expect(cores(20)).toBe("20 cores");
  });

  it("write seconds since the epoch in UTC", () => {
    const t = Date.UTC(2026, 2, 2, 9, 15, 42) / 1000;
    expect(epoch(t)).toBe("2026-03-02 09:15 UTC");
    expect(clock(t)).toBe("09:15");
    expect(instant(t)).toBe("09:15:42 UTC");
  });
});
