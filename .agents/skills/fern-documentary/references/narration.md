# Narration — Cold, Clinical, Evidence

The narrator is not telling a story; the narrator is reading a case file into
the record. The voice never reacts, never sells, never asks the viewer to feel
anything — which is exactly why the viewer does. If the visuals are the
reconstruction, the narration is the paperwork, and the paperwork is scarier.

## Writing rules

- **Present tense, always.** "He signs into the lab" — the reconstruction is
  happening now, in front of the evidence. Past tense turns it into a story;
  present tense keeps it a file.
- **Short declarative sentences.** Full stops are the rhythm. An occasional
  fragment lands like a stamped exhibit: "Eleven visits. No incidents."
- **Numbers are texture.** Clock times, counts, distances, IDs — at least one
  per scene, ideally more: "08:41", "his eleventh visit", "specimen 8-C",
  "four minutes". Precision is what makes an invented incident feel
  documented.
- **Clock callouts anchor scenes.** Each scene's narration cites its in-world
  time near the top — this is the minute-by-minute pillar, spoken aloud.
- **Withhold, then name.** Call things by their clinical designation first
  ("the specimen", "the enclosure", "the individual in the east stairwell")
  and reveal what they actually are only when the timeline forces it.
- **Zero editorializing.** No "shocking", "incredible", "tragic". If a fact is
  shocking, state it flatter than the facts around it.
- **The narrator knows more than they're saying.** Every scene ends on an open
  loop (below). The gap between what the file contains and what's been read so
  far is the suspense engine.

## Word budget

The clinical read is slow — about 2.4 words per second. Budget per block:

| Block length | Target | Hard max |
|---|---|---|
| 30s | ~65 words | 75 |
| 15s | ~32 words | 38 |
| 10s | ~22 words | 26 |

Count the words and print the count next to each scene's narration. TTS voices
pause hard at periods — the clinical style wants exactly those pauses, which
is why the budget is 65, not 80. Narration under budget is better than over:
the edit can breathe; it cannot compress.

## Curiosity loops

Non-final scenes end on an opened door, structural, not melodramatic:

- "What the camera records next has never been fully explained."
- "By the time anyone checks the log, it is already too late."
- "The next entry in the file is a photograph."
- "It will take four minutes for anyone to notice."

The final scene closes the film's big loop, then stamps it shut with a flat
kicker — a reframe, a number, or the file itself:

- "The report runs 214 pages. The word 'accident' appears once."
- "The enclosure was never repaired. It didn't need to be."
- "The file remains open."

## Example lines (the register)

> 08:41. A research intern signs into the east genetics lab. His eleventh
> visit that summer.

> The enclosure holds one specimen. The label reads 8-C. The transfer was
> scheduled for the previous Friday.

> At 09:14, the motion sensor above bench four triggers twice. Nine seconds
> apart. There is no one on camera.

> Search area: one hundred twenty thousand square kilometers. Depth: four
> thousand meters. The first vessel arrives in nineteen hours.

## The master take

After the scene packs, assemble every scene's narration in order into ONE
paste-ready block — SeedAudio generates it as a single take, which keeps the
pacing and tone continuous. Scene labels go outside the block, never inside
(the voice would read them).

## SeedAudio voice card

Include this card with the master take, filled in for the film:

- **Voice:** start from a deep, neutral, low male read with minimal warmth —
  or clone your own. Character in the voice breaks the illusion; flatness is
  the character.
- **Dials:** slow the delivery slightly below default; lower the pitch a
  notch; keep volume even. Small moves — the goal is "reading evidence into
  record", not movie-trailer.
- **Calibration:** generate the calibration sentence 3–5 ways, adjusting one
  dial at a time, until it sounds like evidence. Only then generate the full
  script in one take.
- **Calibration sentence:** [pick the most Fern line from THIS film's
  narration — usually a clock callout with a number in it — and print it
  here.]
- **Save the voice as an asset.** It is now part of the channel's identity,
  same as the characters and locations — every episode reuses it, and from
  this point on, every episode sounds the same.
