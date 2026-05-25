"""
Benchmark Suite for PagedFieldprintAttention
============================================

This script empirically benchmarks the memory bandwidth and latency 
savings of the Fused Triton kernel vs. a Naive Unfused PyTorch implementation.
"""

import torch
import triton
import triton.testing
from fused_attention import paged_fieldprint_attention

def naive_unfused_attention(q, k_ctx, v_ctx, k_anchor, v_anchor):
    """
    Simulates the mathematically valid but hardware-inefficient 
    unfused dual-attention from the original markdown paper.
    Materializes the full N x N matrix in HBM.
    """
    # Concatenate along sequence dimension
    k_full = torch.cat([k_anchor, k_ctx], dim=2)
    v_full = torch.cat([v_anchor, v_ctx], dim=2)
    
    d_k = q.size(-1)
    
    # Materialize N x N attention matrix in HBM
    scores = torch.matmul(q, k_full.transpose(-2, -1)) / (d_k ** 0.5)
    attn = torch.softmax(scores, dim=-1)
    
    # Materialize final output in HBM
    out = torch.matmul(attn, v_full)
    return out

@triton.testing.perf_report(
    triton.testing.Benchmark(
        x_names=['N_CTX'],
        x_vals=[2**i for i in range(10, 16)], # 1024 to 32768
        line_arg='provider',
        line_vals=['naive', 'fused'],
        line_names=['Naive Unfused (PyTorch)', 'PagedFieldprint (Triton)'],
        styles=[('blue', '-'), ('green', '-')],
        ylabel='Latency (ms)',
        plot_name='attention-latency-benchmark',
        args={'BATCH': 1, 'H': 32, 'D_HEAD': 128, 'N_ANCHOR': 128}
    )
)
def benchmark_attention(BATCH, H, N_CTX, D_HEAD, N_ANCHOR, provider):
    q = torch.randn((BATCH, H, N_CTX, D_HEAD), device='cuda', dtype=torch.float16)
    k_ctx = torch.randn((BATCH, H, N_CTX, D_HEAD), device='cuda', dtype=torch.float16)
    v_ctx = torch.randn((BATCH, H, N_CTX, D_HEAD), device='cuda', dtype=torch.float16)
    
    k_anchor = torch.randn((BATCH, H, N_ANCHOR, D_HEAD), device='cuda', dtype=torch.float16)
    v_anchor = torch.randn((BATCH, H, N_ANCHOR, D_HEAD), device='cuda', dtype=torch.float16)

    quantiles = [0.5, 0.2, 0.8]
    if provider == 'naive':
        ms, min_ms, max_ms = triton.testing.do_bench(lambda: naive_unfused_attention(q, k_ctx, v_ctx, k_anchor, v_anchor), quantiles=quantiles)
    if provider == 'fused':
        ms, min_ms, max_ms = triton.testing.do_bench(lambda: paged_fieldprint_attention(q, k_ctx, v_ctx, k_anchor, v_anchor), quantiles=quantiles)
        
    return ms, min_ms, max_ms

if __name__ == '__main__':
    print("Running PagedFieldprintAttention Benchmark Suite...")
    benchmark_attention.run(save_path='.', print_data=True)
