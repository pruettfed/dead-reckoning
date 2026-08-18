import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { Plugin } from "vite";

import * as fx from "./fixtures";

// The version is single-sourced in backend/app/version.py. Read it rather than
// hardcoding one here, or stub mode would quietly contradict the real thing.
function backendVersion(root: string): string {
  try {
    const src = readFileSync(resolve(root, "../backend/app/version.py"), "utf8");
    return src.match(/^VERSION\s*=\s*"([^"]+)"/m)?.[1] ?? "0.0.0-stub";
  } catch {
    return "0.0.0-stub";
  }
}

/**
 * Serves the read-only API from in-process fixtures, so the console can be
 * driven with no backend, no database and no scheduler.
 *
 * Deliberately middleware rather than a sidecar process: a second server would
 * mean a second port to collide with whatever else is bound (docker compose
 * holds 5173), which is the failure this exists to avoid.
 */
export function stubApi(): Plugin {
  let version = "0.0.0-stub";

  return {
    name: "dr-stub-api",
    configResolved(config) {
      version = backendVersion(config.root);
      config.logger.info(`  ➜  Stub API:  serving fixtures as v${version} (VITE_STUB)`);
    },
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const raw = req.url ?? "";
        if (!raw.startsWith("/api/")) return next();

        const url = new URL(raw, "http://stub");
        const path = url.pathname.replace(/^\/api/, "");
        const roi = url.searchParams.get("roi") ?? "north_taiwan";
        const now = Date.now();

        const body = route(path, roi, url, now, version);
        if (body === undefined) {
          res.statusCode = 404;
          res.end(JSON.stringify({ detail: `stub has no route for ${path}` }));
          return;
        }
        res.setHeader("Content-Type", "application/json");
        res.end(JSON.stringify(body));
      });
    },
  };
}

function route(path: string, roi: string, url: URL, now: number, version: string): unknown {
  if (path === "/health") return fx.health(now, version);
  if (path === "/status-message") return fx.STATUS_MESSAGE;
  if (path === "/rois") return fx.ROIS;
  if (path === "/scenes") return fx.scenes(now, roi);
  if (path === "/vessels") return fx.vessels(now, roi);
  if (path === "/vessels/count") return fx.vesselCount(now, roi);
  if (path === "/analysis/next-pass") return fx.nextPass(now, roi);
  if (path === "/analysis/schedule") return fx.schedule(now);

  const detections = path.match(/^\/scenes\/(.+)\/detections$/);
  if (detections) return fx.detections(now, decodeURIComponent(detections[1]), url.searchParams.get("include_land") === "true");

  const track = path.match(/^\/vessels\/(\d+)\/track$/);
  if (track) return fx.track(now, Number(track[1]), Number(url.searchParams.get("hours") ?? "48"));

  const sightings = path.match(/^\/vessels\/(\d+)\/sightings$/);
  if (sightings) return fx.sightings(now, Number(sightings[1]));

  return undefined;
}
