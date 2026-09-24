# blender_scifi_platform/

Procedural rebuild of a Meshy-generated sci-fi circular platform, built directly
in Blender via the Blender MCP socket (`localhost:9876`, addon capability map
in `~/.claude/skills/blender-motion/references/operational-canon.md`). Pure
`bmesh` — no Geometry Nodes, no image textures. ~3,300 faces vs. the source
asset's 1.9M.

Requires Blender 5.2 open with the Blender MCP add-on's server running.

## Run order

```
.venv/bin/python bmcp.py code step1_geo.py    # lathe profile, panels, spokes, emissive dashes, portal
.venv/bin/python bmcp.py code step5_mat.py    # 4 procedural materials, no textures
.venv/bin/python bmcp.py code step6_render.py # camera, lights, world, compositor glare, EEVEE preview
.venv/bin/python bmcp.py code step7_cycles.py # saves a .blend copy, fires a detached Cycles final render
```

Each geometry/material step is idempotent — rerun freely, objects/materials
are purged by name prefix (`SFP_`) before rebuild.

- `bmcp.py` — the raw JSON-over-TCP client (`send(cmd, params)` / `run_code(path)`). No dependencies beyond stdlib.
- `step1_geo.py` — `PROFILE` (radius-factor, z pairs) is the whole disc's cross-section, spun 360° with `bmesh.ops.spin`. `N` = radial resolution, `SPOKE_COUNT` = number of raised spokes. Change either and the rest follows.
- `step5_mat.py` — `SFP_Graphite` (metal), `SFP_EmitCyan`/`SFP_EmitViolet` (dash strips), `SFP_Portal` (radial gradient + concentric rings via shader nodes, no geometry).
- `step6_render.py` — EEVEE preview only, fast iteration loop.
- `step7_cycles.py` — final quality pass: GPU (Metal on Apple Silicon), 512 adaptive samples, OIDN denoise, saved as a detached background `blender -b ... -f 1` subprocess so the MCP socket never blocks on render time.

## Gotchas hit building this (Blender 5.2)

- `bpy.ops.mesh.primitive_*_add()` over MCP can silently fail to link into the scene collection — build meshes via `bmesh` + `bpy.data.objects.new()` + `scene.collection.objects.link()` instead.
- `scene.node_tree` is gone; compositor lives at `scene.compositing_node_group`, and there's no `CompositorNodeComposite` node anymore — output via `NodeGroupOutput` on the group's own `Image` interface socket.
- `CompositorNodeGlare` moved every setting (`glare_type`, `quality`, `mix`, `threshold`) from node properties to input sockets; `Type`/`Quality` are `NodeSocketMenu` taking capitalized strings (`"Bloom"`, `"High"`), not the old enum identifiers.
- A prefix-based purge-and-rebuild (`for o in bpy.data.objects if o.name.startswith(PREFIX)`) will delete non-mesh objects sharing the prefix too — a light named `SFP_Fill` vanishes on rebuild unless the filter also checks `o.type == 'MESH'`.

Full detail and verification history: `~/.claude/skills/blender-motion/references/limits-and-pitfalls.md` and `compositor-nodes.md`.
