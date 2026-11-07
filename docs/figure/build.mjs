// BlendPro architecture figure, built with the "anatomy" isometric kit.
// Run: ANATOMY_KIT=<path to anatomy skill> node docs/figure/build.mjs  ->  docs/images/architecture.svg
//
// The machine: a teleprinter (the LLM agent) punches a narrow tape of compact JSON; the tape runs on guides into a
// sorter cabinet (the BlendPro MCP server) with a lens unit (MediaPipe pose) and an aerial (Tripo3D API). One socket
// cable (port 9877; a capped spare port is the headless 9878) runs to the drawer cabinet (Blender + add-on): a single
// hopper (the main-thread queue) on top, a crank (the timer that drains it) and six handler drawers. Results come back
// as small printed cards on the front rail: text, not photographs.
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { homedir } from "node:os";

const HERE = dirname(fileURLToPath(import.meta.url));
const KIT = process.env.ANATOMY_KIT || join(homedir(), ".claude", "skills", "anatomy");
const k = await import(pathToFileURL(join(KIT, "kit", "iso-kit.mjs")).href);
const G = await import(pathToFileURL(join(KIT, "kit", "lathe.mjs")).href);
const T = await import(pathToFileURL(join(KIT, "kit", "tube.mjs")).href);
const theme = process.argv.includes("--dark") ? "dark" : "light";

const WIDTH = 960;
const HEIGHT = 470;
const PLATE = { x: -300, y: -70, w: 600, d: 150, r: 10 };
const PLATE_H = 10;
const FOOT_H = 5;

const TP = { x: -282, y: -46, w: 92, d: 80, r: 3 };          // teleprinter base
const TP_H = 14;
const TPB = { x: -278, y: -42, w: 64, d: 38, r: 3 };          // teleprinter body (behind keyboard)
const TPB_TOP = 40;
const SV = { x: -136, y: -36, w: 72, d: 62, r: 2 };           // server cabinet
const SV_TOP = 98;
const BC = { x: 24, y: -46, w: 162, d: 72, r: 2 };            // Blender cabinet
const BC_TOP = 82;
const FRONT_SV = SV.y + SV.d;                                 // +y face of the server
const FRONT_BC = BC.y + BC.d;                                 // +y face of the Blender cabinet

const P = k.fitProjection(
  [...k.boxCorners(PLATE, -PLATE_H - FOOT_H, 0), [-76, -24, 168], [100, -10, 126], [-246, -20, 52]],
  WIDTH, HEIGHT, { pad: 22, azimuth: 56 },
);
const at = (p) => k.iso(p, P);
const S = (paths, style) => k.solidSvg(paths, style);
const L = (d, style) => k.lineSvg(d, style);
const D = (pts, style) => k.dotsSvg(pts, style);
const depthXY = (x, y, z = 0) => k.depthOf([x, y, z], P);
const byDepth = (pts) => [...pts].sort((a, b) => depthXY(a[0], a[1]) - depthXY(b[0], b[1]));
const seg = (a, b) => k.segment(a, b, P);

