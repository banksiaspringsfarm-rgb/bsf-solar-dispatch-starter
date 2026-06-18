// BSF Solar Dispatch — iOS home-screen widget for the Scriptable app.
// Mirrors the Android v3 widget: battery SOC hero, PV + surplus, hot-water + air-con state.
// Free app: Scriptable (App Store). Paste this into a new script, edit the CONFIG below,
// then add a Scriptable widget to your home screen and pick this script.

// ─────────────────────────────────────────────────────────────────────────────
// CONFIG — the only things you should need to edit
// ─────────────────────────────────────────────────────────────────────────────

// Your solar-dispatch relay's state endpoint. Form: https://<your-relay-host>/solar/state
// You can ALSO leave this as-is and set the URL per-widget in the widget's Parameter
// field (long-press widget → Edit Widget → Parameter). The Parameter wins if set.
const STATE_URL = "https://YOUR-RELAY-HOST/solar/state";

// SOC colour threshold (battery-chemistry aware).
//   LEAD-ACID (default): 60 — lead-acid should NOT sit low; treat ≤60% as the danger floor.
//   LiFePO4 / lithium:   lower this to ~20 — lithium happily runs much deeper.
// SOC text + ring is RED at/below this, AMBER within ~10% above it, GREEN above that.
const SOC_RED_PCT = 60;

// Header label shown top-left.
const SITE_NAME = "Solar";

// ─────────────────────────────────────────────────────────────────────────────
// Internals
// ─────────────────────────────────────────────────────────────────────────────

const ACCENT = "#F5B62E"; // warm brand yellow, used in both light + dark themes

// Per-widget URL override from the Scriptable Parameter field, else the const above.
const url = (args.widgetParameter && String(args.widgetParameter).trim()) || STATE_URL;

const dark = Device.isUsingDarkAppearance();
const theme = dark
  ? {
      bg: new Color("#11151C"),
      bgTo: new Color("#1B2230"),
      text: new Color("#F2F4F8"),
      dim: new Color("#9AA4B2"),
      chipOn: new Color("#243042"),
      chipOff: new Color("#1A2029"),
      ringTrack: new Color("#2A3340"),
    }
  : {
      bg: new Color("#FFFFFF"),
      bgTo: new Color("#F1F3F7"),
      text: new Color("#1A1F27"),
      dim: new Color("#5C6573"),
      chipOn: new Color("#FCEFC9"),
      chipOff: new Color("#ECEFF3"),
      ringTrack: new Color("#DCE0E6"),
    };

const RED = new Color("#E5484D");
const AMBER = new Color("#F5B62E");
const GREEN = new Color("#2FB87A");

// Colour for an SOC value, chemistry-aware via SOC_RED_PCT.
function socColor(soc) {
  if (soc == null || isNaN(soc)) return theme.dim;
  if (soc <= SOC_RED_PCT) return RED;
  if (soc <= SOC_RED_PCT + 10) return AMBER;
  return GREEN;
}

// Format watts: <1000 → "### W", ≥1000 → "#.# kW". Null-safe → "–".
function fmtW(w) {
  if (w == null || isNaN(w)) return "–";
  const n = Number(w);
  if (Math.abs(n) >= 1000) return (n / 1000).toFixed(1) + " kW";
  return Math.round(n) + " W";
}

function fmtPct(p) {
  if (p == null || isNaN(p)) return "–";
  return Math.round(Number(p)) + "%";
}

// Relative "x ago" from an epoch-ms timestamp.
function relTime(tsMs) {
  if (!tsMs || isNaN(tsMs)) return "no timestamp";
  const sec = Math.max(0, Math.round((Date.now() - Number(tsMs)) / 1000));
  if (sec < 60) return sec + "s ago";
  const min = Math.round(sec / 60);
  if (min < 60) return min + "m ago";
  const hr = Math.round(min / 60);
  if (hr < 24) return hr + "h ago";
  return Math.round(hr / 24) + "d ago";
}

async function fetchState() {
  const req = new Request(url);
  req.timeoutInterval = 12;
  const json = await req.loadJSON();
  if (!json || json.ok !== true) throw new Error("relay returned ok=false");
  return json;
}

