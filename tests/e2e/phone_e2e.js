// End-to-end test: a headless Chromium emulating an Android phone drives a real receiver in --dry-run.
//
//   node tests/e2e/phone_e2e.js [screenshot-folder]
//
// Needs Node and the 'playwright' package with its Chromium (optional dev tool; the receiver does
// not need it). Starts `python -m stusteer run --dry-run` over HTTPS on a free port, opens the page
// as a 915x412 landscape touch phone, then checks that tilt, pedals, buttons and the watchdog reach
// the receiver by reading its dry-run log.
'use strict';

const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..', '..');
const SHOTS = process.argv[2] || null;
const PIN = '246810';
const PORT = 18000 + Math.floor(Math.random() * 1000);

function startReceiver() {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'stusteer-e2e-'));
  const python = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  const proc = spawn(python, ['-m', 'stusteer', 'run', '--dry-run', '--port', String(PORT), '--host', '127.0.0.1', '--pin', PIN],
    { cwd: ROOT, env: { ...process.env, STUSTEER_HOME: home, PYTHONUNBUFFERED: '1' } });
  const log = [];
  proc.stdout.on('data', (d) => log.push(...d.toString().split('\n').filter(Boolean)));
  proc.stderr.on('data', (d) => log.push(...d.toString().split('\n').filter(Boolean)));
  return { proc, log, home };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(fn, what, timeout = 5000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const v = fn();
    if (v) return v;
    await sleep(25);
  }
  throw new Error('timed out waiting for ' + what);
}

function lastPad(log) {
  const lines = log.filter((l) => l.includes(' PAD buttons='));
  return lines[lines.length - 1] || '';
}

function axis(line, name) {
  const m = line.match(new RegExp(name + '=([+-][0-9.]+)'));
  return m ? Number(m[1]) : 0;
}