// ---------------------------------------------------------------- bench
function bench() {
  const out = [`<path class="iso-halo" d="${k.haloOf(PLATE, -PLATE_H - FOOT_H, PLATE_H, P)}"/>`];
  for (const [x, y] of byDepth(k.corners(PLATE, 26))) {
    out.push(S(k.cylinder(x, y, 9, -PLATE_H - FOOT_H, 1.6, P, 28), { tone: "lo" }));
    out.push(S(k.cylinder(x, y, 7, -PLATE_H - FOOT_H + 1.6, FOOT_H - 1.6, P, 28), { tone: "mid" }));
  }
  out.push(S(k.slabOf(PLATE, -PLATE_H, PLATE_H, P, 8, 1.6), { tone: "mid", inner: L(k.sideSeam(PLATE, -PLATE_H / 2, P, 8), { tone: "faint" }) }));
  out.push(L(k.planOutline(k.insetPlan(PLATE, 6), 0, P), { tone: "lo" }));
  const screws = k.corners(PLATE, 12);
  out.push(L(screws.map(([x, y]) => k.ring(x, y, 2.4, 0, P, 16)).join(""), { tone: "lo" }));
  out.push(D(screws.map(([x, y]) => at([x, y, 0])), { size: 0.55 }));
  // ruler strip along the front edge
  const strip = { x: -270, y: PLATE.y + PLATE.d - 16, w: 520, d: 8, r: 1 };
  out.push(S(k.slabOf(strip, 0, 0.6, P, 3), { tone: "lo", crease: "none" }));
  const ticks = k.topTicks(strip.x + 4, strip.x + strip.w - 4, 5, 5, strip.y + 0.8, 0.6, [2.2, 4], P);
  out.push(L(ticks.minor, { tone: "lo" }), L(ticks.major, { tone: "mid" }));
  out.push(D([at([strip.x + 2, strip.y + 4, 0.6]), at([strip.x + strip.w - 2, strip.y + 4, 0.6])], { size: 0.45 }));
  // label plate (the one engraved word)
  const plate = { x: 214, y: 30, w: 66, d: 22, r: 1.5 };
  out.push(S(k.slabOf(plate, 0, 1.2, P, 3, 0.4), { tone: "mid" }));
  out.push(k.faceTextSvg(k.topMatrix([plate.x + plate.w / 2, plate.y + 8], 1.2, P, "x"), "BLENDPRO", { size: 5.2, tone: "mid", anchor: "middle" }));
  for (const yy of [13.5, 16.5, 19.5]) out.push(L(k.lineOnTop([plate.x + 6, plate.y + yy], [plate.x + plate.w - (yy > 18 ? 26 : 6), plate.y + yy], 1.2, P), { tone: "faint", free: true }));
  out.push(D([[plate.x + 2.5, plate.y + 2.5], [plate.x + plate.w - 2.5, plate.y + 2.5], [plate.x + 2.5, plate.y + plate.d - 2.5], [plate.x + plate.w - 2.5, plate.y + plate.d - 2.5]].map(([x, y]) => at([x, y, 1.2])), { size: 0.4 }));
  return out.join("");
}

// ---------------------------------------------------------------- teleprinter (the agent)
function teleprinter() {
  const out = [`<path class="iso-halo" d="${k.haloOf(k.insetPlan(TP, 2), 0, 0.1, P)}" style="filter:blur(3px)"/>`];
  out.push(S(k.slabOf(TP, 0, TP_H, P, 4, 0.8), { tone: "mid", inner: L(k.sideSeam(TP, 4, P, 4), { tone: "faint" }) }));
  out.push(S(k.slabOf(TPB, TP_H, TPB_TOP - TP_H, P, 4, 0.8), { tone: "mid", inner: L(k.sideSeam(TPB, TPB_TOP - 6, P, 4), { tone: "faint" }) }));
  // platen roller along y, with knobs
  const F = G.frameAlong([-246, TPB.y - 3, TPB_TOP + 6], [0, 1, 0]);
  out.push(S(G.disc(0, 2.5, 4.4, F, P), { tone: "mid" }));
  out.push(S(G.disc(2.5, TPB.d + 3.5, 6, F, P), { tone: "hi" }));
  out.push(S(G.disc(TPB.d + 3.5, TPB.d + 6.5, 4.4, F, P), { tone: "mid" }));
  out.push(L(G.ribsOf(F, TPB.d + 3.5, TPB.d + 6.5, 4.4, 18, P, { seams: true }), { tone: "lo" }));
  for (const yy of [TPB.y + 4, TPB.y + TPB.d - 4]) out.push(S(k.slabOf({ x: -252, y: yy - 1.5, w: 12, d: 3, r: 0.6 }, TPB_TOP, 2.2, P, 2), { tone: "mid", crease: "none" }));
  // paper sheet rising from the platen
  out.push(S(k.slabOf({ x: -250, y: TPB.y + 4, w: 1, d: TPB.d - 8, r: 0 }, TPB_TOP + 8, 14, P, 1), { tone: "hi", crease: "none" }));
  // keyboard: 3 rows x 8 keys on the deck in front of the body
  const keys = [];
  for (let row = 0; row < 3; row++) for (let col = 0; col < 8; col++) keys.push([-275 + col * 9.6 + row * 2.2, 2 + row * 9]);
  for (const [x, y] of byDepth(keys)) out.push(S(k.slabOf({ x, y, w: 7.6, d: 7, r: 1.2 }, TP_H, 2.4, P, 3, 0.4), { tone: "hi", crease: "lo" }));
  out.push(S(k.slabOf({ x: -270, y: 28.5, w: 56, d: 4.5, r: 1.6 }, TP_H, 2.2, P, 4, 0.4), { tone: "hi", crease: "lo" })); // space bar
  // tape punch block on the +x end
  out.push(S(k.slabOf({ x: TP.x + TP.w - 14, y: 2, w: 12, d: 18, r: 1.2 }, TP_H, 10, P, 3, 0.5), { tone: "mid" }));
  out.push(D([at([TP.x + TP.w - 8, 6, TP_H + 10]), at([TP.x + TP.w - 8, 16, TP_H + 10])], { size: 0.45 }));
  return out.join("");
}

