import bpy
import os
import subprocess

sc = bpy.context.scene

sc.render.engine = "CYCLES"
sc.cycles.device = "GPU"
sc.cycles.samples = 512
sc.cycles.use_adaptive_sampling = True
sc.cycles.adaptive_threshold = 0.01
sc.cycles.use_denoising = True
try:
    sc.cycles.denoiser = "OPENIMAGEDENOISE"
except (AttributeError, TypeError):
    pass
sc.cycles.max_bounces = 8
sc.cycles.transmission_bounces = 4

sc.render.resolution_x = 1920
sc.render.resolution_y = 1200
sc.render.resolution_percentage = 100
sc.render.film_transparent = False
sc.render.image_settings.file_format = "PNG"
sc.render.image_settings.color_depth = "16"

BLEND = "/tmp/sfp_scene.blend"
OUT = "/tmp/sfp_final_"
for stale in ("/tmp/sfp_final_0001.png",):
    if os.path.exists(stale):
        os.remove(stale)

# copy=True: writes the file without repointing this session at it
bpy.ops.wm.save_as_mainfile(filepath=BLEND, copy=True, compress=True)

# detached background render -- never block the MCP socket on Cycles
proc = subprocess.Popen(
    [bpy.app.binary_path, "-b", BLEND, "-o", OUT, "-F", "PNG", "-f", "1"],
    stdout=open("/tmp/sfp_render.log", "w"),
    stderr=subprocess.STDOUT,
    start_new_session=True,
)
print("LAUNCHED pid", proc.pid, "->", OUT + "0001.png")
