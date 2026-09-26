// Scripted demo recordings of the Grid Copilot dashboard (assets/demo/).
//
// Needs the backend and the dashboard running, Chrome, ffmpeg on PATH, and
// puppeteer-core installed anywhere Node can find it:
//
//   npm install puppeteer-core            (in a scratch directory)
//   node scripts/record_demo.js stills assets/demo
//   node scripts/record_demo.js tour assets/demo          (also: investigate, trust)
//   ffmpeg -i assets/demo/tour.webm -vf "fps=30,format=yuv420p" -c:v libx264 -crf 23 assets/demo/twin-tour.mp4
//
// The clips use the offline rule-based brain (provider=mock) so they are
// reproducible without an API key. Recording writes real decisions to
// data/decisions.jsonl; delete that file afterwards for a clean demo state.
const puppeteer = require("puppeteer-core");
const path = require("path");

const CHROME = process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const BASE = process.env.DEMO_URL || "http://localhost:5173";
const W = 1440, H = 900;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// A visible cursor with a click ripple, since headless recordings show none.
const CURSOR = `
(() => {
  const add = () => {
    if (document.getElementById('demo-cursor')) return;
    const c = document.createElement('div');
    c.id = 'demo-cursor';
    c.style.cssText = 'position:fixed;left:0;top:0;width:18px;height:18px;border-radius:50%;background:rgba(255,255,255,0.9);border:2px solid #0a0e14;box-shadow:0 0 0 3px rgba(56,189,248,0.55);z-index:99999;pointer-events:none;transform:translate(-50%,-50%);transition:transform 0.08s';
    document.body.appendChild(c);
    document.addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
    document.addEventListener('mousedown', e => {
      c.style.transform = 'translate(-50%,-50%) scale(0.7)';
      const r = document.createElement('div');
      r.style.cssText = 'position:fixed;left:' + e.clientX + 'px;top:' + e.clientY + 'px;width:12px;height:12px;border-radius:50%;border:2px solid #38bdf8;transform:translate(-50%,-50%);z-index:99998;pointer-events:none;transition:all 0.5s ease-out;opacity:1';
      document.body.appendChild(r);
      requestAnimationFrame(() => { r.style.width = '56px'; r.style.height = '56px'; r.style.opacity = '0'; });
      setTimeout(() => r.remove(), 600);
    }, true);
    document.addEventListener('mouseup', () => { c.style.transform = 'translate(-50%,-50%)'; }, true);
  };
  if (document.body) add(); else document.addEventListener('DOMContentLoaded', add);
})();`;

let mouse = { x: W / 2, y: H / 2 };

async function moveTo(page, x, y, steps = 25) {
  await page.mouse.move(x, y, { steps });
  mouse = { x, y };
}

async function clickEl(page, selector, { pause = 500 } = {}) {
  const el = await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  await el.evaluate((n) => n.scrollIntoView({ block: "center", behavior: "smooth" }));
  await sleep(700);
  const box = await el.boundingBox();
  const x = box.x + box.width / 2, y = box.y + box.height / 2;
  await moveTo(page, x, y);
  await sleep(pause);
  await page.mouse.down();
  await sleep(90);
  await page.mouse.up();
}

async function scrollTo(page, y, wait = 1400) {
  await page.evaluate((top) => window.scrollTo({ top, behavior: "smooth" }), y);
  await sleep(wait);
}

async function scrollToEl(page, selector, block = "start", wait = 1400) {
  const el = await page.waitForSelector(selector, { timeout: 60000 });
  await el.evaluate((n, b) => n.scrollIntoView({ block: b, behavior: "smooth" }), block);
  await sleep(wait);
}

async function typeInto(page, selector, text) {
  await clickEl(page, selector, { pause: 250 });
  await page.type(selector, text, { delay: 45 });
}

async function launch() {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: "new",
    args: [`--window-size=${W},${H}`, "--force-dark-mode", "--hide-scrollbars"],
    defaultViewport: { width: W, height: H, deviceScaleFactor: 1 },
  });
  const page = await browser.newPage();
  await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: "dark" }]);
  await page.evaluateOnNewDocument(CURSOR);
  return { browser, page };
}

async function open(page, query) {
  await page.goto(`${BASE}/${query}`, { waitUntil: "networkidle0", timeout: 120000 });
  await sleep(800);
  await moveTo(page, mouse.x, mouse.y, 1);
}

// --- clips -------------------------------------------------------------------------