// ---------------------------------------------------------------- punched tape on guides (compact JSON)
const TAPE = { x0: TP.x + TP.w - 2, x1: SV.x, y: 7.5, w: 7, z: 21 };
function tape() {
  const out = [];
  for (const x of [-170, -152]) {
    out.push(S(k.cylinder(x, TAPE.y + TAPE.w / 2, 1.4, 0, TAPE.z - 2, P, 14), { tone: "mid", crease: "none" }));
    out.push(S(k.cylinder(x, TAPE.y + TAPE.w / 2, 3.2, 0, 1.2, P, 18), { tone: "lo" }));
    out.push(S(k.slabOf({ x: x - 2.6, y: TAPE.y - 1.6, w: 5.2, d: TAPE.w + 3.2, r: 0.8 }, TAPE.z - 2, 1.6, P, 2), { tone: "mid" }));
  }
  const strip = { x: TAPE.x0, y: TAPE.y, w: TAPE.x1 - TAPE.x0, d: TAPE.w, r: 0 };
  out.push(S(k.slabOf(strip, TAPE.z - 0.4, 0.5, P, 1), { tone: "hi", lit: true, crease: "none" }));
  const holes = [];
  for (let x = TAPE.x0 + 3; x < TAPE.x1 - 2; x += 3) {
    holes.push(at([x, TAPE.y + 1.4, TAPE.z + 0.1]));
    if ((x * 7) % 5 < 2.6) holes.push(at([x, TAPE.y + 3.4, TAPE.z + 0.1]));
  }
  out.push(D(holes, { size: 0.42, tone: "lo" }));
  return out.join("");
}

