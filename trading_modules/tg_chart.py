"""
trading_modules/tg_chart.py
Vẽ ảnh biểu đồ nến GỌN (PNG) gửi Telegram cho mã KHÔNG mở tab (tab đang mở thì chụp biểu đồ chính).
Hàm THUẦN: nhận nến + các đường giá, không đụng MT5/Tk. Dùng Figure + FigureCanvasAgg riêng (không pyplot)
nên gọi được từ luồng nền Telegram mà không đụng biểu đồ chính.
"""
import io

BG = "#131722"
GRID = "#2a2e39"
TEXT = "#d1d4dc"
UP = "#26a69a"
DOWN = "#ef5350"


def render_chart_png(df, title: str, lines=None, last_price=None, last_color: str = "#00e5ff",
                     width_px: int = 1280, height_px: int = 620, up: str = UP, down: str = DOWN) -> bytes:
    """df: cột timestamps/open/high/low/close (nến cuối có thể đang chạy). lines: [{price, color, label, ls}].
    last_price: giá hiện tại (đường + nhãn bên phải). Trả PNG bytes. Ném ngoại lệ khi dữ liệu sai (bên gọi tự xử lý)."""
    import numpy as np
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    n = len(df)
    if n == 0:
        raise ValueError("Không có nến để vẽ")
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    lo = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    x = np.arange(n)

    dpi = 100
    fig = Figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi, facecolor=BG)
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0.01, 0.07, 0.90, 0.85])
    ax.set_facecolor(BG)

    col = np.where(c >= o, up, down)
    y_min, y_max = float(lo.min()), float(h.max())
    ax.vlines(x, lo, h, colors=col, linewidth=1.0, zorder=3)
    min_body = max((y_max - y_min) * 0.002, 1e-6)
    ax.bar(x, np.maximum(np.abs(c - o), min_body), bottom=np.minimum(o, c), width=0.7, color=col, linewidth=0, zorder=4)

    ylab = ax.get_yaxis_transform()  # x theo trục (0..1), y theo giá
    all_lines = []
    for ln in lines or []:  # đường trùng giá (vd SL AI = SL lệnh): gộp nhãn, không vẽ đè
        try:
            key = round(float(ln["price"]), 2)
        except (KeyError, TypeError, ValueError):
            continue
        same = next((x for x in all_lines if round(float(x["price"]), 2) == key), None)
        if same is None:
            all_lines.append(dict(ln))
        elif ln.get("label"):
            same["label"] = f"{same['label']} | {ln['label']}" if same.get("label") else ln["label"]
    if last_price is not None:
        all_lines.append({"price": float(last_price), "color": last_color, "label": "", "ls": "-."})
    for ln in all_lines:
        try:
            p = float(ln["price"])
        except (KeyError, TypeError, ValueError):
            continue
        if p <= 0:
            continue
        color = ln.get("color") or TEXT
        y_min, y_max = min(y_min, p), max(y_max, p)
        ax.axhline(p, color=color, linestyle=ln.get("ls", "--"), linewidth=0.9, alpha=0.85, zorder=2)
        if ln.get("label"):
            ax.text(0.005, p, f" {ln['label']} ", transform=ylab, color=color, fontsize=8, va="bottom", ha="left", zorder=6,
                    bbox=dict(boxstyle="square,pad=0.15", facecolor=BG, edgecolor=color, lw=0.7, alpha=0.9))
        ax.text(1.002, p, f" {p:.2f} ", transform=ylab, color=BG, fontsize=8, va="center", ha="left", clip_on=False, zorder=7,
                bbox=dict(boxstyle="square,pad=0.15", facecolor=color, edgecolor=color, lw=0.0))

    pad = max((y_max - y_min) * 0.06, 1e-6)
    ax.set_ylim(y_min - pad, y_max + pad)
    ax.set_xlim(-1, n + 2)
    ax.yaxis.tick_right()
    ax.tick_params(colors=TEXT, labelsize=8, length=0)
    ax.grid(color=GRID, linewidth=0.5, zorder=0)
    for sp in ax.spines.values():
        sp.set_color(GRID)
    try:
        ts = df["timestamps"]
        step = max(n // 8, 1)
        idx = list(range(0, n, step))
        ax.set_xticks(idx)
        ax.set_xticklabels([ts.iloc[i].strftime("%H:%M") for i in idx])
    except Exception:
        ax.set_xticks([])
    fig.text(0.01, 0.955, title, color=TEXT, fontsize=11, fontweight="bold", va="center", ha="left")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG)
    return buf.getvalue()
