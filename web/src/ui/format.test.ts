import { describe, expect, it } from "vitest";

import {
  axisTime,
  bytes,
  clock,
  DAY_S,
  cores,
  duration,
  epoch,
  instant,
  moment,
  time,
} from "./format";

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

  it("write seconds since the epoch in the browser's zone, UTC in the tests", () => {
    const t = Date.UTC(2026, 2, 2, 9, 15, 42) / 1000;
    expect(epoch(t)).toBe("2026-03-02 09:15 UTC");
    expect(clock(t)).toBe("09:15");
    expect(axisTime(t, DAY_S)).toBe("09:15");
    expect(axisTime(t, DAY_S + 1)).toBe("03-02 09:15");
    expect(axisTime(Date.UTC(2026, 2, 3) / 1000, 3 * DAY_S)).toBe("03-03");
    expect(instant(t)).toBe("09:15:42 UTC");
    expect(moment(t)).toBe("2026-03-02 09:15:42 UTC");
  });

  it("write a time in a zone with its abbreviation, as the zone's offset moves", () => {
    const zone = "America/Los_Angeles";
    expect(time("2026-03-02T09:15:00+00:00", zone)).toBe(
      "2026-03-02 01:15 PST",
    );
    expect(time("2026-07-02T09:15:00+00:00", zone)).toBe(
      "2026-07-02 02:15 PDT",
    );
    // The day changes with the zone.
    expect(time("2026-03-02T03:00:00+00:00", zone)).toBe(
      "2026-03-01 19:00 PST",
    );
  });
});

describe("a duration", () => {
  it("rounds to the second, unless fine asks for a test's time under 10s", () => {
    expect(duration(0.34)).toBe("0s");
    expect(duration(0.34, true)).toBe("340ms");
    expect(duration(0, true)).toBe("0ms");
    expect(duration(1.42, true)).toBe("1.4s");
    expect(duration(75, true)).toBe("1m 15s");
  });
});