// ---------------------------------------------------------------- server cabinet (BlendPro MCP)
function server() {
  const out = [`<path class="iso-halo" d="${k.haloOf(SV, 0, 0.1, P)}" style="filter:blur(4px)"/>`];
  const plinth = k.insetPlan(SV, -4);
  out.push(S(k.slabOf(plinth, 0, 4, P, 3, 0.5), { tone: "lo" }));
  out.push(S(k.slabOf(SV, 4, SV_TOP - 4, P, 4, 1), { tone: "mid", inner: L(k.sideSeam(SV, 30, P, 4) + k.sideSeam(SV, 66, P, 4), { tone: "faint" }) }));
  out.push(S(k.slabOf(k.insetPlan(SV, -1.5), SV_TOP, 3, P, 4, 1), { tone: "mid" }));
  // vents on the +y face, edge to edge
  out.push(L([72, 76, 80, 84, 88].map((z) => seg([SV.x, FRONT_SV, z], [SV.x + SV.w, FRONT_SV, z])).join(""), { tone: "lo" }));
  // lens unit (MediaPipe pose) on the front face
  const F = G.frameAlong([-100, FRONT_SV, 48], [0, 1, 0]);
  out.push(S(G.disc(0, 2.5, 13, F, P), { tone: "mid" }));
  out.push(S(G.disc(2.5, 11, 10, F, P), { tone: "mid", inner: L(G.arcOf(F, 6, 10, P), { tone: "faint" }) }));
  out.push(L(G.ribsOf(F, 2.5, 6, 10, 28, P, { seams: "first" }), { tone: "lo" }));
  out.push(S(G.disc(11, 13, 7.2, F, P), { tone: "hi" }));
  out.push(L(G.circleOf(F, 13, 4.6, P), { tone: "mid" }), L(G.circleOf(F, 13, 2.2, P), { tone: "lo" }));
  out.push(D(G.dotsOf(F, 2.5, 11.6, 6, P, { phase: 0.3, all: true }), { size: 0.45 }));
  // status lamps on top
  for (const [x, i] of [[-122, 0], [-114, 1], [-106, 2]]) {
    out.push(S(k.cylinder(x, -26, 2.6, SV_TOP + 3, 1.2, P, 16), { tone: "lo" }));
    out.push(S(k.cylinder(x, -26, 1.8, SV_TOP + 4.2, 1.6, P, 16), { tone: i === 1 ? "hi" : "mid" }));
  }
  // card-reader slot plate on top (where the tape is read)
  out.push(S(k.slabOf({ x: -126, y: 0, w: 30, d: 16, r: 1.5 }, SV_TOP + 3, 1.4, P, 3, 0.4), { tone: "mid" }));
  out.push(L(k.lineOnTop([-122, 8], [-100, 8], SV_TOP + 4.4, P), { tone: "lo" }));
  out.push(D([at([-124, 2, SV_TOP + 4.4]), at([-98, 2, SV_TOP + 4.4]), at([-124, 14, SV_TOP + 4.4]), at([-98, 14, SV_TOP + 4.4])], { size: 0.4 }));
  // aerial (Tripo3D API): base, mast, dish, tip
  out.push(S(k.cylinder(-76, -24, 5, SV_TOP + 3, 3, P, 24), { tone: "mid" }));
  out.push(S(k.cylinder(-76, -24, 1.6, SV_TOP + 6, 44, P, 14), { tone: "mid", crease: "none" }));
  out.push(L([SV_TOP + 20, SV_TOP + 34].map((z) => k.sideArc(-76, -24, 1.6, z, P, 10)).join(""), { tone: "faint" }));
  const Fd = G.frameAlong([-76, -24, SV_TOP + 50], G.unit3([-0.35, 0.25, 0.9]));
  out.push(S(G.lathe([[0, 0], [1.2, 6, 1], [3.6, 12, 1], [5.2, 14], [5.2, 0]], Fd, P, { fill: "hull" }), { tone: "hi" }));
  out.push(S(G.sphereOf(G.pointOf(Fd, 9, 0, 0), 1.6, P), { tone: "mid" }));
  // ports on the +x face: 9877 (cable) and the capped headless spare 9878
  for (const [z, capped] of [[34, false], [20, true]]) {
    const Fp = G.frameAlong([SV.x + SV.w, 2, z], [1, 0, 0]);
    out.push(S(G.disc(0, 2, 6, Fp, P), { tone: "mid" }));
    out.push(S(G.disc(2, capped ? 6 : 5, 4.2, Fp, P), { tone: capped ? "lo" : "mid" }));
    out.push(D(G.dotsOf(Fp, 2, 5.2, 4, P, { phase: 0.125, all: true }), { size: 0.38 }));
  }
  return out.join("");
}

// ---------------------------------------------------------------- socket cable
function cable() {
  const route = G.fillet([[SV.x + SV.w + 5, 2, 34], [-44, 2, 34], [-34, 4, 7], [0, 8, 4.2], [BC.x - 2, 8, 4.2]], 7, 9, { P, tube: 2.4 });
  return T.tubePieces(route, 2.4, P, { maxLength: 400, caps: [false, true] }).map((pc) => T.tubeSvg(pc, { tone: "mid" })).join("") +
    S(G.disc(-3, 0, 3.4, G.frameAlong([SV.x + SV.w + 5, 2, 34], [1, 0, 0]), P), { tone: "mid" });
}

