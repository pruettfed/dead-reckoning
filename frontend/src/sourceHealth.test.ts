import { describe, expect, it } from "vitest";

import { aisPill, sarPill } from "./sourceHealth";
import type { SourceHealth } from "./types";

function source(state: string, last_message_at: string | null = null): SourceHealth {
  return { state, last_message_at, lag_seconds: null, connected_since: null, reconnect_count: 0, error_count: 0 };
}

describe("aisPill", () => {
  it("reads ok/AIS LIVE when the socket is connected and fresh", () => {
    const p = aisPill({ ais: source("connected", "2026-08-06T00:00:00Z") });
    expect(p).toEqual({ state: "ok", label: "AIS LIVE", lastMessageAt: "2026-08-06T00:00:00Z" });
  });

  it("reads warn/AIS STALE when the socket is connected but silent", () => {
    const p = aisPill({ ais: source("stale", "2026-08-05T08:31:00Z") });
    expect(p).toEqual({ state: "warn", label: "AIS STALE", lastMessageAt: "2026-08-05T08:31:00Z" });
  });

  it("reads bad/AIS DOWN when the socket is disconnected", () => {
    const p = aisPill({ ais: source("disconnected") });
    expect(p).toEqual({ state: "bad", label: "AIS DOWN", lastMessageAt: null });
  });

  it("reads bad/AIS DOWN when there is no ais source at all", () => {
    const p = aisPill({});
    expect(p).toEqual({ state: "bad", label: "AIS DOWN", lastMessageAt: null });
  });
});

describe("sarPill", () => {
  it("is ok when the source is connected", () => {
    expect(sarPill({ sar_sentinel1: source("connected") }, true)).toBe("ok");
  });

  it("is ok when the source has never connected yet (boot placeholder)", () => {
    expect(sarPill({ sar_sentinel1: source("disconnected") }, true)).toBe("ok");
  });

  it("is warn when the source is degraded", () => {
    expect(sarPill({ sar_sentinel1: source("degraded") }, true)).toBe("warn");
  });

  it("is bad when the source errored (imagery API unreachable)", () => {
    expect(sarPill({ sar_sentinel1: source("error") }, true)).toBe("bad");
  });

  it("is bad when health hasn't loaded yet", () => {
    expect(sarPill({}, false)).toBe("bad");
  });
});
