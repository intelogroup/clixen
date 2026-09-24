# Style Bible — Large-Scale Investigation

Use for stories at geographic scale: a vanished aircraft, a ship lost at sea, a
search operation, a spreading wildfire, a border incident. The world itself is
the evidence board: satellite maps at night, animated flight paths, radar
sweeps, topographic terrain, wide god's-eye framing that makes entire regions
feel alive.

## Visual DNA

- Night-dark cartography: dark ocean, unlit terrain, city lights as texture.
  Darkness gives the glow elements their authority.
- Glow carries the data: route lines, radar arcs, ping ripples, search grids —
  amber and cyan against deep navy.
- Topography is real: relief, coastlines, ridgelines. The map must feel like a
  measured place, not a decorative graphic.
- HUD-adjacent, never literal: thin tick marks, brackets, faint rings —
  abstract shapes only. No readable words or numbers; real labels are added in
  the edit, where they stay crisp.
- Atmosphere over the map: haze, thin clouds, moonlight on water. That's what
  separates cinematic cartography from a slide.

## THE STYLE SUFFIX

Append verbatim to EVERY image and motion prompt (accent pair is fixed for
this format):

> Cinematic satellite-documentary map graphics; dark night terrain and ocean
> tones; deep navy palette with amber and cyan glow accents; glowing route
> lines, radar sweeps and abstract HUD tick marks with no readable text; soft
> atmospheric haze over detailed topographic relief; filmic grade, high-end
> motion-design render; no text, no watermark; 16:9

(Swap the trailing aspect if the film isn't 16:9.)

## Asset prompt scaffolds

**Base map (MAP-xx)** — the region every block returns to:

```
Satellite night view of [region: what's in frame, orientation, how much
coastline/terrain], seen from high orbit at a slight angle; no labels, no
markers yet. [SUFFIX]
```

**Terrain plate (TER-xx)** — for low flyover moments:

```
Low-altitude 3D terrain view of [feature: ridgeline, strait, runway
approach], topographic relief detail, night atmosphere. [SUFFIX]
```

**Overlay element (OVL-xx)** — the data marks, described as glowing shapes:

```
[The element: a glowing amber flight path arcing across dark ocean / a cyan
radar ring with a sweeping fading trail / a rectangular search grid of faint
cyan cells / an expanding ping ripple] over [the base map context]. [SUFFIX]
```

**Vehicle (VEH-xx)** — only if the story tracks one:

```
Stylized [aircraft/vessel] render, clean simplified silhouette, seen from
above and slightly behind, night lighting, no insignia. [SUFFIX]
```

## Motion vocabulary

The format's verbs — build every beat from these:

- slow orbital drift over the region (the resting state of the format)
- a route line drawing itself across the map in real time
- a radar arc sweeping, trail fading behind it
- a search grid crawling cell by cell across the water
- ping ripples expanding at a coordinate, then fading
- a slow zoom from orbit toward sea level (or the reverse — the pull-back
  reveal that shows how big the search area really is)
- clouds and haze sliding under the camera for parallax

## Motion prompt scaffold (30-second block)

```
Continuous 30-second sequence over one region, no cuts.
SETTING: [MAP-xx recap: region, night, weather].
SEQUENCE: 0–8s [motion verb + what appears] ; 8–16s [motion verb] ; 16–24s
[motion verb] ; 24–30s [ending move — usually a pull-back that reveals scale,
or a hold on the last glowing element].
AUDIO: deep ambient drone, faint sonar pings or radio static, low pulsing
score, subtle; no dialogue, no narration.
[SUFFIX]
```

One data event per beat — a sweep, a ping, a grid advancing. Two glow events
at once read as a screensaver; one at a time reads as an investigation.

## Don'ts

- No readable text or numbers in frame — coordinates and names are edit
  overlays.
- No cartoon pins, arrows, or UI buttons — this is cartography, not an app.
- No daylight unless the story demands it; the format lives at night.
- Don't crowd the frame — Fern's maps are mostly empty ocean with one thing
  happening. Emptiness is the scale.