// ---------------------------------------------------------------- Blender cabinet (add-on, queue, handlers)
function blender() {
  const out = [`<path class="iso-halo" d="${k.haloOf(BC, 0, 0.1, P)}" style="filter:blur(4px)"/>`];
  out.push(S(k.slabOf(k.insetPlan(BC, -4), 0, 5, P, 3, 0.5), { tone: "lo" }));
  out.push(S(k.slabOf(BC, 5, BC_TOP - 5, P, 4, 1), { tone: "mid", inner: L(k.sideSeam(BC, 43, P, 4), { tone: "faint" }) }));
  out.push(S(k.slabOf(k.insetPlan(BC, -1.5), BC_TOP, 3, P, 4, 1), { tone: "mid" }));
  // six handler drawers; the top-middle one is pulled out (executing)
  const OPEN = { row: 1, col: 1, pull: 10 };
  for (let row = 0; row < 2; row++) for (let col = 0; col < 3; col++) {
    if (row === OPEN.row && col === OPEN.col) continue;
    out.push(drawer(BC.x + 6 + col * 52, row === 0 ? 11 : 47, 0));
  }
  out.push(drawer(BC.x + 6 + OPEN.col * 52, 47, OPEN.pull));
  // hopper on top: the single main-thread queue
  const HX = 100, HY = -12;
  out.push(S(k.cylinder(HX, HY, 17, BC_TOP + 3, 3, P, 40), { tone: "mid" }));
  out.push(S(k.cylinder(HX, HY, 14, BC_TOP + 6, 30, P, 40, 0.6), { tone: "mid", inner: L(k.sideArc(HX, HY, 14, BC_TOP + 22, P, 24), { tone: "faint" }) }));
  out.push(S(k.cylinder(HX, HY, 15.5, BC_TOP + 36, 2.4, P, 40, 0.5), { tone: "mid" }));
  out.push(S(k.cylinder(HX, HY, 12.2, BC_TOP + 38.4 - 0.2, 0.3, P, 40), { tone: "lo", flat: true }));
  for (let i = 0; i < 5; i++) {
    const card = { x: HX - 8, y: HY - 6 + i * 0.4, w: 16, d: 10, r: 0.6 };
    out.push(S(k.slabOf(card, BC_TOP + 38.6 - (4 - i) * 0.9, 0.6, P, 2), { tone: "hi", crease: "none" }));
  }
  // crank wheel on the +x face: the timer that drains the queue one card at a time
  const Fc = G.frameAlong([BC.x + BC.w, -10, 44], [1, 0, 0]);
  out.push(S(G.disc(0, 2, 7, Fc, P), { tone: "lo" }));
  out.push(S(G.disc(2, 6, 18, Fc, P), { tone: "mid", inner: L(G.arcOf(Fc, 4, 18, P), { tone: "faint" }) }));
  out.push(L(G.spokesOf(Fc, 6, 4, 15.5, 6, P), { tone: "lo" }));
  out.push(L(G.circleOf(Fc, 6, 15.5, P), { tone: "faint" }), L(G.circleOf(Fc, 6, 4, P), { tone: "lo" }));
  const handleAt = G.pointOf(Fc, 6, 13, 0.6);
  const Fh = G.frameAlong(handleAt, [1, 0, 0]);
  out.push(S(G.disc(0, 10, 2.2, Fh, P), { tone: "mid" }));
  out.push(L(G.ribsOf(Fh, 2, 10, 2.2, 14, P, { seams: true }), { tone: "lo" }));
  out.push(D([at(G.pointOf(Fc, 6, 0, 0))], { size: 0.6, tone: "hi" }));
  return out.join("");
}

