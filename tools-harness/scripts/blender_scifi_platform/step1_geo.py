import bmesh
import bpy
from math import cos, pi, sin
from mathutils import Matrix, Vector

R = 2.0
N = 96
PREFIX = "SFP_"

# ---------------------------------------------------------------- idempotency
for ob in [o for o in bpy.data.objects
           if o.name.startswith(PREFIX) and o.type == 'MESH']:
    bpy.data.objects.remove(ob, do_unlink=True)
for me in [m for m in bpy.data.meshes if m.name.startswith(PREFIX)]:
    bpy.data.meshes.remove(me)

SCENE = bpy.context.scene


def new_obj(name, bm):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    SCENE.collection.objects.link(ob)
    return ob


# ---------------------------------------------------------------- body lathe
# (radius_factor, z) from bottom-centre outward, up the rim, inward across top
PROFILE = [
    (0.00, 0.00),   # 0  bottom centre
    (0.98, 0.00),   # 1  bottom outer
    (1.00, 0.10),   # 2  rim wall flare
    (0.97, 0.30),   # 3  rim wall top
    (0.93, 0.32),   # 4  outer band start
    (0.68, 0.32),   # 5  outer band end
    (0.66, 0.28),   # 6  groove wall
    (0.60, 0.28),   # 7  groove floor
    (0.58, 0.32),   # 8  middle band start
    (0.44, 0.32),   # 9  middle band end
    (0.42, 0.36),   # 10 inner lip top
    (0.34, 0.36),   # 11 inner lip flat
    (0.32, 0.21),   # 12 portal recess wall
    (0.00, 0.21),   # 13 portal floor centre
]

bm = bmesh.new()
rings = []          # per profile index: list of verts, or [single centre vert]
for rf, z in PROFILE:
    if rf == 0.0:
        rings.append([bm.verts.new((0.0, 0.0, z))])
    else:
        r = rf * R
        rings.append([
            bm.verts.new((r * cos(2 * pi * j / N), r * sin(2 * pi * j / N), z))
            for j in range(N)
        ])

row_faces = []      # row_faces[i] bridges rings[i] -> rings[i+1]
for i in range(len(rings) - 1):
    a, b = rings[i], rings[i + 1]
    faces = []
    if len(a) == 1:                                   # fan cap from centre
        for j in range(N):
            faces.append(bm.faces.new((a[0], b[j], b[(j + 1) % N])))
    elif len(b) == 1:                                 # fan cap to centre
        for j in range(N):
            faces.append(bm.faces.new((a[j], a[(j + 1) % N], b[0])))
    else:
        for j in range(N):
            k = (j + 1) % N
            faces.append(bm.faces.new((a[j], a[k], b[k], b[j])))
    row_faces.append(faces)

