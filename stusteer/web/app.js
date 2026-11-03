// STUSteer phone controller.
//
// What this page does:
//  1. Asks for the PIN once (kept in localStorage), connects a WebSocket to the receiver on the PC.
//  2. Draws the layouts the receiver sends (buttons, pedals, sticks, a wheel), in percent of the screen.
//  3. Keeps the current value of every control on the active layout and sends the whole set as a
//     "snapshot" whenever something changes (and ten times a second as a heartbeat). The PC turns
//     snapshots into Xbox-pad and keyboard output; a missing control means "released".
//  4. Reads the motion sensors for tilt steering: the angle of gravity in the screen plane, fused
//     with the gyroscope so it is both smooth and fast.
'use strict';

(() => {
  const $ = (sel) => document.querySelector(sel);
  const pad = $('#pad');

  // localStorage can throw (private mode, blocked storage); the page must still work.
  const store = {
    get(key, fallback) {
      try {
        const raw = localStorage.getItem('stusteer.' + key);
        return raw === null ? fallback : JSON.parse(raw);
      } catch { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem('stusteer.' + key, JSON.stringify(value)); } catch { /* ignore */ }
    },
  };

  const state = {
    ws: null,
    backoff: 500,
    profile: null,
    layout: null,
    controls: new Map(),   // id -> control object (def, element, helpers)
    values: new Map(),     // id -> current value; only controls that are active are present
    seq: 0,
    lastSend: 0,
    dirty: false,
    pingId: 0,
    pings: new Map(),
    out: null,
    opts: {
      fusion: store.get('fusion', true),
      smooth: store.get('smooth', 0.08),
      haptics: store.get('haptics', true),
    },
  };

  // ---------------------------------------------------------------------------------------------
  // Connection

  function setStatus(kind, text) {
    $('#status').className = 'dot ' + kind;
    $('#rtt').textContent = text;
  }

  async function checkPin(pin) {
    try {
      const res = await fetch('/api/pin?pin=' + encodeURIComponent(pin), { cache: 'no-store' });
      return res.status;
    } catch { return 0; }
  }

  async function connect() {
    const pin = store.get('pin', '');
    if (!pin) { showPin(''); return; }
    setStatus('connecting', 'connecting');
    const status = await checkPin(pin);
    if (status === 403) { store.set('pin', ''); showPin('Wrong PIN.'); return; }
    if (status === 429) { showPin('Too many wrong PINs. Wait a minute.'); return; }
    if (status !== 200) { retryLater(); return; }

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${proto}//${location.host}/ws?pin=${encodeURIComponent(pin)}`);
    state.ws = ws;
    ws.onopen = () => { state.backoff = 500; setStatus('on', 'online'); };
    ws.onmessage = (event) => {
      let msg;
      try { msg = JSON.parse(event.data); } catch { return; }
      onMessage(msg);
    };
    ws.onclose = (event) => {
      if (state.ws === ws) state.ws = null;
      setStatus('off', 'offline');
      if (event.code === 4000) { toast('Another phone took over.'); return; }
      retryLater();
    };
  }

  function retryLater() {
    setStatus('off', 'offline');
    setTimeout(connect, state.backoff);
    state.backoff = Math.min(state.backoff * 2, 3000);
  }

  function onMessage(msg) {
    if (msg.t === 'welcome') {
      state.profile = msg.profile;
      buildTabs();
      selectLayout(store.get('layout', null));
    } else if (msg.t === 'pong') {
      const sent = state.pings.get(msg.id);
      state.pings.delete(msg.id);
      if (sent !== undefined) setStatus('on', `${Math.round(performance.now() - sent)} ms`);
      state.out = msg.out;
      updateLive();
    } else if (msg.t === 'tuned') {
      const c = state.controls.get(msg.id);
      if (c) c.def.curve = msg.curve;
      renderTuneFields();
    } else if (msg.t === 'saved') {
      toast('Saved on the PC.');
    } else if (msg.t === 'error') {
      toast(msg.error);
    }
  }

  function send(obj) {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) state.ws.send(JSON.stringify(obj));
  }

  function sendSnapshot() {
    const c = {};
    for (const [id, v] of state.values) c[id] = v;
    send({ t: 's', seq: ++state.seq, layout: state.layout && state.layout.id, c });
    state.lastSend = performance.now();
    state.dirty = false;
  }

  // Heartbeat: if nothing changed, still send ~10 times a second, so the PC's watchdog knows we
  // are alive. When the page is hidden, timers stop, and the PC releases everything on its own.
  setInterval(() => { if (performance.now() - state.lastSend > 90) sendSnapshot(); }, 100);
  setInterval(() => {
    const id = ++state.pingId;
    state.pings.set(id, performance.now());
    if (state.pings.size > 10) state.pings.delete(state.pings.keys().next().value);
    send({ t: 'ping', id });
  }, 1000);

  // ---------------------------------------------------------------------------------------------
  // Layouts

  function buildTabs() {
    const tabs = $('#tabs');
    tabs.innerHTML = '';
    for (const layout of state.profile.layouts) {
      const b = document.createElement('button');
      b.textContent = layout.name;
      b.dataset.id = layout.id;
      b.addEventListener('click', () => selectLayout(layout.id));
      tabs.appendChild(b);
    }
  }

  function selectLayout(id) {
    const layouts = state.profile.layouts;
    const layout = layouts.find((l) => l.id === id) || layouts[0];
    state.layout = layout;
    store.set('layout', layout.id);
    for (const b of document.querySelectorAll('#tabs button')) b.classList.toggle('active', b.dataset.id === layout.id);
    pad.innerHTML = '';
    state.controls.clear();
    state.values.clear(); // the old layout's controls are released by the next snapshot
    for (const def of layout.controls) {
      const make = MAKERS[def.type];
      if (!make) continue;
      const control = make(def);
      state.controls.set(def.id, control);
      pad.appendChild(control.el);
    }
    $('#btn-center').hidden = !wheelControl();
    sendSnapshot();
    renderTuneSelect();
  }

  function box(def) {
    const el = document.createElement('div');
    el.className = `ctl type-${def.type}` + (def.accent ? ` accent-${def.accent}` : '');
    el.style.left = def.x + '%';
    el.style.top = def.y + '%';
    el.style.width = def.w + '%';
    el.style.height = def.h + '%';
    if (def.type !== 'wheel') {
      const label = document.createElement('div');
      label.className = 'label';
      label.textContent = def.label || def.id;
      el.appendChild(label);
      if (def.hint) {
        const hint = document.createElement('div');
        hint.className = 'hint';
        hint.textContent = def.hint;
        el.appendChild(hint);
      }
    }
    el.addEventListener('contextmenu', (e) => e.preventDefault());
    return el;
  }

  // Track the fingers on one element with pointer capture: a finger that slides off a button keeps
  // it pressed until it lifts, like a real button held down while driving.
  function track(el, { down, move, up }) {
    const fingers = new Set();
    el.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      el.setPointerCapture(e.pointerId);
      fingers.add(e.pointerId);
      down(e, fingers.size);
    });
    el.addEventListener('pointermove', (e) => { if (fingers.has(e.pointerId) && move) move(e); });
    const end = (e) => {
      if (!fingers.delete(e.pointerId)) return;
      up(e, fingers.size);
    };
    el.addEventListener('pointerup', end);
    el.addEventListener('pointercancel', end);
    el.addEventListener('lostpointercapture', end);
  }

  function buzz() {
    if (state.opts.haptics && navigator.vibrate) navigator.vibrate(12);
  }

  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const round = (v, digits) => { const f = 10 ** digits; return Math.round(v * f) / f; };

  function makeButton(def) {
    const el = box(def);
    track(el, {
      down: (e, n) => {
        if (n > 1) return;
        state.values.set(def.id, 1);
        el.classList.add('on');
        buzz();
        sendSnapshot(); // presses go out at once, not on the next frame
      },
      up: (e, n) => {
        if (n > 0) return;
        state.values.delete(def.id);
        el.classList.remove('on');
        sendSnapshot();
      },
    });
    return { def, el };
  }

  function makePedal(def) {
    const el = box(def);
    const fill = document.createElement('div');
    fill.className = 'fill';
    el.prepend(fill);
    const digital = def.mode === 'digital';
    // Analog: the higher the finger on the pedal, the more it is pressed.
    const valueAt = (e) => {
      if (digital) return 1;
      const r = el.getBoundingClientRect();
      return clamp((r.bottom - e.clientY) / r.height, 0, 1);
    };
    const show = (v) => { fill.style.height = (v * 100) + '%'; };
    track(el, {
      down: (e) => {
        const v = valueAt(e);
        state.values.set(def.id, round(v, 3));
        show(v);
        el.classList.add('on');
        buzz();
        sendSnapshot();
      },
      move: (e) => {
        const v = valueAt(e);
        state.values.set(def.id, round(v, 3));
        show(v);
        state.dirty = true;
      },
      up: (e, n) => {
        if (n > 0) return;
        state.values.delete(def.id);
        show(0);
        el.classList.remove('on');
        sendSnapshot();
      },
    });
    return { def, el };
  }

  function makeStick(def) {
    const el = box(def);
    const base = document.createElement('div');
    const knob = document.createElement('div');
    base.className = 'base';
    knob.className = 'knob';
    base.hidden = knob.hidden = true;
    el.append(base, knob);
    let cx = 0, cy = 0, radius = 50;
    const place = (node, x, y, size) => {
      node.style.width = node.style.height = size + 'px';
      node.style.left = (x - size / 2) + 'px';
      node.style.top = (y - size / 2) + 'px';
    };
    // A floating stick: its centre is wherever the thumb lands, which is easier than hitting a
    // fixed circle without looking.
    track(el, {
      down: (e) => {
        const r = el.getBoundingClientRect();
        radius = Math.min(r.width, r.height) * 0.32;
        cx = clamp(e.clientX - r.left, radius, r.width - radius);
        cy = clamp(e.clientY - r.top, radius, r.height - radius);
        place(base, cx, cy, radius * 2);
        base.hidden = knob.hidden = false;
        update(e);
        sendSnapshot();
      },
      move: (e) => { update(e); state.dirty = true; },
      up: (e, n) => {
        if (n > 0) return;
        state.values.delete(def.id);
        base.hidden = knob.hidden = true;
        sendSnapshot();
      },
    });
    function update(e) {
      const r = el.getBoundingClientRect();
      let dx = (e.clientX - r.left - cx) / radius;
      let dy = (e.clientY - r.top - cy) / radius;
      const m = Math.hypot(dx, dy);
      if (m > 1) { dx /= m; dy /= m; }
      place(knob, cx + dx * radius, cy + dy * radius, radius * 0.9);
      state.values.set(def.id, [round(dx, 3), round(-dy, 3)]); // up is positive, as on an Xbox pad
    }
    return { def, el };
  }

  const WHEEL_SVG = `
    <svg viewBox="-100 -100 200 200" aria-hidden="true">
      <g class="rot">
        <circle r="88" fill="none" stroke="#3a4656" stroke-width="16"/>
        <circle r="88" fill="none" stroke="#2f81f7" stroke-width="3" stroke-dasharray="20 533" transform="rotate(-99)"/>
        <circle r="22" fill="#1f2630" stroke="#3a4656" stroke-width="4"/>
        <path d="M-80 4 L-22 4 M22 4 L80 4 M0 22 L0 80" stroke="#3a4656" stroke-width="14" stroke-linecap="round"/>
      </g>
    </svg>`;

  function makeWheel(def) {
    const el = box(def);
    el.innerHTML = WHEEL_SVG;
    const rot = el.querySelector('.rot');
    const label = document.createElement('div');
    label.className = 'wheel-label';
    label.textContent = def.label || '';
    const deg = document.createElement('div');
    deg.className = 'wheel-deg';
    el.append(label, deg);
    const control = { def, el, angle: 0, touching: false };
    control.show = (a) => {
      rot.setAttribute('transform', `rotate(${a.toFixed(1)})`);
      deg.textContent = `${Math.round(a)}°`;
    };

    if (def.mode === 'touch') {
      // Drag the rim around the centre; the angle accumulates past ±180° up to the lock.
      const half = (def.lock || 360) / 2;
      let last = 0;
      const angleAt = (e) => {
        const r = el.getBoundingClientRect();
        return Math.atan2(e.clientX - (r.left + r.width / 2), -(e.clientY - (r.top + r.height / 2))) * 180 / Math.PI;
      };
      track(el, {
        down: (e) => { control.touching = true; last = angleAt(e); buzz(); },
        move: (e) => {
          const a = angleAt(e);
          let d = a - last;
          if (d > 180) d -= 360;
          if (d < -180) d += 360;
          last = a;
          control.angle = clamp(control.angle + d, -half, half);
          state.dirty = true;
        },
        up: (e, n) => { if (n === 0) control.touching = false; },
      });
    }
    return control;
  }

  const MAKERS = { button: makeButton, pedal: makePedal, stick: makeStick, wheel: makeWheel };

  function wheelControl() {
    for (const c of state.controls.values()) if (c.def.type === 'wheel') return c;
    return null;
  }

  // ---------------------------------------------------------------------------------------------
  // Tilt steering
  //
  // accelerationIncludingGravity is the "up" vector in the phone's own axes (x right, y up in
  // portrait, z out of the screen). We rotate it into the current screen orientation, then the
  // wheel angle is the angle of that vector in the screen plane: 0° held upright, positive when the
  // phone is turned clockwise (steering right). This works however far the phone leans back.
  //
  // The accelerometer alone is noisy and lags when smoothed; the gyroscope (rotationRate.alpha,
  // turn rate around the screen's axis) is smooth and instant but drifts. A complementary filter
  // takes the gyro for fast changes and pulls slowly towards the accelerometer angle.

  const tilt = {
    raw: 0,            // filtered angle, degrees
    offset: store.get('tiltOffset', 0),
    ok: false,
    last: null,
    prevAcc: null,
    gyroGain: -1,      // tilt change = gain * alpha * dt; learned below
    learn: { sxy: 0, sxx: 0, n: 0, done: false },
  };
  const FUSION_TAU = 0.3; // seconds: how slowly the gyro estimate is pulled to the accelerometer

  const wrap = (a) => ((a + 540) % 360) - 180;

  function screenAngle() {
    if (screen.orientation && typeof screen.orientation.angle === 'number') return screen.orientation.angle;
    return typeof window.orientation === 'number' ? window.orientation : 0;
  }

  function onMotion(e) {
    const g = e.accelerationIncludingGravity;
    if (!g || g.x === null || g.y === null) return;
    const now = performance.now();
    const dt = tilt.last === null ? 0.016 : Math.min(0.1, (now - tilt.last) / 1000);
    tilt.last = now;

    const a = screenAngle() * Math.PI / 180;
    const sx = g.x * Math.cos(a) - g.y * Math.sin(a);
    const sy = g.x * Math.sin(a) + g.y * Math.cos(a);
    const acc = Math.hypot(sx, sy) > 2 ? Math.atan2(-sx, sy) * 180 / Math.PI : null; // null: phone lies flat
    const rate = e.rotationRate && typeof e.rotationRate.alpha === 'number' ? e.rotationRate.alpha : null;

    learnGyro(acc, rate, dt);
    let angle = tilt.raw;
    if (state.opts.fusion && rate !== null) {
      angle += tilt.gyroGain * rate * dt;
      if (acc !== null) angle += wrap(acc - angle) * (dt / (FUSION_TAU + dt));
    } else if (acc !== null) {
      angle += wrap(acc - angle) * (dt / (state.opts.smooth + dt));
    }
    tilt.raw = wrap(angle);
    tilt.ok = true;
    tilt.prevAcc = acc;
  }

  // Phones disagree on the gyro's sign and units (some old browsers report rad/s). Learn the
  // factor between gyro rate and accelerometer angle change by least squares while the phone
  // turns, then snap it to the nearest of ±1 (deg/s) or ±57.3 (rad/s).
  function learnGyro(acc, rate, dt) {
    const L = tilt.learn;
    if (L.done || acc === null || tilt.prevAcc === null || rate === null) return;
    const da = wrap(acc - tilt.prevAcc);
    const x = rate * dt;
    if (Math.abs(da) < 0.3 || x === 0) return;
    L.sxy += da * x;
    L.sxx += x * x;
    L.n += 1;
    if (L.n >= 60) {
      const gain = L.sxy / L.sxx;
      tilt.gyroGain = Math.sign(gain || -1) * (Math.abs(gain) > 7.5 ? 180 / Math.PI : 1);
      L.done = true;
    }
  }

  function sensorsAvailable() {
    return 'DeviceMotionEvent' in window && window.isSecureContext;
  }

  async function startSensors() {
    if (typeof DeviceMotionEvent !== 'undefined' && typeof DeviceMotionEvent.requestPermission === 'function') {
      try { await DeviceMotionEvent.requestPermission(); } catch { /* iOS only; Android needs none */ }
    }
    window.addEventListener('devicemotion', onMotion);
  }

  // ---------------------------------------------------------------------------------------------
  // Frame loop: the wheel's value and picture, and coalesced sending of analog changes.

  function frame() {
    const wheel = wheelControl();
    if (wheel) {
      if (wheel.def.mode === 'touch') {
        if (!wheel.touching && wheel.def.autocenter !== false && wheel.angle !== 0) {
          wheel.angle = Math.abs(wheel.angle) < 0.5 ? 0 : wheel.angle * 0.75; // spring back
          state.dirty = true;
        }
      } else if (tilt.ok) {
        const angle = round(wrap(tilt.raw - tilt.offset), 2);
        if (angle !== wheel.angle) { wheel.angle = angle; state.dirty = true; }
      }
      state.values.set(wheel.def.id, round(wheel.angle, 2));
      wheel.show(wheel.angle);
      if (wheel.def.mode !== 'touch') {
        const text = tilt.ok ? (wheel.def.label || '') :
          sensorsAvailable() ? 'Waiting for the motion sensor…' : 'Tilt needs HTTPS (see README)';
        const label = wheel.el.querySelector('.wheel-label');
        if (label.textContent !== text) label.textContent = text;
      }
    }
    if (state.dirty) sendSnapshot();
    requestAnimationFrame(frame);
  }

  // ---------------------------------------------------------------------------------------------
  // Settings and tuning

  function updateLive() {
    if ($('#settings').hidden) return;
    const wheel = wheelControl();
    $('#steer-live').hidden = !wheel;
    if (!wheel) return;
    const axis = wheel.def.bind && wheel.def.bind.axis;
    const out = state.out && axis ? state.out.axes[axis] : 0;
    $('#live-tilt').textContent = `${Math.round(wheel.angle)}°`;
    $('#live-out').textContent = (out >= 0 ? '+' : '') + out.toFixed(2);
    const bar = $('#live-bar');
    bar.style.left = (50 + Math.min(0, out) * 50) + '%';
    bar.style.width = (Math.abs(out) * 50) + '%';
  }

  const TUNE_FIELDS = {
    wheel: [['range', 'Full lock at (°)', 10, 450, 1], ['deadzone', 'Deadzone (°)', 0, 15, 0.1]],
    pedal: [['range', 'Full at', 0.3, 1, 0.01], ['deadzone', 'Deadzone', 0, 0.3, 0.01]],
    stick: [['range', 'Full at', 0.3, 1, 0.01], ['deadzone', 'Deadzone', 0, 0.5, 0.01]],
  };
  const COMMON_FIELDS = [['anti_deadzone', 'Anti-deadzone', 0, 0.5, 0.01], ['gamma', 'Curve (gamma)', 0.5, 3, 0.05]];

  function analogControls() {
    return [...state.controls.values()].filter((c) => c.def.curve);
  }

  function renderTuneSelect() {
    const select = $('#tune-id');
    const current = select.value;
    select.innerHTML = '';
    for (const c of analogControls()) {
      const o = document.createElement('option');
      o.value = c.def.id;
      o.textContent = `${c.def.label || c.def.id} (${c.def.type})`;
      select.appendChild(o);
    }
    if ([...select.options].some((o) => o.value === current)) select.value = current;
    renderTuneFields();
  }

  let tuneTimer = null;
  function renderTuneFields() {
    const box = $('#tune-fields');
    box.innerHTML = '';
    const c = state.controls.get($('#tune-id').value);
    if (!c) { box.textContent = 'No analog controls on this layout.'; return; }
    const curve = c.def.curve;
    for (const [field, text, min, max, step] of [...TUNE_FIELDS[c.def.type], ...COMMON_FIELDS]) {
      const row = document.createElement('label');
      row.className = 'row';
      const input = document.createElement('input');
      Object.assign(input, { type: 'range', min, max, step, value: curve[field] });
      const shown = document.createElement('b');
      shown.textContent = curve[field];
      input.addEventListener('input', () => {
        shown.textContent = input.value;
        clearTimeout(tuneTimer);
        tuneTimer = setTimeout(() => send({ t: 'tune', id: c.def.id, curve: { [field]: Number(input.value) } }), 80);
      });
      row.append(text + ' ', input, shown);
      box.appendChild(row);
    }
    if (c.def.type !== 'pedal') {
      const row = document.createElement('label');
      row.className = 'row';
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = !!curve.invert;
      input.addEventListener('change', () => send({ t: 'tune', id: c.def.id, curve: { invert: input.checked } }));
      row.append(input, ' Invert');
      box.appendChild(row);
    }
  }

  function openSettings() {
    $('#opt-fusion').checked = state.opts.fusion;
    $('#opt-smooth').value = state.opts.smooth;
    $('#opt-smooth-v').textContent = state.opts.smooth + ' s';
    $('#opt-haptics').checked = state.opts.haptics;
    $('#conn-info').textContent =
      `${location.host} · ${location.protocol === 'https:' ? 'HTTPS' : 'HTTP'} · ` +
      `sensors: ${sensorsAvailable() ? 'available' : 'unavailable (needs HTTPS)'} · ` +
      `gyro: ${tilt.learn.done ? 'calibrated' : 'learning'} (gain ${tilt.gyroGain.toFixed(2)})`;
    renderTuneSelect();
    $('#settings').hidden = false;
    updateLive();
  }

  $('#btn-settings').addEventListener('click', openSettings);
  $('#settings-close').addEventListener('click', () => { $('#settings').hidden = true; });
  $('#opt-fusion').addEventListener('change', (e) => { state.opts.fusion = e.target.checked; store.set('fusion', state.opts.fusion); });
  $('#opt-smooth').addEventListener('input', (e) => {
    state.opts.smooth = Number(e.target.value);
    $('#opt-smooth-v').textContent = state.opts.smooth + ' s';
    store.set('smooth', state.opts.smooth);
  });
  $('#opt-haptics').addEventListener('change', (e) => { state.opts.haptics = e.target.checked; store.set('haptics', state.opts.haptics); });
  $('#tune-id').addEventListener('change', renderTuneFields);
  $('#tune-reset').addEventListener('click', () => send({ t: 'tune_reset', id: $('#tune-id').value }));
  $('#tune-save').addEventListener('click', () => send({ t: 'save' }));
  $('#forget-pin').addEventListener('click', () => {
    store.set('pin', '');
    if (state.ws) state.ws.close();
    location.reload();
  });
  setInterval(updateLive, 100);

  $('#btn-center').addEventListener('click', () => {
    const wheel = wheelControl();
    if (!wheel) return;
    if (wheel.def.mode === 'touch') {
      wheel.angle = 0;
    } else {
      tilt.offset = tilt.raw;
      store.set('tiltOffset', tilt.offset);
    }
    state.dirty = true;
    toast('Wheel centred');
  });

  // ---------------------------------------------------------------------------------------------
  // Full screen, screen wake lock, pairing, toasts

  let wakeLock = null;
  async function keepAwake() {
    try {
      if ('wakeLock' in navigator && !wakeLock) {
        wakeLock = await navigator.wakeLock.request('screen');
        wakeLock.addEventListener('release', () => { wakeLock = null; });
      }
    } catch { /* not allowed yet; retried on the next tap */ }
  }

  $('#btn-full').addEventListener('click', async () => {
    try {
      if (!document.fullscreenElement) {
        await document.documentElement.requestFullscreen({ navigationUI: 'hide' });
        if (screen.orientation && screen.orientation.lock) await screen.orientation.lock('landscape').catch(() => {});
      } else {
        await document.exitFullscreen();
      }
    } catch { toast('Full screen is not available here.'); }
  });

  document.addEventListener('pointerdown', keepAwake, { passive: true });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      state.values.clear(); // release everything before the browser stops running us
      sendSnapshot();
    } else {
      keepAwake();
    }
  });

  function showPin(error) {
    $('#pin-panel').hidden = false;
    $('#pin-error').textContent = error || '';
    setTimeout(() => $('#pin-input').focus(), 50);
  }

  $('#pin-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const pin = $('#pin-input').value.trim();
    if (!/^\d{6}$/.test(pin)) { $('#pin-error').textContent = 'The PIN has 6 digits.'; return; }
    const status = await checkPin(pin);
    if (status === 200) {
      store.set('pin', pin);
      $('#pin-panel').hidden = true;
      connect();
    } else {
      $('#pin-error').textContent = status === 403 ? 'Wrong PIN.' :
        status === 429 ? 'Too many wrong PINs. Wait a minute.' : 'Cannot reach the receiver.';
    }
  });

  let toastTimer = null;
  function toast(text) {
    const t = $('#toast');
    t.textContent = text;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 2000);
  }

  // A pin in the address (?pin=123456) pairs without typing; it is removed from the address bar.
  const params = new URLSearchParams(location.search);
  if (/^\d{6}$/.test(params.get('pin') || '')) {
    store.set('pin', params.get('pin'));
    history.replaceState(null, '', location.pathname);
  }

  if (sensorsAvailable()) startSensors();
  requestAnimationFrame(frame);
  connect();

  // For the automated end-to-end test only: lets it feed sensor values and read state.
  window.__stusteer = { state, tilt, onMotion };
})();
