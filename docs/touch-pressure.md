# Touch pressure on phones (2026-11-03)

Question: can a pedal use how hard the thumb presses (idea D in the pedal study)? Does the Samsung
Galaxy S24 have a pressure-sensitive screen?

## Short answer

- **Galaxy S24, S24+ (finger): no real pressure sensor.** [I] Samsung's spec sheets list no force
  sensing for the screen. The pressure-sensitive spot on the S8 and S9 was only under the virtual
  home button. Samsung never shipped a full-screen force-touch display. Android normally reports
  a fixed pressure of 1.0 for a finger on such screens. Some drivers derive a fake "pressure" from
  the contact size instead. [O] XDA reports, Android `MotionEvent` docs
- **What the S24 probably does report: contact size** (the touch ellipse, `touchMajor`/`minor`).
  Chrome passes it to a page as `PointerEvent.width/height` [I]. Flattening the thumb makes it
  bigger, so a "squash your thumb" pedal might work. It is coarse and differs per phone. [VERIFY]
- **S24 Ultra with the S Pen: real pressure** (4096 levels), with `pointerType: "pen"`. That's no
  use for driving with thumbs.

**Check it on your phone in a minute:** STUSteer → ⚙ → *Touch probe*. Press with a fingertip, then
flatten your thumb. It shows the pressure and contact size Chrome receives, and says whether
either one changes.

## Phones that had real force-sensitive screens

| Device | Year | Name | Tag |
| --- | --- | --- | --- |
| iPhone 6s … iPhone XS (not XR) | 2015–2018 | 3D Touch | [O] dropped from iPhone 11 on ("Haptic Touch" is a long press) |
| Huawei Mate S (128 GB version only) | 2015 | Force Touch | [O] |
| ZTE Axon mini | 2015 | Force Touch | [O] |
| Meizu Pro 6 | 2016 | 3D Press | [I] |
| Huawei P9 Plus | 2016 | Press Touch | [I] |
| Google Pixel 4 | 2019 | "deep press", software guess from touch size and timing, no sensor | [I] |

Android itself got only limited support for this (Android N). All of these phones are long out of
production; no current Android phone has a full-screen force sensor that I could find [?].

## Consequence for STUSteer

Pressure pedals are not worth building for the S24. If the touch probe shows contact size
changing smoothly, a "squash" pedal mode could be added later, with a quick calibration (light
touch, full squash). Until then: thumb height (default), slide from touch, or on/off.

Sources: [XDA: getPressure always 1.0 on Galaxy S4](https://xdaforums.com/t/getpressure-always-return-1-0-on-galaxy-s4.2430226/),
[Android MotionEvent reference](https://developer.android.com/reference/android/view/MotionEvent),
[W3C pointer-events list: S Pen pressure values](https://lists.w3.org/Archives/Public/public-pointer-events/2012OctDec/0045.html),
[PhoneArena: Android phones with Force Touch](https://www.phonearena.com/news/Android-smartphones-with-Force-Touch-available---upcoming_id77276),
[Android Authority: Android N supports pressure](https://www.androidauthority.com/google-confirms-android-n-supports-3d-touch-687574/),
[Engadget: Synaptics ClearForce](https://www.engadget.com/2015-10-06-synaptics-clearforce.html),
[MDN: PointerEvent.width](https://www-igm.univ-mlv.fr/~forax/MDN/developer.mozilla.org/en-US/docs/Web/API/PointerEvent/width.html),
[Wikipedia: Samsung Galaxy S24](https://en.wikipedia.org/wiki/Samsung_Galaxy_S24).
