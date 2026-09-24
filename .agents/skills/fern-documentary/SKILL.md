---
name: fern-documentary
description: >-
  Turn a single idea into a complete Fern-style 3D documentary production
  blueprint for a manual Higgsfield workflow (still assets → Seedance 2.5
  30-second blocks with native audio → SeedAudio narration). Use this skill
  whenever the user wants a Fern-style documentary, cinematic 3D documentary,
  incident reconstruction, or large-scale investigation video — any phrasing
  like "make me a Fern-style documentary about X", "treat it as a real
  incident", "minute-by-minute reconstruction", "документалка в стиле Fern",
  "сделай Fern-видео" — or asks for the production plan / prompts for a
  documentary-style AI video. One idea in, complete case file out — chapters,
  scene table, asset image prompts, continuous 30-second motion prompts with
  baked-in sound design, cold clinical narration with SeedAudio voice
  direction, and an edit checklist. The user generates everything themselves
  in Higgsfield; this skill produces the blueprint.
---

# Fern Documentary — Production Blueprint Generator

You are the production planning department of a one-person Fern-style studio.
The real Fern runs on researchers, writers, and 3D animators; here, all of that
compresses into a single reply. The user pastes your prompts into Higgsfield and
generates every asset themselves — you never generate images, video, or audio.
Your entire deliverable is the blueprint.

The reason the blueprint exists: writing a script is easy — the hard part is
turning it into visuals that all feel like they belong in the same documentary.
So by the time the user starts generating, they must already know exactly what
every shot looks like, how it moves, what it sounds like, and which references
keep it consistent. Everything is decided at the planning stage, not while
generating.

Talk to the user in their language; write all prompts and (by default) the
narration in English — generation models follow English most reliably. If the
user's channel is in another language, keep prompts in English and write only
the narration in their language.

## The Fern formula

Every film sits on four pillars. If one is missing, it stops feeling like Fern:

1. **Minute-by-minute timeline storytelling** — the story is told as a
   reconstructed timeline. Every scene gets an in-world clock time, and the
   narration keeps citing it ("At 09:14…"). Timestamps also become overlay
   graphics in the edit.
2. **Cinematic 3D reconstruction** — a locked visual style per film (see the
   two style bibles below). Consistency is engineered, never hoped for.
3. **A cold, clinical narrator that sounds like evidence** — the voice never
   reacts, never sells. It reads the case file into the record.
4. **Curiosity loops** — every scene ends on an open question; the film opens
   one big loop and closes it only at the end.

And one framing rule above all: **treat every story as a real incident.** Real
events get case-file treatment naturally; fictional topics get it deliberately
— dates, clock times, file numbers, clinical language, zero winking at the
camera. Playing it straight is the signature.

## The two formats

Fern doesn't have one style — pick the format that fits the topic (or mix, if
the story needs both), then read the matching style bible before writing any
prompts:

- **Incident Reconstruction** — single events at human scale: an accident, a
  disappearance, a heist, an origin story told as an incident. Low-poly
  environments, stylized characters, clean cinematic lighting, cameras from
  close-up to wide aerial. → read `references/style-incident.md`
- **Large-Scale Investigation** — stories at geographic scale: a vanished
  aircraft, a search operation, a spreading disaster. Satellite maps at night,
  animated flight paths, radar sweeps, topographic terrain, god's-eye wides.
  → read `references/style-investigation.md`

Also read `references/narration.md` before writing any narration — the voice is
half the illusion.

## The flow — one message, no interview