// ─────────────────────────────────────────────────────────────────────────────
// Rendering helpers
// ─────────────────────────────────────────────────────────────────────────────

function applyBackground(widget) {
  const grad = new LinearGradient();
  grad.colors = [theme.bg, theme.bgTo];
  grad.locations = [0, 1];
  grad.startPoint = new Point(0, 0);
  grad.endPoint = new Point(0, 1);
  widget.backgroundGradient = grad;
}

function header(widget) {
  const row = widget.addStack();
  row.centerAlignContent();
  const dot = row.addText("●");
  dot.font = Font.systemFont(9);
  dot.textColor = new Color(ACCENT);
  row.addSpacer(5);
  const label = row.addText(SITE_NAME.toUpperCase());
  label.font = Font.semiboldSystemFont(11);
  label.textColor = theme.dim;
  return row;
}

// HW: 🔥 heating / 💧 idle.  AC: ❄️ on / · off.
function stateChip(stack, label, on, onIcon, offIcon) {
  const chip = stack.addStack();
  chip.centerAlignContent();
  chip.setPadding(4, 8, 4, 8);
  chip.backgroundColor = on ? theme.chipOn : theme.chipOff;
  chip.cornerRadius = 8;
  const icon = chip.addText(on ? onIcon : offIcon);
  icon.font = Font.systemFont(12);
  chip.addSpacer(5);
  const txt = chip.addStack();
  txt.layoutVertically();
  const name = txt.addText(label);
  name.font = Font.systemFont(9);
  name.textColor = theme.dim;
  const st = txt.addText(on ? "ON" : "OFF");
  st.font = Font.semiboldSystemFont(11);
  st.textColor = on ? new Color(ACCENT) : theme.dim;
}

function metric(stack, label, value, valueColor) {
  const col = stack.addStack();
  col.layoutVertically();
  const v = col.addText(value);
  v.font = Font.semiboldSystemFont(17);
  v.textColor = valueColor || theme.text;
  v.lineLimit = 1;
  v.minimumScaleFactor = 0.6;
  const l = col.addText(label);
  l.font = Font.systemFont(10);
  l.textColor = theme.dim;
}

function footnote(widget, text) {
  const f = widget.addText(text);
  f.font = Font.systemFont(9);
  f.textColor = theme.dim;
  f.lineLimit = 1;
}

// ─────────────────────────────────────────────────────────────────────────────
// Layouts
// ─────────────────────────────────────────────────────────────────────────────

function buildSmall(widget, data) {
  const soc = data?.hw?.soc;
  const sc = socColor(soc);

  header(widget);
  widget.addSpacer(4);

  // SOC hero number.
  const heroRow = widget.addStack();
  heroRow.bottomAlignContent();
  const hero = heroRow.addText(fmtPct(soc));
  hero.font = Font.boldSystemFont(40);
  hero.textColor = sc;
  hero.minimumScaleFactor = 0.6;
  hero.lineLimit = 1;
  heroRow.addSpacer(4);
  const lbl = heroRow.addText("SOC");
  lbl.font = Font.semiboldSystemFont(12);
  lbl.textColor = theme.dim;

  if (data?.hw?.curtailed) {
    const c = widget.addText("⚡ curtailed");
    c.font = Font.systemFont(9);
    c.textColor = new Color(ACCENT);
  }

  widget.addSpacer(4);

  // Compact PV / surplus.
  const pv = widget.addText("PV " + fmtW(data?.hw?.pv_total));
  pv.font = Font.systemFont(11);
  pv.textColor = theme.text;
  const sp = widget.addText("Surplus " + fmtW(data?.hw?.surplus_now));
  sp.font = Font.systemFont(11);
  sp.textColor = theme.text;

  widget.addSpacer(4);

  // HW / AC dots compact.
  const dots = widget.addStack();
  dots.centerAlignContent();
  const hwOn = String(data?.hw?.hwState).toLowerCase() === "on";
  const acOn = String(data?.ac?.acState).toLowerCase() === "on";
  const hwd = dots.addText((hwOn ? "🔥" : "💧") + " HW");
  hwd.font = Font.systemFont(11);
  hwd.textColor = hwOn ? new Color(ACCENT) : theme.dim;
  dots.addSpacer(8);
  const acd = dots.addText((acOn ? "❄️" : "·") + " AC");
  acd.font = Font.systemFont(11);
  acd.textColor = acOn ? new Color(ACCENT) : theme.dim;

  widget.addSpacer();
  footnote(widget, "Updated " + relTime(data?.ts));
}

