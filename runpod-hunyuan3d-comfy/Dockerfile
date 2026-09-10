# Hunyuan3D-2.1 image-to-3D as a RunPod Serverless worker.
#
# Built on RunPod's maintained worker-comfyui, which already provides ComfyUI,
# the serverless handler, and the /run request contract. Everything below adds
# the 3D wrapper node, its compiled CUDA extensions, and the model weights.
#
# Why an image and not a pod: two earlier attempts installed these dependencies
# on a running, billed pod. A pip step whose requirements listed `torch`
# replaced the CUDA-matched wheel, which orphaned the compiled extensions and
# surfaced only at generation time as "libcublasLt.so.*[0-9] not found in the
# system path". Here the same mistake fails the build instead, for free, and a
# working image is cached rather than rebuilt per run. See verify_build.py.
#
# Build steps are separate RUN layers on purpose: the compiles and the weight
# download are the slow parts, and a failure in one should not reprice the rest.

FROM runpod/worker-comfyui:5.10.0-base-cuda12.8.1

# The base ships the CUDA *runtime* image (nvidia/cuda:12.8.1-cudnn-runtime),
# which has no nvcc. The two extensions below are compiled CUDA/C++, so the
# build needs the toolkit -- and nvcc on its own is not a compiler, gcc has to
# be there too.
#
# cudart-dev alone is not enough either: the extensions include torch's
# ATen/cuda/CUDAContextLight.h, which includes cusparse.h, cublas_v2.h and
# cusolverDn.h, so those three -dev packages have to be present or the very
# first .cpp fails with "cusparse.h: No such file or directory".
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        python3-dev \
        ninja-build \
        cuda-nvcc-12-8 \
        cuda-cudart-dev-12-8 \
        libcusparse-dev-12-8 \
        libcublas-dev-12-8 \
        libcusolver-dev-12-8 \
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
# verify_build.py compares against this.
RUN python -c "import torch, pathlib; pathlib.Path('/torch-baseline.txt').write_text(torch.__version__)" \
    && echo "torch baseline: $(cat /torch-baseline.txt)"

# The ComfyUI wrapper node for Hunyuan3D-2.1. Its requirements.txt does not list
# torch, which is what makes this safe to install -- but verify_build.py still
# checks, rather than trusting it.
RUN comfy-node-install https://github.com/visualbruno/ComfyUI-Hunyuan3d-2-1

# comfy-node-install resolves the node's requirements with ComfyUI-Manager,
# which installs into comfy-cli's workspace venv (/comfyui/.venv). start.sh
# launches ComfyUI from /opt/venv instead -- the base image says so itself and
# mirrors custom_nodes/*/requirements.txt into /opt/venv to compensate, but that
# runs while building the base, before this node exists. So mirror this node's
# requirements here too, or trimesh/pymeshlab/open3d/meshlib are missing at
# request time while the build still looks clean.
#
# The transformers/huggingface-hub pin is re-applied in the same layer for the
# reason the base gives: this requirements.txt lists both unpinned, and
# transformers 5.x / huggingface-hub 1.x crash ComfyUI at startup. Same RUN, so
# the unwanted versions are not left behind in the layer.
RUN uv pip install -r /comfyui/custom_nodes/ComfyUI-Hunyuan3d-2-1/requirements.txt \
    && uv pip install "transformers>=4.50.3,<5" "huggingface-hub<1.0"

# Compile the two CUDA extensions. This is the step that broke both previous
# attempts; it now fails the build rather than the request.
WORKDIR /comfyui/custom_nodes/ComfyUI-Hunyuan3d-2-1/hy3dpaint/custom_rasterizer
RUN python setup.py install

WORKDIR /comfyui/custom_nodes/ComfyUI-Hunyuan3d-2-1/hy3dpaint/DifferentiableRenderer
RUN python setup.py install

WORKDIR /comfyui

# Model weights, baked into the image. A network volume would reintroduce a
# mount and a cold read on every job, which is most of what we are removing.
COPY download_models.py /tmp/download_models.py
RUN python /tmp/download_models.py

# The stock handler collects only the "images" key from node outputs and drops
# everything else, so the exported GLB would never reach the caller.
COPY patch_handler.py /tmp/patch_handler.py
RUN python /tmp/patch_handler.py

# Last layer: refuse to ship an environment in the state that failed twice.
COPY verify_build.py /tmp/verify_build.py
RUN python /tmp/verify_build.py