async function tour(page) {
  await open(page, "?provider=mock");
  await sleep(2500);
  await moveTo(page, 700, 520);
  await sleep(1200);
  await clickEl(page, "::-p-text(\"Altmühl\")");
  await sleep(3200);
  await clickEl(page, "svg text::-p-text(\"TR-04\")");
  await sleep(3500);
  await scrollToEl(page, "::-p-text(\"Condition and lifecycle\")", "start", 2200);
  await sleep(1500);
  await scrollToEl(page, "::-p-text(\"Duval triangle 1\")", "center", 2200);
  await sleep(1800);
  await scrollTo(page, 0, 1600);
  await clickEl(page, "::-p-text(\"Cooling (ONAN/ONAF\")");
  await sleep(4000);
  await clickEl(page, "nav.crumbs ::-p-text(\"Grid\")");
  await sleep(2500);
}

async function investigate(page) {
  await open(page, "?provider=mock&pace=0.3&asset=TR-02");
  await sleep(2000);
  await scrollToEl(page, "::-p-text(\"Investigate alarm\")", "center", 1500);
  await clickEl(page, "::-p-text(\"Investigate alarm\")");
  await sleep(600);
  await scrollToEl(page, "::-p-text(\"Investigation\")", "start", 1200);
  await sleep(9000); // the timeline streams
  await page.waitForSelector("::-p-text(\"Record decision\")", { timeout: 120000 });
  await scrollToEl(page, "::-p-text(\"Proposal · awaiting\")", "start", 1800);
  await sleep(3500);
  await typeInto(page, 'input[placeholder="Your name"]', "A. Engineer");
  await sleep(400);
  await typeInto(page, "textarea.reason", "Agree. Check both conservator levels on Tuesday's visit.");
  await sleep(500);
  await clickEl(page, "::-p-text(\"Record decision\")");
  await sleep(1500);
  await clickEl(page, "::-p-text(\"Show AAS-style submodel export\")");
  await sleep(1200);
  await scrollToEl(page, "::-p-text(\"Decision recorded\")", "start", 1800);
  await sleep(3000);
  await scrollTo(page, 0, 1200);
  await clickEl(page, "::-p-text(\"Decision log\")");
  await sleep(3500);
}

async function trust(page) {
  await open(page, "?provider=mock&pace=0.25&asset=TR-09");
  await sleep(2500);
  await moveTo(page, 330, 505);
  await sleep(1500);
  await scrollToEl(page, "::-p-text(\"Investigate alarm\")", "center", 1500);
  await clickEl(page, "::-p-text(\"Investigate alarm\")");
  await sleep(600);
  await scrollToEl(page, "::-p-text(\"Investigation\")", "start", 1200);
  await sleep(8000);
  await page.waitForSelector("::-p-text(\"Record decision\")", { timeout: 120000 });
  await scrollToEl(page, "::-p-text(\"Proposal · awaiting\")", "start", 1800);
  await sleep(3000);
  await typeInto(page, 'input[placeholder="Your name"]', "A. Engineer");
  await typeInto(page, "textarea.reason", "Lab sample shows normal hydrogen; the monitor is drifting.");
  await clickEl(page, "::-p-text(\"Record decision\")");
  await sleep(1800);
  await scrollTo(page, 0, 1800);
  await sleep(1200);
  await moveTo(page, 330, 505);
  await sleep(3000);
  await clickEl(page, "::-p-text(\"Online DGA monitor\")");
  await sleep(3500);
}

async function stills(page, outdir) {
  const shots = [
    ["01-grid", "?provider=mock", null],
    ["02-substation", "?provider=mock&substation=Altm%C3%BChl", null],
    ["03-transformer", "?provider=mock&asset=TR-04", null],
    ["04-transformer-trends", "?provider=mock&asset=TR-02", "::-p-text(\"Duval triangle 1\")"],
    ["05-component-oltc", "?provider=mock&asset=TR-02&component=oltc", null],
    ["06-sensor-untrusted", "?provider=mock&asset=TR-06&component=load_measurement", null],
  ];
  for (const [name, q, anchor] of shots) {
    await open(page, q);
    await sleep(1500);
    if (anchor) {
      await scrollToEl(page, anchor, "center", 1500);
    }
    await page.mouse.move(-10, -10);
    await page.evaluate(() => { const c = document.getElementById("demo-cursor"); if (c) c.style.display = "none"; });
    await page.screenshot({ path: path.join(outdir, `${name}.png`) });
  }
}

(async () => {
  const [clip, outdir] = process.argv.slice(2);
  const { browser, page } = await launch();
  try {
    if (clip === "stills") {
      await stills(page, outdir);
    } else {
      const rec = await page.screencast({ path: path.join(outdir, `${clip}.webm`) });
      await ({ tour, investigate, trust })[clip](page);
      await sleep(600);
      await rec.stop();
    }
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exit(1); });
