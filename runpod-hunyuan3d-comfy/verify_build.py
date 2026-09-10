"""Fail the build if the environment is in the state that shipped twice before.

Both earlier attempts ended with a torch that had been silently replaced by some
dependency's requirements, which left the compiled CUDA extensions orphaned. The
image still built and still started; the failure appeared only at generation time
as "libcublasLt.so.*[0-9] not found in the system path".

So a build that reaches this point with a swapped torch, or with an extension
that no longer imports, is a failed build regardless of whether every command
above it returned 0. Run this as the last layer.
"""

import sys

import torch

baseline = open("/torch-baseline.txt").read().strip()

if torch.__version__ != baseline:
    sys.exit(
        f"verify_build: torch was replaced during the build: "
        f"{baseline} -> {torch.__version__}. Some pip step listed torch in its "
        f"requirements; the compiled extensions no longer match."
    )

if not (torch.version.cuda or "").startswith("12.8"):
    sys.exit(f"verify_build: not a cu128 torch: {torch.version.cuda}")

# Importing is the actual test: these are compiled against the torch above, and
# a mismatch surfaces here as a missing CUDA library rather than at request time.
import custom_rasterizer  # noqa: E402
import mesh_processor  # noqa: E402

# The node's own dependencies, checked in the venv start.sh actually launches.
# comfy-node-install puts these in comfy-cli's workspace venv (/comfyui/.venv),
# which is NOT the one that serves requests -- an easy way to ship an image that
# builds cleanly and then fails on its first job with an ImportError.
missing = []
for mod in ("trimesh", "pymeshlab", "pygltflib", "xatlas", "open3d",
            "omegaconf", "meshlib", "timm", "diffusers", "transformers"):
    try:
        __import__(mod)
    except ImportError as e:
        missing.append(f"{mod} ({e})")

if missing:
    sys.exit(
        "verify_build: these node dependencies are absent from the runtime venv "
        "(" + sys.prefix + "): " + ", ".join(missing)
    )

print(f"verify_build: ok — torch {torch.__version__}, cuda {torch.version.cuda}")
print(f"verify_build: runtime venv {sys.prefix}")
print(f"verify_build: custom_rasterizer {custom_rasterizer.__file__}")
print(f"verify_build: mesh_processor {mesh_processor.__file__}")