let failures = 0;
function check(ok, text) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${text}`);
  if (!ok) failures += 1;
}

(async () => {
  const rx = startReceiver();
  await waitFor(() => rx.log.some((l) => l.includes('PIN:')), 'the receiver to start', 15000);
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 915, height: 412 },
    screen: { width: 915, height: 412 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
    ignoreHTTPSErrors: true, // like tapping "Proceed" on Chrome's certificate warning
    userAgent: 'Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36',
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  const cdp = await context.newCDPSession(page);
  const touch = (type, points) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: points });
  const centre = async (sel) => {
    const b = await page.locator(sel).boundingBox();
    return { x: b.x + b.width / 2, y: b.y + b.height / 2, b };
  };

  try {
    await page.goto(`https://127.0.0.1:${PORT}/?pin=${PIN}`);
    await page.waitForSelector('#tabs button.active');
    await waitFor(() => rx.log.some((l) => l.includes('phone connected')), 'the phone to connect');
    check(true, 'phone page loads over HTTPS and pairs with the PIN from the address');
    check(await page.evaluate(() => window.isSecureContext && 'DeviceMotionEvent' in window),
      'secure context: the motion sensors are available to the page');
    check(await page.evaluate(() => location.search === ''), 'the PIN is removed from the address bar');
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, '1-drive-tilt.png') });

    // Tilt: feed accelerometer readings for a phone turned 30 degrees clockwise (steering right).
    await page.evaluate(async () => {
      const angle = (screen.orientation ? screen.orientation.angle : 0) * Math.PI / 180;
      const tiltDeg = 30, g = 9.81, t = tiltDeg * Math.PI / 180;
      const sx = -g * Math.sin(t), sy = g * Math.cos(t);     // the "up" vector as the screen sees it
      const x = sx * Math.cos(angle) + sy * Math.sin(angle);  // rotated back into the phone's axes
      const y = -sx * Math.sin(angle) + sy * Math.cos(angle);
      for (let i = 0; i < 120; i++) {
        window.dispatchEvent(new DeviceMotionEvent('devicemotion', {
          accelerationIncludingGravity: { x, y, z: 3 }, rotationRate: { alpha: 0, beta: 0, gamma: 0 }, interval: 16,
        }));
        await new Promise((r) => setTimeout(r, 16));
      }
    });
    await sleep(150);
    const expected = (30 - 0.8) / (60 - 0.8); // the default wheel curve: 0.8 deg deadzone, full lock at 60
    const lx = axis(lastPad(rx.log), 'LX');
    check(Math.abs(lx - expected) < 0.04, `tilt 30 deg right -> LX ${lx.toFixed(3)} (expected ~${expected.toFixed(3)})`);
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, '2-drive-tilted.png') });

    // Centre button: the current tilt becomes straight ahead.
    await page.click('#btn-center');
    await waitFor(() => Math.abs(axis(lastPad(rx.log), 'LX')) < 0.01, 'the wheel to centre');
    check(true, 'Centre makes the current tilt straight ahead (LX back to 0)');

    // Gas pedal held at 75 % of its height.
    const gas = await centre('.type-pedal.accent-green');
    const gy = gas.b.y + gas.b.height * 0.25;
    await touch('touchStart', [{ x: gas.x, y: gy, id: 1 }]);
    await waitFor(() => axis(lastPad(rx.log), 'RT') > 0.5, 'the gas pedal');
    const rt = axis(lastPad(rx.log), 'RT');
    const t75 = (0.75 - 0.02) / 0.98, s75 = t75 * t75 * (3 - 2 * t75); // default S-curve
    check(Math.abs(rt - s75) < 0.04, `gas pedal (thumb height) at 75 % -> RT ${rt.toFixed(3)} (S-curve, expected ~${s75.toFixed(3)})`);

    // While still on the gas, a second finger taps Handbrake (multitouch).
    const hb = await centre('text=Handbrake');
    await touch('touchStart', [{ x: gas.x, y: gy, id: 1 }, { x: hb.x, y: hb.y, id: 2 }]);
    await waitFor(() => lastPad(rx.log).includes('buttons=[RB]'), 'the handbrake');
    check(axis(lastPad(rx.log), 'RT') > 0.5, 'multitouch: gas stays down while the handbrake is pressed');
    await touch('touchEnd', [{ x: gas.x, y: gy, id: 1 }]);
    await touch('touchEnd', []);
    await waitFor(() => lastPad(rx.log).includes('buttons=[]') && axis(lastPad(rx.log), 'RT') === 0, 'release');
    check(true, 'lifting both fingers releases RB and RT');

    // An LSPDFR key on the Drive layout: Backup -> keyboard B.
    const bk = await centre('text=Backup');
    await touch('touchStart', [{ x: bk.x, y: bk.y, id: 3 }]);
    await sleep(20);
    await touch('touchEnd', []);
    await waitFor(() => rx.log.some((l) => l.includes('KEY B up')), 'B to be released');
    check(rx.log.some((l) => l.includes('KEY B down')), 'Backup button -> key B down, then up (after the minimum hold)');

    // Other layouts, for the screenshots and for a key combo on the LSPDFR page.
    await page.click('#tabs button:has-text("LSPDFR")');
    const stop = await centre('text=Traffic stop');
    await touch('touchStart', [{ x: stop.x, y: stop.y, id: 4 }]);
    await waitFor(() => rx.log.some((l) => l.includes('KEY LSHIFT down')), 'L-Shift');
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, '3-lspdfr.png') });
    await touch('touchEnd', []);
    await waitFor(() => rx.log.some((l) => l.includes('KEY LSHIFT up')), 'L-Shift up');
    check(true, 'LSPDFR page: Traffic stop holds Left Shift while pressed');

    await page.click('#tabs button:has-text("On foot")');
    const ls = await centre('.type-stick >> nth=0');
    await touch('touchStart', [{ x: ls.x, y: ls.y, id: 5 }]);
    await touch('touchMove', [{ x: ls.x, y: ls.y - 60, id: 5 }]);
    await waitFor(() => axis(lastPad(rx.log), 'LY') > 0.5, 'the left stick');
    check(axis(lastPad(rx.log), 'LY') > 0.5, `left stick pushed up -> LY ${axis(lastPad(rx.log), 'LY').toFixed(3)}`);
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, '4-on-foot.png') });
    await touch('touchEnd', []);

    await page.click('#tabs button:has-text("Drive (touch)")');
    const wheel = await centre('.type-wheel');
    const r = Math.min(wheel.b.width, wheel.b.height) * 0.4;
    await touch('touchStart', [{ x: wheel.x, y: wheel.y - r, id: 6 }]);
    for (let a = 0; a <= 90; a += 10) {
      const rad = a * Math.PI / 180;
      await touch('touchMove', [{ x: wheel.x + r * Math.sin(rad), y: wheel.y - r * Math.cos(rad), id: 6 }]);
      await sleep(16);
    }
    await sleep(100);
    const tlx = axis(lastPad(rx.log), 'LX');
    check(Math.abs(tlx - (90 - 2) / (180 - 2)) < 0.05, `touch wheel turned 90 deg -> LX ${tlx.toFixed(3)}`);
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, '5-drive-touch.png') });
    await touch('touchEnd', []);
    await waitFor(() => Math.abs(axis(lastPad(rx.log), 'LX')) < 0.01, 'the wheel to spring back');
    check(true, 'touch wheel springs back to centre when released');

    await page.click('#btn-settings');
    await page.waitForSelector('#settings:not([hidden])');
    await page.selectOption('#tune-id', 't_wheel');
    await page.locator('#tune-fields input[type=range]').first().fill('120');
    await waitFor(() => rx.log.some((l) => l.includes("tuned phone/t_wheel: curve={'range': 120}")), 'the tune message');
    check(true, 'tuning slider changes the curve on the PC live');
    await page.click('#settings-close');

    // Pedal B mode: switch the gas pedal to "slide from touch" in the tuning panel.
    await page.click('#tabs button:has-text("Drive (tilt)")');
    await page.click('#btn-settings');
    await page.selectOption('#tune-id', 'gas');
    await page.locator('#tune-fields select').first().selectOption('relative');
    await waitFor(() => rx.log.some((l) => l.includes("tuned phone/gas: curve={} options={'mode': 'relative'}")), 'mode change');
    await page.waitForSelector('#tune-fields input[type=range]');
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, '6-settings.png') });
    await page.click('#settings-close');
    const g3 = await centre('.type-pedal.accent-green');
    const top = g3.b.y + g3.b.height * 0.2; // the thumb lands high, where thumb-height mode gives ~80 %
    await touch('touchStart', [{ x: g3.x, y: top, id: 8 }]);
    await sleep(80);
    check(axis(lastPad(rx.log), 'RT') === 0, 'slide-from-touch: landing high on the pedal starts at 0 (no jump)');
    const g3b = await centre('.type-pedal.accent-green');
    await touch('touchStart', [{ x: g3.x, y: g3b.b.y + 4, id: 8 }]); // slide up as far as it goes
    await touch('touchMove', [{ x: g3.x, y: g3b.b.y + 4, id: 8 }]);
    await waitFor(() => axis(lastPad(rx.log), 'RT') > 0.2, 'relative travel');
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, '7-relative-pedal.png') });
    await touch('touchEnd', []);
    await waitFor(() => axis(lastPad(rx.log), 'RT') === 0, 'release');
    check(true, 'slide-from-touch: sliding up from the touch point raises RT, lifting releases it');

    // Watchdog: hold the handbrake, then the phone goes silent (browser frozen, Wi-Fi lost).
    const hb2 = await centre('text=Handbrake');
    await touch('touchStart', [{ x: hb2.x, y: hb2.y, id: 7 }]);
    await waitFor(() => lastPad(rx.log).includes('buttons=[RB]'), 'handbrake held');
    await cdp.send('Emulation.setScriptExecutionDisabled', { value: true }).catch(() => {});
    await page.evaluate(() => { window.__stusteer.state.ws.send = () => {}; }).catch(() => {});
    await waitFor(() => rx.log.some((l) => l.includes('releasing everything')), 'the watchdog', 3000);
    check(lastPad(rx.log).includes('buttons=[]'), 'watchdog: phone goes silent with the handbrake held -> released within 0.5 s');
    check(errors.length === 0, 'no JavaScript errors' + (errors.length ? ': ' + errors.join(' | ') : ''));
  } catch (e) {
    check(false, String(e && e.stack || e));
  } finally {
    await browser.close();
    rx.proc.kill('SIGINT');
    await sleep(300);
    if (process.env.E2E_LOG) fs.writeFileSync(process.env.E2E_LOG, rx.log.join('\n'));
    fs.rmSync(rx.home, { recursive: true, force: true });
  }
  console.log(failures ? `${failures} check(s) failed` : 'all checks passed');
  process.exit(failures ? 1 : 0);
})();
