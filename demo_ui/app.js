/* ============================================================
   ITSU · Facial Recognition Device UI — behaviour
   States: idle · success · failed
   ============================================================ */

(() => {
  "use strict";

  /* Inline SVG icons (white). Kept inline so the overlay renders instantly
     with no extra network request on the device. */
  // Path data is the exact source artwork (viewBox 23.2 x 28.8) — shape unchanged.
  // Success is structured so the reveal can be animated: left dot → smile wipe → right dot.
  const P = {
    smileUp:   "M8.1,18.5c0.3,1.8,2,3.1,3.8,2.8c1.8,0,3.2-1.5,3.2-3.2c0.1-1,0.1-2.1,0.1-3.1c0-0.5,0-1,0.1-1.5c0.4-2,2.4-3.3,4.4-2.9c1.9,0.2,3.3,1.7,3.3,3.6c0.2,2.5,0,5.1-0.8,7.5c-1.1,3.5-4,6.1-7.6,6.8c-2.3,0.5-4.7,0.5-7-0.2c-3.3-0.9-5.9-3.5-6.9-6.8c-0.6-1.8-0.8-3.7-0.7-5.6c0-0.6,0-1.2,0.1-1.8c0-1.4,0.9-2.7,2.2-3.3c1.5-0.6,3.1-0.4,4.3,0.6c0.7,0.6,1.1,1.4,1.2,2.3C8,15.2,7.8,16.9,8.1,18.5z",
    smileDown: "M15.1,20.7c-0.3-1.8-2-3.1-3.8-2.8c-1.8,0-3.2,1.5-3.2,3.2C8,22.1,8,23.2,8,24.2c0,0.5-0.1,1-0.2,1.6c-0.4,2-2.4,3.3-4.4,2.9c-1.9-0.2-3.3-1.7-3.3-3.6c-0.2-2.5,0-5.1,0.8-7.5c1-3.5,4-6.2,7.6-6.9c2.3-0.5,4.7-0.5,7,0.2c3.3,0.9,5.9,3.5,6.9,6.8c0.6,1.8,0.8,3.7,0.7,5.6c0,0.6,0,1.2-0.1,1.8c0,1.4-0.9,2.7-2.2,3.2c-1.4,0.6-3.1,0.4-4.3-0.6c-0.7-0.6-1.1-1.4-1.2-2.3C15.2,24,15.4,22.3,15.1,20.7z",
    dotRight:  "M19.1,8.7c-2.2,0.1-4-1.7-4.1-3.9c0-0.1,0-0.1,0-0.2c0-2.3,1.7-4.1,4-4.2s4.1,1.7,4.2,4C23.2,6.8,21.4,8.6,19.1,8.7L19.1,8.7L19.1,8.7z",
    dotLeft:   "M4,8.5c-2.2,0-4-1.8-4-4c0-0.1,0-0.1,0-0.1c0-2.2,1.9-4,4.1-4s4,1.9,4,4.1S6.2,8.5,4,8.5L4,8.5z",
  };

  // Reveal mask centerlines — a stroke is "drawn" along the mouth's centerline
  // (stroke-dashoffset) so the complete, fixed-thickness shape is exposed like a
  // brush stroke. Round linecap = rounded leading edge; the stroke is wide enough
  // to fully cover the mouth band, so the shape never scales or stretches.
  const MOUTH_CENTER = {
    // Smile: left tip → down through the bottom → right tip.
    smile: "M1.2,12.45 Q3.65,16.5 5.75,19.95 Q7.85,23.4 9.675,24.125 Q11.5,24.85 13.175,24.075 Q14.85,23.3 16.775,20.825 Q18.7,18.35 21.35,12.4",
    // Frown: the smile centerline rotated 180° (matches smileDown), still drawn L→R.
    frown: "M1.85,26.8 Q4.5,20.85 6.425,18.375 Q8.35,15.9 10.025,15.125 Q11.7,14.35 13.525,15.075 Q15.35,15.8 17.45,19.25 Q19.55,22.7 22,26.75",
  };

  // Round-capped stroke drawn along `centerline`, blurred slightly so the leading
  // edge feathers softly. Used as a moving reveal mask over the solid mouth shape.
  // The round caps are what fully cover the mouth's rounded tips at the end of the
  // draw (butt caps would clip them flat). To keep those caps from leaking a disc of
  // the mouth through the mask while the draw is parked at rest, the dash is offset by
  // one cap radius so the caps sit just off the path when hidden — see the
  // stroke-dashoffset math in styles.css (.smile-draw / .frown-draw).
  const drawMask = (id, centerline, drawClass) =>
    `<filter id="${id}-feather" x="-40%" y="-60%" width="180%" height="220%">` +
      `<feGaussianBlur stdDeviation="0.4"/>` +
    `</filter>` +
    `<mask id="${id}" maskUnits="userSpaceOnUse" x="-6" y="-6" width="36" height="42">` +
      `<path class="${drawClass}" d="${centerline}" fill="none" stroke="#fff" ` +
        `stroke-width="12" stroke-linecap="round" stroke-linejoin="round" ` +
        `pathLength="100" filter="url(#${id}-feather)"/>` +
    `</mask>`;

  const ICONS = {
    success:
      `<svg class="face face--success" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 23.2 28.8" fill="#fff" aria-hidden="true">` +
        `<defs>${drawMask("smileMask", MOUTH_CENTER.smile, "smile-draw")}</defs>` +
        `<path class="dot dot--left" d="${P.dotLeft}"/>` +
        `<path class="smile" d="${P.smileUp}" mask="url(#smileMask)"/>` +
        `<path class="dot dot--right" d="${P.dotRight}"/>` +
      `</svg>`,
    // Failed reveal is structured the same way: left dot → right dot → frown draw.
    failed:
      `<svg class="face face--failed" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 23.2 28.8" fill="#fff" aria-hidden="true">` +
        `<defs>${drawMask("frownMask", MOUTH_CENTER.frown, "frown-draw")}</defs>` +
        `<path class="dot dot--left" d="${P.dotLeft}"/>` +
        `<path class="dot dot--right" d="${P.dotRight}"/>` +
        `<path class="frown" d="${P.smileDown}" mask="url(#frownMask)"/>` +
      `</svg>`,
  };

  /* Static brand mark for the screensaver — the smile with its two dots.
     Reuses the exact source artwork; no animation, no reveal mask. */
  const BRAND_FACE =
    `<svg class="brand-face" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 23.2 28.8" fill="#fff" aria-hidden="true">` +
      `<path d="${P.dotLeft}"/>` +
      `<path d="${P.smileUp}"/>` +
      `<path d="${P.dotRight}"/>` +
    `</svg>`;

  const DEFAULT_NAME = "Emma";

  const els = {
    body: document.body,
    video: document.getElementById("camera"),
    icon: document.getElementById("icon"),
    primary: document.getElementById("line-primary"),
    secondary: document.getElementById("line-secondary"),
    cameraError: document.getElementById("camera-error"),
    devpanel: document.getElementById("devpanel"),
    // Screensaver
    screensaver: document.getElementById("screensaver"),
    brand: document.getElementById("screensaver-brand"),
    clock: document.getElementById("clock"),
    hint: document.getElementById("saver-hint"),
    companyLogo: document.getElementById("company-logo"),
    attendance: document.getElementById("attendance"),
    // Camera-state code button (commented out in index.html -- kept here for
    // possible future re-enable; getElementById returns null harmlessly).
    // codeBtn: document.getElementById("code-btn"),
    codeDisplay: document.getElementById("code-display"),
    keypadGrid: document.getElementById("keypad-grid"),
    keypadBack: document.getElementById("keypad-back"),
  };

  /* Demo code for the built-in keypad flow. The real system should verify the
     code itself and drive the result via deviceUI.codeApproved()/codeRejected();
     override this default with deviceUI.setExpectedCode("…"). */
  let expectedCode = "1234";
  const MAX_CODE_LEN = 8;
  let entered = "";

  /* ---------------------------------------------- State machine */

  function render(state, opts = {}) {
    switch (state) {
      case "success": {
        const name = (opts.name || DEFAULT_NAME).trim();
        els.icon.innerHTML = ICONS.success;
        // Single line, one size: "Hello Emma"
        els.primary.textContent = `Hello ${name}`;
        els.secondary.textContent = "";
        els.secondary.hidden = true;
        break;
      }
      case "failed": {
        els.icon.innerHTML = ICONS.failed;
        els.primary.textContent = "Verification Failed";
        els.secondary.textContent = "Please try again";
        els.secondary.hidden = false;
        break;
      }
      /* ---- New states (existing success/failed above are unchanged) ---- */
      case "screensaver":
      case "screensaver-basic": {
        // Resting screen: brand mark + clock + attendance toggle + logo.
        // Two variants share the identical surface — "screensaver" keeps the
        // Check In / Check Out toggle (subject to attendance config), while
        // "screensaver-basic" is the same idle screen with attendance omitted.
        els.brand.innerHTML = BRAND_FACE;
        updateClock();
        els.icon.innerHTML = "";
        els.primary.textContent = "";
        els.secondary.textContent = "";
        els.secondary.hidden = true;
        break;
      }
      case "keypad": {
        // Fresh code-entry screen.
        setEntered("");
        els.icon.innerHTML = "";
        els.primary.textContent = "";
        els.secondary.textContent = "";
        els.secondary.hidden = true;
        break;
      }
      case "code-success": {
        // Same success visual, but entry was by code — the identity is unknown,
        // so greet without a name.
        els.icon.innerHTML = ICONS.success;
        els.primary.textContent = "Hello";
        els.secondary.textContent = "";
        els.secondary.hidden = true;
        break;
      }
      case "code-failed": {
        els.icon.innerHTML = ICONS.failed;
        els.primary.textContent = "Verification Failed";
        els.secondary.textContent = "Please try again";
        els.secondary.hidden = false;
        break;
      }
      case "idle":
      default:
        state = "idle";
        els.icon.innerHTML = "";
        els.primary.textContent = "";
        els.secondary.textContent = "";
        els.secondary.hidden = true;
        break;
    }
    els.body.dataset.state = state;
  }

  /* Public API — the recognition backend drives the UI through this.
       deviceUI.success("Emma"); deviceUI.failed(); deviceUI.idle();
     Optional auto-return to idle after `hold` ms. */
  let holdTimer = null;
  function setState(state, opts = {}) {
    clearTimeout(holdTimer);
    render(state, opts);
    // "Face Not Recognized" auto-dismisses back to the live camera so the user
    // can immediately try again — mirrors the wrong-PIN flow. Any explicit
    // hold passed by the caller wins.
    if (state === "failed" && !opts.hold) {
      opts = { ...opts, hold: 2500, then: "idle" };
    }
    if (opts.hold && state !== "idle") {
      // Auto-return after `hold` ms — to idle by default, or opts.then if given.
      const next = opts.then || "idle";
      holdTimer = setTimeout(() => setState(next), opts.hold);
    }
  }

  window.deviceUI = {
    setState,
    idle: () => setState("idle"),
    // Camera state is the existing "idle" screen (state 1).
    camera: () => setState("idle"),
    // Idle / resting screen. screensaver() keeps the Check In / Check Out
    // toggle (subject to attendance config); screensaverBasic() is the same
    // idle screen without any attendance control.
    screensaver: () => setState("screensaver"),
    screensaverBasic: () => setState("screensaver-basic"),
    success: (name, hold) => setState("success", { name, hold }),
    failed: (hold) => setState("failed", { hold }),
    // Code-entry flow
    codeEntry: () => setState("keypad"),
    codeApproved: (hold) => setState("code-success", { hold }),
    codeRejected: (hold) => setState("code-failed", { hold }),
    setExpectedCode: (code) => { expectedCode = String(code); },
    setLogo: (src) => setLogo(src),
    setHintText: (text) => { if (els.hint) els.hint.textContent = text; },
    // Employee attendance — controlled by the admin/device-management system.
    // The Check In / Check Out toggle only appears while enabled.
    setAttendanceEnabled: (on) => setAttendanceEnabled(on),
    setAttendanceMode: (mode) => setAttendanceMode(mode),
    // Current toggle value ("in" | "out") — read when the device wakes.
    getAttendanceMode: () => els.attendance?.dataset.mode || "in",
  };

  /* ---------------------------------------------- Screensaver (clock + logo) */

  function updateClock() {
    const now = new Date();
    const hh = String(now.getHours()).padStart(2, "0");
    const mm = String(now.getMinutes()).padStart(2, "0");
    els.clock.textContent = `${hh}:${mm}`;
  }

  /* Drop in a per-company logo. Pass a URL/data-URI, or "" to restore the
     placeholder. Companies can also just replace the markup in index.html. */
  function setLogo(src) {
    if (src) {
      els.companyLogo.innerHTML =
        `<img src="${src}" alt="Company logo" class="screensaver__logo-img">`;
      els.companyLogo.dataset.empty = "false";
    } else {
      els.companyLogo.innerHTML =
        `<span class="screensaver__logo-placeholder">YOUR&nbsp;LOGO</span>`;
      els.companyLogo.dataset.empty = "true";
    }
  }

  /* ---------------------------------------------- Employee attendance toggle */

  /* Enable/disable from the admin system. When disabled the toggle is not
     rendered at all (CSS hides it); the resting screen is just clock + logo. */
  function setAttendanceEnabled(on) {
    els.screensaver.dataset.attendance = on ? "enabled" : "disabled";
  }

  function setAttendanceMode(mode) {
    if (mode !== "in" && mode !== "out") return;
    els.attendance.dataset.mode = mode;
    els.attendance.querySelectorAll(".attendance__opt").forEach((btn) => {
      const active = btn.dataset.mode === mode;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-pressed", String(active));
    });
  }

  function initAttendance() {
    els.attendance.addEventListener("click", (e) => {
      const opt = e.target.closest(".attendance__opt");
      if (!opt) return;
      // Switching Check In / Check Out must not wake the device.
      e.stopPropagation();
      setAttendanceMode(opt.dataset.mode);
    });
  }

  /* ---------------------------------------------- Code entry (keypad) */

  function setEntered(value) {
    entered = value;
    els.codeDisplay.textContent = entered;
    els.codeDisplay.dataset.empty = entered ? "false" : "true";
  }

  function pressKey(key) {
    if (key === "back") {
      setEntered(entered.slice(0, -1));
    } else if (key === "ok") {
      submitCode();
    } else if (/^\d$/.test(key) && entered.length < MAX_CODE_LEN) {
      setEntered(entered + key);
    }
  }

  function submitCode() {
    if (!entered) return;
    const ok = entered === expectedCode;
    if (ok) {
      // Approved → greet (no name), then return to the resting screensaver.
      setState("code-success", { hold: 4000, then: "screensaver" });
    } else {
      // Wrong → show failure, then reopen the keypad to try again.
      setState("code-failed", { hold: 3000, then: "keypad" });
    }
  }

  function initCodeFlow() {
    // Camera-state button opens the keypad. Commented out — the "Enter a
    // code" button is currently removed from index.html (no PIN fallback in
    // the current flow), so els.codeBtn is undefined. Re-enable both together
    // if a PIN-entry state is needed again in the future.
    // els.codeBtn.addEventListener("click", () => setState("keypad"));

    // Keypad taps.
    els.keypadGrid.addEventListener("click", (e) => {
      const key = e.target.closest("[data-key]")?.dataset.key;
      if (key) pressKey(key);
    });

    // Back out of the keypad to the camera state.
    els.keypadBack.addEventListener("click", () => setState("idle"));

    // Tapping the screensaver used to act as the physical wake button in this
    // preview. Commented out — waking is now driven entirely from Python via
    // the tap listener installed by BRIDGE_SETUP_JS in gui_web/web_window.py,
    // so the JS-side state and the Python session state can't get out of sync.
    // document
    //   .getElementById("screensaver")
    //   .addEventListener("click", () => setState("idle"));
  }

  /* ---------------------------------------------- Camera */

  async function startCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      return showCameraError();
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 1280 } },
        audio: false,
      });
      els.video.srcObject = stream;
      els.cameraError.hidden = true;
    } catch (err) {
      console.warn("Camera could not start:", err);
      showCameraError();
    }
  }

  function showCameraError() {
    els.cameraError.hidden = false;
  }

  /* ---------------------------------------------- Preview controls */

  function initDevControls() {
    els.devpanel.addEventListener("click", (e) => {
      const cmd = e.target.closest("[data-cmd]")?.dataset.cmd;
      if (cmd) setState(cmd);
    });

    document.addEventListener("keydown", (e) => {
      if (e.target.matches("input, textarea")) return;

      // While the keypad is open, the number keys type the code instead of
      // switching preview states.
      if (els.body.dataset.state === "keypad") {
        if (/^\d$/.test(e.key)) { pressKey(e.key); e.preventDefault(); return; }
        if (e.key === "Backspace") { pressKey("back"); return; }
        if (e.key === "Enter") { pressKey("ok"); return; }
        if (e.key === "Escape") { setState("idle"); return; }
        return;
      }

      switch (e.key) {
        case "0": setState("screensaver"); break;
        case "9": setState("screensaver-basic"); break;
        case "1": setState("idle"); break;
        case "c":
        case "C": setState("keypad"); break;
        case "2": setState("success"); break;
        case "3": setState("failed"); break;
        case "a":
        case "A":
          // Preview: toggle the employee-attendance feature on/off.
          setAttendanceEnabled(els.screensaver.dataset.attendance !== "enabled");
          break;
        case "h":
        case "H": els.devpanel.hidden = !els.devpanel.hidden; break;
      }
    });
  }

  /* ---------------------------------------------- Boot */

  els.brand.innerHTML = BRAND_FACE;
  render("screensaver");        // device rests on the screensaver
  updateClock();
  setInterval(updateClock, 1000);
  startCamera();                // camera runs behind, revealed on wake
  initCodeFlow();
  initAttendance();
  initDevControls();
})();
