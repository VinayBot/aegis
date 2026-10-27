# AEGIS design system

**Theme: inside the machine, in deep space.** A command deck for an AI system: true black space with
drifting stars and a faint Milky Way, frosted glass panels like the robot's tablet, and one accent
family taken from the robots themselves. Every token lives in `frontend/src/styles/tokens.css`; no
colour literal appears anywhere else in the components.

## Colour

Sampled from the robot pictures (`scripts/build_assets.py` measured them):

| Role | Token | Value | Source |
| --- | --- | --- | --- |
| Ground | `--space-0` | `#000000` | deep space |
| Raised ground | `--space-1`, `--space-2` | `#06070c`, `#0c0f1a` | panel steps |
| Ink | `--ink` | `#eaf0ff` | starlight |
| Secondary ink | `--ink-soft`, `--ink-mute` | `#b9c3e6`, `#8a95bf` | |
| Accent, live | `--cyan` | `#74d7f7` | the hover robot's LED face (144,200,243) and the standing robot's eye rings (153,214,233) |
| Accent, cached | `--violet` | `#c4a2f5` | the hover robot's backdrop (200,165,243) |
| Warning, degraded | `--amber` | `#ffc25c` | status only |
| Broken, rejected | `--red` | `#ff6b7a` | status only |

**One accent family.** Cyan means live and healthy; violet means served from memory (cache or
coalesced). Amber and red appear only for warnings and failures. The site was briefly multi-hued;
it was pulled back to this on review because it read as decoration rather than meaning.

Contrast, computed with the WCAG formula (on `--space-0` / on glass over black): ink 18.4 / 17.3,
ink-soft 12.0 / 11.3, ink-mute 7.1 / 6.7, cyan 12.8 / 12.1, violet 9.8 / 9.3, amber 13.1 / 12.4,
red 7.6 / 7.2, black text on the cyan button 12.8. All pass AA (4.5:1). `--ink-faint` (3.7 / 3.5)
is used only for decorative or disabled marks, never for text a reader needs.

## Type

| Role | Face | Use |
| --- | --- | --- |
| Display | Oxanium Variable | headlines, big numbers: squared, technical, still readable |
| UI | Sora Variable | everything else, from 14 px |
| Code | JetBrains Mono Variable | real code, endpoints and trace data only |

Self-hosted through `@fontsource-variable` (no network font requests). Headlines are two lines or
fewer at every width; display tracking -0.035em; body 16-20 px at 1.5-1.65 leading.

## Surfaces

- **Glass** (`.glass`): translucent `--glass`, a 12 px backdrop blur, and a 1 px inner highlight.
  A soft light follows the pointer across each panel.
- **Glow border** (`.glow-border`): a conic light on the panel's edge that runs only while hovered
  or for live panels (`.is-live`); at rest it is a faint static edge. Glow is earned.

## Motion

| Token | Value | Use |
| --- | --- | --- |
| `--dur-micro` | 120 ms | presses, toggles |
| `--dur-std` | 200 ms | hovers, small state changes |
| `--dur-large` | 320 ms | panels, page changes |
| `--ease-ui` | `cubic-bezier(0.32, 0.72, 0, 1)` | UI entrances |
| springs | stiffness 120-380 | anything physical: robots, panels, the nav pill |

Rules: animate only transform, opacity, filter (sparingly) and shader uniforms; lists stagger 30 ms
and stop after ten items; numbers count only on meaningful change; every effect has a
`prefers-reduced-motion` form that keeps its content. Frame times are measured, not assumed
(`make perf`, see the validation report).

### Signature moves

- **The dive:** scrolling pushes into the hover robot's face screen, which dissolves into a bloomed
  three.js field of neurons (somas, dendrites, axons, firing pulses) with five live subsystems, then
  pulls out into the architecture. three.js loads only on the first scroll.
- **The tunnel and the reel:** a scroll-driven perspective tunnel of the site's own page screenshots
  (Home) and a curved CSS-3D reel of them (Docs), adapted from two 21st.dev components.
- **The galaxy:** the Architecture page opens on a 3D galaxy (core = gateway, six phase planets,
  backend moons, one comet per real request); the Detailed tab is the full live node map with a
  light pass through every node.
- **The living sky:** a WebGL star field that glows briefly with each real request and warms to red
  at the edges while a breaker is open.
- **The docked assistant:** an original 3D "sentinel" robot built in three.js (white armour, black
  visor, cyan scanner eye, floating hands, a thruster beam over a glowing pad) docked bottom right.
  It hovers with "Ask me anything", perks up and follows the pointer on hover, and wakes with a hop
  when clicked. It loads after the page has settled.
- **Request light:** on the architecture map every moving light is one real request; its colour is
  its outcome and its speed is the stage's real latency.

## Pictures

Two robots supplied by the user, lifted from their backgrounds on-device with Apple Vision
(`scripts/cutout.swift`) and exported as AVIF/WebP at several widths (largest first-screen image
47 KB): the hover robot (hero, with a live LED face and a live chart on its tablet) and the standing
robot (Meet AEGIS, with eyes that follow the pointer). Overlay regions (face screen polygon, eyes,
tablet quad) are measured once and stored in `frontend/src/config/robotRegions.json`.
Meet AEGIS uses the user's 1.20.15 robot picture. The chatbot is modelled, not a picture (see
docs/DECISIONS.md, Images). The tunnel and the reel show screenshots of this site's own pages
(`tools/screens.mjs`), not stock photography.

## Skills used

Taste Skill set the craft rules kept here (type scale, two-line headlines, no eyebrows, no index
numbers, one accent, quiet dashboards, earned glow). Its "no glass, no glow, no violet" house rules
conflict with this brief, whose brand is glass and the robots' own cyan and violet; per the skill's
own "existing brand" exception the brand wins on those three rules only (see docs/DECISIONS.md).
UI UX Pro Max supplied the immersive-experience pattern, the pre-delivery checklist and the
avoid-list; its suggested "Brutalism" style and all-monospace pairing were rejected for this subject.
