# Hunyuan3D-2.1 image-to-3D as a RunPod Serverless worker.
#
# Built on RunPod's maintained worker-comfyui, which already provides ComfyUI,
# the serverless handler, and the /runsync request contract. Everything below
# adds the 3D wrapper node, its compiled CUDA extensions, and the model weights.
#
# Why an image and not a pod: two earlier attempts installed these dependencies
# on a running, billed pod. A pip step whose requirements listed `torch`
# replaced the CUDA-matched wheel, which orphaned the compiled extensions and
# surfaced only at generation time as "libcublasLt.so.*[0-9] not found in the
# system path". Here the same mistake fails the build instead, for free, and a
# working image is cached rather than rebuilt per run. See the guard at the end.

FROM runpod/worker-comfyui:5.10.0-base-cuda12.8.1

# The base ships the CUDA *runtime* image (nvidia/cuda:12.8.1-cudnn-runtime),
# which has no nvcc. The two extensions below are compiled CUDA/C++, so the
# build needs the toolkit -- and nvcc on its own is not a compiler, gcc has to
# be there too.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cuda-nvcc-12-8 \
        cuda-cudart-dev-12-8 \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ENV CUDA_HOME=/usr/local/cuda
ENV PATH=/usr/local/cuda/bin:${PATH}
# Build for the GPU families RunPod actually schedules: Ampere (A40/A100),
# Ada (L4/L40S/4090) and Hopper. Without this, setup.py probes the build host,
# which has no GPU, and produces an extension that matches nothing.
ENV TORCH_CUDA_ARCH_LIST="8.0;8.6;8.9;9.0+PTX"

# Record the torch that ships with the base image, before anything else runs.
# The guard at the end of this file compares against it.
RUN python -c "import torch; open('/torch-baseline.txt','w').write(torch.__version__)" \
    && echo "torch baseline: $(cat /torch-baseline.txt)"

# The ComfyUI wrapper node for Hunyuan3D-2.1. Its requirements.txt does not list
# torch, which is what makes this safe to install -- but the guard below still
# verifies that, rather than trusting it.
RUN comfy-node-install https://github.com/visualbruno/ComfyUI-Hunyuan3d-2-1

WORKDIR /comfyui/custom_nodes/ComfyUI-Hunyuan3d-2-1

# Compile the two CUDA extensions. This is the step that broke both previous
# attempts; it now fails the build rather than the request.
RUN cd hy3dpaint/custom_rasterizer && python setup.py install
RUN cd hy3dpaint/DifferentiableRenderer && python setup.py install

# Model weights, baked into the image. A network volume would reintroduce a
# mount and a cold read on every job, which is most of what we are removing.
# Upstream ships these as model.fp16.ckpt inside per-model directories, but the
# nodes list ComfyUI's flat models/diffusion_models and models/vae folders, so
# the names have to be flattened. These exact filenames are what workflow.json
# asks for -- change one and you must change the other.
RUN mkdir -p /comfyui/models/diffusion_models /comfyui/models/vae && python -c "\
from huggingface_hub import hf_hub_download; import shutil; \
shutil.copy(hf_hub_download('tencent/Hunyuan3D-2.1', 'hunyuan3d-dit-v2-1/model.fp16.ckpt'), \
            '/comfyui/models/diffusion_models/hunyuan3d-dit-v2-1-fp16.ckpt'); \
shutil.copy(hf_hub_download('tencent/Hunyuan3D-2.1', 'hunyuan3d-vae-v2-1/model.fp16.ckpt'), \
            '/comfyui/models/vae/hunyuan3d-vae-v2-1-fp16.ckpt')"

WORKDIR /comfyui

# The stock handler collects only the "images" key from node outputs and drops
# everything else, so the exported GLB would never reach the caller. See the
# patch's docstring.
COPY patch_handler.py /tmp/patch_handler.py
RUN python /tmp/patch_handler.py

# Final guard. An image that reaches here with a swapped torch, or with an
# extension that no longer imports, is a failed build regardless of whether
# every command above returned 0 -- that exact combination is what shipped a
# dead environment twice before.
RUN python -c "\
import torch, custom_rasterizer, mesh_processor; \
baseline = open('/torch-baseline.txt').read().strip(); \
assert torch.__version__ == baseline, \
    f'torch was replaced during build: {baseline} -> {torch.__version__}'; \
assert torch.version.cuda.startswith('12.8'), f'not a cu128 torch: {torch.version.cuda}'; \
print('guard ok:', torch.__version__, torch.version.cuda)"
