/* How the bunny gets from A to B: it runs, hops, rides a skateboard along a straight line, or sits
 * on a rocket. pickTravel chooses; the draw helpers paint the vehicle under the bunny's feet. */

export const TRAVEL = ["run", "hop", "skate", "rocket"];

/** `rand` is a 0..1 source so tests can pin the choice. Short trips run or skate; long ones hop, skate or fly. */
export function pickTravel(len, rand = Math.random) {
  const r = rand();
  if (len <= 150) return "run";
  if (len <= 380) return r < 0.5 ? "run" : "skate";
  return r < 1 / 3 ? "hop" : r < 2 / 3 ? "skate" : "rocket";
}

const put = (ctx, grid, colors, px, ox, oy) => {
  for (let r = 0; r < grid.length; r++) for (let c = 0; c < grid[r].length; c++) {
    const ch = grid[r][c];
    if (ch === ".") continue;
    ctx.fillStyle = colors[ch];
    ctx.fillRect(ox + c * px, oy + r * px, px + 0.5, px + 0.5);
  }
};

const BOARD = [
  ".KKKKKKKKKKKKKKKKK.",
  "KAAAAAAAAAAAAAAAAAK",
  ".KKKKKKKKKKKKKKKKK.",
];
const BOARD_C = { K: "#14141e", A: "#ffb246" };

/** Skateboard under the feet at (x, y): deck, and two wheels whose spokes turn with `spin`. */
export function drawSkateboard(ctx, x, y, px, spin, alpha = 1) {
  ctx.save();
  ctx.globalAlpha = alpha;
  const w = BOARD[0].length * px, ox = x - w / 2, oy = y - px; // the feet stand on the deck
  put(ctx, BOARD, BOARD_C, px, ox, oy);
  for (const wx of [3, 14]) {
    ctx.fillStyle = "#14141e"; ctx.fillRect(ox + (wx - 1) * px, oy + 3 * px, 3 * px, 3 * px);
    ctx.fillStyle = "#d8d8e0"; ctx.fillRect(ox + wx * px, oy + (3 + (spin & 1)) * px, px, px); // spoke flips each tick
  }
  ctx.restore();
}

/** The painted road: a dashed amber line from a to b, drawn up to progress p (0..1). */
export function drawRoad(ctx, a, b, p, alpha = 1) {
  ctx.save();
  ctx.globalAlpha = alpha * 0.55;
  ctx.strokeStyle = "rgb(255,178,70)";
  ctx.lineWidth = 3;
  ctx.setLineDash([10, 8]);
  ctx.beginPath();
  ctx.moveTo(a[0], a[1]);
  ctx.lineTo(a[0] + (b[0] - a[0]) * p, a[1] + (b[1] - a[1]) * p);
  ctx.stroke();
  ctx.restore();
}

const ROCKET = [
  "....KKKKKKKKKKKK.....",
  "..KKWWWWWWWWWWWWKKK..",
  ".KWWWWWWWWBBBWWWWRRK.",
  "KFFWWWWWWWBBBWWWWRRRK",
  ".KFFDDDDDDDDDDDDDRRK.",
  "..KKKKKKKKKKKKKKKKK..",
];
const ROCKET_C = { K: "#14141e", W: "#eef0f6", D: "#b8bccb", B: "#5aa8ff", R: "#d8452e", F: "#d8452e" };

/** A rocket centred at (x, y), nose along `angle`, with a flickering flame behind it. */
export function drawRocketRide(ctx, x, y, angle, px, flicker, alpha = 1) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.translate(x, y);
  ctx.rotate(angle);
  if (Math.cos(angle) < 0) ctx.scale(1, -1);
  const w = ROCKET[0].length * px, h = ROCKET.length * px;
  const fl = (flicker & 1) ? 7 : 5; // flame length in art px
  ctx.fillStyle = "#ffe9a0"; ctx.fillRect(-w / 2 - fl * px, -px, fl * px, 2 * px);
  ctx.fillStyle = "#ff8a30"; ctx.fillRect(-w / 2 - (fl - 2) * px, -2 * px, (fl - 2) * px, 4 * px);
  put(ctx, ROCKET, ROCKET_C, px, -w / 2, -h / 2);
  ctx.restore();
}
