"""AutoKaraoke: semi-automatic karaoke track generator."""

import os

__version__ = "1.0.0"

# PyTorch >= 2.14 swaps some CUDA ops for Triton kernels compiled on first use, which needs
# a C compiler most users do not have. The regular kernels are fast enough here.
os.environ.setdefault("TORCH_DISABLE_NATIVE_JIT", "1")