# panel breakup: group contiguous faces of a row, inset each group as one panel
def panel_rows(row, group, thickness, depth, detail=False):
    for g in range(0, N, group):
        grp = [row[(g + k) % N] for k in range(group)]
        bmesh.ops.inset_region(
            bm, faces=grp, thickness=thickness, depth=depth,
            use_boundary=True, use_even_offset=True,
        )
        if not detail:
            continue
        # recessed border frame just inside the panel edge
        bmesh.ops.inset_region(
            bm, faces=grp, thickness=thickness * 0.55, depth=-0.0030 * R,
            use_boundary=True, use_even_offset=True,
        )
        # raised service pad over the middle third of the panel
        pad = grp[group // 3: max(group // 3 + 1, (2 * group) // 3)]
        bmesh.ops.inset_region(
            bm, faces=pad, thickness=thickness * 0.45, depth=0.0055 * R,
            use_boundary=True, use_even_offset=True,
        )


panel_rows(row_faces[4], 6, 0.022 * R, -0.010 * R, detail=True)   # outer -> 16
panel_rows(row_faces[8], 12, 0.020 * R, -0.010 * R, detail=True)  # middle -> 8
panel_rows(row_faces[2], 4, 0.010 * R, -0.008 * R)   # rim wall slots -> 24
panel_rows(row_faces[10], 12, 0.012 * R, -0.006 * R)  # inner lip

bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
body = new_obj(PREFIX + "Body", bm)

# ---------------------------------------------------------------- spokes
SPOKE_COUNT = 8
bm = bmesh.new()
for s in range(SPOKE_COUNT):
    sub = bmesh.new()
    r0, r1 = 0.60 * R, 0.97 * R
    hw, z0, z1 = 0.075 * R, 0.26, 0.355
    v = [sub.verts.new(p) for p in (
        (r0, -hw, z0), (r1, -hw, z0), (r1, hw, z0), (r0, hw, z0),
        (r0, -hw, z1), (r1, -hw, z1), (r1, hw, z1), (r0, hw, z1),
    )]
    sub.faces.new((v[0], v[3], v[2], v[1]))            # bottom
    top = sub.faces.new((v[4], v[5], v[6], v[7]))      # top
    sub.faces.new((v[0], v[1], v[5], v[4]))
    sub.faces.new((v[1], v[2], v[6], v[5]))
    sub.faces.new((v[2], v[3], v[7], v[6]))
    sub.faces.new((v[3], v[0], v[4], v[7]))
    bmesh.ops.inset_region(
        sub, faces=[top], thickness=0.022 * R, depth=-0.012 * R,
        use_boundary=True, use_even_offset=True,
    )
    bmesh.ops.recalc_face_normals(sub, faces=sub.faces[:])
    bmesh.ops.rotate(
        sub, verts=sub.verts[:], cent=(0, 0, 0),
        matrix=Matrix.Rotation(2 * pi * s / SPOKE_COUNT, 3, "Z"),
    )
    me_tmp = bpy.data.meshes.new("SFP_tmp")
    sub.to_mesh(me_tmp)
    sub.free()
    bm.from_mesh(me_tmp)
    bpy.data.meshes.remove(me_tmp)
spokes = new_obj(PREFIX + "Spokes", bm)


# ---------------------------------------------------------------- emissive dashes
def arc_dash(bm, r_in, r_out, a0, a1, z, steps=8):
    inner = [bm.verts.new((r_in * cos(a), r_in * sin(a), z))
             for a in [a0 + (a1 - a0) * t / steps for t in range(steps + 1)]]
    outer = [bm.verts.new((r_out * cos(a), r_out * sin(a), z))
             for a in [a0 + (a1 - a0) * t / steps for t in range(steps + 1)]]
    for t in range(steps):
        bm.faces.new((inner[t], inner[t + 1], outer[t + 1], outer[t]))


# cyan: groove ring dashes + rim top dashes, 60% duty cycle per segment
bm = bmesh.new()
for count, r_mid, half_w, z in ((16, 0.630 * R, 0.009 * R, 0.283),
                                (24, 0.952 * R, 0.006 * R, 0.323)):
    span = 2 * pi / count
    for i in range(count):
        a0 = i * span + span * 0.20
        arc_dash(bm, r_mid - half_w, r_mid + half_w, a0, a0 + span * 0.52, z)
bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
strips_cyan = new_obj(PREFIX + "StripsCyan", bm)

# violet: spoke channel lines + inner lip dashes
bm = bmesh.new()
for s in range(SPOKE_COUNT):
    sub = bmesh.new()
    r0, r1, hw, z = 0.64 * R, 0.94 * R, 0.016 * R, 0.346
    v = [sub.verts.new(p) for p in ((r0, -hw, z), (r1, -hw, z), (r1, hw, z), (r0, hw, z))]
    sub.faces.new(v)
    bmesh.ops.rotate(
        sub, verts=sub.verts[:], cent=(0, 0, 0),
        matrix=Matrix.Rotation(2 * pi * s / SPOKE_COUNT, 3, "Z"),
    )
    me_tmp = bpy.data.meshes.new("SFP_tmp")
    sub.to_mesh(me_tmp)
    sub.free()
    bm.from_mesh(me_tmp)
    bpy.data.meshes.remove(me_tmp)
span = 2 * pi / 24
for i in range(24):
    a0 = i * span + span * 0.25
    arc_dash(bm, 0.378 * R, 0.391 * R, a0, a0 + span * 0.50, 0.363)
bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
strips_violet = new_obj(PREFIX + "StripsViolet", bm)

# ---------------------------------------------------------------- portal disc
bm = bmesh.new()
rim = [bm.verts.new((0.315 * R * cos(2 * pi * j / N), 0.315 * R * sin(2 * pi * j / N), 0.215))
       for j in range(N)]
bm.faces.new(rim)
bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
portal = new_obj(PREFIX + "Portal", bm)

# ---------------------------------------------------------------- bevel
for ob in (body, spokes):
    mod = ob.modifiers.new("SFP_Bevel", "BEVEL")
    mod.width = 0.006 * R
    mod.segments = 2
    mod.limit_method = "ANGLE"
    mod.angle_limit = 0.52

bpy.context.view_layer.update()
print("OBJS", [(o.name, len(o.data.polygons)) for o in bpy.data.objects
               if o.name.startswith(PREFIX) and o.type == "MESH"])
