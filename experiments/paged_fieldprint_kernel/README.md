# PagedFieldprintAttention Kernel Benchmark

This directory contains the Triton kernel implementation and benchmark suite for the `PagedFieldprintAttention` mechanism proposed in the Verifiable Dual-Path Architecture.

## Architecture

Modern autoregressive generation relies heavily on fused attention kernels (like FlashAttention) to prevent HBM memory thrashing. Unfused additions of cryptographic identity anchors force intermediate matrices to be written back to HBM, destroying inference throughput. 

Our custom Triton kernel, `paged_fieldprint_attention_kernel`, resolves this by computing the attention scores for the cryptographic anchor tokens in phase 1, and the standard context tokens in phase 2, scaling and accumulating the softmax reduction entirely in SRAM.

## Files
- `fused_attention.py`: The Triton kernel implementation.
- `benchmark.py`: The `triton.testing.perf_report` harness comparing the naive PyTorch implementation against our fused Triton kernel.

## Execution

This benchmark requires a CUDA-enabled NVIDIA GPU.

```bash
pip install torch triton
python benchmark.py
```

The script will sweep across context lengths from 1,024 to 32,768 and generate `attention-latency-benchmark.csv` and a PNG plot demonstrating the $O(N)$ vs $O(N^2)$ memory bandwidth costs.