function buildMedium(widget, data) {
  const soc = data?.hw?.soc;
  const sc = socColor(soc);

  header(widget);
  widget.addSpacer(6);

  const body = widget.addStack();
  body.centerAlignContent();

  // Left: SOC hero.
  const left = body.addStack();
  left.layoutVertically();
  const heroRow = left.addStack();
  heroRow.bottomAlignContent();
  const hero = heroRow.addText(fmtPct(soc));
  hero.font = Font.boldSystemFont(46);
  hero.textColor = sc;
  hero.minimumScaleFactor = 0.6;
  hero.lineLimit = 1;
  heroRow.addSpacer(5);
  const lbl = heroRow.addText("SOC");
  lbl.font = Font.semiboldSystemFont(13);
  lbl.textColor = theme.dim;
  if (data?.hw?.curtailed) {
    const c = left.addText("⚡ panels curtailed");
    c.font = Font.systemFont(10);
    c.textColor = new Color(ACCENT);
  }

  body.addSpacer();

  // Right: PV + surplus metrics.
  const right = body.addStack();
  right.layoutVertically();
  metric(right, "PV now", fmtW(data?.hw?.pv_total));
  right.addSpacer(8);
  metric(right, "Surplus", fmtW(data?.hw?.surplus_now), new Color(ACCENT));

  widget.addSpacer(10);

  // HW + AC chips row.
  const chips = widget.addStack();
  chips.centerAlignContent();
  const hwOn = String(data?.hw?.hwState).toLowerCase() === "on";
  const acOn = String(data?.ac?.acState).toLowerCase() === "on";
  stateChip(chips, "Hot Water", hwOn, "🔥", "💧");
  chips.addSpacer(10);
  stateChip(chips, "Air-Con", acOn, "❄️", "·");

  widget.addSpacer();
  footnote(widget, "Updated " + relTime(data?.ts) + "  ·  " + SITE_NAME);
}

function buildError(widget, message) {
  applyBackground(widget);
  header(widget);
  widget.addSpacer(8);
  const t = widget.addText("No data");
  t.font = Font.boldSystemFont(20);
  t.textColor = RED;
  widget.addSpacer(4);
  const m = widget.addText("Check relay / URL");
  m.font = Font.systemFont(12);
  m.textColor = theme.text;
  const d = widget.addText(message || "");
  d.font = Font.systemFont(9);
  d.textColor = theme.dim;
  d.lineLimit = 2;
  widget.addSpacer();
  footnote(widget, "Updated " + relTime(Date.now()));
}

// ─────────────────────────────────────────────────────────────────────────────
// Main
// ─────────────────────────────────────────────────────────────────────────────

async function createWidget() {
  const widget = new ListWidget();
  widget.setPadding(14, 14, 14, 14);

  // iOS budgets widget refreshes; ~10 min is the practical floor. This is a hint
  // to the OS, not a guarantee — it may refresh less often to save battery.
  widget.refreshAfterDate = new Date(Date.now() + 10 * 60 * 1000);

  let data = null;
  let err = null;
  try {
    data = await fetchState();
  } catch (e) {
    err = e && e.message ? e.message : String(e);
  }

  if (err || !data) {
    buildError(widget, err);
    return widget;
  }

  applyBackground(widget);

  // config.widgetFamily is undefined when run in-app → treat as medium preview.
  const family = config.widgetFamily || "medium";
  if (family === "small") buildSmall(widget, data);
  else buildMedium(widget, data); // medium + large fall through to the full layout

  return widget;
}

const widget = await createWidget();

if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  // In-app run: show a medium preview so you can confirm it pulls data.
  await widget.presentMedium();
}

Script.complete();
