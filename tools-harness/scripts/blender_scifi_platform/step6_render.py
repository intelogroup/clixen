import bpy
from math import radians
from mathutils import Vector

R = 2.0
sc = bpy.context.scene

# ------------------------------------------------------------------- camera
cam = bpy.data.objects["Camera"]
cam.data.lens = 55.0
cam.location = (4.55, -4.55, 2.85)
direction = Vector((0.0, 0.0, 0.22)) - cam.location
cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

# ------------------------------------------------------------------- lighting
lamp = bpy.data.objects["Light"]
lamp.data.type = "AREA"
lamp.data.shape = "DISK"
lamp.data.size = 4.0 * R
lamp.data.energy = 520.0
lamp.data.color = (0.72, 0.82, 1.0)
lamp.location = (2.2, 2.6, 4.4)
d = Vector((0, 0, 0.3)) - lamp.location
lamp.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()

# dark backdrop so the emissives carry the image
world = sc.world
world.use_nodes = True
bg = world.node_tree.nodes.get("Background")
bg.inputs["Color"].default_value = (0.030, 0.033, 0.042, 1.0)
bg.inputs["Strength"].default_value = 1.0

# warm fill from the opposite side so panels read instead of going black
fill = bpy.data.objects.get("SFP_Fill")
if fill is None:
    fdata = bpy.data.lights.new("SFP_Fill", type="AREA")
    fill = bpy.data.objects.new("SFP_Fill", fdata)
    sc.collection.objects.link(fill)
fill.data.type = "AREA"
fill.data.shape = "DISK"
fill.data.size = 6.0 * R
fill.data.energy = 160.0
fill.data.color = (1.0, 0.86, 0.70)
fill.location = (-3.6, -3.2, 2.6)
fd = Vector((0, 0, 0.3)) - fill.location
fill.rotation_euler = fd.to_track_quat("-Z", "Y").to_euler()

# ------------------------------------------------------------------- glow
sc.use_nodes = True
ng = sc.compositing_node_group
if ng is None:
    ng = bpy.data.node_groups.new("SFP_Comp", "CompositorNodeTree")
    sc.compositing_node_group = ng
ng.nodes.clear()
if not any(s.name == "Image" and s.in_out == "OUTPUT" for s in ng.interface.items_tree):
    ng.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
rl = ng.nodes.new("CompositorNodeRLayers")
glare = ng.nodes.new("CompositorNodeGlare")
glare.inputs["Type"].default_value = "Bloom"
glare.inputs["Quality"].default_value = "High"
glare.inputs["Threshold"].default_value = 1.0
glare.inputs["Strength"].default_value = 0.32
glare.inputs["Size"].default_value = 0.55
group_out = ng.nodes.new("NodeGroupOutput")
ng.links.new(rl.outputs["Image"], glare.inputs["Image"])
ng.links.new(glare.outputs["Image"], group_out.inputs["Image"])

# ------------------------------------------------------------------- render
sc.render.engine = "BLENDER_EEVEE"
sc.render.resolution_x = 1280
sc.render.resolution_y = 800
sc.render.resolution_percentage = 100
sc.render.filepath = "/tmp/sfp_preview.png"
sc.render.image_settings.file_format = "PNG"
try:
    sc.eevee.taa_render_samples = 64
except AttributeError:
    pass

bpy.ops.render.render(write_still=True)
print("RENDERED", sc.render.filepath, sc.render.engine)
