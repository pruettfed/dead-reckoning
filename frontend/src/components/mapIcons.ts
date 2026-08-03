import L from "leaflet";

import { C, MONO, hexA } from "../theme";

const CORNERS = [
  "left:-4px;top:-4px;border-left:2px solid COL;border-top:2px solid COL",
  "right:-4px;top:-4px;border-right:2px solid COL;border-top:2px solid COL",
  "left:-4px;bottom:-4px;border-left:2px solid COL;border-bottom:2px solid COL",
  "right:-4px;bottom:-4px;border-right:2px solid COL;border-bottom:2px solid COL",
];

export function cornerBracketsHtml(color: string, animate: boolean): string {
  const anim = animate
    ? "opacity:0;animation:dr-flash .14s steps(1,end) 4 forwards"
    : "opacity:1";
  return CORNERS.map(
    (c) => `<div style="position:absolute;${c.split("COL").join(color)};width:6px;height:6px;${anim}"></div>`,
  ).join("");
}

// Fixed box: the design scales markers by hull length, which this pipeline does
// not measure, and an invented size would be a measurement on the map.
const BOX_W = 20;
const BOX_H = 13;

export function detectionIcon(opts: {
  color: string;
  label: string;
  selected: boolean;
  ring: boolean;
}): L.DivIcon {
  const { color, label, selected, ring } = opts;
  const ringHtml = ring
    ? `<div style="position:absolute;inset:-3px;border:1px solid ${color};opacity:.5;animation:dr-ring 2.2s ease-out infinite"></div>`
    : "";
  const brackets = selected ? cornerBracketsHtml(color, false) : "";
  const tag = `<div style="position:absolute;left:${BOX_W + 7}px;top:50%;transform:translateY(-50%);white-space:nowrap;font:500 9px ${MONO};letter-spacing:.07em;color:${color};background:rgba(10,12,14,${selected ? 0.85 : 0.68});padding:2px 6px;border:1px solid ${hexA(color, selected ? 0.6 : 0.32)}">${label}</div>`;
  return L.divIcon({
    className: "",
    iconSize: [BOX_W, BOX_H],
    iconAnchor: [BOX_W / 2, BOX_H / 2],
    html: `<div style="position:relative;width:${BOX_W}px;height:${BOX_H}px">${ringHtml}${brackets}<div style="position:absolute;inset:0;border:${selected ? 2 : 1.5}px solid ${color};background:${hexA(color, selected ? 0.14 : 0.05)}"></div>${tag}</div>`,
  });
}

export function vesselIcon(cog: number, selected: boolean): L.DivIcon {
  const c = selected ? "#ffffff" : C.accent;
  const wedge = `<div style="position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:9px;height:12px;background:${c};opacity:.95;clip-path:polygon(50% 0%,100% 78%,50% 100%,0% 78%)"></div>`;
  return L.divIcon({
    className: "",
    iconSize: [26, 26],
    iconAnchor: [13, 13],
    html: `<div style="width:26px;height:26px;position:relative">${selected ? cornerBracketsHtml(c, false) : ""}<div style="position:absolute;inset:0;transform:rotate(${cog}deg)">${wedge}</div></div>`,
  });
}