function drawer(x, z, pull) {
  const out = [];
  const w = 46, h = 30, face = FRONT_BC;
  if (pull > 0) {
    // drawer box sliding out of its opening
    out.push(S(k.slabOf({ x: x + 1, y: face - 30, w: w - 2, d: 30 + pull, r: 0.5 }, z + 2, h - 6, P, 2), { tone: "lo", crease: "none" }));
    out.push(S(k.slabOf({ x: x + 3, y: face - 4, w: w - 6, d: pull, r: 0.3 }, z + h - 4.1, 0.3, P, 1), { tone: "faint", flat: true }));
    // printed cards standing in the open drawer
    for (let i = 0; i < 4; i++) out.push(S(k.slabOf({ x: x + 8 + i * 8, y: face + pull - 12, w: 1, d: 9, r: 0 }, z + h - 4, 5, P, 1), { tone: "hi", crease: "none" }));
  }
  const y = face + pull;
  out.push(S(k.slabOf({ x, y, w, d: 2.4, r: 0.8 }, z, h, P, 2, 0.5), { tone: "mid" }));
  // handle bar and label holder
  out.push(S(k.slabOf({ x: x + w / 2 - 8, y: y + 2.4, w: 16, d: 2.6, r: 1 }, z + 8, 2.6, P, 3), { tone: "mid" }));
  out.push(S(k.slabOf({ x: x + w / 2 - 7, y: y + 2.4, w: 14, d: 0.6, r: 0 }, z + 17, 7, P, 1), { tone: "lo", crease: "none" }));
  for (const zz of [z + 19.5, z + 21.5]) out.push(L(seg([x + w / 2 - 5, y + 3, zz], [x + w / 2 + (zz > z + 20 ? 1 : 5), y + 3, zz]), { tone: "faint", free: true }));
  out.push(D([at([x + w / 2 - 6, y + 3, z + 23.4]), at([x + w / 2 + 6, y + 3, z + 23.4])], { size: 0.35 }));
  return out.join("");
}

// ---------------------------------------------------------------- return rail: results come back as text cards
function returnRail() {
  const out = [];
  const rail = { x: -268, y: 46, w: 450, d: 4, r: 1 };
  out.push(S(k.slabOf(rail, 0, 2.6, P, 2, 0.4), { tone: "mid" }));
  for (const x of [rail.x - 4, rail.x + rail.w]) out.push(S(k.slabOf({ x, y: 43, w: 4, d: 10, r: 1 }, 0, 6, P, 2, 0.4), { tone: "mid" }));
  out.push(D([rail.x + 10, rail.x + rail.w - 10].map((x) => at([x, rail.y + 2, 2.6])), { size: 0.4 }));
  for (const x of [-230, -60, 110]) {
    out.push(S(k.slabOf({ x, y: 41, w: 22, d: 14, r: 1 }, 2.6, 1, P, 2, 0.3), { tone: "hi" }));
    for (const [i, yy] of [44, 47, 50, 53].entries()) out.push(L(k.lineOnTop([x + 3, yy], [x + (i === 3 ? 11 : 19), yy], 3.6, P), { tone: "lo", free: true }));
  }
  return out.join("");
}

const svg = k.figureSvg({
  width: WIDTH,
  height: HEIGHT,
  standalone: theme,
  label: "BlendPro as a bench machine. A teleprinter (the LLM agent) punches a narrow tape of compact JSON that runs on guides into a sorter cabinet (the BlendPro MCP server) carrying a lens unit (MediaPipe pose) and an aerial (Tripo3D API). One socket cable runs to a drawer cabinet (Blender with the add-on): a hopper on top is the single main-thread queue, a crank drains it one command at a time, and six drawers are the handlers. Results return along the front rail as small printed cards: text, not screenshots.",
  body: [bench(), teleprinter(), tape(), server(), cable(), blender(), returnRail()],
});

const out = resolve(HERE, "..", "images", theme === "light" ? "architecture.svg" : "architecture-dark.svg");
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, svg);
if (process.argv.includes("--html")) {
  writeFileSync(join(HERE, `preview-${theme}.html`), k.pageHtml({ title: "BlendPro", theme, body: svg, width: WIDTH + 40 }));
}
console.log("wrote", out, (svg.length / 1024).toFixed(0) + " KB");
