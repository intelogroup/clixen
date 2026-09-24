#!/usr/bin/env bash
# Check the parts of the image build that keep failing, locally, before pushing.
#
# Every failure so far has come from the same layer: the apt install either
# cannot resolve, or resolves but leaves a header the extension compile needs
# missing. Those cost ~7-10 minutes each on RunPod because the base image is
# ~14 GB and gets pulled first. Reproducing just that layer takes about two
# minutes here.
#
# Deliberately does NOT run nvcc. The GPU compile is emulated on Apple Silicon
# and is slow and unreliable, and all three failures so far happened in c++ or
# in apt, before nvcc was ever reached. This checks what actually breaks.
#
# Requires Docker with an amd64 emulator (Docker Desktop provides one).
# Usage: ./preflight.sh

set -euo pipefail
cd "$(dirname "$0")"

# Reuse the real Dockerfile's package list rather than a copy that can drift.
apt_layer=$(python3 - <<'PY'
import re, pathlib
src = pathlib.Path("Dockerfile").read_text()
m = re.search(r"^RUN apt-get update.*?(?=\n[A-Z#])", src, re.S | re.M)
if not m:
    raise SystemExit("preflight: could not find the apt-get RUN line in Dockerfile")
print(m.group(0))
PY
)

base=$(grep -m1 '^FROM ' Dockerfile | awk '{print $2}')
echo "preflight: base $base"

# The headers the extension compile needs, reached via torch's
# ATen/cuda/CUDAContext.h and Python.h. A missing one here is exactly the
# failure that costs a full RunPod build.
cat > /tmp/preflight.Dockerfile <<EOF
FROM $base
$apt_layer
RUN set -e; \\
    missing=""; \\
    for h in /usr/include/python3.12/Python.h \\
             /usr/local/cuda/include/cusparse.h \\
             /usr/local/cuda/include/cublas_v2.h \\
             /usr/local/cuda/include/cusolverDn.h \\
             /usr/local/cuda/include/cuda_runtime.h; do \\
      [ -f "\$h" ] || missing="\$missing \$h"; \\
    done; \\
    if [ -n "\$missing" ]; then echo "preflight: MISSING HEADERS:\$missing"; exit 1; fi; \\
    command -v nvcc >/dev/null || { echo "preflight: nvcc not on PATH"; exit 1; }; \\
    command -v ninja >/dev/null || { echo "preflight: ninja not on PATH"; exit 1; }; \\
    echo "preflight: headers and toolchain present"
EOF

docker buildx build --platform linux/amd64 \
    -f /tmp/preflight.Dockerfile --progress=plain -t hy3d-preflight /tmp

echo "preflight: ok -- apt resolves and every header the compile needs is present"
