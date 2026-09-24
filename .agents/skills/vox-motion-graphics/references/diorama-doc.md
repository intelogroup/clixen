# Paper-Diorama Documentary Style ("WHO BLINKS?" playbook)

The second house style: a cinematic vintage paper-diorama documentary —
aged sepia newsprint worlds, censor-bar cutout figures, one burnt-orange
accent, letterpress prop typography, tungsten light, macro tilt-shift.
Born from reverse-engineering a reference video and battle-tested on the
"WHO BLINKS?" nuclear-treaty explainer. Use it when the brief says
cinematic / dramatic / investigative / "like the AI bubble video", or when
the topic is geopolitics, money, or power.

## Style key

Reusable style key already generated — attach its job id as an image
reference instead of regenerating:

```
STYLE KEY (diorama): 0561c26f-ad53-44da-815d-a8796d32d864
```

If a fresh key is ever needed, the prompt that produced it
(`generate_image`, `nano_banana_pro`, 16:9):

```
Cinematic vintage paper diorama style swatch, documentary collage
aesthetic: a miniature three-dimensional landscape built entirely from
aged sepia newspaper sheets and cardboard, torn edges, layered paper
canyon walls of old newsprint, monochrome archival photo cutouts of
anonymous suited figures standing among the paper structures with black
censor bars over their eyes, one dominant burnt-orange paper prop as the
single color accent against the sepia world, distressed letterpress print
texture, warm tungsten documentary lighting with deep shadows, macro
tilt-shift lens look with shallow depth of field, film grain and dust.
Handcrafted physical paper materials only — no letters, no words, no
numbers, no logos. Non-photorealistic scene content, no live-action
people, stylized paper craft world.
```

## STYLE tokens (open every clip prompt with these)

```
cinematic vintage paper diorama, aged sepia newsprint world, monochrome
halftone print, monochrome archival cutout figures with black censor bars
over their eyes, single burnt-orange accent, distressed letterpress,
warm tungsten light, macro tilt-shift shallow depth of field, film grain,
handcrafted stop-motion paper feel, non-photorealistic, no live-action
```

## Prop typography

Unlike the Mixed Media style (which bans all in-clip text), this style
CARRIES short letterpress text on props — that's its signature. One label
per scene, 1–2 words or a number ("EXPIRED", "1,000", "AUGUST",
"WHO BLINKS?"), always described as "distressed letterpress" on a torn
burnt-orange paper element, and always fenced in the negative:
`No text anywhere except "<LABEL>". No gibberish letters…`.

## Reusable prop assets (attach to keep objects consistent)

Generated 1:1 on plain backgrounds with the style key referenced — pass
alongside the style key as extra `image_references` and say "the X from
the reference image" in the prompt so the object doesn't morph between
clips:

| Prop | Job id |
|---|---|
| Paper nuclear missile (orange nose) | 0cb0ada4-5376-44fe-8950-822425825336 |
| Aged newspaper front page (censor-bar portrait) | 4cf403d1-6791-4661-af13-7d61330accdd |
| Powder keg "WHO BLINKS?" + coiled fuse | 68d803d1-3876-4410-9be4-9d800f6913be |
| Three leader cutouts (US red tie / RU / CN) | bd35a771-ddd9-456f-827a-18027293d1b0 |

New props: `generate_image` + `nano_banana_pro`, 1:1, style key attached,
"Single reusable prop asset, centered on a plain warm off-white paper
background… Nothing else in frame."

## Engine: seedance_2_0 (ref-grade)

```
generate_video
  model: "seedance_2_0"
  duration: 10
  resolution: "720p"        # 45 cr; 1080p = 90 cr
  mode: "std"
  aspect_ratio: "16:9"
  genre: "noir"             # consistent dark grade across clips
  generate_audio: true      # native SFX/drone sound design — keep it
  medias: [ { value: "<style key>", role: "image_references" }, …props ]
```

Seedance executes in-prompt cuts ("Shot 1 … Cut to shot 2 …"), reads
"speed ramp", "FPV", "whip pan" literally, and renders real fire/embers
beautifully. Its native audio (fuse crackle, drones, impacts) survives
assembly under the voiceover — design it in the prompt ("Sound design: …
No speech.").

gemini_omni (30 cr) is the fallback — notably it renders RECOGNIZABLE
politician likenesses from descriptions where seedance refuses (see
moderation notes).

## Fake-oner block prompt shape

Every clip = one continuous camera move; every boundary hidden in motion
blur so hard cuts read as a single unbroken shot:

```
<STYLE tokens> — shot as ONE continuous high-energy FPV camera move with
aggressive speed ramps.
The shot: [emerges from motion-blurred <previous element>] … [one impact
moment every ~3s: slam / stamp / shockwave / snap] … [ends fully
motion-blurred mid-<dive/whip/fall/flare>].
Sound design: [3–5 concrete diegetic events]. No speech.
No text anywhere except "<LABEL>". No gibberish letters, no captions,
no watermark, no photorealism, no live-action.
```

Worked example (opening block of "WHO BLINKS?"):

```
…shot as ONE continuous high-energy FPV camera move with aggressive speed
ramps.
The shot: from black, EXTREME slow-motion macro of a halftone-printed
human eye on newsprint as a thick black censor bar SLAMS down over it
like a guillotine, paper dust exploding on impact. Violent speed-ramp
pull-back reveals it is a giant newspaper front-page portrait of a
heavyset elderly American statesman with a long red tie; a gust RIPS the
page away revealing a second portrait — a compact stern Russian
statesman — ripped away again to a third — an East Asian statesman —
each rip faster than the last. The camera then DIVES at full speed into a
tearing gap in a giant aged treaty document as a burnt-orange stamp
punches the letterpress word "EXPIRED" across it; the lens plunges
through the torn fibers into swirling paper dust, ending mid-dive fully
motion-blurred.
Sound design: guillotine slam with dust whump, three accelerating page
rips, one massive stamp punch, rushing paper wind. No speech.
No text except "EXPIRED". …
```

## Moderation map (hard-won)

- **Named politicians in video prompts → job FAILS** on seedance (submits
  fine, dies at render). Names are fine in the TTS voiceover.
- **Close-up recognizable statesman faces** (even described, unnamed) →
  seedance fails; **gemini_omni renders them** — route face-forward
  blocks to gemini, keep the same style key.
- Mid-shot / full-body "leader with red tie / compact Russian / East
  Asian statesman" descriptions pass on BOTH engines. Censor bars over
  the eyes both sell the editorial look and defuse likeness issues.
- **"mushroom cloud" → nsfw flag.** Replace with another silhouette
  (hourglass worked and fit the deadline theme better).
- The server intercepts stylized prompts with `preset_recommendation`
  notices (3D RENDER / IN THE DARK / DROWN IN MUSIC / FREE FALL…). Never
  accept — resubmit with `declined_preset_id` from `retry_literal_with`.
  The id only suppresses that exact preset; a new prompt may trip a
  different one.

## Music

No standalone music model is usable through this MCP (sonilo_music is
game-pipeline-only — decline, don't substitute). Options: rely on
seedance's native drone/SFX bed (usually enough), or brief an external
generator (Suno/Udio) and mix locally. A measured brief that matched the
reference: ~46 BPM heartbeat pulse, sub-bass drone + low cello, almost no
highs, 8-second breathing swells, loud open, single climax at 80% of
runtime, rapid decay to silence.