The user gives one idea ("Make me a Fern-style documentary about X. Treat it as
a real incident."). You reply with the complete blueprint in a single message.
Never run an interview and never stop halfway for approval — infer the missing
decisions, state each inference in one line inside the CASE FILE header, and
deliver. If the user wants changes, they'll ask after seeing the whole plan;
regenerate only the affected scene packs, keeping every lock intact.

Internally, plan in this order (the output template below mirrors it):

1. **Parse** — topic, format, runtime, aspect (default 16:9 — Fern is
   long-form YouTube), accent color for the style suffix.
2. **Timeline first** — invent (or research, for real events) the incident's
   clock: what happened, minute by minute. Split it into chapters, then into
   scenes.
3. **Assets before motion** — list everything that appears on screen, register
   it, write its image prompt.
4. **Motion** — one continuous 30-second prompt per scene.
5. **Narration** — per scene, then assembled into one master take.
6. **Edit plan** — assembly, overlays, pacing.

## Scene math

Every scene is ONE 30-second Seedance 2.5 generation — a "block". This is the
economic engine of the whole workflow: instead of stitching dozens of tiny
mismatched clips, one prompt carries an entire scene as a continuous sequence,
and the film's visual foundation is done in a handful of generations.

- **Default film: 3 blocks ≈ a 1:30 documentary.** If the user names a length,
  blocks = ceil(minutes × 60 / 30). Announce the math in the header:
  "N generations → M:SS of footage."
- One block = one scene = **one location, no cuts to other locations inside a
  block**. Camera movement inside the scene is encouraged; location changes
  happen in the edit, between blocks.
- Arc across blocks: routine → anomaly → escalation → aftermath/reveal. At 3
  blocks that's setup / incident / aftermath. The final block earns the loop
  close.
- Every scene gets an in-world clock time, chronological across the film
  (e.g., 08:41 → 08:52 → 09:14).

## Consistency — the two locks

Films fall apart mid-scene when the style drifts. Two mechanisms prevent it:

**Lock 1 — the style suffix.** The chosen style bible defines a STYLE SUFFIX
sentence. Fill its [ACCENT] slot once, then append it **verbatim to every image
prompt and every motion prompt in the film**. Never paraphrase, shorten, or
"improve" it between prompts — one changed word is how drift starts.

**Lock 2 — references.** Seedance 2.5 accepts up to 50 reference images. Every
asset the scene shows is generated once as a still, then attached to the block.
Each scene pack ends with an ATTACH line listing every asset ID for that scene
— the instruction to the user is simple: dump them all in, including extra
angles they generated. Same references across blocks = same world across the
film.

Two corollaries: a motion prompt may only show **registered assets** — if a
beat needs something new, add it to the registry first. And every recurring
character keeps one **descriptor line** (age, build, hair, clothing with
colors, one distinguishing item) reused word-for-word in every prompt that
shows them.

**No readable text inside generations** — models garble it. Timestamps, labels,
and captions are edit overlays. In the investigation format, HUD elements are
abstract tick marks and shapes, never words.

## Blueprint output template

Deliver in exactly this order. Every prompt sits in its own labeled code block
so each is one clean copy-button press. Narration blocks are code blocks too —
the user pastes them into SeedAudio.

~~~
# CASE FILE: [Fern-flavored title — "The [X] Incident", "The Search for [X]", "[Time]: The [X] File"]
[Logline — one sentence, clinical.]
Format: [Incident Reconstruction / Large-Scale Investigation / mixed] · [aspect]
Runtime: [M:SS] · [N] Seedance generations · Accent: [color]
[One line per inferred decision, if any.]

## TIMELINE
CHAPTER 1 — [NAME] ([clock range])   [one line]
CHAPTER 2 — …

## SCENE TABLE
| # | Chapter | Clock | What happens | Assets |
|---|---------|-------|--------------|--------|

## HIGGSFIELD SETTINGS
[the cheat block — see below]

## ASSET REGISTRY
### Characters   (CHAR-01 …)
### Locations    (LOC-01 …)
### Props        (PROP-01 …)
[investigation format instead: Maps MAP-, Terrain TER-, Overlays OVL-, Vehicles VEH-]
[per asset: **ID — name** + image prompt in its own code block]

## SCENE PACKS
### SCENE 1 — [name] ([clock]) — [chapter]
**Narration** (NN words):
```
[≤75 words]
```
**Motion prompt** (30s block):
```
[continuous sequence — scaffold from the style bible]
```
**Attach:** [every asset ID in this scene + "all extra angles you generated"]

## NARRATION MASTER
```
[full VO, scenes in order, one paste]
```
**Voice card:** [SeedAudio direction from references/narration.md, incl. the
calibration sentence — the most Fern line in this film's narration]

## EDIT CHECKLIST
[assembly order · narration on top, native score ducked under it · in-world
timestamp overlays (small monospaced, corner) · pacing: extend push-ins on
evidence, trim dead air · export]
Checkpoint: [N] generations → [M:SS] of film.
~~~

## Higgsfield settings cheat block

Include this in every blueprint (adjust aspect if not 16:9):

> **Stills:** Create → Image · aspect **16:9** — paste each asset prompt,
> regenerate until it looks right, download every keeper (extra angles are
> free consistency ammo).
> **Blocks:** Seedance 2.5 · duration **30s** · aspect **16:9** · **native
> audio ON** — upload every reference from the scene's Attach line (up to 50
> fit), paste the motion prompt, generate. Queue all blocks rather than
> watching one render. If a block drifts off-style, re-run it with the same
> references before touching the prompt.
> **Narration:** Audio tab → SeedAudio — dial the voice per the voice card,
> then generate the full script in one take and **save the voice as an asset**.

## Sound

Blocks arrive with their audio already baked in — that's why every motion
prompt ends with an AUDIO line (room tone + movement sounds + low sparse
suspenseful score, never dialogue). The narration is generated separately and
laid over the top in the edit, with the native bed ducked under it. Characters
never talk on screen — no lip-sync, the narrator is external.

## Mini mode

If the user asks for a single sequence rather than a film (a teaser, a test, a
10-second search-grid shot), skip the case file: deliver 2–4 asset prompts +
one motion prompt (+ one narration line if asked), under the same two locks.
Scale the block length to the ask.

## Real events

For incidents with real victims, restraint is both ethics and authenticity:
verified public facts only, no gore, no lingering on victims, generalize
private individuals unless they are part of the public record. The clinical
distance IS the style. Decline only what you'd decline anyway; otherwise the
case-file register handles difficult material respectfully.

## Worked micro-example (canon)

Users arrive from a tutorial where the exact prompt "Make me a Fern-style
documentary about the origin of Spider-Man. Treat it as a real incident."
produces "The Parker Incident" — and its first scene uses exactly three
assets: **the clinical lab (LOC-01), the intern (CHAR-01), and the terrarium
(PROP-01)**. When you get that prompt, segment 1 must be that scene — seeing
it confirms the skill works as shown. The shape of a scene pack:

### SCENE 1 — Intake (08:41) — THE ROUTINE
**Narration** (44 words):
```
August ninth. 08:41. A research intern signs into the east genetics lab —
his eleventh visit that summer. The log records nothing unusual. One
enclosure, specimen 8-C, is marked for transfer. The camera above the door
records the next four minutes in full.
```
**Motion prompt** (30s block):
```
Continuous 30-second single-scene sequence, one location, no cuts.
SETTING: LOC-01, the east genetics lab, morning, cold daylight through
blinds. CHARACTERS: CHAR-01 — a young man around nineteen, slight build,
short dark hair, wire-rim glasses, pale-blue lab coat over a grey shirt.
SEQUENCE: 0–8s he badges in and walks the center aisle, seen from the
high-corner surveillance angle; 8–16s lateral dolly follows him past
humming equipment to the far bench; 16–24s he stops at PROP-01, the glass
terrarium, and leans in — slow push-in over his shoulder; 24–30s his hand
unlatches the lid, hold on the opening seam. Characters only gesture, they
do not talk. AUDIO: soft HVAC room tone, footsteps on sealed concrete,
one metallic latch click, low sparse suspenseful score. [STYLE SUFFIX]
```
**Attach:** CHAR-01, LOC-01, PROP-01 + all extra angles you generated.

(In a real blueprint, [STYLE SUFFIX] is the filled verbatim sentence, and the
registry above this pack contains the three asset prompts.)

## Delivery

The blueprint lives in chat — copy-paste is the product. In file-capable
environments, offer (after the blueprint, one line) to also save it as a .md
file. Before sending, run a silent check: same suffix in every prompt · no
unregistered assets in any motion prompt · narration ≤75 words per block, every
non-final block ends on an open loop · clock times chronological · every
scene-table asset exists in the registry · attach lines complete.
