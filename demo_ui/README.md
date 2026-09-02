# ITSU · Facial Recognition Device UI

The on-device interface for a facial-recognition reader. It renders **only the
screen content** — a live camera feed with a light purple result overlay. It is
not a website or a mobile app, and it does not draw the device hardware.

Zero build step: plain HTML, CSS and JavaScript.

## States

| State | Screen |
|-------|--------|
| **Screensaver** | Resting screen: a clock + a company logo placeholder over a deep violet field. The camera runs behind but is fully covered. |
| **Idle / Camera** | Live camera feed. A card tap (card modes) or a screen tap (`face_only`) wakes the device from the screensaver to here. |
| **Success** | Camera continues behind a semi-transparent purple overlay + smile icon + `Hello` / *name*. |
| **Failed** | Camera continues behind the overlay + sad icon + `Verification Failed` / `Please try again`. |

### Flow

```
Screensaver ──(card tap / screen tap)──▶ Camera ──▶ Success | Failed ──▶ Screensaver
```

The camera-recognition states (**Success** / **Failed**) are driven by the recognition backend.

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

// Screensaver
deviceUI.screensaver();       // resting screen (clock + logo)
deviceUI.camera();            // wake to the live camera (= idle)
deviceUI.setLogo("logo.svg"); // drop a company logo into the screensaver
```

## Preview / QA

A discreet preview panel is built in for testing without a backend:

- Press **H** to show/hide the preview control bar.
- Keys **0 / 9 / 1 / 2 / 3** → Screensaver / Screensaver (no attendance) / Camera / Success / Failed.

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
