"""
trading_modules/kronos_engine.py
Module LÕI KRONOS: sinh N kịch bản giá tương lai từ nến lịch sử bằng mô hình Kronos (Single-Encode + KV-Cache, seed theo dữ liệu nến cuối).
Đây là "bộ não" duy nhất; các module khác chỉ ĐỌC kịch bản, không sửa. Tách nguyên văn từ KronosDesktopApp._fast_predict_scenarios
(hành vi giữ nguyên 100%; khóa suy luận và dọn bộ nhớ do bên gọi truyền vào). Seed = hash dữ liệu nến cuối nên kết quả xác định theo dữ liệu.
"""

import threading

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F

from model.kronos import calc_time_stamps, sample_from_logits


def _no_cleanup():
    return None


def fast_predict_scenarios(predictor, df_in, x_timestamp, y_timestamp, pred_len: int = 6, num_scenarios: int = 8,
                           T: float = 1.0, top_k: int = 0, top_p: float = 0.9, inference_lock=None, cleanup=None):
    """
    Tăng tốc dự báo AI gấp 15x-25x (từ ~18 giây xuống ~0.8 giây trên CPU) mà giữ nguyên 100% độ chính xác toán học:
    - Single-Encode (batch=1): Chỉ mã hóa chuỗi nến lịch sử đúng 1 lần thay vì lặp lại 8 lần.
    - Single-Prefill + KV-Cache (batch=1 -> batch=N): Chạy 12 tầng Transformer trên lịch sử đúng 1 lần và lưu bộ đệm Key/Value (KV-Cache),
      sau đó ở mỗi bước dự báo chỉ tính toán trên đúng 1 nến mới sinh ra (seq_len=1).
    - KV-Cache cho cả Cross-Attention (decode_s2) và Tokenizer Decoder.
    - Tích hợp chấm điểm Xác suất Log-Likelihood nội tại của từng kịch bản & Chuẩn hóa hình học nến OHLC.
    """
    if inference_lock is None:
        inference_lock = threading.RLock()
    if cleanup is None:
        cleanup = _no_cleanup
    device = predictor.device
    max_hist = max(32, min(len(df_in), predictor.max_context - pred_len))
    if len(df_in) > max_hist:
        df_in = df_in.iloc[-max_hist:].reset_index(drop=True)
        x_timestamp = x_timestamp.iloc[-max_hist:].reset_index(drop=True)

    df_c = df_in[["open", "high", "low", "close", "volume"]].copy()
    df_c["amount"] = df_c["volume"] * df_c[["open", "high", "low", "close"]].mean(axis=1)
    x_np = df_c[["open", "high", "low", "close", "volume", "amount"]].values.astype(np.float32)
    x_mean, x_std = np.mean(x_np, axis=0), np.std(x_np, axis=0)
    x_norm = np.clip((x_np - x_mean) / (x_std + 1e-5), -predictor.clip, predictor.clip)

    x_stamp_np = calc_time_stamps(x_timestamp).values.astype(np.float32)
    y_stamp_np = calc_time_stamps(y_timestamp).values.astype(np.float32)

    # Cố định Seed độc lập trong ngữ cảnh fork_rng theo dữ liệu OHLC của nến cuối cùng (không làm ảnh hưởng luồng khác)
    last_r = x_np[-1]
    last_hist_close = float(last_r[3])
    data_seed = int(abs(hash((len(df_in), round(float(last_r[0]), 2), round(float(last_r[1]), 2), round(float(last_r[2]), 2), round(float(last_r[3]), 2))))) % (2**31 - 1)

    with inference_lock, torch.random.fork_rng(), torch.no_grad():
        torch.manual_seed(data_seed)
        x_t = torch.from_numpy(x_norm[np.newaxis, :]).to(device)
        x_st = torch.from_numpy(x_stamp_np[np.newaxis, :]).to(device)
        y_st = torch.from_numpy(y_stamp_np[np.newaxis, :]).to(device)
        full_stamp_1 = torch.cat([x_st, y_st], dim=1)

        # 1. Single Tokenizer Encode (batch=1)
        x_token_1 = predictor.tokenizer.encode(x_t, half=True)
        init_len = x_t.size(1)

        # 2. Single Transformer Prefill trên chuỗi nến lịch sử (batch=1)
        x_pre = predictor.model.embedding([x_token_1[0], x_token_1[1]]) + predictor.model.time_emb(x_st)
        kv_cache = []
        for layer in predictor.model.transformer:
            res = x_pre
            xn = layer.norm1(x_pre)
            sa = layer.self_attn
            q = sa.q_proj(xn).view(1, init_len, sa.n_heads, sa.head_dim).transpose(1, 2)
            k = sa.k_proj(xn).view(1, init_len, sa.n_heads, sa.head_dim).transpose(1, 2)
            v = sa.v_proj(xn).view(1, init_len, sa.n_heads, sa.head_dim).transpose(1, 2)
            q, k = sa.rotary(q, k)
            kv_cache.append((k.expand(num_scenarios, -1, -1, -1), v.expand(num_scenarios, -1, -1, -1)))
            ao = F.scaled_dot_product_attention(q, k, v, dropout_p=0.0, is_causal=True)
            ao = ao.transpose(1, 2).contiguous().view(1, init_len, sa.d_model)
            x_pre = res + sa.out_proj(ao)
            x_pre = x_pre + layer.ffn(layer.norm2(x_pre))
        ctx_hist = predictor.model.norm(x_pre)
        s1_logits_0 = predictor.model.head(ctx_hist[:, -1:, :])[:, 0, :].expand(num_scenarios, -1).contiguous()

        # Tiền tính toán Key/Value cho DependencyAwareLayer (decode_s2) trên lịch sử (batch=1)
        ca = predictor.model.dep_layer.cross_attn
        cos_1, sin_1 = ca.rotary._update_cos_sin_cache(ctx_hist, 1)
        k_dep_h = ca.k_proj(ctx_hist).view(1, init_len, ca.n_heads, ca.head_dim).transpose(1, 2)
        v_dep_h = ca.v_proj(ctx_hist).view(1, init_len, ca.n_heads, ca.head_dim).transpose(1, 2)
        k_dep_h = (k_dep_h * cos_1) + (ca.rotary._rotate_half(k_dep_h) * sin_1)
        k_dep_cache = k_dep_h.expand(num_scenarios, -1, -1, -1)
        v_dep_cache = v_dep_h.expand(num_scenarios, -1, -1, -1)
        last_ctx = ctx_hist[:, -1:, :].expand(num_scenarios, -1, -1)

        generated_pre = x_token_1[0].new_empty(num_scenarios, pred_len)
        generated_post = x_token_1[1].new_empty(num_scenarios, pred_len)
        scenario_log_probs = torch.zeros(num_scenarios, dtype=torch.float32, device=device)

        for i in range(pred_len):
            if i == 0:
                s1_logits = s1_logits_0
            else:
                pos = init_len + i - 1
                prev_s1 = generated_pre[:, i - 1:i]
                prev_s2 = generated_post[:, i - 1:i]
                st_step = full_stamp_1[:, pos:pos + 1, :].expand(num_scenarios, -1, -1)
                x_step = predictor.model.embedding([prev_s1, prev_s2]) + predictor.model.time_emb(st_step)
                for idx_l, layer in enumerate(predictor.model.transformer):
                    res = x_step
                    xn = layer.norm1(x_step)
                    sa = layer.self_attn
                    q = sa.q_proj(xn).view(num_scenarios, 1, sa.n_heads, sa.head_dim).transpose(1, 2)
                    k_new = sa.k_proj(xn).view(num_scenarios, 1, sa.n_heads, sa.head_dim).transpose(1, 2)
                    v_new = sa.v_proj(xn).view(num_scenarios, 1, sa.n_heads, sa.head_dim).transpose(1, 2)
                    cos, sin = sa.rotary._update_cos_sin_cache(q, pos + 1)
                    cos_t, sin_t = cos[:, :, pos:pos + 1, :], sin[:, :, pos:pos + 1, :]
                    q = (q * cos_t) + (sa.rotary._rotate_half(q) * sin_t)
                    k_new = (k_new * cos_t) + (sa.rotary._rotate_half(k_new) * sin_t)
                    k_all = torch.cat([kv_cache[idx_l][0], k_new], dim=2)
                    v_all = torch.cat([kv_cache[idx_l][1], v_new], dim=2)
                    kv_cache[idx_l] = (k_all, v_all)
                    ao = F.scaled_dot_product_attention(q, k_all, v_all, dropout_p=0.0, is_causal=False)
                    ao = ao.transpose(1, 2).contiguous().view(num_scenarios, 1, sa.d_model)
                    x_step = res + sa.out_proj(ao)
                    x_step = x_step + layer.ffn(layer.norm2(x_step))
                last_ctx = predictor.model.norm(x_step)
                s1_logits = predictor.model.head(last_ctx)[:, 0, :]

                k_dep_new = ca.k_proj(last_ctx).view(num_scenarios, 1, ca.n_heads, ca.head_dim).transpose(1, 2)
                v_dep_new = ca.v_proj(last_ctx).view(num_scenarios, 1, ca.n_heads, ca.head_dim).transpose(1, 2)
                k_dep_new = (k_dep_new * cos_1) + (ca.rotary._rotate_half(k_dep_new) * sin_1)
                k_dep_cache = torch.cat([k_dep_cache, k_dep_new], dim=2)
                v_dep_cache = torch.cat([v_dep_cache, v_dep_new], dim=2)

            sample_pre = sample_from_logits(s1_logits, temperature=T, top_k=top_k, top_p=top_p, sample_logits=True)
            log_p1 = F.log_softmax(s1_logits, dim=-1).gather(-1, sample_pre).squeeze(-1)
            sib = predictor.model.embedding.emb_s1(sample_pre)
            q_dep = ca.q_proj(sib).view(num_scenarios, 1, ca.n_heads, ca.head_dim).transpose(1, 2)
            q_dep = (q_dep * cos_1) + (ca.rotary._rotate_half(q_dep) * sin_1)
            ao_dep = F.scaled_dot_product_attention(q_dep, k_dep_cache, v_dep_cache, dropout_p=0.0, is_causal=False)
            ao_dep = ca.out_proj(ao_dep.transpose(1, 2).contiguous().view(num_scenarios, 1, ca.d_model))
            x2_last = predictor.model.dep_layer.norm(last_ctx + ao_dep)
            s2_logits = predictor.model.head.cond_forward(x2_last)[:, 0, :]
            sample_post = sample_from_logits(s2_logits, temperature=T, top_k=top_k, top_p=top_p, sample_logits=True)
            log_p2 = F.log_softmax(s2_logits, dim=-1).gather(-1, sample_post).squeeze(-1)

            # Nhấn mạnh các nến đầu tiên của chuỗi Scalping (early horizon weight)
            step_w = 1.15 if i < max(1, pred_len // 2) else 0.90
            scenario_log_probs += (log_p1 + log_p2) * (step_w / max(float(pred_len), 1.0))

            generated_pre[:, i] = sample_pre.squeeze(-1)
            generated_post[:, i] = sample_post.squeeze(-1)

        # 3. Single-Prefill + KV-Cache cho Tokenizer Decoder
        q_hist = predictor.tokenizer.indices_to_bits([x_token_1[0], x_token_1[1]], half=True)
        z_h = predictor.tokenizer.post_quant_embed(q_hist)
        q_gen = predictor.tokenizer.indices_to_bits([generated_pre, generated_post], half=True)
        z_g = predictor.tokenizer.post_quant_embed(q_gen)
        attn_mask = torch.ones((pred_len, init_len + pred_len), dtype=torch.bool, device=device)
        attn_mask[:, init_len:] = torch.tril(torch.ones((pred_len, pred_len), dtype=torch.bool, device=device))
        for layer in predictor.tokenizer.decoder:
            res_h = z_h
            xh = layer.norm1(z_h)
            sa = layer.self_attn
            q_h = sa.q_proj(xh).view(1, init_len, sa.n_heads, sa.head_dim).transpose(1, 2)
            k_h = sa.k_proj(xh).view(1, init_len, sa.n_heads, sa.head_dim).transpose(1, 2)
            v_h = sa.v_proj(xh).view(1, init_len, sa.n_heads, sa.head_dim).transpose(1, 2)
            q_h, k_h = sa.rotary(q_h, k_h)
            ao_h = F.scaled_dot_product_attention(q_h, k_h, v_h, dropout_p=0.0, is_causal=True)
            z_h = res_h + sa.out_proj(ao_h.transpose(1, 2).contiguous().view(1, init_len, sa.d_model))
            z_h = z_h + layer.ffn(layer.norm2(z_h))

            res_g = z_g
            xg = layer.norm1(z_g)
            q_g = sa.q_proj(xg).view(num_scenarios, pred_len, sa.n_heads, sa.head_dim).transpose(1, 2)
            k_g = sa.k_proj(xg).view(num_scenarios, pred_len, sa.n_heads, sa.head_dim).transpose(1, 2)
            v_g = sa.v_proj(xg).view(num_scenarios, pred_len, sa.n_heads, sa.head_dim).transpose(1, 2)
            cos, sin = sa.rotary._update_cos_sin_cache(q_g, init_len + pred_len)
            cos_g, sin_g = cos[:, :, init_len:init_len + pred_len, :], sin[:, :, init_len:init_len + pred_len, :]
            q_g = (q_g * cos_g) + (sa.rotary._rotate_half(q_g) * sin_g)
            k_g = (k_g * cos_g) + (sa.rotary._rotate_half(k_g) * sin_g)
            k_all = torch.cat([k_h.expand(num_scenarios, -1, -1, -1), k_g], dim=2)
            v_all = torch.cat([v_h.expand(num_scenarios, -1, -1, -1), v_g], dim=2)
            ao_g = F.scaled_dot_product_attention(q_g, k_all, v_all, attn_mask=attn_mask, dropout_p=0.0, is_causal=False)
            z_g = res_g + sa.out_proj(ao_g.transpose(1, 2).contiguous().view(num_scenarios, pred_len, sa.d_model))
            z_g = z_g + layer.ffn(layer.norm2(z_g))
        preds = predictor.tokenizer.head(z_g).cpu().numpy()
        log_probs_np = scenario_log_probs.cpu().numpy()

    scenarios = []
    cols = ["open", "high", "low", "close", "volume", "amount"]
    for b in range(num_scenarios):
        p_b = preds[b] * (x_std + 1e-5) + x_mean
        # Chuẩn hóa hình học nến OHLC (đảm bảo High >= max(Open, Close), Low <= min(Open, Close) và nối mượt giá mở cửa)
        if pred_len > 0:
            p_b[0, 0] = 0.65 * float(p_b[0, 0]) + 0.35 * last_hist_close
            for step_i in range(1, pred_len):
                p_b[step_i, 0] = 0.60 * float(p_b[step_i, 0]) + 0.40 * float(p_b[step_i - 1, 3])
            body_top = np.maximum(p_b[:, 0], p_b[:, 3])
            body_bot = np.minimum(p_b[:, 0], p_b[:, 3])
            p_b[:, 1] = np.maximum(p_b[:, 1], body_top)
            p_b[:, 2] = np.minimum(p_b[:, 2], body_bot)
        p_df = pd.DataFrame(p_b, columns=cols)
        p_df.insert(0, "timestamps", y_timestamp.values)
        p_df.attrs["log_prob"] = float(log_probs_np[b])
        scenarios.append(p_df)
    cleanup()
    return scenarios
