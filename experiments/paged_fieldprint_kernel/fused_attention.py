"""
PagedFieldprintAttention: Custom Triton Kernel Implementation
===========================================================

This module implements the fused CUDA/Triton kernel proposed in Paper 02.
It computes:
Output = FusedSoftmax( (Q [K, K_anchor]^T) / sqrt(d) ) [V, V_anchor]

By computing the anchor contribution explicitly inside the SRAM before
writing back to HBM, it avoids the catastrophic memory thrashing of unfused dual-attention.
"""

import torch
import triton
import triton.language as tl

@triton.jit
def paged_fieldprint_attention_kernel(
    Q, K_ctx, V_ctx, K_anchor, V_anchor, Out,
    stride_qz, stride_qh, stride_qm, stride_qk,
    stride_kz, stride_kh, stride_kn, stride_kk,
    stride_vz, stride_vh, stride_vn, stride_vk,
    stride_oz, stride_oh, stride_om, stride_ok,
    Z, H, N_CTX, N_ANCHOR,
    BLOCK_M: tl.constexpr, BLOCK_DMODEL: tl.constexpr, BLOCK_N: tl.constexpr,
):
    start_m = tl.program_id(0)
    off_hz = tl.program_id(1)

    # Initialize offsets
    q_offset = off_hz * stride_qh
    k_ctx_offset = off_hz * stride_kh
    v_ctx_offset = off_hz * stride_vh
    k_anchor_offset = off_hz * stride_kh # Assuming same layout
    v_anchor_offset = off_hz * stride_vh

    # Block pointers
    offs_m = start_m * BLOCK_M + tl.arange(0, BLOCK_M)
    offs_n = tl.arange(0, BLOCK_N)
    offs_d = tl.arange(0, BLOCK_DMODEL)
    
    q_ptrs = Q + q_offset + offs_m[:, None] * stride_qm + offs_d[None, :] * stride_qk
    o_ptrs = Out + q_offset + offs_m[:, None] * stride_om + offs_d[None, :] * stride_ok

    # Load Q
    q = tl.load(q_ptrs)
    
    # Initialize accumulators
    m_i = tl.zeros([BLOCK_M], dtype=tl.float32) - float("inf")
    l_i = tl.zeros([BLOCK_M], dtype=tl.float32)
    acc = tl.zeros([BLOCK_M, BLOCK_DMODEL], dtype=tl.float32)

    sm_scale = 1.0 / (BLOCK_DMODEL ** 0.5)

    # -------------------------------------------------------------
    # PHASE 1: Process the Cryptographic Anchor Tokens
    # -------------------------------------------------------------
    # We process the anchor tokens first. They compete in the softmax.
    for start_n in range(0, N_ANCHOR, BLOCK_N):
        k_ptrs = K_anchor + k_anchor_offset + (start_n + offs_n[None, :]) * stride_kn + offs_d[:, None] * stride_kk
        v_ptrs = V_anchor + v_anchor_offset + (start_n + offs_n[:, None]) * stride_vn + offs_d[None, :] * stride_vk
        
        k = tl.load(k_ptrs)
        qk = tl.dot(q, k) * sm_scale
        
        # Softmax logic
        m_ij = tl.maximum(m_i, tl.max(qk, 1))
        p = tl.math.exp(qk - m_ij[:, None])
        l_ij = tl.sum(p, 1)
        
        # Scaling previous acc
        alpha = tl.math.exp(m_i - m_ij)
        acc = acc * alpha[:, None]
        
        # Update
        v = tl.load(v_ptrs)
        acc += tl.dot(p.to(tl.float16), v)
        m_i = m_ij
        l_i = l_i * alpha + l_ij

    # -------------------------------------------------------------
    # PHASE 2: Process the Standard Context Tokens
    # -------------------------------------------------------------
    for start_n in range(0, N_CTX, BLOCK_N):
        k_ptrs = K_ctx + k_ctx_offset + (start_n + offs_n[None, :]) * stride_kn + offs_d[:, None] * stride_kk
        v_ptrs = V_ctx + v_ctx_offset + (start_n + offs_n[:, None]) * stride_vn + offs_d[None, :] * stride_vk
        
        k = tl.load(k_ptrs)
        qk = tl.dot(q, k) * sm_scale
        
        # Softmax logic
        m_ij = tl.maximum(m_i, tl.max(qk, 1))
        p = tl.math.exp(qk - m_ij[:, None])
        l_ij = tl.sum(p, 1)
        
        alpha = tl.math.exp(m_i - m_ij)
        acc = acc * alpha[:, None]
        
        v = tl.load(v_ptrs)
        acc += tl.dot(p.to(tl.float16), v)
        m_i = m_ij
        l_i = l_i * alpha + l_ij

    # Normalize and write back
    acc = acc / l_i[:, None]
    tl.store(o_ptrs, acc.to(tl.float16))

def paged_fieldprint_attention(q, k_ctx, v_ctx, k_anchor, v_anchor):
    # Shape expectations: [batch, heads, seq_len, d_model]
    Z, H, N_CTX, D_HEAD = q.shape
    _, _, N_ANCHOR, _ = k_anchor.shape
    
    out = torch.empty_like(q)
    
    BLOCK_M = 128
    BLOCK_N = 64
    
    grid = (triton.cdiv(N_CTX, BLOCK_M), Z * H)
    
    paged_fieldprint_attention_kernel[grid](
        q, k_ctx, v_ctx, k_anchor, v_anchor, out,
        q.stride(0), q.stride(1), q.stride(2), q.stride(3),
        k_ctx.stride(0), k_ctx.stride(1), k_ctx.stride(2), k_ctx.stride(3),
        v_ctx.stride(0), v_ctx.stride(1), v_ctx.stride(2), v_ctx.stride(3),
        out.stride(0), out.stride(1), out.stride(2), out.stride(3),
        Z, H, N_CTX, N_ANCHOR,
        BLOCK_M=BLOCK_M, BLOCK_DMODEL=D_HEAD, BLOCK_N=BLOCK_N,
    )
    return out
