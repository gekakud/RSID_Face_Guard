# ITSU · Facial Recognition Device UI

The on-device interface for a facial-recognition reader. It renders **only the
screen content** — a live camera feed with a light purple result overlay. It is
not a website or a mobile app, and it does not draw the device hardware.

Zero build step: plain HTML, CSS and JavaScript.

## States

| State | Screen |
|-------|--------|
| **Screensaver** | Resting screen: a clock + a company logo placeholder over a deep violet field. The camera runs behind but is fully covered. |
| **Idle / Camera** | Live camera feed with an on-screen **Enter a code** button. Pressing the device's physical button wakes the device from the screensaver to here. |
| **Code entry** | Number keypad + a display area showing the code as it is typed. Opened by tapping **Enter a code**. |
| **Success** | Camera continues behind a semi-transparent purple overlay + smile icon + `Hello` / *name*. |
| **Failed** | Camera continues behind the overlay + sad icon + `Verification Failed` / `Please try again`. |
| **Code approved** | Same smile success visual on a **solid** violet background (no camera) + `Hello` only — entry by code doesn't know the person's name. |
| **Wrong code** | Same sad failed visual on a **solid** rose background (no camera) + `Verification Failed` / `Please try again`. |

### Flow

```
Screensaver ──(physical button)──▶ Camera ──(tap "Enter a code")──▶ Code entry
                                                                        │
                                              correct ◀──── OK ────▶ wrong
                                                 │                     │
                                          Code approved            Wrong code
                                          → Screensaver            → Code entry
```

The camera-recognition states (**Success** / **Failed**) are independent and driven by the recognition backend.

## Run it

Camera access requires a secure context — `https://` or `localhost`.

```bash
python3 -m http.server 8080
# open http://localhost:8080  and allow camera access
```

## Driving the UI from the recognition backend

The recognition system controls the screen through `window.deviceUI`:

```js
deviceUI.success("Emma");     // greet a recognised person
deviceUI.success("Emma", 4000); // ...and auto-return to idle after 4s
deviceUI.failed();            // verification failed
deviceUI.failed(4000);        // ...auto-return to idle after 4s
deviceUI.idle();              // back to camera-only

// Screensaver + code flow
deviceUI.screensaver();       // resting screen (clock + logo)
deviceUI.camera();            // wake to the live camera (= idle)
deviceUI.codeEntry();         // open the number keypad
deviceUI.codeApproved();      // code accepted → "Hello" (solid bg, no camera)
deviceUI.codeRejected();      // code rejected → "Verification Failed"
deviceUI.setExpectedCode("1234"); // set the code the built-in keypad checks against
deviceUI.setLogo("logo.svg"); // drop a company logo into the screensaver
```

The built-in keypad verifies against `setExpectedCode` (default `1234`) and shows
the approved / wrong-code screens itself. To verify codes on the backend instead,
skip the keypad's OK check and call `deviceUI.codeApproved()` / `codeRejected()`
directly.

## Preview / QA

A discreet preview panel is built in for testing without a backend:

- Press **H** to show/hide the preview control bar.
- Keys **0 / 1 / c / 2 / 3** → Screensaver / Camera / Code entry / Success / Failed.
- In the code-entry screen the number keys type the code (Enter = OK, Esc = back).

## Design

- Typeface: **Quicksand** (400–700).
- Overlay: light, semi-transparent violet gradient with a faint backdrop blur so
  the user always sees themselves behind the interface.
- All text is centered; icons are white. Palette and timing live in the `:root`
  block of `styles.css`.

## Files

```
index.html                 screen markup
styles.css                 layout, overlay, typography, palette
app.js                     state machine, camera, public deviceUI API
assets/icons/              icon-success.svg · icon-failed.svg
```
