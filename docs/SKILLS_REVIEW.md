# Skills review

Every skill was cloned to a scratch folder and read before installation: what it executes, whether it
makes network calls, and what it writes. All are installed at project level in `.claude/skills/`
(git-ignored).

| Skill | Source | What it runs | Network | Writes | Verdict |
| --- | --- | --- | --- | --- | --- |
| Arena | github.com/Jakeschincariol/arena-skill | `bracket.py`, standard library only | none | `.arena/` in the working directory | safe; used for the 8-agent design tournament |
| Taste Skill (design-taste-frontend) | github.com/Leonxlnx/taste-skill | Node scripts: `draw.mjs`, `lint.mjs`, `inspect.mjs` (local server on 127.0.0.1 + headless browser), `assets.mjs` | only `assets.mjs`, when asked, to open image collections (Openverse, Wikimedia, AIC, the Met) | `.taste-skill/` | safe; installed copy verified byte-identical to the reviewed clone |
| Web Design Guidelines (Vercel) | vercel-labs/agent-skills | nothing; instructs the agent | fetches the latest rules from raw.githubusercontent.com at use | none | safe, with a caveat: its instructions are remote and can change; the fetched rules were read before applying |
| UI UX Pro Max | ui-ux-pro-max-cli 2.15.0 (npm) | `uipro init` copies files; Python search scripts over local CSVs | `init` can download a release zip from GitHub; installed with `--offline` so only the vetted npm bundle was used | `.claude/skills/ui-ux-pro-max` and siblings | safe; Python scripts make no network calls |
| three.js skills (shaders, post-processing, R3F, animation) | github.com/cloudai-x/threejs-skills | nothing (markdown only) | none | none | safe |
| motion-framer, gsap-scrolltrigger, modern-web-design, animated-component-libraries, lightweight-3d-effects | github.com/freshtechbro/claudedesignskills | optional Python generators (not run) | none found | none | safe; knowledge files used only |

Skills were installed with the `skills` CLI (`npx skills add ... --agent claude-code`) and
`uipro init --ai claude --offline`; the second batch was copied from the reviewed clones.

## How each was used

- **Taste Skill:** type scale, two-line headlines, no eyebrows or index numbers, equal margins,
  quiet app screens, the motion layer (entrances, feedback, a signature move), the look-and-fix loop
  on Playwright screenshots. Three house rules (no glass, no glow, no violet) conflict with the
  brand; the skill's own "existing brand" exception was applied (DECISIONS.md).
- **UI UX Pro Max:** design-system query (immersive pattern, checklist, avoid-list) and font catalogue.
  Its "Brutalism" style and all-monospace pairing were rejected for this subject.
- **awesome-design-md:** not used. The look came from the two robot pictures (palette sampled from
  them) and the skills above, so no outside DESIGN.md was needed; ours is at the repo root.
- **Image to Code (Taste Skill):** there is no image generator here; the user's two robot pictures and
  Playwright screenshots of the work served as the visual references.
- **three.js post-processing / shaders:** bloom on the neural interior; the raw-WebGL star sky.
- **Motion:** every UI animation (springs, layout animation for the nav pill, scroll-linked values).
  GSAP was not needed: Motion's `useScroll` handled the pinned dive with a sticky stage.
- **Web Design Guidelines:** run as an audit at the end; findings and fixes are in VALIDATION_REPORT.md.
- **Playwright:** screenshots at 1440 after visual changes, a final pass at 1440, 1280 and 768
  (VALIDATION_REPORT.md), and frame-time profiling on the Mac GPU (`make perf`).
