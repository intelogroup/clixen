# Style Bible — Incident Reconstruction

Use for single events at human scale: an accident, a disappearance, a break-in,
a mechanical failure, an origin story told as an incident. The look Fern uses
for its minute-by-minute reconstructions: low-poly environments, stylized
characters, clean cinematic lighting, camera angles from close-up to wide
aerial. The register is "evidence render" — the calm of a forensic
reconstruction, not the energy of an action scene.

## Visual DNA

- Stylized low-poly 3D: simplified geometry, softly textured materials — never
  photoreal, never cartoon-bouncy. Think premium courtroom reconstruction.
- Characters: simplified geometric human figures, minimal facial detail. They
  read through posture and blocking, not expressions.
- Lighting does the drama: realistic, cinematic, motivated by the scene (window
  daylight, fluorescent panels, a single desk lamp at night). Soft volumetric
  shafts and gentle haze.
- Palette: muted and desaturated with ONE accent color that marks what matters
  (the specimen, the warning light, the evidence). Choose the accent once per
  film — amber by default, red when danger leads the story, cyan for
  clinical/tech stories — and bake it into the suffix.
- Scale cues everywhere: rooms feel measured, props feel placed. The frame
  should look like it could be exhibit B.

## THE STYLE SUFFIX

Fill [ACCENT] once, then append verbatim to EVERY image and motion prompt:

> Stylized low-poly 3D documentary reconstruction render; simplified geometric
> human figures with minimal facial detail; clean soft-textured materials;
> realistic cinematic lighting with soft volumetric shafts; muted desaturated
> palette with a single [ACCENT] accent; gentle filmic haze and shallow depth
> of field; high-end production render; no text, no watermark; 16:9

(Swap the trailing aspect if the film isn't 16:9.)

## Asset prompt scaffolds

Assets are reference stills — clean, isolated, made to be reused across every
block that shows them.

**Location (LOC-xx)** — an empty plate; people contaminate location references:

```
Empty location plate, no people, no animals — [the space: layout, key
furniture and equipment, materials, light sources, time of day; name where
the accent color lives, if it appears here]. Wide coverage of the room from
a natural corner height. [SUFFIX]
```

**Character (CHAR-xx)** — a reference sheet, plus a descriptor line you will
reuse verbatim everywhere:

```
Character reference sheet, full body, neutral standing pose, plain light-grey
studio background — [DESCRIPTOR LINE: age, build, hair, clothing with colors,
one distinguishing item]. Front three-quarter view. [SUFFIX]
```

**Prop (PROP-xx)**:

```
Object reference, isolated on a plain light-grey background — [the object:
materials, size cue relative to a hand or table, state]. [SUFFIX]
```

Optional but valuable: a second angle of the key location (aerial establisher)
and a "state" variant of the key prop (intact / opened / damaged) — generated
from the base prompt with the state changed, everything else verbatim.

## Camera grammar

Pick 2–3 per block; vary across the film. Fern's range runs close-up to wide
aerial:

- slow push-in — the evidence move; ends most scenes
- high-corner surveillance angle, locked off — makes footage feel recovered
- wide aerial establisher — opens scenes, sells scale
- slow lateral dolly — follows movement without excitement
- top-down schematic — turns a room into a diagram
- static tripod mid-shot — lets an action simply happen on record

Procedural pace throughout: characters move deliberately, actions take their
real duration. Slow reads as documentary; fast reads as animation.

## Motion prompt scaffold (30-second block)

```
Continuous 30-second single-scene sequence, one location, no cuts.
SETTING: [LOC-xx recap: place, time of day, light].
CHARACTERS: [each CHAR-xx descriptor line, verbatim].
SEQUENCE: 0–8s [action + camera] ; 8–16s [action + camera] ; 16–24s [action +
camera] ; 24–30s [action + camera — end on a push-in or a held frame].
Characters only gesture and act, they do not talk.
AUDIO: [room tone of this space], [movement/action sounds, named], low sparse
suspenseful score, subtle; no dialogue, no narration.
[SUFFIX]
```

Three to five beats per block. Each beat is one physical action the camera can
see — "he hesitates" is invisible; "he stops with his hand on the latch" is a
shot.

## Don'ts

- No second location inside a block — split it into two scenes instead.
- No unregistered elements — if the beat needs a forklift, register PROP-04
  first.
- No readable text anywhere in frame (screens glow, they don't say words).
- No gore, no lingering on injury — the camera arrives after, or cuts away
  before. Aftermath tells it colder anyway.
- No comedy in the frame. If the story has irony, the narration carries it
  deadpan.
