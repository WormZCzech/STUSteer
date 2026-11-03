# GTA V and LSPDFR bindings used by the default profile

The default profile (`stusteer/profiles/gta5_lspdfr.json`) mixes two kinds of output:

- **Xbox pad buttons and axes** for GTA V itself (driving, walking, camera). GTA V Legacy's
  controller layout is fixed by the game; you can switch between a few presets in
  Settings → Controls, but not rebind freely. The profile assumes the default preset.
- **Keyboard keys** for LSPDFR, because its actions are keyboard keys by default and the pad has no
  spare buttons in a car.

Tags: [O] seen in a source, [VERIFY] check in your game. Fan guides disagree on some bindings, so
everything LSPDFR-related is [VERIFY] against your own `lspdfr/keys.ini` (in the game folder) or the
in-game LSPDFR key-binding menu. STUSteer does not read that file; change the profile instead.

## GTA V, Xbox controller, in a vehicle

| Action | Pad | Profile control | Tag |
| --- | --- | --- | --- |
| Steer | Left stick X | `wheel`, `t_wheel` | [O] |
| Accelerate | RT | `gas` | [O] |
| Brake / reverse | LT | `brake` | [O] |
| Handbrake | RB | `handbrake` | [O] guides agree |
| Horn; tap in an emergency vehicle toggles the siren | L3 (LS click) | `horn` | [O] horn; [VERIFY] siren tap |
| Look behind | R3 (RS click) | `lookback` | [O] |
| Headlights | D-pad right | `lights` | [O] |
| Radio | D-pad left | `radio` | [O] guides differ on tap vs hold |
| Phone | D-pad up | `phone` | [O] |
| Enter / exit vehicle | Y | `exit` | [O] |
| Change camera | Back | `cam` | [O] |
| Pause | Start | `pause` | [O] |

## GTA V, on foot

| Action | Pad | Tag |
| --- | --- | --- |
| Move / look | Left / right stick | [O] |
| Aim / shoot | LT / RT | [O] |
| Weapon wheel | LB (hold) | [O] |
| Cover | RB | [O] |
| Sprint / jump / reload-melee / enter vehicle | A / X / B / Y | [O] |
| Character wheel | D-pad down (hold) | [VERIFY] |

## LSPDFR 0.4 (keyboard defaults)

| Action | Key | Profile control | Tag |
| --- | --- | --- | --- |
| Accept callout | Y | `accept`, `l_accept` | [O] several guides; [VERIFY] |
| End callout | End | `endcall`, `l_end` | [VERIFY] |
| Traffic stop / pursuit (hold) | Left Shift | `stop`, `l_stop` | [O] two guides; [VERIFY] |
| Backup menu | B | `backup`, `l_backup` | [O] several guides; [VERIFY] |
| Interact / talk during a stop | E | `interact` | [O] one guide; [VERIFY] |
| Menu navigation (RageNativeUI menus) | Arrows, Enter, Backspace | `nav_*` | [VERIFY] |
| RAGE Plugin Hook console | F4 | `console` | [O] |

Plugins (StopThePed, Ultimate Backup, Policing Redefined…) bring their own keys. Add a button per
action to the LSPDFR layout: `{"id": "stp_menu", "type": "button", "label": "STP menu", "x": …,
"bind": {"key": ["LCTRL", "N"]}}`; key names are in `stusteer/keys.py`.

## Keyboard and controller together

GTA V switches its on-screen prompts to whichever device was used last. Pressing a keyboard key
while driving with the pad briefly shows keyboard prompts. The car still steers from the pad.
[VERIFY in your game]. If something misbehaves, the fallback is to bind the LSPDFR action to a pad
button combination in `keys.ini` and use a `pad` button in the profile instead.

Sources: [gamer-guides LSPDFR controls](https://gamer-guides.com/grand-theft-auto-v/lspdfr-controls/),
[progameguides LSPDFR keybinds](https://progameguides.com/gta/all-lspdfr-controls-and-keybinds/),
[Steam guide: Complete LSPDFR Keybinds](https://steamcommunity.com/sharedfiles/filedetails/?id=1271329469),
[StrategyWiki GTA V controls](https://strategywiki.org/wiki/Grand_Theft_Auto_V/Controls),
[GTABoom controls](https://www.gtaboom.com/gta-5-controls-pc-keyboard-playstation-and-xbox/).
