import bpy

PREFIX = "SFP_"

for m in [m for m in bpy.data.materials if m.name.startswith(PREFIX)]:
    bpy.data.materials.remove(m)


def fresh(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    return mat, mat.node_tree.nodes, mat.node_tree.links


def assign(obj_name, mat):
    ob = bpy.data.objects[obj_name]
    ob.data.materials.clear()
    ob.data.materials.append(mat)


# ------------------------------------------------------------- graphite metal
mat, nodes, links = fresh(PREFIX + "Graphite")
out = nodes.new("ShaderNodeOutputMaterial")
bsdf = nodes.new("ShaderNodeBsdfPrincipled")
bsdf.inputs["Base Color"].default_value = (0.285, 0.262, 0.232, 1.0)
bsdf.inputs["Metallic"].default_value = 0.72
bsdf.inputs["Roughness"].default_value = 0.42

coord = nodes.new("ShaderNodeTexCoord")
noise = nodes.new("ShaderNodeTexNoise")
noise.inputs["Scale"].default_value = 38.0
noise.inputs["Detail"].default_value = 6.0
ramp = nodes.new("ShaderNodeValToRGB")
ramp.color_ramp.elements[0].position = 0.35
ramp.color_ramp.elements[0].color = (0.28, 0.28, 0.28, 1.0)
ramp.color_ramp.elements[1].position = 0.68
ramp.color_ramp.elements[1].color = (0.50, 0.50, 0.50, 1.0)
links.new(coord.outputs["Object"], noise.inputs["Vector"])
links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
links.new(ramp.outputs["Color"], bsdf.inputs["Roughness"])
links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
graphite = mat
assign(PREFIX + "Body", graphite)
assign(PREFIX + "Spokes", graphite)


# ------------------------------------------------------------- emissive strips
def emissive(name, color, strength):
    mat, nodes, links = fresh(name)
    out = nodes.new("ShaderNodeOutputMaterial")
    em = nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (*color, 1.0)
    em.inputs["Strength"].default_value = strength
    links.new(em.outputs["Emission"], out.inputs["Surface"])
    return mat


assign(PREFIX + "StripsCyan", emissive(PREFIX + "EmitCyan", (0.05, 0.80, 1.0), 2.6))
assign(PREFIX + "StripsViolet", emissive(PREFIX + "EmitViolet", (0.52, 0.22, 1.0), 2.6))

# ------------------------------------------------------------- portal
mat, nodes, links = fresh(PREFIX + "Portal")
out = nodes.new("ShaderNodeOutputMaterial")
em = nodes.new("ShaderNodeEmission")

coord = nodes.new("ShaderNodeTexCoord")
length = nodes.new("ShaderNodeVectorMath")
length.operation = "LENGTH"
maprange = nodes.new("ShaderNodeMapRange")
maprange.inputs["From Min"].default_value = 0.0
maprange.inputs["From Max"].default_value = 0.63   # portal disc radius (0.315 * R)
links.new(coord.outputs["Object"], length.inputs[0])
links.new(length.outputs["Value"], maprange.inputs["Value"])

# radial gradient: hot white-cyan core -> violet -> dark rim
grad = nodes.new("ShaderNodeValToRGB")
gr = grad.color_ramp
gr.elements[0].position = 0.0
gr.elements[0].color = (0.92, 0.99, 1.0, 1.0)
gr.elements[1].position = 0.11
gr.elements[1].color = (0.52, 0.82, 1.0, 1.0)     # cold blue halo round the core
for pos, col in ((0.32, (0.50, 0.46, 0.96, 1.0)),  # lavender
                 (0.62, (0.26, 0.13, 0.55, 1.0)),  # deep violet
                 (1.00, (0.03, 0.02, 0.09, 1.0))):
    gr.elements.new(pos).color = col
links.new(maprange.outputs["Result"], grad.inputs["Fac"])

# concentric ring lines
wave = nodes.new("ShaderNodeTexWave")
wave.wave_type = "RINGS"
wave.rings_direction = "SPHERICAL"
wave.inputs["Scale"].default_value = 9.0
wave.inputs["Distortion"].default_value = 0.0
wring = nodes.new("ShaderNodeValToRGB")
wring.color_ramp.elements[0].position = 0.46
wring.color_ramp.elements[0].color = (0.70, 0.70, 0.70, 1.0)
wring.color_ramp.elements[1].position = 0.56
wring.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
links.new(coord.outputs["Object"], wave.inputs["Vector"])
links.new(wave.outputs["Fac"], wring.inputs["Fac"])

mix = nodes.new("ShaderNodeMix")
mix.data_type = "RGBA"
mix.blend_type = "MULTIPLY"
mix.inputs["Factor"].default_value = 1.0
links.new(grad.outputs["Color"], mix.inputs[6])
links.new(wring.outputs["Color"], mix.inputs[7])
links.new(mix.outputs[2], em.inputs["Color"])

# brightness falls off with radius so the core reads hottest
strength = nodes.new("ShaderNodeMapRange")
strength.inputs["From Min"].default_value = 0.0
strength.inputs["From Max"].default_value = 1.0
strength.inputs["To Min"].default_value = 1.75
strength.inputs["To Max"].default_value = 0.28
links.new(maprange.outputs["Result"], strength.inputs["Value"])
links.new(strength.outputs["Result"], em.inputs["Strength"])
links.new(em.outputs["Emission"], out.inputs["Surface"])
assign(PREFIX + "Portal", mat)

print("MATS", sorted(m.name for m in bpy.data.materials if m.name.startswith(PREFIX)))
print("SLOTS", [(o.name, [s.name for s in o.data.materials])
                for o in bpy.data.objects
                if o.name.startswith(PREFIX) and o.type == "MESH"])
