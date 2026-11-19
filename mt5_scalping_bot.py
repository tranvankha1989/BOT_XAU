import os
import sys
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

os.chdir(os.path.dirname(os.path.abspath(__file__)))

import json
import re
import math
import time
import types
import queue
import shutil
import subprocess
import ctypes
import traceback
import threading
import calendar
import urllib.request
import urllib.error
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timedelta, timezone, date
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import numpy as np
import pandas as pd


def _load_packed_trading_modules():
    """Chế độ đóng gói (launcher + core_engine.bin): các module trading_modules.* nằm trong container nhị phân của launcher.
    Nạp chúng TRƯỚC khi import (tương thích cả launcher cũ). Chạy từ mã nguồn thì không có container -> bỏ qua."""
    try:
        raw = getattr(sys.modules.get("__main__"), "_CONTAINER_RAW_DICT", None)
        if not raw:
            return
        import marshal
        names = sorted((k for k in raw if k == "trading_modules" or k.startswith("trading_modules.")),
                       key=lambda k: (k != "trading_modules", k != "trading_modules.indicators", k))
        for k in names:
            m = sys.modules.get(k)
            if m is not None and not getattr(m, "_packed_loaded", False):
                exec(marshal.loads(raw[k]), m.__dict__)
                m._packed_loaded = True
    except Exception:
        pass


_load_packed_trading_modules()
from trading_modules.structure import analyze_structure_context, nearest_swing_dist, heikin_ashi_colors
from trading_modules.price_action import evaluate_price_action
from trading_modules.swing_structure import analyze_swing_structure
import gc
import torch
import torch.nn.functional as F

# Cấu hình giới hạn luồng PyTorch CPU: Giữ CPU hoạt động mát mẻ ở mức an toàn (tối đa 4 luồng hoặc 50% CPU),
# chống sốc tải AVX-512/AVX2 gây sụt áp nguồn (Kernel-Power Event ID 41) hoặc quá nhiệt bo mạch chủ.
try:
    _cpu_cnt = os.cpu_count() or 4
    _safe_threads = min(4, max(1, _cpu_cnt // 2))
    torch.set_num_threads(_safe_threads)
    if hasattr(torch, "set_num_interop_threads"):
        try:
            torch.set_num_interop_threads(min(2, max(1, _safe_threads // 2)))
        except Exception:
            pass
except Exception:
    pass

AI_THREAD_CHOICES = ["Auto", "2", "4", "6", "8"]


def resolve_ai_threads(value, cpu_count=None) -> tuple:
    """Số luồng PyTorch cho phân tích AI theo ô 'Số luồng AI'. Không bao giờ vượt mức an toàn min(4, CPU logic // 2)
    (chống Kernel-Power 41). Trả (số luồng, bị hạ về mức an toàn hay không). 'Auto'/rỗng/sai -> mức an toàn."""
    cpu = cpu_count or os.cpu_count() or 4
    cap = min(4, max(1, cpu // 2))
    try:
        n = int(str(value).strip())
    except (ValueError, TypeError):
        return cap, False
    if n < 1:
        return cap, False
    if n > cap:
        return cap, True
    return n, False


import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.patches import Rectangle
from matplotlib.collections import LineCollection, PolyCollection

from model import Kronos, KronosTokenizer, KronosPredictor
from model.kronos import calc_time_stamps, sample_from_logits

# Thử import MetaTrader5
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

# Thử import gradio_client
try:
    from gradio_client import Client
    HF_CLIENT_AVAILABLE = True
except ImportError:
    HF_CLIENT_AVAILABLE = False

# Múi giờ Việt Nam (UTC+7 / GMT+7) cố định để tính PnL và reset giới hạn ngày (không phụ thuộc giờ OS máy hay giờ Server MT5)
VN_TZ = timezone(timedelta(hours=7))

# Đường dẫn lưu cấu hình người dùng, Nhật ký (Logs) & Icon trong AppData (không tạo file phụ trong thư mục dự án)
SETTINGS_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Kronos_MT5_Bot")
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "user_settings.json")
DEFAULT_MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"  # đường dẫn cài đặt MT5 mặc định
INSTANCE_LOCK_FILE = os.path.join(SETTINGS_DIR, "app_instance.json")
LOG_DIR = os.path.join(SETTINGS_DIR, "logs")
ERROR_LOG_FILE = os.path.join(LOG_DIR, "app_error.log")
ICON_ICO_FILE = os.path.join(SETTINGS_DIR, "khatran_icon.ico")
ICON_PNG_FILE = os.path.join(SETTINGS_DIR, "khatran_icon.png")
_SINGLETON_MUTEX_HANDLE = None

try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("KhaTran.Kronos.ScalpingBot.M5.v2")
except Exception:
    pass

MODEL_CONFIGS = {
    "AI-Gold-Exness (Chuyên sâu Exness)": {
        "model": "Kronos-Gold-Exness",
        "tokenizer": "Kronos-Tokenizer-Gold-Exness",
        "fallback_model": "NeoQuasar/Kronos-base",
        "fallback_tokenizer": "NeoQuasar/Kronos-Tokenizer-base",
        "max_context": 512,
        "is_custom": True,
    },
    "AI-base (102.3M - Mạnh nhất)": {
        "model": "NeoQuasar/Kronos-base",
        "tokenizer": "NeoQuasar/Kronos-Tokenizer-base",
        "max_context": 512,
        "is_custom": False,
    },
    "AI-small (24.7M - Nhanh)": {
        "model": "NeoQuasar/Kronos-small",
        "tokenizer": "NeoQuasar/Kronos-Tokenizer-base",
        "max_context": 512,
        "is_custom": False,
    },
    "AI-mini (4.1M - Siêu nhẹ)": {
        "model": "NeoQuasar/Kronos-mini",
        "tokenizer": "NeoQuasar/Kronos-Tokenizer-2k",
        "max_context": 2048,
        "is_custom": False,
    }
}

BG_DARK = "#10141d"
PANEL_BG = "#19202c"
SECTION_HEADER_BG = "#232d3f"
TEXT_MAIN = "#eaecef"
TEXT_MUTED = "#8b98a9"
GOLD_ACCENT = "#f0b90b"
GREEN_UP = "#0ecb81"
RED_DOWN = "#f6465d"
CYAN_LIVE = "#00e5ff"

# =============================================================================
# TỪ ĐIỂN HƯỚNG DẪN CHI TIẾT CÁC THÔNG SỐ CÀI ĐẶT (PARAM HELP GUIDE)
# Hiển thị khi người dùng bấm vào biểu tượng chấm hỏi tròn [?] cạnh mỗi thông số
# =============================================================================
PARAM_HELP_GUIDE = {
    # ─── MỤC 1: CÀI ĐẶT CƠ BẢN ───
    "symbol": {
        "title": "Mã giao dịch MT5 (Symbol)",
        "what_is_it": "Tên cặp tiền hoặc hàng hóa giao dịch được khai báo trên sàn MT5 (ví dụ: XAUUSD, XAUUSDm, BTCUSD, EURUSD, GBPUSD...). Ký tự đuôi như 'm', 'c', 'pro' phụ thuộc vào loại tài khoản và sàn môi giới (Exness, ICMarkets, XM...).",
        "how_to_set": "- Kiểm tra chính xác tên mã hiển thị trong cửa sổ Market Watch trên MT5 của bạn.\n- Tài khoản Standard Exness: Nhập 'XAUUSDm'. Tài khoản Pro/Zero: Nhập 'XAUUSD'.\n- Khi đổi mã, bot sẽ tự động nhận diện giá Bid/Ask, cấu trúc Spread và nạp cấu hình riêng của mã đó."
    },
    "analysis_tf": {
        "title": "Khung phân tích AI (Analysis Timeframe)",
        "what_is_it": "Khung thời gian nến mà mô hình Deep Learning phân tích ngữ cảnh lớn để dự báo xu hướng đa kịch bản (Ensemble) trong các bước tiếp theo.",
        "how_to_set": "- M15 (Khuyên dùng chuẩn): Cho góc nhìn đa chu kỳ 90 phút cực kỳ tin cậy, khử nhiễu tốt cho vàng XAUUSD.\n- H1: Dành cho giao dịch theo sóng lớn trong ngày (Day Trading).\n- M5: Dành cho thị trường biến động nhanh hoặc đánh siêu lướt sóng."
    },
    "tf": {
        "title": "Khung vào lệnh Scalp (Scalp Timeframe)",
        "what_is_it": "Khung thời gian nến thực thi giao dịch. Bot sẽ quét các điều kiện Entry, tính toán biên độ nến ATR, độ giãn Spread và kích hoạt lệnh tại khung này.",
        "how_to_set": "- M5 (Khuyên dùng chuẩn): Điểm cân bằng hoàn hảo cho Scalping vàng, nến đủ ổn định và tỷ lệ tín hiệu giả thấp.\n- M1: Chỉ nên dùng khi tài khoản có Spread cực thấp (< 0.20$) và muốn nhịp lệnh nhanh.\n- Khung vào lệnh nên nhỏ hơn hoặc bằng Khung phân tích AI."
    },
    "lot": {
        "title": "Khối lượng vào lệnh (Lot Size)",
        "what_is_it": "Số lot giao dịch cố định được mở cho mỗi lệnh giao dịch mới (hoặc mỗi lệnh nhồi).",
        "how_to_set": "- Vốn 200$ - 500$: Cài 0.01 lot.\n- Vốn 1,000$: Cài 0.02 - 0.03 lot.\n- Vốn 3,000$ - 5,000$: Cài 0.05 - 0.10 lot.\n- Nguyên tắc vàng: Giữ tổng rủi ro khi dính SL của tất cả các lệnh không vượt quá 1% - 2% vốn tài khoản."
    },
    "max_pos": {
        "title": "Số lệnh chưa BE tối đa (Max At-Risk Positions)",
        "what_is_it": "Giới hạn số lượng vị thế ĐANG CHỊU RỦI RO (chưa dời SL về hòa vốn BE) mở đồng thời tối đa của cặp tiền này.\n- QUY TẮC BẢO VỆ: Các lệnh ĐÃ DỜI BE (Risk-Free) sẽ tự động được LOẠI BỎ KHỎI BỘ ĐẾM, cho phép bot tiếp tục nhồi thêm lệnh pullback mới mà vẫn kiểm soát rủi ro tài khoản an toàn tuyệt đối!",
        "how_to_set": "- Đánh an toàn: Đặt 1 đến 2 lệnh chưa BE.\n- Đánh nhồi sóng dài: Đặt 3 đến 4 lệnh (khi lệnh cũ dời BE, bot sẽ tự động giải phóng hạn mức để mở thêm lệnh mới theo sóng).\n- Cảnh báo: Không nên đặt quá nhiều lệnh chưa BE để tránh rủi ro ký quỹ khi thị trường biến động giật mạnh."
    },
    "mt5_path": {
        "title": "Đường dẫn MT5 (terminal64.exe)",
        "what_is_it": "Vị trí chương trình MetaTrader 5 mà bot sẽ mở và kết nối. Bot dùng tài khoản đang đăng nhập trên MT5 này (DEMO hay THẬT tùy bạn đăng nhập).",
        "how_to_set": "- Mặc định: C:\\Program Files\\MetaTrader 5\\terminal64.exe\n- Nếu cài MT5 ở thư mục khác (vd. bản MT5 riêng của Exness): bấm chuột phải biểu tượng MT5 > Open file location, rồi dán đường dẫn đầy đủ tới terminal64.exe vào đây và bấm Kết nối MT5.\n- Đường dẫn sai/không tồn tại: bot tự tìm MT5 đang chạy trên máy."
    },
    "ai_threads": {
        "title": "Số luồng AI (CPU)",
        "what_is_it": "Số luồng CPU dùng để chạy phân tích AI. Chỉ ảnh hưởng TỐC ĐỘ, không làm AI chính xác hơn. Hai máy đặt CÙNG số luồng sẽ cho kết quả phân tích AI giống nhau (khác số luồng thì sai số làm tròn khác, đôi khi đổi kịch bản AI). Áp dụng chung cho cả XAUUSD và BTCUSD.",
        "how_to_set": "- Auto (mặc định): bot tự chọn mức an toàn của máy (tối đa 4 luồng và không quá 1/2 số luồng CPU).\n- Muốn 2 máy ra cùng kết quả: đặt cùng một số trên cả 2 máy, chọn số mà máy YẾU hơn chạy được (vd. 2).\n- Chọn vượt mức an toàn của máy (vd. 6, 8 trên máy 4 luồng): bot tự hạ về mức an toàn và ghi log, để chống quá tải/sập nguồn."
    },
    "magic_number": {
        "title": "Mã định danh lệnh (Magic Number)",
        "what_is_it": "Mã số định danh riêng biệt gắn vào mỗi lệnh của bot trên MT5, giúp bot nhận diện và chỉ quản lý đúng các lệnh của mình, hoàn toàn không can thiệp vào lệnh tay của bạn hoặc của bot khác.",
        "how_to_set": "- Mỗi cặp tiền hoặc mỗi bot nên dùng 1 Magic Number khác nhau (VD: Tab XAUUSD dùng 20260501, Tab BTCUSD dùng 20260502).\n- Lưu ý: Không đổi Magic Number khi đang có lệnh mở, nếu đổi bot sẽ mất quyền quản lý lệnh cũ."
    },

    # ─── MỤC 2: BỘ LỌC ENTRY & NHỒI LỆNH ───
    "scenarios": {
        "title": "Số kịch bản AI (Ensemble Scenarios)",
        "what_is_it": "Số lượng kịch bản đường đi giá ngẫu nhiên mà mô hình AI mô phỏng (Monte Carlo Ensemble) để tính toán xác suất tăng/giảm trong tương lai.",
        "how_to_set": "- Khuyên dùng chuẩn: 16 đến 32 kịch bản (Cân bằng lý tưởng giữa tốc độ suy luận và độ chính xác).\n- Nếu máy có GPU mạnh (GTX/RTX) hoặc dùng Cloud: Có thể đặt 32 - 64 kịch bản để dự báo mượt và phân tán chuẩn xác hơn.\n- Lưu ý: Không nên đặt < 8 vì số kịch bản quá ít dễ gây sai lệch xác suất ngẫu nhiên."
    },
    "consensus": {
        "title": "Độ đồng thuận tối thiểu (Consensus %)",
        "what_is_it": "Tỷ lệ phần trăm tối thiểu các kịch bản AI phải cùng chỉ về một hướng (đồng thuận BUY hoặc đồng thuận SELL) thì mới thỏa điều kiện vào lệnh.",
        "how_to_set": "- Chuẩn Scalping hiệu quả: 70% - 75% (Cho tỷ lệ vào lệnh hợp lý và Winrate cao).\n- Đánh chọn lọc / Rủi ro thấp: 80% - 85% (Ít lệnh hơn nhưng mỗi lệnh có độ tin cậy rất cao).\n- Tránh đặt < 65% vì tín hiệu bị nhiễu do hai phe Mua và Bán đang giằng co tương đương."
    },
    "max_dispersion": {
        "title": "Độ phân tán tối đa (Max Dispersion x ATR)",
        "what_is_it": "Đo lường độ lệch / bất đồng giữa các kịch bản dự báo của AI so với biên độ nến ATR. Nếu các đường dự báo tản ra quá xa nhau (thị trường đang biến động hỗn loạn hoặc sắp có tin mạnh), bot sẽ từ chối vào lệnh.",
        "how_to_set": "- Khuyên dùng: 2.0 đến 2.5 (x ATR).\n- Nếu đặt 0: Tắt bộ lọc phân tán (chấp nhận vào lệnh kể cả khi AI phân vân).\n- Nên duy trì ở mức 2.5 để tự động né các pha quét râu 2 đầu lúc thị trường nhiễu loạn."
    },
    "min_move": {
        "title": "Biên độ dự báo tối thiểu (Min Expected Move)",
        "what_is_it": "Khoảng cách giá tối thiểu (USD) mà AI dự báo giá sẽ di chuyển tới đỉnh/đáy tiếp theo. Nếu AI dự báo giá chỉ nhích một đoạn quá ngắn, bot sẽ bỏ qua vì lợi nhuận không đủ bù phí giao dịch.",
        "how_to_set": "- Vàng XAUUSD: Cài đặt từ 1.2$ đến 1.5$ (tương đương 12 - 15 pip).\n- Không nên để < 0.8$ vì biên độ quá hẹp dễ bị nuốt bởi Spread và trượt giá."
    },
    "expected_move_k": {
        "title": "Hệ số k biên độ AI bù chi phí (k x (Spread + Slippage))",
        "what_is_it": "Hệ số an toàn nhân với chi phí giao dịch ước lượng (Spread + Slippage). Bot yêu cầu biên độ dự báo của AI phải lớn hơn hoặc bằng max(min_move, k * (spread + slippage_est)) để đảm bảo lợi nhuận kỳ vọng luôn vượt trội chi phí sàn.",
        "how_to_set": "- Mặc định: 3.0 (Biên độ kỳ vọng phải tối thiểu gấp 3 lần chi phí spread + trượt giá).\n- Đánh thận trọng / Giờ biến động: 3.5 - 4.0.\n- Đánh tài khoản siêu rẻ (Zero/Raw Spread): 2.0 - 2.5."
    },
    "max_spread": {
        "title": "Spread tối đa cho phép (Max Spread)",
        "what_is_it": "Mức chênh lệch tối đa giữa giá Mua (Ask) và giá Bán (Bid) tính bằng USD. Nếu Spread thực tế của sàn vượt quá giá trị này, bot sẽ chặn không cho mở lệnh.",
        "how_to_set": "- Vàng tài khoản Pro/Zero: 0.25$ - 0.35$ (2.5 - 3.5 pip).\n- Vàng tài khoản Standard: 0.35$ - 0.45$.\n- Bảo vệ cực kỳ quan trọng: Giúp tránh vào lệnh lúc nửa đêm giao phiên (4h-6h sáng) hoặc lúc tin Non-Farm / CPI ra làm giãn Spread."
    },
    "sl_atr": {
        "title": "Khoảng cách SL AI (x ATR14)",
        "what_is_it": "Hệ số nhân với chỉ số ATR14 để tính điểm Cắt Lỗ (Stop Loss) tự động co giãn theo độ biến động thực tế của thị trường.",
        "how_to_set": "- Chuẩn Scalping: 1.5 x ATR (Cho khoảng thở tự nhiên, chống bị quét râu nến ngẫu nhiên).\n- Đánh ngắn ép rủi ro: 1.0 - 1.2 x ATR.\n- Phiên Mỹ biến động mạnh: 1.8 - 2.0 x ATR."
    },
    "sl_buffer": {
        "title": "Giá an toàn cộng thêm vào SL (+ Buffer)",
        "what_is_it": "Khoảng giá cộng dồn thêm ra ngoài vùng cản/đáy dự báo của AI để làm lớp đệm an toàn chống các pha quét râu nến (Spike/Wick). Khoảng này KHÔNG nằm trong ô 'SL tối đa': SL thật xa nhất ≈ SL tối đa + Giá an toàn (ví dụ 3.0 + 2.5 = 5.5 giá).",
        "how_to_set": "- Vàng XAUUSD: Khuyên dùng 0.3$ đến 0.5$ (3 - 5 pip).\n- Giúp lệnh không bị dính SL oan rồi giá mới quay đầu chạy đúng xu hướng."
    },
    "max_sl_price": {
        "title": "SL tối đa cố định (Max SL Price)",
        "what_is_it": "Khoảng giá tối đa từ giá vào lệnh tới điểm SL do AI chọn (đỉnh/đáy), CHƯA tính ô 'Giá an toàn cộng thêm vào SL'. Nếu giá hiện tại cách điểm SL của AI xa hơn mức này, bot KHÔNG ép SL lại mà canh giá hồi về gần điểm SL rồi mới vào lệnh. SL thật đặt trên MT5 = điểm SL của AI cộng thêm Giá an toàn, nên SL thật xa nhất ≈ SL tối đa + Giá an toàn (ví dụ 3.0 + 2.5 = 5.5 giá; lot 0.02 ≈ 11$ mỗi lệnh).",
        "how_to_set": "- Tính mức lỗ chấp nhận được theo tổng: SL tối đa + Giá an toàn (ví dụ muốn SL thật không quá 4.0 giá: đặt 3.0 + 1.0 hoặc 2.0 + 2.0).\n- Đặt nhỏ: SL thật gần hơn nhưng bot phải chờ giá về sát điểm SL nên ít lệnh hơn.\n- Khi TP không đủ 2R, bot có thể tự giảm mức này còn một nửa cho lệnh đó.\n- Nếu nhập 0: Bot để SL tự do theo AI (không giới hạn)."
    },
    "rr": {
        "title": "Tỷ lệ Lợi nhuận / Rủi ro (R:R Ratio)",
        "what_is_it": "Tỷ lệ giữa khoảng cách Chốt Lời (TP) so với Cắt Lỗ (SL). Ví dụ R:R = 2.0 nghĩa là nếu SL cách 3$ thì TP sẽ tự động được đặt cách 6$.",
        "how_to_set": "- Khuyên dùng: 1.5 đến 2.0 (Tỷ lệ vàng giúp tài khoản tăng trưởng bền vững kể cả khi Winrate chỉ cần 45%-55%).\n- Nếu nhập 0: Bot sẽ tự động lấy TP theo mục tiêu giá dự báo của mô hình AI."
    },
    "entry_mode": {
        "title": "Chế độ nhồi lệnh (Entry Mode)",
        "what_is_it": "Quy tắc chiến thuật khi mở thêm lệnh thứ 2, thứ 3:\n- 'Chỉ nhồi khi đã khóa lãi lệnh trước (Risk-Free)': Chỉ mở lệnh mới khi các lệnh cũ đã được dời SL về hòa vốn hoặc có lãi.\n- 'Nhồi giá tốt hơn (DCA)': Mở thêm lệnh khi giá đi ngược nhưng có giá tốt hơn để hạ giá vốn bình quân. Chỉ so với lệnh cùng chiều CHƯA dời BE (lệnh đã BE không chặn lệnh mới).\n- 'Tự do theo AI': Mở thêm bất cứ khi nào AI báo tín hiệu mới và thỏa khoảng cách tối thiểu.",
        "how_to_set": "- An toàn nhất: Chọn 'Risk-Free' (Không bao giờ gia tăng rủi ro khi lệnh cũ chưa an toàn).\n- Tận dụng sóng: Chọn 'Nhồi giá tốt hơn (DCA)' kết hợp giới hạn Số lệnh tối đa = 2 hoặc 3."
    },
    "allow_opposite_entry": {
        "title": "Vào lệnh ngược chiều khi lệnh cũ đang âm",
        "what_is_it": "Khi TẮT (như cũ): đang có lệnh BUY âm (hoặc đang chờ hồi về Entry) thì bot không mở lệnh SELL mới dù AI báo SELL đủ điều kiện (và ngược lại). Khi BẬT: AI báo chiều mới đủ điều kiện thì vẫn vào, lệnh cũ vẫn được quản lý riêng (chờ về Entry / SL / hết giờ).",
        "how_to_set": "- TẮT (Mặc định): giữ luật cũ.\n- BẬT: không bỏ lỡ tín hiệu đảo chiều; đổi lại có lúc giữ cả BUY và SELL cùng lúc (vẫn tính trong Số lệnh tối đa). Tài khoản phải cho phép lệnh 2 chiều (Hedging)."
    },
    "min_entry_spacing": {
        "title": "Khoảng cách nhồi tối thiểu (Min Spacing)",
        "what_is_it": "Khoảng cách giá tối thiểu giữa lệnh mới và lệnh đã có gần nhất, chống việc bot vào nhiều lệnh liên tiếp sát sàn sạt nhau tại cùng một mức giá.",
        "how_to_set": "- Vàng XAUUSD: Đặt 1.0$ đến 1.5$ (10 - 15 pip).\n- Giúp các vị thế được rải đều qua các vùng cản kháng cự/hỗ trợ khác nhau."
    },
    "one_order_per_candle": {
        "title": "Chỉ mở tối đa 1 lệnh mỗi cây nến",
        "what_is_it": "Chặn không cho bot mở quá 1 lệnh trong cùng một cây nến giao dịch (ví dụ nến M5). Ngăn ngừa việc thị trường giật nảy giá làm kích hoạt nhiều lệnh liên tục.",
        "how_to_set": "- LUÔN TÍCH CHỌN (BẬT). Đây là cơ chế bảo vệ cốt lõi chống spam lệnh khi nến biến động mạnh."
    },
    "trend_filter": {
        "title": "Lọc Cấu Trúc Thị Trường BOS/CHoCH (xác nhận 2 nến)",
        "what_is_it": "Loại bỏ hoàn toàn các đường MA trễ nải (như EMA34/89). Thay vào đó, bot phân tích trực tiếp Cấu Trúc Đỉnh/Đáy thực tế (Market Structure - Swing Highs/Lows), Phá vỡ cấu trúc (BOS) và Đảo chiều cấu trúc (CHoCH) theo chuẩn Price Action/SMC trên M15 và M5/M1. Khi thị trường đang phá đáy rơi tự do (Bearish BOS), bot sẽ CHẶN 100% tín hiệu BUY cản tàu. Ngược lại khi thị trường đang phá đỉnh tăng mạnh (Bullish BOS), bot sẽ CHẶN 100% tín hiệu SELL cản tàu. Khi có CHoCH đảo chiều hoặc thị trường tích lũy, bot lập tức unblock để vào lệnh không trễ 1 nhịp nến nào. Khi đang canh vùng, bot xét LẠI cấu trúc ngay trước khi khớp lệnh: nếu cấu trúc đã quay ngược chiều thì chưa vào lệnh (vẫn giữ canh vùng).",
        "how_to_set": "- LUÔN BẬT (Khuyên dùng chuẩn): Phản ứng tức thì theo giá thực (Zero Indicator Lag, không bị trễ nến như MA), bảo vệ tài khoản tối đa."
    },
    "confirm_entry_pa": {
        "title": "Xác nhận lực nến Price Action (Chống bắt dao rơi)",
        "what_is_it": "Khi giá chạm vào Vùng Canh Giá của AI, bot không khớp lệnh ngay mù quáng mà sẽ quan sát nến Scalping (M1/M5). Nếu nến đang cắm đầu rơi tự do (thân nến đỏ đặc Marubozu), bot sẽ CHỜ và CHỈ khớp lệnh khi xuất hiện tín hiệu lực cầu đỡ giá (rút râu dưới, nến xanh đảo chiều, hoặc bật nảy từ đáy).",
        "how_to_set": "- LUÔN BẬT (Mặc định): Loại bỏ hoàn toàn tình trạng 'bắt dao rơi' ngay giữa cây nến đang giảm dốc."
    },
    "pa_fail_open": {
        "title": "PA Fail-Open (Chấp nhận vào lệnh khi thiếu dữ liệu/lỗi)",
        "what_is_it": "Chính sách an toàn khi kiểm tra Price Action. Khi tắt (False - Mặc định), nếu thiếu nến, không lấy được tick hoặc có lỗi ngoại lệ, bot sẽ từ chối vào lệnh để phòng ngừa rủi ro. Khi bật (True), bot sẽ bỏ qua lỗi và cho phép vào lệnh.",
        "how_to_set": "- TẮT (False - Khuyên dùng): Chế độ an toàn Fail-Safe, bảo vệ tài khoản khi đường truyền mạng hoặc MT5 không ổn định.\n- BẬT (True): Chỉ dùng khi mạng gián đoạn nhưng vẫn muốn bot tận dụng tối đa tín hiệu."
    },
    "pa_min_confirmations": {
        "title": "Số điều kiện Price Action tối thiểu (1-3)",
        "what_is_it": "Số lượng điều kiện nến PA cần thỏa mãn đồng thời trong 3 tín hiệu:\n1) Rút râu nến (Wick rejection).\n2) Nến đảo chiều (Bullish/Bearish turnaround candle).\n3) Giá bật nảy từ đỉnh/đáy (Tick bounce).\n- Đặt 1: Chỉ cần 1 trong 3 điều kiện (logic OR cũ).\n- Đặt 2: Cần ít nhất 2 điều kiện kết hợp (chắc chắn hơn).\n- Đặt 3: Cần cả 3 điều kiện đồng thời (siêu chọn lọc).",
        "how_to_set": "- 1 (Mặc định): Giữ nguyên độ nhạy cao của bản chuẩn.\n- 2: Khuyên dùng cho giao dịch đảo chiều để lọc nhiễu tốt hơn.\n- 3: Rất ít lệnh, chỉ vào khi nến có phản ứng cực kỳ mạnh."
    },
    "structure_conflict_mode": {
        "title": "Xử lý xung đột Cấu Trúc Đa Khung (M5 vs M15)",
        "what_is_it": "Khi khung Scalping (M5) và Khung Phân Tích (M15) cho cấu trúc đối lập nhau (1 khung Tăng, 1 khung Giảm).",
        "how_to_set": "- Trung lập (không chặn) (Mặc định): Trả về Trung Lập. LƯU Ý: khi 2 khung ngược chiều, bộ lọc cấu trúc KHÔNG chặn cả BUY lẫn SELL (lệnh vẫn được vào).\n- Theo khung vào lệnh: Ưu tiên khung M5 (chặn theo cấu trúc M5).\n- Theo khung AI: Ưu tiên khung M15 (chặn theo cấu trúc M15). Chặt hơn Trung lập khi thị trường lệch pha giữa 2 khung."
    },
    "debug_trace": {
        "title": "Ghi nhật ký debug theo nến (Debug Trace)",
        "what_is_it": "Khi bật, bot ghi thêm file logs/trace/trace_YYYY-MM-DD.jsonl: số liệu AI và quyết định của từng nến phân tích, lý do từng cổng canh vùng chặn lệnh, và trạng thái lệnh đang mở. Dùng để tra lại vì sao bot vào/không vào/đóng lệnh. KHÔNG đổi luật giao dịch.",
        "how_to_set": "- TẮT (Mặc định): chạy bình thường.\n- BẬT: khi cần chẩn đoán một ngày giao dịch. Tốn khoảng 1 MB mỗi ngày."
    },
    "recovery_close": {
        "title": "Gỡ vốn (Recovery Close)",
        "what_is_it": "Khi tổng lãi/lỗ ĐÃ CHỐT trong ngày đang âm, bot đóng 1 lệnh đang lãi (chưa dời BE) có lãi >= phần lỗ cần gỡ, để gỡ vốn trước khi giá quay lại hòa vốn. Mỗi lần đóng cách nhau tối thiểu 20 giây.",
        "how_to_set": "- TẮT (Mặc định).\n- BẬT: ưu tiên gỡ vốn trong ngày; đổi lại có thể cắt sớm lệnh lẽ ra chạy tới TP. Mới kiểm thử phát lại 2 ngày, chưa đủ bằng chứng."
    },
    "stack_half_be": {
        "title": "Nhồi lệnh: thoát nửa số lệnh giá xấu ở BE+",
        "what_is_it": "Khi số lệnh CÙNG CHIỀU chưa dời BE tăng lên n >= 2, bot chọn n/2 lệnh (làm tròn xuống) có giá vào XẤU NHẤT (BUY: giá cao nhất, SELL: giá thấp nhất) và cho các lệnh đó thoát ở hòa vốn: dời TP về Entry + 'Giá BE+', giá hồi về là đóng. Các lệnh giá đẹp giữ nguyên SL/TP để chạy tiếp.\n- 2 lệnh: thoát 1 lệnh xấu, giữ 1.\n- 3 lệnh: thoát lệnh xấu nhất, giữ 2.\n- 4 lệnh: thoát 2, giữ 2 (6 lệnh: thoát 3, giữ 3).\nChỉ chọn lại khi số lệnh TĂNG; lệnh xấu đóng xong không kéo theo lệnh khác.",
        "how_to_set": "- TẮT (Mặc định).\n- BẬT: khi dùng nhồi lệnh (Số lệnh tối đa >= 2), nhất là chế độ 'Nhồi giá tốt hơn (DCA)'. Đổi lại: khi giá chạy thẳng tới TP, các lệnh bị chọn chỉ lãi khoảng BE+ thay vì đủ TP. Chưa kiểm chứng bằng phát lại nhiều ngày."
    },
    "structure_direction": {
        "title": "Hướng vào lệnh theo cấu trúc đỉnh/đáy (Thử nghiệm)",
        "what_is_it": "Khi bật, hướng vào lệnh do cấu trúc đỉnh/đáy xoay M5 quyết định: sóng giảm thì BÁN khi giá hồi về kháng cự và bị từ chối, chỉ MUA khi giá phá đỉnh hồi (đổi cấu trúc); sóng tăng làm ngược lại; đi ngang thì không vào. AI Kronos vẫn phải xác nhận biên độ dự báo.",
        "how_to_set": "- TẮT (Mặc định, khuyên dùng).\n- Phát lại 17-18/11 khi bật cho kết quả kém hơn, chưa khuyên bật."
    },
    "use_confidence_gate": {
        "title": "Cổng độ tin cậy AI (AI Confidence Gate)",
        "what_is_it": "Khi bật, hệ thống yêu cầu điểm tin cậy tổng hợp của AI (Confidence %, bao gồm đồng thuận AI, độ phân tán kịch bản và cộng điểm ngữ cảnh Macro/OrderFlow/Wick) phải đạt từ ngưỡng Consensus trở lên mới cho phép vào lệnh thay vì chỉ xét đồng thuận kịch bản thuần.",
        "how_to_set": "- TẮT (False - Mặc định): Vào lệnh dựa trên tỷ lệ đồng thuận kịch bản AI (Bull/Bear %).\n- BẬT (True): Chế độ siêu chọn lọc, chỉ vào lệnh khi cả kịch bản AI và các tầng ngữ cảnh (Macro/Order-Flow/Râu nến) cùng đồng thuận cao."
    },
    "blocked_trading_windows": {
        "title": "Khung giờ cấm giao dịch (Blocked Trading Windows - UTC+7)",
        "what_is_it": "Danh sách các khung giờ cấm bot quét tín hiệu và cấm mở lệnh mới (nhằm né tin tức mạnh như CPI, Non-Farm, FOMC, hoặc giờ giao phiên).\nĐịnh dạng:\n- Theo ngày cụ thể: 'YYYY-MM-DD HH:MM-HH:MM' (VD: '2026-11-12 19:25-20:00')\n- Lặp lại hàng tuần: 'THU 19:25-20:00' (hoặc T2..T7, CN, MON..SUN)\n- Lặp lại hàng ngày: 'HH:MM-HH:MM' (VD: '04:00-06:00')\n- Nhiều khung giờ phân tách nhau bằng dấu phẩy ',' hoặc chấm phẩy ';'.\n- Khi đến giờ cấm: Bot hủy vùng đang canh, không mở zone mới nhưng KHÔNG đụng đến các lệnh đã mở.",
        "how_to_set": "- Nhập ví dụ: 'THU 19:25-20:30, FRI 19:25-21:00' để né tin tức tối thứ 5 và thứ 6.\n- Để trống: Tắt bộ lọc (giao dịch bình thường 24/5)."
    },

    # ─── MỤC 3: QUẢN LÝ LỆNH & CHỐT LỜI ───
    "auto_be": {
        "title": "Tự dời SL về hòa vốn (Break-Even - BE)",
        "what_is_it": "Khi lệnh đang mở đạt mức lợi nhuận mục tiêu (tính theo R), bot sẽ tự động kéo đường Cắt Lỗ (SL) về đúng giá vào lệnh (Entry) để triệt tiêu hoàn toàn rủi ro thua lỗ.",
        "how_to_set": "- LUÔN BẬT. Giúp bảo toàn vốn gốc, biến mọi lệnh đạt kỳ vọng ban đầu thành lệnh 'không thể thua'."
    },
    "be_trigger_r": {
        "title": "Mức kích hoạt hòa vốn (BE Trigger R)",
        "what_is_it": "Mức lợi nhuận (tính theo tỷ lệ R của SL ban đầu) mà lệnh phải đạt được để bot kích hoạt dời SL về hòa vốn.",
        "how_to_set": "- Chuẩn mực: 1.0R (Ví dụ SL ban đầu là 3$, khi giá lãi đạt 3$ sẽ kéo ngay SL về hòa).\n- Muốn bảo vệ vốn sớm: Đặt 0.8R."
    },
    "auto_trail": {
        "title": "Tự trượt SL khóa lãi (Trailing Stop)",
        "what_is_it": "Khi giá tiếp tục chạy sâu vào vùng có lãi, bot sẽ tự động trượt đường SL tịnh tiến theo sau giá để khóa lợi nhuận và gồng lãi theo xu hướng.",
        "how_to_set": "- RẤT NÊN BẬT khi thị trường có xu hướng rõ ràng, giúp tối đa hóa lợi nhuận mà không sợ bị thị trường đảo chiều bất ngờ lấy lại hết lãi."
    },
    "trail_dist_r": {
        "title": "Khoảng cách bám giá Trailing (Trail Distance R)",
        "what_is_it": "Khoảng cách thả lỏng (tính theo tỷ lệ R) giữa giá hiện tại và đường SL trượt theo sau.",
        "how_to_set": "- Khuyên dùng: 0.35R đến 0.50R.\n- Khoảng cách này vừa đủ để giá có không gian rung lắc tự nhiên mà không bị dính SL quá sớm, đồng thời vẫn bảo vệ được phần lớn lợi nhuận khi đảo chiều."
    },
    "auto_rev_close": {
        "title": "Bảo vệ đảo chiều thông minh (Reversal Guard)",
        "what_is_it": "Tính năng bảo vệ chủ động: Khi bạn đang nắm giữ lệnh (ví dụ BUY) nhưng AI phát hiện cấu trúc thị trường bất ngờ đảo chiều sang SELL mạnh mẽ:\n- Nếu lệnh đang CÓ LÃI: Bot sẽ chủ động CHỐT LỜI NGAY để bảo vệ tiền lời.\n- Nếu lệnh đang TẠM LỖ: Bot KHÔNG cắt lỗ tháo chạy, mà nâng TP về sát giá hòa vốn (Entry+) để đón nhịp hồi thoát lệnh an toàn.",
        "how_to_set": "- LUÔN BẬT (Mặc định). Tính năng này giúp giảm thiểu tối đa các pha lỗ nặng do thị trường quay ngoắt 180 độ."
    },
    "rev_consensus": {
        "title": "Đồng thuận đảo chiều - Lệnh chưa BE (%)",
        "what_is_it": "Độ đồng thuận của AI theo chiều ngược lại cần đạt tối thiểu bao nhiêu % để kích hoạt cơ chế Bảo vệ đảo chiều cho các lệnh CHƯA DỜI HÒA VỐN (đang chịu rủi ro vốn ban đầu).",
        "how_to_set": "- Khuyên dùng: 65% đến 75% (Mặc định 75%).\n- Bảo vệ sớm vốn tài khoản khi thị trường có dấu hiệu quay đầu ngược chiều."
    },
    "rev_be_consensus": {
        "title": "Đồng thuận đảo chiều - Lệnh đã BE (0=Tắt, %)",
        "what_is_it": "Ngưỡng đồng thuận AI tối thiểu theo chiều ngược lại để kích hoạt chốt lời cho các lệnh ĐÃ DỜI HÒA VỐN (BE+).\n- THÔNG MINH: Lệnh đã dời BE là lệnh Risk-Free (hoàn toàn không còn rủi ro mất vốn), do đó cần ngưỡng đồng thuận cao hơn lệnh thường để tránh bị chốt non khi sóng lớn vẫn còn tiếp diễn.\n- Nếu đặt 0: TẮT (Không bao giờ ép chốt lệnh đã BE khi AI đảo chiều, để lệnh tự do gồng lãi theo Trailing Stop hoặc TP).",
        "how_to_set": "- Khuyên dùng: 85% đến 90% (Mặc định 90%).\n- Đặt = 0 nếu muốn thả rông cho các lệnh đã dời BE tự gồng lãi tối đa."
    },
    "rev_min_move": {
        "title": "Biên độ ngược chiều tối thiểu (Giá)",
        "what_is_it": "Biên độ giá dự báo ngược chiều tối thiểu để kích hoạt bảo vệ đảo chiều. Nếu đặt 0, chỉ cần AI đạt đủ độ đồng thuận là bot sẽ kích hoạt ngay.",
        "how_to_set": "- Khuyên dùng: Đặt 0 (hoặc 1.0$ nếu muốn chỉ phản ứng khi xu hướng ngược có xung lực mạnh)."
    },
    "be_plus_price": {
        "title": "Giá lãi nhỏ cộng thêm vào Entry (BE+)",
        "what_is_it": "Khoảng giá cộng thêm vào điểm Entry khi dời SL hoặc dời TP về hòa vốn (Break-Even Plus), nhằm bù đắp chi phí hoa hồng (Commission) và trượt giá sàn, giúp lệnh khi thoát vẫn có một chút tiền lãi xanh nhẹ.",
        "how_to_set": "- Vàng XAUUSD: Đặt 0.2$ đến 0.3$ (2 - 3 pip).\n- Đảm bảo thoát lệnh hòa vốn mà không bị âm tiền phí sàn."
    },
    "rev_loss_mode": {
        "title": "Chế độ xử lý lệnh đang lỗ khi AI đảo chiều (Reverse Loss Mode)",
        "what_is_it": "Hành vi xử lý khi AI phát hiện xu hướng đã đảo chiều nhưng lệnh hiện tại đang bị âm:\n- 'Chờ hồi về Entry' (Mặc định): Giữ nguyên lệnh, dời TP về hòa vốn (Entry + be_plus_price) để chờ giá hồi thoát nhẹ lãi/hòa.\n- 'Cắt khi đảo mạnh': Đóng lệnh ngay lập tức nếu AI đảo chiều cực mạnh (Đồng thuận ngược ≥ rev_be_consensus và xuất hiện CHoCH cấu trúc ngược).\n- 'Siết SL 50%': Siết đường SL hiện tại lại 50% khoảng cách để thu hẹp rủi ro thua lỗ tối đa.",
        "how_to_set": "- 'Chờ hồi về Entry' (Mặc định - An toàn theo sóng hồi).\n- 'Cắt khi đảo mạnh' (Khuyên dùng khi muốn dứt khoát cắt lệnh khi thị trường bẻ gãy cấu trúc CHoCH).\n- 'Siết SL 50%' (Giảm 1/2 rủi ro nhưng vẫn cho lệnh cơ hội hồi)."
    },
    "basket_tp_usd": {
        "title": "Chốt lời chùm lệnh tổng (Basket TP USD)",
        "what_is_it": "Khi tổng lợi nhuận của TẤT CẢ các lệnh đang mở (chùm lệnh) cộng lại đạt số tiền USD này, bot sẽ đóng toàn bộ các lệnh ngay lập tức để thu lợi nhuận về.",
        "how_to_set": "- Đánh 2-3 lệnh 0.01 lot: Đặt 10$ - 20$.\n- Nếu đặt 0: Tắt tính năng chốt chùm (mỗi lệnh sẽ chốt độc lập theo TP/SL riêng của nó)."
    },
    "basket_cooldown_mins": {
        "title": "Thời gian nghỉ sau chốt chùm (Basket Cooldown)",
        "what_is_it": "Số phút bot tạm dừng không mở thêm lệnh mới sau một pha chốt lời chùm lệnh thành công, giúp thị trường hạ nhiệt và tránh bị cuốn vào nhịp đu đỉnh/đáy.",
        "how_to_set": "- Khuyên dùng: 15 đến 30 phút (Đặt 0 để tắt)."
    },
    "max_hold_mins": {
        "title": "Thời gian giữ lệnh tối đa (Max Holding Time)",
        "what_is_it": "Giới hạn số phút tối đa một lệnh được phép duy trì trên thị trường do người dùng cài đặt (ví dụ 15 phút, 60 phút). Tránh việc lệnh bị ngâm quá lâu trong vùng thị trường đi ngang hoặc sideway.\n- THÔNG MINH: Bot tự động miễn trừ (không ép đóng) cho các lệnh ĐÃ DỜI HÒA VỐN (BE) hoặc ĐANG TRAILING STOP để bảo vệ trọn vẹn đà gồng lãi theo sóng lớn!",
        "how_to_set": "- Scalping nhanh: Đặt 15 - 30 phút (lệnh không bứt phá được sẽ tự kích hoạt xử lý).\n- Nếu đặt 0: Tắt tính năng giới hạn thời gian (lệnh sẽ chạy cho đến khi chạm TP, SL hoặc có tín hiệu đảo chiều)."
    },
    "max_hold_action": {
        "title": "Xử lý khi vượt thời gian giữ lệnh",
        "what_is_it": "Cách thức bot xử lý lệnh khi đã chạm mốc thời gian giữ tối đa:\n- 'Dời SL về BE+ (nếu lãi) & Dời TP về hòa (nếu lỗ)' (Mặc định): Lãi thì dời SL về Entry+ và để lệnh chạy tiếp tới TP (không đóng lệnh đang lãi); lỗ thì dời TP về hòa vốn.\n- 'Dời SL về hòa vốn (BE+)': Khóa rủi ro, cho thị trường thêm cơ hội chạy tiếp.\n- 'Đóng tất cả (Bất kể Lãi/Lỗ)': Cắt dứt khoát giải phóng lệnh ngay lập tức.",
        "how_to_set": "- Khuyên dùng phương án mặc định ('Dời SL về BE+ nếu lãi & Dời TP về hòa nếu lỗ'): không đóng sớm lệnh đang lãi, vẫn khóa hòa vốn."
    },
    "daily_tp_usd": {
        "title": "Mục tiêu Lãi trong ngày (Daily Take Profit)",
        "what_is_it": "Mức lợi nhuận mục tiêu trong một ngày tính theo giờ Việt Nam (UTC+7). Khi tổng lãi chốt trong ngày chạm mốc này, bot sẽ tự động ngưng giao dịch để bảo toàn thành quả.",
        "how_to_set": "- Khuyên dùng: Đặt tương đương 2% - 5% vốn tài khoản (Ví dụ TK 1,000$ thì đặt 30$ - 50$).\n- Nếu đặt 0: Bot sẽ chạy liên tục không giới hạn mục tiêu ngày."
    },
    "daily_sl_usd": {
        "title": "Giới hạn Lỗ tối đa trong ngày (Daily Stop Loss)",
        "what_is_it": "Mức thua lỗ tối đa cho phép trong một ngày. Nếu tổng lỗ trong ngày chạm ngưỡng này, bot lập tức ngừng toàn bộ giao dịch để cắt chuỗi xui và bảo vệ vốn gốc.",
        "how_to_set": "- Khuyên dùng: Đặt tương đương 3% - 5% vốn tài khoản (Ví dụ TK 1,000$ thì đặt 30$ - 50$).\n- TUYỆT ĐỐI KHÔNG NÊN ĐỂ 0 (Luôn cài đặt để làm chốt chặn an toàn cho tài khoản)."
    },
    "max_consec_loss": {
        "title": "Số lệnh thua liên tiếp để tạm nghỉ (Consecutive Losses)",
        "what_is_it": "Nếu gặp liên tiếp N lệnh bị dính SL (thị trường đang trong pha bất thường không phù hợp mô hình), bot sẽ tự kích hoạt cơ chế đóng băng tạm thời.",
        "how_to_set": "- Khuyên dùng: 2 đến 3 lệnh.\n- Giúp bảo toàn tâm lý và vốn khi thị trường vào phiên xấu."
    },
    "cooldown_mins": {
        "title": "Thời gian tạm ngừng sau chuỗi thua (Cooldown Mins)",
        "what_is_it": "Số phút bot tạm dừng hoạt động sau khi chạm giới hạn thua liên tiếp hoặc chạm SL nhanh.",
        "how_to_set": "- Khuyên dùng: 30 đến 60 phút.\n- Cho thị trường thời gian hoàn thành nhịp biến động bất thường trước khi bot tiếp tục giao dịch."
    },
    "fast_sl_cooldown_candles": {
        "title": "Nghỉ nếu dính SL nhanh (Số nến AI)",
        "what_is_it": "Nếu một lệnh vừa mở chưa đầy 1 cây nến AI đã bị quét cắn SL, chứng tỏ thị trường vừa có biến động giật mạnh bất thường. Bot sẽ tự ngắt tạm thời số cây nến AI tiếp theo để tránh bị quét liên hoàn.",
        "how_to_set": "- Cài theo số cây nến của khung AI phân tích (Ví dụ AI chạy M5 thì 1 = 1 nến 5p, 2 = 2 nến 10p; AI chạy M15 thì 1 = 15p, 2 = 30p).\n- Đặt 0 để tắt."
    },
    "fast_sl_same_dir": {
        "title": "SL nhanh: chỉ nghỉ chiều vừa thua",
        "what_is_it": "Áp dụng cho 'Nghỉ nếu SL nhanh'. Khi BẬT: lệnh dính SL nhanh là BUY thì chỉ tạm ngừng vào BUY trong thời gian nghỉ; lệnh SELL nếu có tín hiệu vẫn vào bình thường (và ngược lại). Khi TẮT: nghỉ cả hai chiều như cũ.",
        "how_to_set": "- TẮT (Mặc định): nghỉ cả BUY lẫn SELL.\n- BẬT: chỉ nghỉ chiều vừa bị SL nhanh. Chưa có đủ phát lại để kết luận tốt hơn."
    },
    "fast_sl_cooldown_mins": {
        "title": "Nghỉ nếu dính SL nhanh (Số nến AI)",
        "what_is_it": "Nếu một lệnh vừa mở chưa đầy 1 cây nến AI đã bị quét cắn SL, chứng tỏ thị trường vừa có biến động giật mạnh bất thường. Bot sẽ tự ngắt tạm thời số cây nến AI tiếp theo để tránh bị quét liên hoàn.",
        "how_to_set": "- Cài theo số cây nến của khung AI phân tích (Ví dụ AI chạy M5 thì 1 = 1 nến 5p, 2 = 2 nến 10p; AI chạy M15 thì 1 = 15p, 2 = 30p).\n- Đặt 0 để tắt."
    },

    # ─── MỤC 4: PHÂN TÍCH AI & MÔ HÌNH ───
    "engine": {
        "title": "Chế độ chạy động cơ AI (Execution Engine)",
        "what_is_it": "Lựa chọn môi trường thực thi suy luận mô hình AI:\n- 'Chạy trực tiếp máy Local': Chạy thẳng trên máy tính cá nhân của bạn (tận dụng CPU hoặc GPU NVIDIA nếu có).\n- 'Ưu tiên Hugging Face -> Local': Gửi dữ liệu nến lên máy chủ Cloud Hugging Face Space để suy luận không tốn tài nguyên máy, nếu mạng lỗi sẽ tự động chuyển về máy Local.",
        "how_to_set": "- Nếu máy tính có GPU NVIDIA hoặc CPU khá: Chọn 'Chạy trực tiếp máy Local' để tốc độ nhanh nhất.\n- Nếu dùng VPS hoặc máy yếu: Chọn 'Ưu tiên Hugging Face'."
    },
    "model": {
        "title": "Mô hình AI dự báo (AI Model Architecture)",
        "what_is_it": "Chọn phiên bản trọng số mô hình Deep Learning:\n- AI-base (102.3M - Mạnh nhất): Mô hình kích thước lớn nhất, độ sâu phân tích cao nhất, khả năng nắm bắt xu hướng tốt nhất.\n- AI-Gold-Exness: Phiên bản tinh chỉnh riêng cho dữ liệu nến vàng sàn Exness.\n- AI-small (24.7M) / AI-mini: Nhẹ hơn, suy luận cực nhanh cho máy yếu.",
        "how_to_set": "- Khuyên dùng: 'AI-base (102.3M - Mạnh nhất)' để đạt độ chính xác cao nhất."
    },
    "hf_space": {
        "title": "Hugging Face Space ID",
        "what_is_it": "Tên định danh Space API máy chủ của bạn trên nền tảng Hugging Face nếu chạy chế độ Cloud.",
        "how_to_set": "- Giữ nguyên giá trị mặc định 'tranvankha2807/Kronos_Xau' trừ khi bạn tự xây dựng server Space riêng."
    },
    "hf_token": {
        "title": "Hugging Face User Access Token",
        "what_is_it": "Mã token xác thực API cá nhân trên Hugging Face (huggingface.co/settings/tokens) trong trường hợp Space được cài đặt ở chế độ Riêng tư (Private).",
        "how_to_set": "- Để trống nếu Space của bạn ở chế độ Công khai (Public)."
    },
    "lookback": {
        "title": "Số nến lịch sử đưa vào AI (Lookback Window)",
        "what_is_it": "Số lượng cây nến quá khứ được trích xuất và đưa vào chuỗi thời gian để AI học quy luật và dự báo tương lai.",
        "how_to_set": "- Khuyên dùng: 450 đến 512 nến (tương đương khoảng 40 - 50 giờ dữ liệu trên khung M5).\n- Đây là độ dài ngữ cảnh tối ưu theo kiến trúc Attention của mô hình Kronos."
    },
    "pred_len": {
        "title": "Số nến dự báo tương lai (Prediction Length)",
        "what_is_it": "Số lượng cây nến tiếp theo mà AI sẽ vẽ đường dự báo trên biểu đồ.",
        "how_to_set": "- Mặc định: 6 nến (Trên khung M5 tương đương 30 phút, trên M15 tương đương 90 phút).\n- Khoảng cách 6 nến là tối ưu nhất để xác định đỉnh/đáy cho một nhịp Scalping."
    },

    # ─── MỤC 5: THÔNG BÁO & ĐIỀU KHIỂN TELEGRAM ───
    "telegram_enabled": {
        "title": "Bật/Tắt kết nối Telegram",
        "what_is_it": "Kích hoạt tính năng gửi tin nhắn thông báo vào/đóng lệnh tự động và cho phép bạn điều khiển bot từ xa bằng điện thoại qua Telegram.",
        "how_to_set": "- Tích chọn để bật tính năng."
    },
    "telegram_bot_token": {
        "title": "Telegram Bot Token",
        "what_is_it": "Mã bảo mật HTTP API của Bot Telegram do @BotFather cấp.",
        "how_to_set": "- Mở ứng dụng Telegram, tìm kiếm @BotFather, gửi lệnh /newbot để tạo bot và copy mã token dán vào đây."
    },
    "telegram_chat_id": {
        "title": "ID nhóm chat Telegram (có thể để trống)",
        "what_is_it": "ID nhóm Telegram mà bot gửi thông báo vào (số âm, dạng -5282444977 hoặc -100...).\n- Để TRỐNG: bot gửi tin RIÊNG cho 'ID người điều khiển' (ô bên dưới).\n- Vẫn dán được ID cá nhân (số dương) như bản cũ.",
        "how_to_set": "- Thêm @getidsbot (hoặc @RawDataBot) vào nhóm, lấy số 'id' của phần 'This chat' (có dấu -), dán vào đây; lấy xong xóa bot đó khỏi nhóm.\n- Không dùng nhóm: để trống và điền 'ID người điều khiển'."
    },
    "telegram_admin_ids": {
        "title": "ID người điều khiển (chỉ người này được Bật/Tắt bot)",
        "what_is_it": "ID tài khoản Telegram CÁ NHÂN được phép bật/tắt bot giao dịch (nút Bật/Dừng, lệnh /pause, /resume...), kể cả khi bấm trong nhóm. Người khác trong nhóm chỉ xem được thông báo, Báo cáo, Phân tích AI.\n- Để trống: Chat ID là chat riêng thì như cũ (chính bạn); Chat ID là NHÓM (số âm) thì không ai bật/tắt được qua Telegram.\n- Có thể điền nhiều ID, cách nhau dấu phẩy. Bạn cũng nhắn riêng cho bot để điều khiển được.",
        "how_to_set": "- Nhắn riêng cho @getidsbot hoặc @userinfobot để lấy ID của bạn (số dương, dạng 123456789) rồi dán vào đây.\n- Không dán ID nhóm (số âm) vào ô này."
    },
    "telegram_notify_open": {
        "title": "Thông báo khi mở lệnh mới",
        "what_is_it": "Gửi tin nhắn chi tiết kèm giá Entry, SL, TP và tín hiệu AI mỗi khi bot vào một lệnh mới.",
        "how_to_set": "- Tích chọn (Khuyên dùng)."
    },
    "telegram_notify_close": {
        "title": "Thông báo khi đóng lệnh",
        "what_is_it": "Gửi tin nhắn tổng kết lợi nhuận ($ và R) kèm lý do đóng lệnh (chốt TP, Trailing Stop, cắt lỗ, hòa vốn) mỗi khi có lệnh kết thúc.",
        "how_to_set": "- Tích chọn (Khuyên dùng)."
    }
}

DEFAULT_PROFILE_SETTINGS = {
    "XAUUSD": {
        "symbol": "XAUUSDm",
        "analysis_tf": "M15",
        "tf": "M5",
        "lot": "0.01",
        "max_pos": "3",
        "magic_number": "20260501",
        "mt5_path": DEFAULT_MT5_PATH,
        "ai_threads": "Auto",
        "scenarios": "8",
        "consensus": "75",
        "max_dispersion": "2.5",
        "min_move": "1.2",
        "max_spread": "0.35",
        "sl_atr": "1.5",
        "sl_buffer": "0.5",
        "max_sl_price": "5.0",
        "rr": "2.0",
        "min_entry_spacing": "1.0",
        "entry_mode": "Chỉ nhồi khi đã khóa lãi lệnh trước (Risk-Free)",
        "allow_opposite_entry": False,
        "one_order_per_candle": True,
        "trend_filter": False,
        "confirm_entry_pa": True,
        "pa_wait_bar_close": True,
        "pa_fail_open": False,
        "pa_min_confirmations": "1",
        "expected_move_k": "3",
        "use_confidence_gate": False,
        "debug_trace": False,
        "recovery_close": False,
        "stack_half_be": False,
        "structure_direction": False,
        "structure_conflict_mode": "neutral",
        "blocked_trading_windows": "",
        "auto_be": True,
        "be_trigger_r": "1.0",
        "auto_trail": True,
        "trail_dist_r": "0.35",
        "auto_rev_close": True,
        "rev_consensus": "75",
        "rev_be_consensus": "90",
        "rev_min_move": "0",
        "be_plus_price": "0.2",
        "rev_loss_mode": "wait_be",
        "basket_tp_usd": "0",
        "basket_cooldown_mins": "15",
        "max_hold_mins": "15",
        "max_hold_action": "Dời SL về BE+ (nếu lãi) & Dời TP về hòa (nếu lỗ)",
        "daily_tp_usd": "0",
        "daily_sl_usd": "0",
        "max_consec_loss": "0",
        "cooldown_mins": "30",
        "fast_sl_cooldown_candles": "1",
        "fast_sl_same_dir": False,
        "engine": "Chạy trực tiếp máy Local",
        "model": "AI-base (102.3M - Mạnh nhất)",
        "hf_space": "tranvankha2807/Kronos_Xau",
        "hf_token": "",
        "lookback": "450",
        "pred_len": "6",
        "realtime_chart": True,
        "telegram_enabled": False,
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "telegram_admin_ids": "",
        "telegram_notify_open": True,
        "telegram_notify_close": True,
    },
    "BTCUSD": {
        "symbol": "BTCUSDm",
        "analysis_tf": "M15",
        "tf": "M5",
        "lot": "0.01",
        "max_pos": "3",
        "magic_number": "20260502",
        "mt5_path": DEFAULT_MT5_PATH,
        "ai_threads": "Auto",
        "scenarios": "8",
        "consensus": "75",
        "max_dispersion": "2.5",
        "min_move": "60.0",
        "max_spread": "30.0",
        "sl_atr": "1.5",
        "sl_buffer": "20.0",
        "max_sl_price": "350.0",
        "rr": "2.0",
        "min_entry_spacing": "50.0",
        "entry_mode": "Chỉ nhồi khi đã khóa lãi lệnh trước (Risk-Free)",
        "allow_opposite_entry": False,
        "one_order_per_candle": True,
        "trend_filter": False,
        "confirm_entry_pa": True,
        "pa_wait_bar_close": True,
        "pa_fail_open": False,
        "pa_min_confirmations": "1",
        "expected_move_k": "3",
        "use_confidence_gate": False,
        "debug_trace": False,
        "recovery_close": False,
        "stack_half_be": False,
        "structure_direction": False,
        "structure_conflict_mode": "neutral",
        "blocked_trading_windows": "",
        "auto_be": True,
        "be_trigger_r": "1.0",
        "auto_trail": True,
        "trail_dist_r": "0.35",
        "auto_rev_close": True,
        "rev_consensus": "75",
        "rev_be_consensus": "90",
        "rev_min_move": "0",
        "be_plus_price": "5.0",
        "rev_loss_mode": "wait_be",
        "basket_tp_usd": "0",
        "basket_cooldown_mins": "15",
        "max_hold_mins": "15",
        "max_hold_action": "Dời SL về BE+ (nếu lãi) & Dời TP về hòa (nếu lỗ)",
        "daily_tp_usd": "0",
        "daily_sl_usd": "0",
        "max_consec_loss": "0",
        "cooldown_mins": "30",
        "fast_sl_cooldown_candles": "1",
        "fast_sl_same_dir": False,
        "engine": "Chạy trực tiếp máy Local",
        "model": "AI-base (102.3M - Mạnh nhất)",
        "hf_space": "tranvankha2807/Kronos_Xau",
        "hf_token": "",
        "lookback": "450",
        "pred_len": "6",
        "realtime_chart": True,
        "telegram_enabled": False,
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "telegram_admin_ids": "",
        "telegram_notify_open": True,
        "telegram_notify_close": True,
    },
}

PROFILE_STATE_ATTRS = (
    "auto_loop_running",
    "is_analyzing",
    "last_candle_time",
    "last_ana_candle_time",
    "last_recommendation",
    "pending_pullback_order",
    "daily_limit_locked_date",
    "daily_limit_reason",
    "daily_limit_locked_kind",
    "daily_limit_locked_threshold",
    "cached_daily_pnl",
    "cached_win_rate",
    "cached_win_count",
    "cached_loss_count",
    "cached_be_count",
    "cached_closed_count",
    "consec_loss_count",
    "cooldown_until",
    "last_cooldown_deal_ticket",
    "basket_cooldown_until",
    "fast_sl_cooldown_until",
    "last_fast_sl_deal_ticket",
    "is_basket_closing",
    "cached_scenarios",
    "cached_mean_df",
    "cached_action",
    "cached_sl",
    "cached_tp",
    "cached_df_hist",
    "cached_df_analysis",
    "user_zoomed",
    "saved_xlim",
    "saved_ylim",
    "_last_detail_prefix",
    "_sig_title",
    "_sig_color",
)

# Các thuộc tính do luồng AI cập nhật trực tiếp vào profile_ctx (luồng UI 1 giây không được ghi đè ngược dữ liệu cũ lên)
AI_MANAGED_STATE_ATTRS = frozenset({
    "is_analyzing",
    "last_candle_time",
    "last_ana_candle_time",
    "last_recommendation",
    "pending_pullback_order",
    "cached_scenarios",
    "cached_mean_df",
    "cached_action",
    "cached_sl",
    "cached_tp",
    "cached_df_hist",
    "cached_df_analysis",
    "_last_detail_prefix",
    "_sig_title",
    "_sig_color",
    # Chốt chùm (Basket): luồng chốt chùm chỉ ghi vào ctx -> KHÔNG đồng bộ đè từ self mỗi giây. Trước đây tab đang mở bị xóa
    # tạm nghỉ sau chốt chùm sau ~1 giây (vào lệnh lại ngay) và mất khóa chống đóng trùng (rà soát backtest vs live 19/11/2026)
    "basket_cooldown_until",
    "is_basket_closing",
})


class CollapsibleSection(tk.Frame):
    """Mục giao diện có thể thu gọn / sổ xuống theo kiểu Accordion (mặc định thu gọn)."""
    def __init__(self, parent, title: str, initially_open: bool = False, group_list: list = None, on_reset: callable = None):
        super().__init__(parent, bg=PANEL_BG)
        self.pack(fill=tk.X, pady=2)
        self.title = title
        self.is_open = initially_open
        self.group_list = group_list
        self.on_reset = on_reset
        if self.group_list is not None:
            self.group_list.append(self)

        self.header_frame = tk.Frame(self, bg=SECTION_HEADER_BG)
        self.header_frame.pack(fill=tk.X)

        self.header_btn = tk.Button(
            self.header_frame,
            text=self._get_header_text(),
            bg=SECTION_HEADER_BG,
            fg=GOLD_ACCENT,
            activebackground="#2d3a52",
            activeforeground=GOLD_ACCENT,
            font=("Segoe UI", 9, "bold"),
            relief=tk.FLAT,
            anchor="w",
            padx=8,
            pady=4,
            command=self.toggle
        )
        self.header_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Nút "↺ Mặc định" bên phải tiêu đề đã được ẩn theo yêu cầu; mỗi mục vẫn có nút khôi phục mặc định ở cuối mục.
        self.reset_btn = None

        self.content = tk.Frame(self, bg=PANEL_BG, padx=6, pady=3)
        if self.is_open:
            self.content.pack(fill=tk.X)

    def _get_header_text(self) -> str:
        arrow = "▾" if self.is_open else "▸"
        return f"{arrow}  {self.title}"

    def close_section(self):
        if self.is_open:
            self.is_open = False
            self.header_btn.config(text=self._get_header_text())
            self.content.pack_forget()

    def toggle(self):
        if not self.is_open and self.group_list is not None:
            for other in self.group_list:
                if other is not self:
                    other.close_section()
        self.is_open = not self.is_open
        self.header_btn.config(text=self._get_header_text())
        if self.is_open:
            self.content.pack(fill=tk.X)
        else:
            self.content.pack_forget()



class DatePickerDialog:
    """Cửa sổ Pop-up Lịch chọn ngày độc lập, thuần Tkinter không phụ thuộc bên ngoài."""
    def __init__(self, parent, initial_date=None, on_date_selected=None):
        self.parent = parent
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Chọn Ngày")
        self.dialog.configure(bg="#19202c")
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        self.on_date_selected = on_date_selected
        if isinstance(initial_date, str):
            try:
                self.curr_dt = datetime.strptime(initial_date.strip(), "%Y-%m-%d").date()
            except Exception:
                self.curr_dt = date.today()
        elif isinstance(initial_date, (datetime, date)):
            self.curr_dt = initial_date if isinstance(initial_date, date) else initial_date.date()
        else:
            self.curr_dt = date.today()

        self.sel_year = self.curr_dt.year
        self.sel_month = self.curr_dt.month
        self.selected_date = self.curr_dt

        # Thanh tiêu đề Tháng/Năm kèm nút chuyển tháng
        hdr = tk.Frame(self.dialog, bg="#10141d", padx=8, pady=6)
        hdr.pack(fill=tk.X)

        tk.Button(
            hdr, text="◀", bg="#232d3f", fg="#eaecef", relief=tk.FLAT, font=("Segoe UI", 9, "bold"),
            padx=6, pady=2, cursor="hand2", command=self._prev_month
        ).pack(side=tk.LEFT)

        self.lbl_month_year = tk.Label(
            hdr, text=f"Tháng {self.sel_month:02d} / {self.sel_year}",
            bg="#10141d", fg=GOLD_ACCENT, font=("Segoe UI", 10, "bold")
        )
        self.lbl_month_year.pack(side=tk.LEFT, expand=True)

        tk.Button(
            hdr, text="▶", bg="#232d3f", fg="#eaecef", relief=tk.FLAT, font=("Segoe UI", 9, "bold"),
            padx=6, pady=2, cursor="hand2", command=self._next_month
        ).pack(side=tk.RIGHT)

        # Hàng tiêu đề thứ trong tuần (T2 -> CN)
        days_frame = tk.Frame(self.dialog, bg="#19202c", padx=6, pady=4)
        days_frame.pack(fill=tk.X)

        day_names = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]
        for idx, d_name in enumerate(day_names):
            fg_c = RED_DOWN if idx == 6 else (GOLD_ACCENT if idx == 5 else TEXT_MUTED)
            tk.Label(
                days_frame, text=d_name, width=4, bg="#19202c", fg=fg_c, font=("Segoe UI", 8, "bold")
            ).grid(row=0, column=idx, padx=2, pady=2)

        # Lưới các ngày trong tháng
        self.cal_grid_frame = tk.Frame(self.dialog, bg="#19202c", padx=6, pady=4)
        self.cal_grid_frame.pack(fill=tk.BOTH, expand=True)

        # Thanh nút dưới cùng (Hôm nay / Đóng)
        btn_bar = tk.Frame(self.dialog, bg="#10141d", padx=8, pady=6)
        btn_bar.pack(fill=tk.X)

        tk.Button(
            btn_bar, text="Hôm Nay", bg="#235c46", fg="#ffffff", font=("Segoe UI", 8, "bold"),
            relief=tk.FLAT, padx=8, pady=2, cursor="hand2", command=self._select_today
        ).pack(side=tk.LEFT)
        tk.Button(
            btn_bar, text="Đóng", bg="#232d3f", fg="#eaecef", font=("Segoe UI", 8),
            relief=tk.FLAT, padx=8, pady=2, cursor="hand2", command=self.dialog.destroy
        ).pack(side=tk.RIGHT)

        self._render_calendar()

        # Căn giữa cửa sổ con so với cửa sổ cha
        try:
            self.dialog.update_idletasks()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            w = self.dialog.winfo_width()
            h = self.dialog.winfo_height()
            x = px + max(0, (pw - w) // 2)
            y = py + max(0, (ph - h) // 2)
            self.dialog.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _prev_month(self):
        if self.sel_month == 1:
            self.sel_month = 12
            self.sel_year -= 1
        else:
            self.sel_month -= 1
        self._render_calendar()

    def _next_month(self):
        if self.sel_month == 12:
            self.sel_month = 1
            self.sel_year += 1
        else:
            self.sel_month += 1
        self._render_calendar()

    def _select_today(self):
        t = date.today()
        self._on_day_click(t.year, t.month, t.day)

    def _render_calendar(self):
        self.lbl_month_year.config(text=f"Tháng {self.sel_month:02d} / {self.sel_year}")
        for w in self.cal_grid_frame.winfo_children():
            w.destroy()

        cal = calendar.monthcalendar(self.sel_year, self.sel_month)
        today = date.today()

        for r_idx, week in enumerate(cal):
            for c_idx, day_num in enumerate(week):
                if day_num == 0:
                    lbl = tk.Label(self.cal_grid_frame, text="", width=4, bg="#19202c")
                    lbl.grid(row=r_idx, column=c_idx, padx=2, pady=2)
                else:
                    is_today = (self.sel_year == today.year and self.sel_month == today.month and day_num == today.day)
                    is_sel = (self.sel_year == self.selected_date.year and self.sel_month == self.selected_date.month and day_num == self.selected_date.day)

                    bg_c = GOLD_ACCENT if is_sel else ("#1b4d3e" if is_today else "#232d3f")
                    fg_c = "#000000" if is_sel else (GREEN_UP if is_today else "#eaecef")

                    btn = tk.Button(
                        self.cal_grid_frame, text=str(day_num), width=4, bg=bg_c, fg=fg_c,
                        font=("Segoe UI", 8, "bold" if (is_today or is_sel) else "normal"),
                        relief=tk.FLAT, cursor="hand2",
                        command=lambda y=self.sel_year, m=self.sel_month, d=day_num: self._on_day_click(y, m, d)
                    )
                    btn.grid(row=r_idx, column=c_idx, padx=2, pady=2)

    def _on_day_click(self, year, month, day):
        chosen_date = date(year, month, day)
        date_str = chosen_date.strftime("%Y-%m-%d")
        if self.on_date_selected:
            self.on_date_selected(date_str)
        self.dialog.destroy()


from trading_modules.trading_window import is_trading_window_blocked
from trading_modules.risk import calc_effective_max_sl, calc_effective_max_sl_ai
from trading_modules.param_rules import validate_param_values
from trading_modules.pnl_calendar import PnlCalendarDialog, daily_pnl
from trading_modules.kronos_engine import fast_predict_scenarios
from trading_modules.symbol_profile import symbol_tolerances
from trading_modules.tick_resample import resample_ticks_to_s1
from trading_modules.position_rules import step_position
from trading_modules.scenario_scoring import score_scenarios
from trading_modules.indicators import calculate_atr
from trading_modules.kronos_sampling import compute_adaptive_sampling_params
from trading_modules.backtest_report import _BT_WIN_EPS, build_backtest_report, format_backtest_report, export_backtest_csv


class KronosDesktopApp:
    def __init__(self, root: tk.Tk, lic_mgr=None):
        self.root = root
        self.root.title("Trading AI PRO by Kha Tran Design — Scalping Vàng & Quản Lý Lệnh Real-Time (MT5 Exness)")
        self.root.geometry("1450x880")
        self.root.configure(bg=BG_DARK)
        self._apply_window_and_taskbar_icon()

        if lic_mgr is None:
            try:
                from kronos_license import LicenseManager
                self.lic_mgr = LicenseManager()
            except Exception:
                self.lic_mgr = None
        else:
            self.lic_mgr = lic_mgr

        try:
            from kronos_updater import AppUpdater, get_current_app_version
            self.app_version = get_current_app_version()
            self.updater = AppUpdater(self.app_version)
        except Exception:
            self.app_version = "1.3.7"
            self.updater = None

        self._has_new_version = False
        self._latest_update_info = None
        self._version_blink_after_id = None
        self._version_blink_state = False
        self._new_version_tag = ""

        self.local_cache = {}
        self.initial_r_cache = {}  # Lưu khoảng cách 1R (|Entry - SL ban đầu|) theo từng ticket
        self._initial_r_saved = {}  # Bản đã ghi ra file gần nhất (A11): chỉ ghi lại khi cache thay đổi
        self._load_initial_r_cache()  # A11: nạp lại 1R của các lệnh còn mở sau khi khởi động lại
        self.pending_be_exit_tickets = set()  # Lưu các ticket đang âm bị AI báo đảo chiều -> chờ hồi về Entry thoát
        self.pending_pullback_order = None  # Lưu tín hiệu chờ giá hồi về <= SL tối đa (1 giá = 10 pip) mới vào lệnh
        self.daily_limit_locked_date = None  # Ngày (YYYY-MM-DD) đã chạm mục tiêu Lãi/Lỗ ngày -> Dừng giao dịch hết ngày
        self.daily_limit_reason = ""
        self.daily_limit_locked_kind = None
        self.daily_limit_locked_threshold = 0.0
        self.cached_daily_pnl = 0.0
        self.cached_win_rate = 0.0
        self.cached_win_count = 0
        self.cached_loss_count = 0
        self.cached_be_count = 0
        self.cached_closed_count = 0
        self.consec_loss_count = 0  # Chuỗi lệnh thua liên tiếp hiện tại trong ngày
        self.cooldown_until = None  # Thời điểm hết hạn tạm ngừng sau chuỗi thua
        self.last_cooldown_deal_ticket = None
        self.basket_cooldown_until = None  # Thời điểm hết hạn tạm nghỉ sau khi chốt lời chùm lệnh
        self.fast_sl_cooldown_until = None  # Thời điểm hết hạn tạm ngừng khi có lệnh dính SL < 5 phút
        self.last_fast_sl_deal_ticket = None
        self.is_basket_closing = False  # Khóa bảo vệ chống đóng lặp nhiều luồng khi chốt chùm
        self.left_menu_visible = False  # Mặc định thu gọn vào
        self.drawer_width = 355
        self.drawer_x = -350
        self._drawer_anim_job = None
        self.auto_loop_running = False
        self.is_analyzing = False
        self.last_candle_time = None
        self.last_ana_candle_time = None
        self.last_recommendation = None
        self.heartbeat_toggle = False
        self._loading_settings = False

        # Chế độ hiển thị biểu đồ: "LIVE_POSITIONS" (Tab 1) hoặc "AI_FORECAST" (Tab 2)
        self.chart_mode = "LIVE_POSITIONS"

        # Lưu trữ dữ liệu dự báo gần nhất
        self.cached_scenarios = None
        self.cached_mean_df = None
        self.cached_action = None
        self.cached_sl = None
        self.cached_tp = None
        self.cached_df_hist = None
        self.cached_df_analysis = None

        # Trạng thái tương tác biểu đồ kiểu TradingView (Zoom / Pan / Kéo thanh giá Y-Scale & X-Scale / Chuột phải Reset)
        self.user_zoomed = False
        self.saved_xlim = None
        self.saved_ylim = None
        self._selected_deal_ticket_focus = None
        self._active_viewing_closed_deal = None
        self._closed_deals_by_ticket = {}
        self._drag_start_px = None
        self._drag_start_xlim = None
        self._drag_start_ylim = None
        self._drag_mode = None  # "PAN", "Y_SCALE", hoặc "X_SCALE"
        self._chart_visible_bars = 170
        self._chart_hist_df = None
        self._chart_mean_df = None
        self._chart_all_times = []
        self._chart_n_hist = 0
        self._chart_pred_gap = 0
        self._chart_total_x = 0
        self._pos_text_artists = []
        self._forming_wick_line = None
        self._forming_body_rect = None
        self._live_price_hline = None
        self._live_price_annot = None
        self._candle_countdown_annot = None
        self._last_drawn_chart_key = None
        self._last_drawn_pos_sig = None
        self._redraw_pending = False
        self._redraw_full_needed = True
        self._chart_bg_bbox = None
        self._last_deals_check_ts = 0.0
        self._cached_deals_stats = (0.0, 0, None)
        self._cached_deals_by_sym = {}
        self._cached_closed_deals_map = {}
        self._cached_pid_magic = {}
        self._pid_magic_backoff = {}
        self._logged_magic_fail_pids = set()
        self._ai_scan_lock = threading.Lock()
        self._ai_inference_lock = threading.RLock()
        self._mt5_lock = threading.RLock()
        self._log_file_lock = threading.Lock()
        self._order_guard_lock = threading.Lock()
        self._pos_action_in_flight = set()
        self._order_in_flight = {"XAUUSD": False, "BTCUSD": False}
        self._modify_fail_until = {}  # ticket -> mốc time.monotonic() được phép sửa SL/TP lại (backoff 15s)
        self._modify_fail_log_ts = {}  # ticket -> mốc log lỗi sửa SL/TP gần nhất (1 lần/15s/ticket)
        self._last_order_open_mono = {"XAUUSD": 0.0, "BTCUSD": 0.0}
        self._last_order_candle_ts = {"XAUUSD": None, "BTCUSD": None}
        self._auto_worker_token = {"XAUUSD": 0, "BTCUSD": 0}
        self._last_mt5_order_mono = 0.0
        self._ai_queue = queue.Queue()
        self._ai_queue_lock = threading.Lock()
        self._ai_queued_profiles = set()
        self._ai_current_running_pk = None
        self._app_closing = False
        self._sig_title = "⚪ ĐANG TẢI DỮ LIỆU & PHÂN TÍCH AI..."
        self._sig_color = GOLD_ACCENT
        self.active_profile_key = "XAUUSD"
        self.profile_settings = {
            "XAUUSD": dict(DEFAULT_PROFILE_SETTINGS["XAUUSD"]),
            "BTCUSD": dict(DEFAULT_PROFILE_SETTINGS["BTCUSD"]),
        }
        self.chart_view_tf_by_profile = {
            "XAUUSD": "M5",
            "BTCUSD": "M5",
        }
        self.profile_ctx = {
            "XAUUSD": self._create_empty_profile_ctx("XAUUSD"),
            "BTCUSD": self._create_empty_profile_ctx("BTCUSD"),
        }
        self.pos_tree_by_profile = {}
        self.pos_summary_lbl_by_profile = {}
        self.pred_tree_by_profile = {}
        self.pred_title_by_profile = {}
        self.log_text_by_profile = {}
        self.side_drawer_by_profile = {}
        self.side_paned_by_profile = {}
        self.side_sub_frames_by_profile = {}
        self.side_mode_btns_by_profile = {}
        self.side_toggle_btn_by_profile = {}
        self.show_ai_chart_overlay = True
        self.cross_vline = None
        self.cross_hline = None
        self.cross_price_annot = None
        self.cross_time_annot = None
        self.cross_ohlc_text = None
        self._last_hover_xy = None
        self._saved_bottom_panel_h = 240
        self.right_paned = None

        # Trạng thái hiển thị % trên nút Reload Pill góc phải trên & đồng bộ trạng thái Bot
        self._scan_pct = 100.0
        self._scan_target_pct = 100.0
        self._scan_anim_job = None
        self._last_detail_prefix = None

        # Trạng thái cửa sổ kiểu MacBook (Traffic Light Minimize / Maximize / Close + Kéo thanh tiêu đề)
        self._win_maximized = False
        self._win_normal_geom = self._get_centered_geometry(1450, 880)
        self._win_drag_offset = None
        self._mac_ctrl_hover = False
        self._minimizing_to_taskbar = False

        # Trạng thái màn hình Loading khởi động kiểu MacBook (Xoay tròn + % Real-Time)
        self._is_initial_loading = True
        self._loading_cur_pct = 0.0
        self._loading_target_pct = 12.0
        self._spinner_step = 0
        self._spinner_lines = []
        self._loading_anim_job = None

        # Trạng thái Telegram Notifier & Điều Khiển Từ Xa
        self._telegram_worker_token = 0
        self._telegram_sent_deal_tickets = set()
        self._telegram_queue = queue.Queue()
        self._app_session_start_ts = int(time.time())
        # Nút bấm chỉ nằm dưới tin Telegram MỚI NHẤT: nhớ mã tin đang có nút để gỡ khi gửi tin mới
        self._tg_kb_lock = threading.Lock()
        self._tg_last_kb_msg_id = None

        # Trạng thái Module Kiểm thử Chiến Lược (Backtest Tick-Level Engine)
        self._backtest_unlocked = False
        self._bt_cancel_flag = False
        self._bt_worker_thread = None
        self._bt_trades_cache = []
        self._bt_equity_points = []
        self.bt_sym_var = tk.StringVar(value="XAUUSDm")
        self.bt_tf_var = tk.StringVar(value="M5")
        self.bt_start_var = tk.StringVar(value=(datetime.now(tz=VN_TZ) - timedelta(days=7)).strftime("%Y-%m-%d"))
        self.bt_end_var = tk.StringVar(value=datetime.now(tz=VN_TZ).strftime("%Y-%m-%d"))
        self.bt_capital_var = tk.StringVar(value="1000")
        self.bt_lot_var = tk.StringVar(value="0.02")
        self.bt_pass_var = tk.StringVar()
        self.bt_data_precision_var = tk.StringVar(value="⚡ Nến Tick 1s (S1)")
        self.bt_use_ai_var = tk.BooleanVar(value=True)

        self._cleanup_workspace_junk()
        self._init_file_logger()
        self._setup_styles()
        self._build_ui()
        self.load_user_settings()
        self._bind_auto_save_settings()
        self._build_startup_loading_overlay()
        self._register_active_instance_lock()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close_window)
        self.root.bind("<Escape>", self._deselect_all_orders_and_reset)
        self.root.bind("<ButtonPress>", lambda e: self._hide_hover_note(), add="+")
        self.root.bind("<Escape>", lambda e: self._hide_hover_note(), add="+")

        # Khởi động luồng Hàng Đợi Phân Tích AI (FIFO Queue Worker) tuần tự, đảm bảo XAUUSD & BTCUSD không bao giờ tranh chấp hay trộn dữ liệu
        self._ai_queue_worker_thread = threading.Thread(target=self._ai_queue_consumer_loop, daemon=True)
        self._ai_queue_worker_thread.start()

        # Khởi động luồng gửi tin và luồng điều khiển Telegram từ xa qua điện thoại
        threading.Thread(target=self._telegram_dispatcher_loop, daemon=True).start()
        threading.Thread(target=self._telegram_polling_worker, daemon=True).start()

        # Khởi động kết nối, chạy phân tích AI lần đầu tự động và bật luồng Real-Time
        self.root.after(150, self.check_mt5_connection)
        self.root.after(350, self.trigger_single_scan)
        self.root.after(600, self._restore_auto_running_after_update)
        self.root.after(1000, self._realtime_tick_loop)
        self.root.after(2000, self._check_update_on_startup)

    def _restore_auto_running_after_update(self):
        """Khôi phục tự động trạng thái công tắc AUTO RUNNING của các Tab ngay sau khi ứng dụng tự khởi động lại từ bản cập nhật OTA."""
        try:
            running_list = getattr(self, "_pending_restore_running_profiles", None)
            if not running_list:
                return
            self._pending_restore_running_profiles = []
            for pk in running_list:
                if pk in ("XAUUSD", "BTCUSD") and not self.profile_ctx.get(pk, {}).get("auto_loop_running", False):
                    self.start_auto_scan(profile_key=pk)
            self.log(
                f"🚀 [TỰ ĐỘNG ÁP DỤNG SAU CẬP NHẬT v{self.app_version}] Đã nạp toàn bộ cài đặt mới, khôi phục trạng thái AUTO ({', '.join(running_list)}) và gửi Request cho AI phân tích lại!"
            )
        except Exception as e:
            self.log_error("RESTORE_AUTO_AFTER_UPDATE", e, show_in_ui=False)

    def _create_empty_profile_ctx(self, profile_key: str) -> dict:
        """Khởi tạo bộ trạng thái độc lập cho từng Tab (XAUUSD / BTCUSD)."""
        sym_default = DEFAULT_PROFILE_SETTINGS.get(profile_key, {}).get("symbol", profile_key)
        return {
            "auto_loop_running": False,
            "is_analyzing": False,
            "last_candle_time": None,
            "last_ana_candle_time": None,
            "_last_ai_scan_config_sig": None,
            "last_recommendation": None,
            "pending_pullback_order": None,
            "daily_limit_locked_date": None,
            "daily_limit_reason": "",
            "daily_limit_locked_kind": None,
            "daily_limit_locked_threshold": 0.0,
            "cached_daily_pnl": 0.0,
            "cached_win_rate": 0.0,
            "cached_win_count": 0,
            "cached_loss_count": 0,
            "cached_be_count": 0,
            "cached_closed_count": 0,
            "consec_loss_count": 0,
            "cooldown_until": None,
            "last_cooldown_deal_ticket": None,
            "basket_cooldown_until": None,
            "fast_sl_cooldown_until": None,
            "last_fast_sl_deal_ticket": None,
            "is_basket_closing": False,
            "cached_scenarios": None,
            "cached_mean_df": None,
            "cached_action": None,
            "cached_sl": None,
            "cached_tp": None,
            "cached_df_hist": None,
            "cached_df_analysis": None,
            "user_zoomed": False,
            "saved_xlim": None,
            "saved_ylim": None,
            "_last_detail_prefix": None,
            "_sig_title": f"⏳ ĐANG TẢI DỮ LIỆU & PHÂN TÍCH AI ({sym_default})...",
            "_sig_color": GOLD_ACCENT,
        }

    def _ui_after(self, delay_ms: int, fn, *args):
        """Gọi hàm cập nhật giao diện an toàn từ mọi luồng (Thread-Safe)."""
        try:
            if threading.current_thread() is threading.main_thread() and delay_ms == 0:
                fn(*args)
            else:
                self.root.after(delay_ms, fn, *args)
        except Exception:
            pass

    def _get_symbol_tolerances(self, symbol: str, tick=None) -> dict:
        """
        Trả về bộ ngưỡng đệm kỹ thuật tự động co giãn chuẩn xác theo từng mã giao dịch (XAUUSDm vs BTCUSDm):
        - Vàng (XAUUSDm ~ 4,670$, spread ~ 0.16$): bước nhảy Trailing 0.40$, đệm dời SL 0.50$, dung sai bằng nhau 0.05$.
        - Bitcoin (BTCUSDm ~ 75,860$, spread ~ 6.40$): bước nhảy Trailing >= 5.0$ (hoặc 0.8x Spread), đệm dời SL >= 8.0$ (hoặc 1.2x Spread),
          giúp BTCUSD không bị spam lệnh sửa SL mỗi giây hoặc bị MT5 từ chối vì Stops Level.
        """
        spread = 0.0
        stops_dist = 0.0
        try:
            if MT5_AVAILABLE:
                with self._mt5_lock:
                    if tick is None and symbol:
                        tick = mt5.symbol_info_tick(symbol)
                    if tick is not None and tick.ask > 0 and tick.bid > 0:
                        spread = max(float(tick.ask - tick.bid), 0.0)
                    if symbol:
                        s_info = mt5.symbol_info(symbol)
                        if s_info is not None:
                            stops_lvl = getattr(s_info, "trade_stops_level", 0) or 0
                            pt = getattr(s_info, "point", 0.01) or 0.01
                            stops_dist = float(stops_lvl) * float(pt)
        except Exception:
            pass

        return symbol_tolerances(symbol, spread, stops_dist)

    def _get_profile_cfg(self, profile_key: str = None) -> dict:
        """Lấy thông số cài đặt độc lập của Tab được chỉ định (an toàn đa luồng, không bao giờ trộn thông số giữa XAUUSD và BTCUSD)."""
        pk = profile_key or self.active_profile_key
        if (
            pk == self.active_profile_key
            and not self._loading_settings
            and hasattr(self, "symbol_var")
            and threading.current_thread() is threading.main_thread()
        ):
            try:
                smap = self._get_settings_vars_map()
                cfg = {k: v.get() for k, v in smap.items()}
                self.profile_settings[pk] = dict(cfg)
                return dict(cfg)
            except Exception:
                pass
        return dict(self.profile_settings.get(pk, DEFAULT_PROFILE_SETTINGS.get(pk, {})))

    def _sync_self_to_active_ctx(self, include_ai_state: bool = False):
        """
        Lưu trạng thái giao diện và thông số của Tab đang mở vào bộ nhớ riêng của Tab đó.
        Mặc định KHÔNG ghi đè ngược các thuộc tính do luồng AI quản lý (AI_MANAGED_STATE_ATTRS) để tránh xung đột với luồng quét AI ngầm.
        """
        if self._loading_settings:
            return
        pk = self.active_profile_key
        ctx = self.profile_ctx[pk]
        for attr in PROFILE_STATE_ATTRS:
            if not include_ai_state and attr in AI_MANAGED_STATE_ATTRS:
                continue
            ctx[attr] = getattr(self, attr, None)
        if hasattr(self, "symbol_var") and threading.current_thread() is threading.main_thread():
            smap = self._get_settings_vars_map()
            self.profile_settings[pk] = {k: v.get() for k, v in smap.items()}

    def _update_forecast_tab_header(self):
        """Cập nhật thanh tiêu đề Bảng 6 nến dự báo AI tích hợp trong từng Tab (XAUUSD / BTCUSD)."""
        for pk_i in ("XAUUSD", "BTCUSD"):
            cfg = self.profile_settings.get(pk_i, DEFAULT_PROFILE_SETTINGS.get(pk_i, {}))
            sym = str(cfg.get("symbol", pk_i)).strip()
            ana_tf = str(cfg.get("analysis_tf", cfg.get("tf", "M5"))).strip()
            scalp_tf = str(cfg.get("tf", "M5")).strip()
            tf_desc = f"Khung {ana_tf}" if ana_tf == scalp_tf else f"AI {ana_tf} -> Scalp {scalp_tf}"
            try:
                lbl = getattr(self, "pred_title_by_profile", {}).get(pk_i)
                if lbl is not None and lbl.winfo_exists():
                    lbl.config(
                        text=f"📌 6 NẾN DỰ BÁO AI: {sym} ({tf_desc})",
                        fg=GOLD_ACCENT if pk_i == "XAUUSD" else CYAN_LIVE
                    )
            except Exception:
                pass
        pk = self.active_profile_key
        if pk in getattr(self, "pred_tree_by_profile", {}):
            self.tree = self.pred_tree_by_profile[pk]
        if pk in getattr(self, "log_text_by_profile", {}):
            self.log_text = self.log_text_by_profile[pk]

    def _sync_active_ctx_to_self(self):
        """Nạp toàn bộ trạng thái, thông số Menu, công tắc AUTO và bảng lệnh của Tab đang chọn lên giao diện."""
        pk = self.active_profile_key
        ctx = self.profile_ctx[pk]
        for attr in PROFILE_STATE_ATTRS:
            setattr(self, attr, ctx.get(attr))
        self._loading_settings = True
        try:
            smap = self._get_settings_vars_map()
            prof_cfg = self.profile_settings.get(pk, DEFAULT_PROFILE_SETTINGS[pk])
            for k, var in smap.items():
                if k in prof_cfg:
                    var.set(prof_cfg[k])
            self.auto_trade_var.set(bool(self.auto_loop_running))
        finally:
            self._loading_settings = False
        if pk in self.pos_tree_by_profile:
            self.pos_tree = self.pos_tree_by_profile[pk]
        if pk in self.pos_summary_lbl_by_profile:
            self.pos_summary_lbl = self.pos_summary_lbl_by_profile[pk]
        if hasattr(self, "drawer_title_lbl"):
            self.drawer_title_lbl.config(text=f"BẢNG ĐIỀU KHIỂN & CÀI ĐẶT ({pk})")
        self._update_forecast_tab_header()

    def _switch_active_profile(self, target_key: str):
        """Chuyển đổi dứt khoát giữa Tab XAUUSD và Tab BTCUSD: của Tab nào chỉ hiển thị đúng Tab đó."""
        if target_key not in ("XAUUSD", "BTCUSD"):
            return
        if target_key != self.active_profile_key:
            self._sync_self_to_active_ctx(include_ai_state=False)
            self.active_profile_key = target_key
            self._sync_active_ctx_to_self()
            self.save_user_settings()
        else:
            if target_key in self.pos_tree_by_profile:
                self.pos_tree = self.pos_tree_by_profile[target_key]
            if target_key in self.pos_summary_lbl_by_profile:
                self.pos_summary_lbl = self.pos_summary_lbl_by_profile[target_key]
            if hasattr(self, "drawer_title_lbl"):
                self.drawer_title_lbl.config(text=f"BẢNG ĐIỀU KHIỂN & CÀI ĐẶT ({target_key})")
            self._update_forecast_tab_header()

        # Cập nhật công tắc AUTO và nút Reload theo đúng trạng thái của Tab đang mở
        self._draw_toggle_switch()
        if self.is_analyzing:
            self._start_scan_progress_anim()
        else:
            self._scan_pct = 100.0
            self._scan_target_pct = 100.0
            self._draw_reload_pill()

        # Đồng bộ nút khung thời gian hiển thị trên thanh công cụ biểu đồ
        active_view_tf = getattr(self, "chart_view_tf_by_profile", {}).get(target_key, self.tf_var.get().strip())
        self._highlight_active_tf_btn(active_view_tf)

        # Cập nhật hộp tín hiệu (Signal Box) của Tab đang mở
        sig_title = self._sig_title or f"⏳ ĐANG TẢI DỮ LIỆU & PHÂN TÍCH AI ({self.symbol_var.get().strip()})..."
        sig_color = self._sig_color or GOLD_ACCENT
        self.signal_title_lbl.config(text=sig_title, fg=sig_color)
        if self._last_detail_prefix:
            self.signal_detail_lbl.config(text=self._last_detail_prefix)
        else:
            self.signal_detail_lbl.config(
                text="💡 Rê chuột: Crosshair | Lăn chuột: Co giãn nến | Kéo chuột: Di chuyển | Chuột phải / Nháy đúp: Reset."
            )
        self._update_bot_status_bar(target_key)

        # Cập nhật bảng 6 cây nến dự báo AI theo đúng Tab đang mở
        if hasattr(self, "tree"):
            for row in self.tree.get_children():
                self.tree.delete(row)
            if self.cached_mean_df is not None:
                for _, r in self.cached_mean_df.iterrows():
                    ts_str = r["timestamps"].strftime("%Y-%m-%d %H:%M")
                    self.tree.insert(
                        "", tk.END,
                        values=(ts_str, f"{r['open']:.2f}", f"{r['high']:.2f}", f"{r['low']:.2f}", f"{r['close']:.2f}")
                    )

        # Cập nhật ngay giá Live, bảng lệnh đang mở và thông tin nhanh cho Tab vừa chọn
        try:
            with self._mt5_lock:
                mt5_ready = bool(MT5_AVAILABLE and self._mt5_initialize())
            if mt5_ready:
                sym = self.symbol_var.get().strip()
                tf_s = self.tf_var.get().strip()
                with self._mt5_lock:
                    mt5.symbol_select(sym, True)
                    tick = mt5.symbol_info_tick(sym)
                pos_cnt, tot_prof = self._update_open_positions_and_manage(sym, profile_key=target_key)
                self._check_daily_profit_loss_limits(tot_prof, tick, profile_key=target_key)
                self._update_left_quick_info(sym, tf_s, tick, pos_cnt, tot_prof)
        except Exception:
            pass

        # Nếu Tab vừa chuyển sang chưa có dữ liệu nến/dự báo AI -> Đưa vào hàng đợi quét AI cho Tab đó
        if self.cached_df_hist is None and not self.is_analyzing:
            self.trigger_single_scan(profile_key=target_key)

    def _cleanup_workspace_junk(self):
        """Tự động dọn dẹp mọi thư mục đệm (__pycache__, .pytest_cache, file .pyc/.tmp/.log) nếu có phát sinh."""
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            for root_dir, dirs, files in os.walk(base_dir):
                if ".git" in root_dir:
                    continue
                for d in list(dirs):
                    if d in ("__pycache__", ".pytest_cache", ".mypy_cache"):
                        shutil.rmtree(os.path.join(root_dir, d), ignore_errors=True)
                for f in files:
                    if f.endswith((".pyc", ".pyo", ".tmp", ".log")):
                        try:
                            os.remove(os.path.join(root_dir, f))
                        except Exception:
                            pass
        except Exception:
            pass

    def _build_startup_loading_overlay(self):
        """Màn hình khởi động 'Đang tải dữ liệu' với vòng xoay kiểu MacBook và phần trăm Real-Time (0% -> 100%)."""
        self.loading_overlay = tk.Frame(self.root, bg="#0d1119")
        self.loading_overlay.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        self.loading_overlay.lift()

        center_box = tk.Frame(self.loading_overlay, bg="#0d1119", padx=40, pady=35)
        center_box.place(relx=0.5, rely=0.47, anchor="center")

        # Vòng xoay 12 nan hoa kiểu MacBook / macOS Activity Indicator
        self.spinner_canvas = tk.Canvas(center_box, width=116, height=116, bg="#0d1119", highlightthickness=0)
        self.spinner_canvas.pack(pady=(0, 14))

        cx, cy = 58, 58
        r_in, r_out = 26, 48
        self._spinner_lines = []
        for i in range(12):
            angle_rad = math.radians(i * 30 - 90)
            x1 = cx + r_in * math.cos(angle_rad)
            y1 = cy + r_in * math.sin(angle_rad)
            x2 = cx + r_out * math.cos(angle_rad)
            y2 = cy + r_out * math.sin(angle_rad)
            line_id = self.spinner_canvas.create_line(
                x1, y1, x2, y2, width=5, capstyle=tk.ROUND, fill="#252f42"
            )
            self._spinner_lines.append(line_id)

        # Hiển thị Phần trăm Real-Time ngay chính giữa bên dưới vòng xoay
        self.loading_pct_lbl = tk.Label(
            center_box, text="0%", bg="#0d1119", fg=GOLD_ACCENT,
            font=("Segoe UI", 22, "bold")
        )
        self.loading_pct_lbl.pack(pady=(0, 4))

        self.loading_title_lbl = tk.Label(
            center_box, text="Đang tải dữ liệu...", bg="#0d1119", fg=TEXT_MAIN,
            font=("Segoe UI", 13, "bold")
        )
        self.loading_title_lbl.pack(pady=(0, 6))

        self.loading_sub_lbl = tk.Label(
            center_box, text="Đang khởi tạo kết nối MetaTrader 5 & Mô hình AI...",
            bg="#0d1119", fg="#8b98a9", font=("Segoe UI", 9)
        )
        self.loading_sub_lbl.pack(pady=(0, 14))

        # Thanh tiến trình mượt mà bên dưới
        self.loading_bar_canvas = tk.Canvas(
            center_box, width=320, height=6, bg="#1b2333", highlightthickness=0
        )
        self.loading_bar_canvas.pack()
        self.loading_bar_fill = self.loading_bar_canvas.create_rectangle(
            0, 0, 0, 6, fill=GOLD_ACCENT, outline=""
        )

        self._animate_startup_loader()

    def _set_loading_stage(self, target_pct: float, sub_text: str):
        self._scan_target_pct = max(self._scan_target_pct, float(target_pct))
        if not self._is_initial_loading:
            return
        self._loading_target_pct = max(self._loading_target_pct, float(target_pct))
        self._ui_after(0, lambda: self.loading_sub_lbl.config(text=sub_text) if hasattr(self, "loading_sub_lbl") and self.loading_sub_lbl.winfo_exists() else None)

    @staticmethod
    def _draw_capsule(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, fill: str, outline: str = "", width: int = 1):
        """Vẽ hình viên thuốc bo tròn 2 đầu tuyệt đối mượt (Stadium/Pill) bằng 2 hình tròn + hình chữ nhật giữa."""
        h = max(y2 - y1, 2.0)
        r = h / 2.0
        if x2 - x1 < h:
            x2 = x1 + h
        if outline and width > 0:
            canvas.create_oval(x1, y1, x1 + h, y2, fill=fill, outline=outline, width=width)
            canvas.create_oval(x2 - h, y1, x2, y2, fill=fill, outline=outline, width=width)
            canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline="")
            canvas.create_line(x1 + r, y1, x2 - r, y1, fill=outline, width=width)
            canvas.create_line(x1 + r, y2, x2 - r, y2, fill=outline, width=width)
        else:
            canvas.create_oval(x1, y1, x1 + h, y2, fill=fill, outline="")
            canvas.create_oval(x2 - h, y1, x2, y2, fill=fill, outline="")
            canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline="")

    def _draw_reload_pill(self):
        """Vẽ nút Reload dạng viên thuốc bo tròn kèm thanh tiến trình và % trực quan."""
        if not hasattr(self, "reload_canvas") or not self.reload_canvas.winfo_exists():
            return
        c = self.reload_canvas
        c.delete("all")
        w, h = 118, 32
        x1, y1, x2, y2 = 2, 2, w - 2, h - 2

        if self.is_analyzing:
            self._draw_capsule(c, x1, y1, x2, y2, fill="#161d2a", outline="#f0b90b", width=1)
            pct = min(max(self._scan_pct, 4.0), 100.0)
            fill_w = max((x2 - x1 - 4) * (pct / 100.0), y2 - y1 - 4)
            self._draw_capsule(c, x1 + 2, y1 + 2, min(x1 + 2 + fill_w, x2 - 2), y2 - 2, fill="#264653", outline="")
            c.create_text(
                w / 2, h / 2,
                text=f"⏳ {int(round(pct))}%",
                fill=GOLD_ACCENT,
                font=("Segoe UI", 9, "bold")
            )
        else:
            bg_col = "#243044"
            self._draw_capsule(c, x1, y1, x2, y2, fill=bg_col, outline="#3d4f6d", width=1)
            c.create_text(
                w / 2, h / 2,
                text="✓ 100%",
                fill="#10b981",
                font=("Segoe UI", 9, "bold")
            )

    def _start_scan_progress_anim(self):
        self._scan_pct = 8.0
        self._scan_target_pct = 24.0
        if self._scan_anim_job is not None:
            try:
                self.root.after_cancel(self._scan_anim_job)
            except Exception:
                pass
            self._scan_anim_job = None
        self._draw_reload_pill()
        self._step_scan_progress_anim()

    def _step_scan_progress_anim(self):
        if not self.is_analyzing:
            return
        if self._scan_pct < self._scan_target_pct:
            diff = self._scan_target_pct - self._scan_pct
            self._scan_pct = min(self._scan_target_pct, self._scan_pct + max(0.8, diff * 0.22))
        elif self._scan_pct < 95.0:
            self._scan_pct = min(95.0, self._scan_pct + 0.25)
        self._draw_reload_pill()
        self._scan_anim_job = self.root.after(60, self._step_scan_progress_anim)

    def _finish_scan_progress_anim(self):
        if self._scan_anim_job is not None:
            try:
                self.root.after_cancel(self._scan_anim_job)
            except Exception:
                pass
            self._scan_anim_job = None
        self._scan_pct = 100.0
        self._scan_target_pct = 100.0
        self._draw_reload_pill()

    @staticmethod
    def _lerp_hex_color(c1_hex: str, c2_hex: str, t: float) -> str:
        """Nội suy màu mượt mà giữa 2 mã HEX (#RRGGBB)."""
        t = max(0.0, min(1.0, float(t)))
        r1, g1, b1 = int(c1_hex[1:3], 16), int(c1_hex[3:5], 16), int(c1_hex[5:7], 16)
        r2, g2, b2 = int(c2_hex[1:3], 16), int(c2_hex[3:5], 16), int(c2_hex[5:7], 16)
        r = int(r1 + (r2 - r1) * t)
        g = int(g1 + (g2 - g1) * t)
        b = int(b1 + (b2 - b1) * t)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _draw_toggle_switch(self, progress: float = None):
        """Vẽ công tắc gạt (Toggle Switch) bo tròn mềm mại cho START / STOP với thanh trượt mượt mà (0.0 = STOPPED, 1.0 = RUNNING)."""
        if not hasattr(self, "toggle_switch_canvas") or not self.toggle_switch_canvas.winfo_exists():
            return
        c = self.toggle_switch_canvas
        c.delete("all")
        w, h = 118, 32
        x1, y1, x2, y2 = 2, 2, w - 2, h - 2
        knob_d = (y2 - y1) - 6

        if progress is None:
            progress = 1.0 if self.auto_loop_running else 0.0
        self._toggle_progress = progress

        # Tọa độ giới hạn của nút tròn di chuyển
        kx1_min = x1 + 3
        kx1_max = (x2 - 3) - knob_d
        kx1 = kx1_min + progress * (kx1_max - kx1_min)
        kx2 = kx1 + knob_d

        # Màu sắc chuyển dịch mượt mà giữa STOPPED (#222a3a / #f6465d) và RUNNING (#0ecb81 / #17e896)
        bg_col = self._lerp_hex_color("#222a3a", "#0ecb81", progress)
        border_col = self._lerp_hex_color("#f6465d", "#17e896", progress)
        knob_col = self._lerp_hex_color("#f6465d", "#ffffff", progress)

        self._draw_capsule(c, x1, y1, x2, y2, fill=bg_col, outline=border_col, width=1)
        c.create_oval(kx1, y1 + 3, kx2, y2 - 3, fill=knob_col, outline="")

        if progress >= 0.5:
            c.create_text(
                (x1 + kx1) / 2 + 4, h / 2,
                text="RUNNING",
                fill="#071a12",
                font=("Segoe UI", 8, "bold")
            )
        else:
            c.create_text(
                (kx2 + x2) / 2 - 2, h / 2,
                text="STOPPED",
                fill="#f6465d",
                font=("Segoe UI", 8, "bold")
            )

    def _animate_toggle_switch(self, target_on: bool, step: int = 1, total_steps: int = 6):
        """Hiệu ứng gạt công tắc trượt mượt mà (Smooth Glide Animation ~60ms) chuẩn cảm ứng 60FPS."""
        if not hasattr(self, "toggle_switch_canvas") or not self.toggle_switch_canvas.winfo_exists():
            return
        t = step / float(total_steps)
        # Cosine ease-in-out curve
        ease_t = 0.5 * (1.0 - math.cos(math.pi * t))
        prog = ease_t if target_on else (1.0 - ease_t)
        self._draw_toggle_switch(progress=prog)
        if step < total_steps:
            self.root.after(10, self._animate_toggle_switch, target_on, step + 1, total_steps)
        else:
            self._draw_toggle_switch(progress=1.0 if target_on else 0.0)

    def _animate_startup_loader(self):
        if not self._is_initial_loading or not hasattr(self, "loading_overlay") or not self.loading_overlay.winfo_exists():
            return

        # 1. Cập nhật hiệu ứng xoay tròn 12 nan hoa kiểu MacBook
        palette = [
            "#ffffff", "#ffe082", "#f0b90b", "#d4a10a",
            "#b3860b", "#8f6b0d", "#6e5310", "#523f14",
            "#3c3018", "#2d261c", "#23201d", "#1c1b1b"
        ]
        for idx, line_id in enumerate(self._spinner_lines):
            color_idx = (self._spinner_step - idx) % 12
            self.spinner_canvas.itemconfig(line_id, fill=palette[color_idx])
        self._spinner_step = (self._spinner_step + 1) % 12

        # 2. Tăng phần trăm Real-Time mượt mà hướng tới _loading_target_pct
        if self._loading_cur_pct < self._loading_target_pct:
            diff = self._loading_target_pct - self._loading_cur_pct
            step = max(0.7, diff * 0.18) if diff > 3.0 else 0.45
            self._loading_cur_pct = min(self._loading_target_pct, self._loading_cur_pct + step)
        elif self._loading_cur_pct < 95.0:
            # Nhích nhẹ khi đang đợi mô hình AI tính toán kịch bản
            self._loading_cur_pct = min(95.0, self._loading_cur_pct + 0.15)

        pct_int = int(round(self._loading_cur_pct))
        self.loading_pct_lbl.config(text=f"{pct_int}%")
        bar_w = int(320 * (min(max(self._loading_cur_pct, 0.0), 100.0) / 100.0))
        self.loading_bar_canvas.coords(self.loading_bar_fill, 0, 0, bar_w, 6)

        self._loading_anim_job = self.root.after(50, self._animate_startup_loader)

    def _finish_startup_loading(self):
        """Đưa phần trăm lên 100% và đóng màn hình Loading để vào thẳng giao diện chính."""
        if not self._is_initial_loading:
            return
        self._is_initial_loading = False
        if self._loading_anim_job is not None:
            try:
                self.root.after_cancel(self._loading_anim_job)
            except Exception:
                pass
            self._loading_anim_job = None

        if hasattr(self, "loading_overlay") and self.loading_overlay.winfo_exists():
            try:
                self.loading_pct_lbl.config(text="100%")
                self.loading_title_lbl.config(text="Hoàn tất dữ liệu!")
                self.loading_sub_lbl.config(text="Đang mở giao diện biểu đồ Real-Time...")
                self.loading_bar_canvas.coords(self.loading_bar_fill, 0, 0, 320, 6)
            except Exception:
                pass
            self.root.after(220, lambda: self.loading_overlay.destroy() if hasattr(self, "loading_overlay") and self.loading_overlay.winfo_exists() else None)

    def _get_centered_geometry(self, win_w: int = 1450, win_h: int = 880) -> str:
        """Tính toán tọa độ để cửa sổ ứng dụng luôn nằm chính giữa màn hình khi khởi động."""
        try:
            self.root.update_idletasks()
            rect = (ctypes.c_long * 4)()
            if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):
                x0, y0, x1, y1 = int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])
                work_w = max(x1 - x0, win_w)
                work_h = max(y1 - y0, win_h)
                pos_x = x0 + max(0, (work_w - win_w) // 2)
                pos_y = y0 + max(0, (work_h - win_h) // 2)
                return f"{win_w}x{win_h}+{pos_x}+{pos_y}"
        except Exception:
            pass
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        pos_x = max(0, (sw - win_w) // 2)
        pos_y = max(0, (sh - 40 - win_h) // 2)
        return f"{win_w}x{win_h}+{pos_x}+{pos_y}"

    def _apply_window_and_taskbar_icon(self):
        """Gán biểu tượng Cá Voi viền tròn trong suốt, căn giữa màn hình và kích hoạt thanh tiêu đề MacBook."""
        try:
            centered_geom = self._get_centered_geometry(1450, 880)
            self._win_normal_geom = centered_geom
            self.root.geometry(centered_geom)
            app_dir = os.path.dirname(os.path.abspath(__file__))
            candidate_icos = [
                os.path.join(app_dir, "license-key-icon.ico"),
                os.path.join(app_dir, "tools", "license-key-icon.ico"),
                os.path.join(app_dir, "tools", "app_icon.ico"),
                ICON_ICO_FILE
            ]
            for ico_p in candidate_icos:
                if os.path.exists(ico_p):
                    self.root.iconbitmap(default=ico_p)
                    self.root.wm_iconbitmap(ico_p)
                    break
            if os.path.exists(ICON_PNG_FILE):
                self._app_icon_img = tk.PhotoImage(file=ICON_PNG_FILE)
                self.root.iconphoto(True, self._app_icon_img)
        except Exception:
            pass
        self._apply_dark_titlebar()
        self.root.after(120, self._apply_dark_titlebar)
        self.root.bind("<Map>", self._on_window_map_restore)

    def _apply_dark_titlebar(self):
        """Ẩn thanh tiêu đề vuông [— □ ✕] mặc định của Windows để dùng 3 nút tròn Traffic Light kiểu MacBook, đồng thời giữ nguyên Icon dưới Taskbar."""
        try:
            self.root.overrideredirect(True)
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            GWL_EXSTYLE = -20
            WS_EX_APPWINDOW = 0x00040000
            WS_EX_TOOLWINDOW = 0x00000080
            SWP_FRAMECHANGED = 0x0020
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            new_ex_style = (ex_style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
            if ex_style != new_ex_style:
                ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_ex_style)
                ctypes.windll.user32.SetWindowPos(
                    hwnd, 0, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED
                )
        except Exception:
            pass

    def _on_window_map_restore(self, event=None):
        pass

    def _minimize_window(self):
        """Thu nhỏ cửa sổ xuống thanh Taskbar Windows êm ái khi bấm nút tròn màu Vàng kiểu MacBook mà không bị chớp giật viền trắng."""
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            # SW_MINIMIZE = 6 kích hoạt hiệu ứng thu nhỏ DWM tự nhiên của Windows, không gọi overrideredirect(False) để tránh làm vỡ/chớp cửa sổ
            ctypes.windll.user32.ShowWindow(hwnd, 6)
        except Exception:
            try:
                self.root.iconify()
            except Exception:
                pass

    def _toggle_maximize_window(self, event=None):
        """Phóng to toàn màn hình (chừa thanh Taskbar) hoặc thu hồi kích thước cũ khi bấm nút tròn màu Xanh lá kiểu MacBook."""
        try:
            if not getattr(self, "_win_maximized", False):
                self._win_normal_geom = self.root.geometry()
                rect = (ctypes.c_long * 4)()
                if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):
                    x0, y0, x1, y1 = rect[0], rect[1], rect[2], rect[3]
                    self.root.geometry(f"{x1 - x0}x{y1 - y0}+{x0}+{y0}")
                else:
                    sw = self.root.winfo_screenwidth()
                    sh = self.root.winfo_screenheight() - 42
                    self.root.geometry(f"{sw}x{sh}+0+0")
                self._win_maximized = True
            else:
                self.root.geometry(getattr(self, "_win_normal_geom", "1450x880+60+40"))
                self._win_maximized = False
            self._draw_mac_window_controls()
            self.root.after(100, self._init_right_paned_sash)
        except Exception:
            pass

    def _start_win_drag(self, event):
        if getattr(self, "_win_maximized", False):
            return
        self._win_drag_offset = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _on_win_drag(self, event):
        if getattr(self, "_win_maximized", False) or not getattr(self, "_win_drag_offset", None):
            return
        nx = event.x_root - self._win_drag_offset[0]
        ny = max(0, event.y_root - self._win_drag_offset[1])
        self.root.geometry(f"+{nx}+{ny}")

    def _draw_mac_window_controls(self):
        """Vẽ cụm 3 nút tròn Thu Phóng & Đóng chuẩn MacBook (Vàng: Thu nhỏ − | Xanh lá: Phóng to ⤢ | Đỏ: Đóng ×)."""
        if not hasattr(self, "mac_win_ctrl_canvas") or not self.mac_win_ctrl_canvas.winfo_exists():
            return
        c = self.mac_win_ctrl_canvas
        c.delete("all")
        w, h = 92, 28
        cy = h / 2.0
        r = 7.5
        hover = getattr(self, "_mac_ctrl_hover", False)

        centers = [
            (18.0, "#ffbd2e", "#dfa123", "MIN"),
            (46.0, "#28c840", "#1aab29", "MAX"),
            (74.0, "#ff5f57", "#e0443e", "CLOSE"),
        ]
        for cx, fill_col, border_col, kind in centers:
            c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=fill_col, outline=border_col, width=1)
            if hover:
                if kind == "MIN":
                    c.create_line(cx - 3.8, cy, cx + 3.8, cy, fill="#5a3e00", width=1.8, capstyle=tk.ROUND)
                elif kind == "MAX":
                    c.create_polygon(
                        cx - 3.6, cy - 3.6, cx + 1.2, cy - 3.6, cx - 3.6, cy + 1.2,
                        fill="#084d14", outline=""
                    )
                    c.create_polygon(
                        cx + 3.6, cy + 3.6, cx - 1.2, cy + 3.6, cx + 3.6, cy - 1.2,
                        fill="#084d14", outline=""
                    )
                elif kind == "CLOSE":
                    c.create_line(cx - 3.2, cy - 3.2, cx + 3.2, cy + 3.2, fill="#5a0004", width=1.8, capstyle=tk.ROUND)
                    c.create_line(cx - 3.2, cy + 3.2, cx + 3.2, cy - 3.2, fill="#5a0004", width=1.8, capstyle=tk.ROUND)

    def _on_mac_win_ctrl_click(self, event):
        x = event.x
        if x < 32:
            self._minimize_window()
        elif x < 60:
            self._toggle_maximize_window()
        else:
            self._on_close_window()

    def _update_license_badge_ui(self):
        """Cập nhật hiển thị số ngày bản quyền hoặc trạng thái trên thanh Header."""
        if not hasattr(self, "license_badge_btn") or not self.license_badge_btn.winfo_exists():
            return
        if not hasattr(self, "lic_mgr") or self.lic_mgr is None:
            return
        try:
            st = self.lic_mgr.check_status()
            b_text = st.get("badge_text", "[ Bản quyền ]")
            b_color = st.get("badge_color", "#38bdf8")
            self.license_badge_btn.config(
                text=b_text,
                fg=b_color,
                activeforeground=b_color,
            )
        except Exception:
            pass

    def _open_license_dialog(self):
        """Mở cửa sổ Kích Hoạt Bản Quyền Ứng Dụng để xem HWID hoặc nhập key gia hạn."""
        if not hasattr(self, "lic_mgr") or self.lic_mgr is None:
            try:
                from kronos_license import LicenseManager
                self.lic_mgr = LicenseManager()
            except Exception:
                return
        try:
            from kronos_license import show_activation_dialog
            show_activation_dialog(self.root, self.lic_mgr, is_startup_block=False)
            self._update_license_badge_ui()
        except Exception as e:
            self.log_error("OPEN_LICENSE_DIALOG", e)

    def _check_update_on_startup(self):
        """Tự động kiểm tra bản cập nhật mới trên GitHub Release khi khởi động ứng dụng."""
        if not hasattr(self, "updater") or self.updater is None:
            return

        def _on_res(res):
            if res.get("has_update"):
                self._latest_update_info = res
                self._ui_after(0, lambda: self._start_version_badge_blink(res.get("latest_version")))

        self.updater.check_update_async(_on_res)

    def _start_version_badge_blink(self, latest_ver: str):
        """Bật hiệu ứng nhấp nháy chữ trên nút phiên bản khi có bản cập nhật mới."""
        self._has_new_version = True
        self._new_version_tag = latest_ver
        self._version_blink_state = False
        self._run_version_badge_blink()

    def _run_version_badge_blink(self):
        if not getattr(self, "_has_new_version", False):
            return
        if not hasattr(self, "version_badge_btn") or not self.version_badge_btn.winfo_exists():
            return

        self._version_blink_state = not self._version_blink_state
        if self._version_blink_state:
            self.version_badge_btn.config(
                text=f"Có bản cập nhật v{self._new_version_tag}!",
                bg=PANEL_BG,
                fg="#fef08a",
                activebackground=PANEL_BG,
                activeforeground="#fef08a",
            )
        else:
            self.version_badge_btn.config(
                text=f"Có bản cập nhật v{self._new_version_tag}!",
                bg=PANEL_BG,
                fg="#f59e0b",
                activebackground=PANEL_BG,
                activeforeground="#fde047",
            )
        self._version_blink_after_id = self.root.after(600, self._run_version_badge_blink)

    def _on_click_version_badge(self):
        """Mở cửa sổ giới thiệu tính năng của bản cập nhật mới hoặc kiểm tra phiên bản thủ công."""
        if not hasattr(self, "updater") or self.updater is None:
            try:
                from kronos_updater import AppUpdater, get_current_app_version
                self.app_version = get_current_app_version()
                self.updater = AppUpdater(self.app_version)
            except Exception:
                return

        if getattr(self, "_has_new_version", False) and getattr(self, "_latest_update_info", None):
            try:
                import importlib, sys
                if "kronos_updater" in sys.modules:
                    importlib.reload(sys.modules["kronos_updater"])
            except Exception:
                pass
            from kronos_updater import show_update_modal
            show_update_modal(self.root, self.updater, self._latest_update_info, on_complete=self._on_update_hot_applied)
        else:
            self.version_badge_btn.config(text="Đang kiểm tra...", fg="#38bdf8")

            def _on_checked(res):
                def _ui():
                    if res.get("has_update"):
                        self._latest_update_info = res
                        self._start_version_badge_blink(res.get("latest_version"))
                        try:
                            import importlib, sys
                            if "kronos_updater" in sys.modules:
                                importlib.reload(sys.modules["kronos_updater"])
                        except Exception:
                            pass
                        from kronos_updater import show_update_modal
                        show_update_modal(self.root, self.updater, res, on_complete=self._on_update_hot_applied)
                    else:
                        self.version_badge_btn.config(
                            text=f"Phiên bản mới nhất  v{self.app_version}  🔄",
                            bg=PANEL_BG,
                            fg="#10b981",
                        )
                        self.show_toast(f"Bạn đang sử dụng phiên bản mới nhất (v{self.app_version}).", is_error=False, duration_ms=2500)

                self._ui_after(0, _ui)

            self.updater.check_update_async(_on_checked)

    def _stop_version_badge_blink(self):
        """Dừng hiệu ứng nhấp nháy chữ trên nút phiên bản."""
        self._has_new_version = False
        after_id = getattr(self, "_version_blink_after_id", None)
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except Exception:
                pass
            self._version_blink_after_id = None

    def _setup_backtest_tab(self):
        """Khởi tạo tab Backtest nếu chưa có trong bottom_notebook."""
        if hasattr(self, "bottom_notebook") and not hasattr(self, "backtest_tab"):
            self.backtest_tab = tk.Frame(self.bottom_notebook, bg=PANEL_BG)
            self.bottom_notebook.add(self.backtest_tab, text="🔒 BACKTEST (ADMIN)")
            self._build_backtest_tab_ui(self.backtest_tab)

    def _on_update_hot_applied(self, success: bool, new_version: str = ""):
        """
        Xử lý ngay sau khi gói cập nhật OTA đã tải xong:
        - Nạp lại mã nguồn & động cơ mới vào RAM (hỗ trợ cả file .py lẫn khối nhị phân mã hóa core_engine.bin khi chạy từ Trading AI PRO.exe).
        - Tự động làm mới toàn bộ giao diện (UI), nạp lại cài đặt (Settings) và gọi lại Backend (MT5 + AI Scan + Auto Loop)
          để áp dụng 100% tính năng & thông số mới ngay lập tức mà không cần người dùng tắt/mở lại App thủ công.
        """
        if not success:
            return
        v_str = str(new_version or self.app_version).strip().lstrip("vV")
        self.app_version = v_str
        self._has_new_version = False
        self._latest_update_info = None
        self._stop_version_badge_blink()

        # 1. Lưu lại cài đặt hiện tại và ghi nhớ trạng thái AUTO của từng Tab (XAUUSD / BTCUSD) trước khi làm mới
        prev_active_pk = getattr(self, "active_profile_key", "XAUUSD")
        running_profiles = [
            pk for pk in ("XAUUSD", "BTCUSD")
            if self.profile_ctx.get(pk, {}).get("auto_loop_running", False)
            or (pk == prev_active_pk and getattr(self, "auto_loop_running", False))
        ]
        try:
            self.save_user_settings(show_feedback=False)
        except Exception:
            pass

        # Khởi động lại MƯỢT: ẩn cửa sổ ngay rồi mở tiến trình mới, bỏ bước dựng lại UI tại chỗ
        # (trước đây dựng lại UI rồi 300ms sau vẫn khởi động lại -> app chớp tắt nhiều lần)
        if getattr(self, "updater", None) is not None:
            try:
                self._app_closing = True
                self.auto_loop_running = False
                for pk in self.profile_ctx:
                    self.profile_ctx[pk]["auto_loop_running"] = False
                    self._auto_worker_token[pk] = self._auto_worker_token.get(pk, 0) + 1
                self.log(f"🎉 [CẬP NHẬT] Đã nâng cấp lên v{v_str}, đang khởi động lại ứng dụng...")
            except Exception:
                pass
            try:
                self.root.withdraw()
                self.root.update_idletasks()
            except Exception:
                pass
            try:
                if MT5_AVAILABLE and self._mt5_lock.acquire(timeout=3.0):
                    try:
                        mt5.shutdown()
                    finally:
                        self._mt5_lock.release()
            except Exception:
                pass
            try:
                self._release_active_instance_lock()
            except Exception:
                pass
            self.updater.restart_application()
            return

        # 2. Hot-Reload toàn bộ module phụ trợ & mã nguồn chính mới vào RAM (từ file .py hoặc giải mã core_engine.bin)
        try:
            import importlib
            import importlib.util
            import marshal
            import hashlib
            import types

            reloaded_mod = None
            app_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
            bot_py_candidates = [
                os.path.join(app_dir, "mt5_scalping_bot.py"),
                os.path.abspath(__file__),
            ]
            for cand_py in bot_py_candidates:
                if cand_py and os.path.exists(cand_py) and cand_py.lower().endswith(".py"):
                    try:
                        for sub_mod, sub_fn in (("kronos_license", "kronos_license.py"), ("kronos_updater", "kronos_updater.py")):
                            sub_p = os.path.join(os.path.dirname(cand_py), sub_fn)
                            if os.path.exists(sub_p):
                                s_spec = importlib.util.spec_from_file_location(sub_mod, sub_p)
                                s_mod = importlib.util.module_from_spec(s_spec)
                                s_spec.loader.exec_module(s_mod)
                                sys.modules[sub_mod] = s_mod
                        spec = importlib.util.spec_from_file_location("mt5_scalping_bot_reloaded", cand_py)
                        reloaded_mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(reloaded_mod)
                        break
                    except Exception:
                        reloaded_mod = None

            # Dự phòng giải mã trực tiếp từ core_engine.bin trong RAM nếu chạy bản .exe đóng gói không có file .py
            if reloaded_mod is None:
                bin_path = os.path.join(app_dir, "core_engine.bin")
                if os.path.exists(bin_path):
                    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
                    salt = b"TRADING_AI_PRO_KHA_TRAN_DESIGN_IMMUTABLE_PROPRIETARY_SALT_2026"
                    eng_key = hashlib.sha256(b"KHA_TRAN_TRADING_ALGORITHM_AES_KEY_V1" + salt).digest()
                    aad_hdr = b"TRADING_AI_PRO_ENCRYPTED_BINARY_CONTAINER_V1"
                    with open(bin_path, "rb") as f_bin:
                        raw_bin = f_bin.read()
                    container_bytes = AESGCM(eng_key).decrypt(raw_bin[:12], raw_bin[12:], aad_hdr)
                    raw_dict = marshal.loads(container_bytes)
                    for sub_m in ("kronos_license", "kronos_updater"):
                        if sub_m in raw_dict:
                            m_obj = types.ModuleType(sub_m)
                            exec(marshal.loads(raw_dict[sub_m]), m_obj.__dict__)
                            sys.modules[sub_m] = m_obj
                    if "mt5_scalping_bot" in raw_dict:
                        reloaded_mod = types.ModuleType("mt5_scalping_bot_reloaded")
                        exec(marshal.loads(raw_dict["mt5_scalping_bot"]), reloaded_mod.__dict__)

            if reloaded_mod is not None:
                cur_mod = sys.modules.get(__name__)
                for const_name in (
                    "DEFAULT_PROFILE_SETTINGS",
                    "PARAM_HELP_GUIDE",
                    "PROFILE_STATE_ATTRS",
                    "AI_MANAGED_STATE_ATTRS",
                ):
                    if hasattr(reloaded_mod, const_name):
                        val_const = getattr(reloaded_mod, const_name)
                        globals()[const_name] = val_const
                        if cur_mod is not None:
                            setattr(cur_mod, const_name, val_const)

                if hasattr(reloaded_mod, "KronosDesktopApp"):
                    new_cls = reloaded_mod.KronosDesktopApp
                    for k, v in new_cls.__dict__.items():
                        if callable(v) or isinstance(v, property):
                            setattr(KronosDesktopApp, k, v)
                            if cur_mod is not None and hasattr(cur_mod, "KronosDesktopApp"):
                                setattr(getattr(cur_mod, "KronosDesktopApp"), k, v)
        except Exception as reload_err:
            self.log(f"⚠️ [OTA HOT-PATCH] Nạp phương thức mới: {reload_err}")

        # 3. Khởi tạo bổ sung các thuộc tính mới (nếu phiên bản mới vừa thêm vào __init__)
        default_new_attrs = {
            "show_ai_chart_overlay": True,
            "_chart_pred_gap": 0,
            "pos_tree_by_profile": {},
            "pos_summary_lbl_by_profile": {},
            "pred_tree_by_profile": {},
            "pred_title_by_profile": {},
            "log_text_by_profile": {},
            "side_drawer_by_profile": {},
            "side_paned_by_profile": {},
            "side_sub_frames_by_profile": {},
            "side_mode_btns_by_profile": {},
            "side_toggle_btn_by_profile": {},
            "chart_view_tf_by_profile": {"XAUUSD": "M5", "BTCUSD": "M5"},
        }
        for attr_k, attr_def in default_new_attrs.items():
            if not hasattr(self, attr_k) or isinstance(attr_def, dict):
                setattr(self, attr_k, dict(attr_def) if isinstance(attr_def, dict) else attr_def)

        # 4. Dựng lại toàn bộ Giao diện (UI) tại chỗ và nạp lại Cài đặt mới (load_user_settings)
        try:
            for child in list(self.root.winfo_children()):
                try:
                    child.destroy()
                except Exception:
                    pass
            self._setup_styles()
            self._build_ui()
            self.load_user_settings()
            self._bind_auto_save_settings()
        except Exception as ui_err:
            self.log(f"⚠️ [OTA UI REBUILD] Làm mới giao diện: {ui_err}")

        # 5. Cập nhật nhãn phiên bản mới nhất trên Header & Thanh tiêu đề
        if hasattr(self, "version_badge_btn") and self.version_badge_btn.winfo_exists():
            self.version_badge_btn.config(
                text=f"Phiên bản mới nhất  v{v_str}  ✓",
                bg=PANEL_BG,
                fg="#10b981",
            )
        try:
            self.root.title(f"Trading AI PRO — MT5 Bot Auto Scalping | v{v_str}")
        except Exception:
            pass

        # 6. Tự động Request lại toàn bộ Backend: Kết nối MT5, Bản quyền, Khởi động lại luồng AUTO bằng code mới & Quét lại AI ngay lập tức!
        try:
            if hasattr(self, "updater") and self.updater is not None:
                self.updater.current_version = v_str
            self.check_mt5_connection()
            self._update_license_badge_ui()

            # Làm mới toàn bộ các luồng Background Worker sang mã nguồn v_str mới
            for pk in ("XAUUSD", "BTCUSD"):
                self._auto_worker_token[pk] = self._auto_worker_token.get(pk, 0) + 1
                if pk in running_profiles:
                    self.profile_ctx[pk]["auto_loop_running"] = False
                    self.start_auto_scan(profile_key=pk)

            # Xóa cache chữ ký biểu đồ cũ và lập tức gửi Request cho Backend AI phân tích lại + vẽ lại biểu đồ mới
            self._last_drawn_chart_key = None
            self._last_drawn_pos_sig = None
            self.trigger_single_scan(profile_key=self.active_profile_key)
            for pk in running_profiles:
                if pk != self.active_profile_key:
                    self.trigger_single_scan(profile_key=pk)
            self._refresh_realtime_chart(force_full_redraw=True)
            self.show_toast(
                f"✓ Đã nâng cấp v{v_str}, nạp lại Cài đặt & Request Backend AI thành công!",
                is_error=False,
                duration_ms=2500
            )
            self.log(
                f"🎉 [CẬP NHẬT & ĐỒNG BỘ BACKEND] Đã nâng cấp lên v{v_str}, tự động nạp lại cài đặt mới và khởi chạy lại dịch vụ nền."
            )
            # Tự động khởi chạy lại tiến trình mới sạch sẽ sau 300ms để đảm bảo 100% core_engine.bin và các luồng ngầm chạy mã mới nhất
            if hasattr(self, "updater") and self.updater is not None:
                self.root.after(300, self.updater.restart_application)
        except Exception as be_err:
            self.log(f"⚠️ [OTA BACKEND RELOAD] Khởi tạo lại dịch vụ nền: {be_err}")
            if hasattr(self, "updater") and self.updater is not None:
                self.root.after(300, self.updater.restart_application)

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=BG_DARK)
        style.configure("Panel.TFrame", background=PANEL_BG)
        style.configure("TLabel", background=PANEL_BG, foreground=TEXT_MAIN, font=("Segoe UI", 9))
        style.configure("Treeview", background="#131822", foreground=TEXT_MAIN, fieldbackground="#131822", rowheight=23, font=("Consolas", 9))
        style.configure(
            "Treeview.Heading",
            background="#1e293b",
            foreground=GOLD_ACCENT,
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            borderwidth=0,
            lightcolor="#1e293b",
            darkcolor="#1e293b",
            bordercolor="#131822",
        )
        style.map(
            "Treeview.Heading",
            background=[("pressed", "#1e293b"), ("active", "#1e293b")],
            foreground=[("pressed", GOLD_ACCENT), ("active", GOLD_ACCENT)],
            relief=[("pressed", "flat"), ("active", "flat")],
        )
        style.configure("TNotebook", background=BG_DARK, borderwidth=0, tabmargins=[2, 2, 2, 0])
        style.configure(
            "TNotebook.Tab",
            background="#1a2230",
            foreground="#8b98a9",
            padding=[14, 5],
            font=("Segoe UI", 9, "bold"),
            borderwidth=1
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", "#0284c7"), ("active", "#28354a")],
            foreground=[("selected", "#ffffff"), ("active", "#ffffff")],
            padding=[("selected", [14, 5]), ("!selected", [14, 5])],
            expand=[("selected", [0, 0, 0, 0]), ("!selected", [0, 0, 0, 0])]
        )

    def _build_ui(self):
        # =====================================================================
        # 1. THANH TIÊU ĐỀ KIỂU MACBOOK: NÚT ☰ BÊN TRÁI, ĐỒNG HỒ GIỮA, 3 NÚT TRÒN THU PHÓNG MACBOOK BÊN PHẢI
        # =====================================================================
        top_bar = tk.Frame(self.root, bg=PANEL_BG, pady=4, padx=10, highlightthickness=1, highlightbackground="#252f42")
        top_bar.pack(fill=tk.X, padx=8, pady=(6, 4))
        top_bar.bind("<ButtonPress-1>", self._start_win_drag)
        top_bar.bind("<B1-Motion>", self._on_win_drag)
        top_bar.bind("<Double-Button-1>", self._toggle_maximize_window)

        self.menu_toggle_btn = tk.Button(
            top_bar, text="☰", bg="#252f42", fg="#ffffff",
            activebackground="#32415c", activeforeground="#ffffff",
            font=("Segoe UI", 12, "bold"), relief=tk.FLAT, width=3, pady=0, cursor="hand2",
            command=self.toggle_left_menu
        )
        self.menu_toggle_btn.pack(side=tk.LEFT, padx=(0, 8))

        app_title_lbl = tk.Label(
            top_bar, text="Trading AI PRO by Kha Tran Design",
            bg=PANEL_BG, fg=GOLD_ACCENT, font=("Segoe UI", 9, "bold")
        )
        app_title_lbl.pack(side=tk.LEFT, padx=(0, 8))
        app_title_lbl.bind("<ButtonPress-1>", self._start_win_drag)
        app_title_lbl.bind("<B1-Motion>", self._on_win_drag)
        app_title_lbl.bind("<Double-Button-1>", self._toggle_maximize_window)

        # Nút Huy hiệu Phiên bản & Kiểm tra cập nhật (Chữ màu xanh lá, không nền)
        self.version_badge_btn = tk.Button(
            top_bar,
            text=f"Phiên bản mới nhất  v{self.app_version}  🔄",
            bg=PANEL_BG,
            fg="#10b981",
            activebackground=PANEL_BG,
            activeforeground="#34d399",
            font=("Segoe UI", 8, "bold"),
            relief=tk.FLAT,
            bd=0,
            padx=4,
            pady=0,
            cursor="hand2",
            command=self._on_click_version_badge,
        )
        self.version_badge_btn.pack(side=tk.LEFT, padx=(0, 10))
        self.version_badge_btn.bind(
            "<Enter>",
            lambda e: self.version_badge_btn.config(fg="#34d399")
            if not getattr(self, "_has_new_version", False)
            else None
        )
        self.version_badge_btn.bind(
            "<Leave>",
            lambda e: self.version_badge_btn.config(fg="#10b981")
            if not getattr(self, "_has_new_version", False)
            else None
        )

        # Cụm 3 nút tròn Thu Phóng & Đóng cửa sổ chuẩn MacBook ở góc trên bên phải (Thay cho — □ ✕ vuông của Windows)
        self.mac_win_ctrl_canvas = tk.Canvas(
            top_bar, width=92, height=28, bg=PANEL_BG, highlightthickness=0, cursor="hand2"
        )
        self.mac_win_ctrl_canvas.pack(side=tk.RIGHT, padx=(8, 0))
        self.mac_win_ctrl_canvas.bind("<Enter>", lambda e: (setattr(self, "_mac_ctrl_hover", True), self._draw_mac_window_controls()))
        self.mac_win_ctrl_canvas.bind("<Leave>", lambda e: (setattr(self, "_mac_ctrl_hover", False), self._draw_mac_window_controls()))
        self.mac_win_ctrl_canvas.bind("<Button-1>", self._on_mac_win_ctrl_click)
        self._draw_mac_window_controls()

        # Nút Huy hiệu Bản quyền nằm ở góc phải ngay cạnh cụm nút thu phóng cửa sổ
        self.license_badge_btn = tk.Button(
            top_bar,
            text="[ Bản quyền ]",
            bg="#111827",
            fg="#38bdf8",
            activebackground="#1e293b",
            activeforeground="#38bdf8",
            font=("Segoe UI", 9, "bold"),
            relief=tk.FLAT,
            padx=10,
            pady=2,
            cursor="hand2",
            command=self._open_license_dialog,
        )
        self.license_badge_btn.pack(side=tk.RIGHT, padx=(0, 10))
        self._update_license_badge_ui()

        # Căn giữa thông số đếm giờ & giá Live ngay chính giữa thanh trên cùng
        self.live_clock_lbl = tk.Label(
            top_bar, text="LIVE | Bid: --- | Ask: --- | Đóng nến: --:--",
            bg="#121722", fg=CYAN_LIVE, font=("Consolas", 9, "bold"), padx=14, pady=4
        )
        self.live_clock_lbl.pack(anchor="center")
        self.live_clock_lbl.bind("<ButtonPress-1>", self._start_win_drag)
        self.live_clock_lbl.bind("<B1-Motion>", self._on_win_drag)
        self.live_clock_lbl.bind("<Double-Button-1>", self._toggle_maximize_window)

        # KHUNG CHÍNH (RIGHT PANEL CHIẾM TOÀN MÀN HÌNH)
        self.main_frame = tk.Frame(self.root, bg=BG_DARK)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=2)
        main_frame = self.main_frame

        # =====================================================================
        # 2. CỘT PHẢI (HIỂN THỊ CỐ ĐỊNH 100% KHÔNG BỊ CO GIÃN KHI ĐÓNG/MỞ MENU)
        # =====================================================================
        self.right_panel = tk.Frame(main_frame, bg=BG_DARK)
        self.right_panel.pack(fill=tk.BOTH, expand=True)
        right_panel = self.right_panel

        # =====================================================================
        # 3. NGĂN KÉO MENU TRÁI (CHIỀU CAO BẰNG 100% CHIỀU CAO APP, NÚT ☰ NẰM TRONG TAB MENU)
        # =====================================================================
        self.left_panel = tk.Frame(
            self.root, bg=PANEL_BG, width=self.drawer_width, padx=10, pady=8,
            highlightthickness=1, highlightbackground="#384661"
        )
        self.left_panel.pack_propagate(False)
        # Đặt trực tiếp trên self.root với y=0, relheight=1.0 để chiều cao bằng đúng 100% chiều cao App!
        self.left_panel.place(x=self.drawer_x, y=0, relheight=1.0, width=self.drawer_width)
        left_panel = self.left_panel

        # THANH TIÊU ĐỀ TRÊN CÙNG CỦA MENU: CHỨA NÚT 3 GẠCH (☰) NẰM CHUNG TRONG TAB MENU
        drawer_header = tk.Frame(left_panel, bg=PANEL_BG)
        drawer_header.pack(fill=tk.X, pady=(0, 6))

        self.drawer_close_btn = tk.Button(
            drawer_header, text="☰", bg="#252f42", fg="#ffffff",
            activebackground="#32415c", activeforeground="#ffffff",
            font=("Segoe UI", 12, "bold"), relief=tk.FLAT, width=3, pady=0,
            command=self.toggle_left_menu
        )
        self.drawer_close_btn.pack(side=tk.LEFT, padx=(0, 8))

        # Ổ khóa bên phải tiêu đề: mặc định KHÓA (thông số mờ, không sửa được); bấm để mở khóa chỉnh thông số (chỉ giao diện)
        self._params_locked = True
        self._param_syncs = []
        self.params_lock_btn = tk.Button(
            drawer_header, text="🔒", bg="#252f42", fg=GOLD_ACCENT,
            activebackground="#32415c", activeforeground=GOLD_ACCENT,
            font=("Segoe UI", 12, "bold"), relief=tk.FLAT, width=3, pady=0, cursor="hand2",
            command=self.toggle_params_lock
        )
        self.params_lock_btn.pack(side=tk.RIGHT, padx=(8, 0))

        self.drawer_title_lbl = tk.Label(
            drawer_header, text="BẢNG ĐIỀU KHIỂN & CÀI ĐẶT (XAUUSD)",
            bg=PANEL_BG, fg=GOLD_ACCENT, font=("Segoe UI", 9, "bold"), anchor="w"
        )
        self.drawer_title_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # KHỐI ĐẦU MENU: KẾT NỐI MT5, CHART REAL-TIME & DÒNG HIỂN THỊ SỐ DƯ BÊN DƯỚI
        menu_top_card = tk.Frame(left_panel, bg="#121722", padx=8, pady=6, highlightthickness=1, highlightbackground="#263147")
        menu_top_card.pack(fill=tk.X, pady=(0, 6))

        mt5_row = tk.Frame(menu_top_card, bg="#121722")
        mt5_row.pack(fill=tk.X, pady=(0, 4))
        tk.Button(
            mt5_row, text="Kết nối MT5", bg="#2b3548", fg=TEXT_MAIN,
            font=("Segoe UI", 8, "bold"), relief=tk.FLAT, padx=10, pady=2,
            command=self.check_mt5_connection
        ).pack(side=tk.LEFT, padx=(0, 8))

        self.auto_trade_var = tk.BooleanVar(value=False)
        self.realtime_chart_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            mt5_row, text="Chart Real-Time",
            variable=self.realtime_chart_var, bg="#121722", fg=CYAN_LIVE, selectcolor=BG_DARK,
            activebackground="#121722", activeforeground=CYAN_LIVE,
            font=("Segoe UI", 8, "bold")
        ).pack(side=tk.RIGHT)

        # Dòng thông tin Số dư nằm riêng bên dưới chữ Kết nối MT5 (không có ô tick)
        self.mt5_status_lbl = tk.Label(
            menu_top_card, text="Đang kết nối MT5...", bg="#121722", fg=GOLD_ACCENT,
            font=("Segoe UI", 8, "bold"), anchor="w"
        )
        self.mt5_status_lbl.pack(fill=tk.X, pady=(2, 2))

        self.accordion_sections = []

        # Mục 1: 1. Cài đặt cơ bản (Mặc định thu gọn)
        sec1 = CollapsibleSection(
            left_panel, "1. Cài đặt cơ bản", initially_open=False,
            group_list=self.accordion_sections,
            on_reset=lambda: self.reset_section_settings(1)
        )
        self.symbol_var = self._add_labeled_entry(sec1.content, "Mã giao dịch MT5:", "XAUUSDm", help_key="symbol")
        self.analysis_tf_var = self._add_labeled_combo(sec1.content, "Khung phân tích AI:", ["M1", "M5", "M15", "H1"], "M15", help_key="analysis_tf")
        self.tf_var = self._add_labeled_combo(sec1.content, "Khung vào lệnh (Scalp):", ["M1", "M5", "M15", "H1"], "M5", help_key="tf")
        self.lot_var = self._add_labeled_entry(sec1.content, "Khối lượng (Lot):", "0.01", help_key="lot")
        self.max_pos_var = self._add_labeled_entry(sec1.content, "Số lệnh tối đa:", "3", help_key="max_pos")
        self.magic_number_var = self._add_labeled_entry(sec1.content, "Magic Number (Mã lệnh):", "20260501", help_key="magic_number")
        self.mt5_path_var = self._add_labeled_entry(sec1.content, "Đường dẫn MT5:", DEFAULT_MT5_PATH, entry_width=18, help_key="mt5_path")
        self.ai_threads_var = self._add_labeled_combo(sec1.content, "Số luồng AI (CPU):", AI_THREAD_CHOICES, "Auto", help_key="ai_threads")
        tk.Button(
            sec1.content, text="↺ Khôi phục mặc định Mục 1 (Cài đặt cơ bản)", bg="#161e2e", fg="#94a3b8",
            activebackground="#25334c", activeforeground=GOLD_ACCENT,
            font=("Segoe UI", 7, "bold"), relief=tk.FLAT, pady=2, cursor="hand2",
            command=lambda: self.reset_section_settings(1)
        ).pack(fill=tk.X, pady=(6, 2))

        # Mục 2: 2. Bộ lọc Entry & Nhồi lệnh (Mặc định thu gọn)
        sec2 = CollapsibleSection(
            left_panel, "2. Bộ lọc Entry & Nhồi lệnh", initially_open=False,
            group_list=self.accordion_sections,
            on_reset=lambda: self.reset_section_settings(2)
        )
        def _grp(parent, title):
            tk.Label(parent, text=f"─── {title} ───", bg=PANEL_BG, fg=GOLD_ACCENT,
                     font=("Segoe UI", 7, "bold"), pady=4).pack(fill=tk.X)

        # Nhóm 1: Tín hiệu AI
        _grp(sec2.content, "TÍN HIỆU AI")
        self.scenarios_var = self._add_labeled_entry(sec2.content, "Số kịch bản AI (Ensemble):", "8", help_key="scenarios")
        self.consensus_var = self._add_labeled_entry(sec2.content, "Độ đồng thuận tối thiểu (%):", "75", help_key="consensus")
        self.use_confidence_gate_var = tk.BooleanVar(value=False)
        self._add_checkbutton(sec2.content, "Cổng độ tin cậy AI (Confidence % ≥ Consensus)", self.use_confidence_gate_var, help_key="use_confidence_gate")
        self.max_dispersion_var = self._add_labeled_entry(sec2.content, "Độ phân tán tối đa (x ATR, 0=Tắt):", "2.5", help_key="max_dispersion")
        self.min_move_var = self._add_labeled_entry(sec2.content, "Biên độ tối thiểu (Giá):", "1.2", help_key="min_move")
        self.expected_move_k_var = self._add_labeled_entry(sec2.content, "Hệ số k biên độ bù phí (k*Spread):", "3", help_key="expected_move_k")

        # Nhóm 2: SL & TP
        _grp(sec2.content, "SL & TP")
        self.sl_atr_var = self._add_labeled_entry(sec2.content, "Khoảng cách SL AI (x ATR14):", "1.5", help_key="sl_atr")
        self.sl_buffer_var = self._add_labeled_entry(sec2.content, "+ Giá an toàn vào SL (Giá):", "0.5", help_key="sl_buffer")
        self.max_sl_price_var = self._add_labeled_entry(sec2.content, "SL tối đa (Giá, 0=Theo AI):", "5.0", help_key="max_sl_price")
        self.rr_var = self._add_labeled_entry(sec2.content, "Tỷ lệ R:R (0=TP Theo AI):", "2.0", help_key="rr")

        # Nhóm 3: Bộ lọc vào lệnh
        _grp(sec2.content, "BỘ LỌC VÀO LỆNH")
        self.max_spread_var = self._add_labeled_entry(sec2.content, "Spread tối đa (Giá):", "0.35", help_key="max_spread")
        self.blocked_trading_windows_var = self._add_labeled_entry(
            sec2.content, "Khung giờ cấm (UTC+7, phẩy ngăn):", "", help_key="blocked_trading_windows"
        )
        self.confirm_entry_pa_var = tk.BooleanVar(value=True)
        self._add_checkbutton(sec2.content, "Xác nhận Price Action (Tránh bắt dao rơi)", self.confirm_entry_pa_var, help_key="confirm_entry_pa")
        self.pa_min_confirmations_var = self._add_labeled_combo(
            sec2.content,
            "   Số điều kiện PA tối thiểu (1-3):",
            ["1", "2", "3"],
            "1",
            help_key="pa_min_confirmations"
        )
        self.pa_fail_open_var = tk.BooleanVar(value=False)
        self._add_checkbutton(sec2.content, "PA Fail-Open (Cho phép khi mất tick/nến)", self.pa_fail_open_var, help_key="pa_fail_open")
        # Lọc cấu trúc BOS/CHoCH (trend_filter) và Xung đột cấu trúc 2 khung: ẨN khỏi giao diện (người dùng không dùng nữa).
        # Biến giữ lại để lưu/nạp cấu hình; trend_filter luôn bị đặt TẮT khi nạp cấu hình (load_user_settings).
        # Replay vẫn thử lại được: tools/live_replay.py --set trend_filter=True
        self.trend_filter_var = tk.BooleanVar(value=False)
        self.structure_conflict_mode_var = tk.StringVar(value="neutral")
        self.structure_direction_var = tk.BooleanVar(value=False)
        self._add_checkbutton(sec2.content, "Hướng theo cấu trúc đỉnh/đáy (thử nghiệm)", self.structure_direction_var, help_key="structure_direction")

        # Nhóm 4: Quy tắc nhồi lệnh & chống trùng giá
        _grp(sec2.content, "QUY TẮC NHỒI LỆNH")
        self.entry_mode_var = self._add_labeled_combo(
            sec2.content, "Chế độ nhồi lệnh:",
            [
                "Chỉ nhồi khi đã khóa lãi lệnh trước (Risk-Free)",
                "Nhồi giá tốt hơn (DCA)",
                "Tự do theo AI (Cách >= khoảng cách tối thiểu)"
            ],
            "Chỉ nhồi khi đã khóa lãi lệnh trước (Risk-Free)",
            help_key="entry_mode"
        )
        self.min_entry_spacing_var = self._add_labeled_entry(sec2.content, "Khoảng cách nhồi tối thiểu (Giá):", "1.0", help_key="min_entry_spacing")
        self.allow_opposite_entry_var = tk.BooleanVar(value=False)
        self._add_checkbutton(sec2.content, "Vẫn vào lệnh ngược chiều khi lệnh cũ đang âm", self.allow_opposite_entry_var, help_key="allow_opposite_entry")
        self.one_order_per_candle_var = tk.BooleanVar(value=True)
        self._add_checkbutton(sec2.content, "Chỉ mở tối đa 1 lệnh mỗi cây nến", self.one_order_per_candle_var, help_key="one_order_per_candle")

        tk.Button(
            sec2.content, text="↺ Khôi phục mặc định Mục 2 (Bộ lọc Entry & Nhồi lệnh)", bg="#161e2e", fg="#94a3b8",
            activebackground="#25334c", activeforeground=GOLD_ACCENT,
            font=("Segoe UI", 7, "bold"), relief=tk.FLAT, pady=2, cursor="hand2",
            command=lambda: self.reset_section_settings(2)
        ).pack(fill=tk.X, pady=(6, 2))

        # Mục 3: 3. Quản lý lệnh & Chốt lời (Mặc định thu gọn)
        sec3 = CollapsibleSection(
            left_panel, "3. Quản lý lệnh & Chốt lời", initially_open=False,
            group_list=self.accordion_sections,
            on_reset=lambda: self.reset_section_settings(3)
        )
        tk.Label(sec3.content, text="─── DỜI SL & BẢO VỆ LỆNH ───", bg=PANEL_BG, fg=GOLD_ACCENT,
                 font=("Segoe UI", 7, "bold"), pady=4).pack(fill=tk.X)
        self.auto_be_var = tk.BooleanVar(value=True)
        self._add_checkbutton(sec3.content, "Tự dời SL về đúng Entry theo R:R:", self.auto_be_var, help_key="auto_be")
        self.be_trigger_r_var = self._add_labeled_entry(sec3.content, "   Mức kích hoạt (R, VD 1.0 = 1R):", "1.0", help_key="be_trigger_r")
        self.be_plus_price_var = self._add_labeled_entry(sec3.content, "Giá lãi nhỏ cộng vào Entry khi BE+ (Giá):", "0.2", help_key="be_plus_price")

        self.auto_trail_var = tk.BooleanVar(value=True)
        self._add_checkbutton(sec3.content, "Tự trượt SL khóa lãi (Trailing Stop):", self.auto_trail_var, help_key="auto_trail")
        self.trail_dist_r_var = self._add_labeled_entry(sec3.content, "   Khoảng cách bám giá (R, chỉ khi bật Trailing):", "0.35", help_key="trail_dist_r")

        self.auto_rev_close_var = tk.BooleanVar(value=True)
        self._add_checkbutton(sec3.content, "Bảo vệ đảo chiều (Lãi: Chốt | Lỗ: Chờ về Entry+)", self.auto_rev_close_var, help_key="auto_rev_close", fg=GOLD_ACCENT)
        self.rev_consensus_var = self._add_labeled_entry(sec3.content, "   Đồng thuận đảo chiều - Lệnh chưa BE (%):", "75", help_key="rev_consensus")
        self.rev_be_consensus_var = self._add_labeled_entry(sec3.content, "   Đồng thuận đảo chiều - Lệnh đã BE (0=Tắt, %):", "90", help_key="rev_be_consensus")
        self.rev_min_move_var = self._add_labeled_entry(sec3.content, "   Biên độ ngược chiều (Giá, 0=Tắt):", "0", help_key="rev_min_move")
        self.rev_loss_mode_var = self._add_coded_combo(
            sec3.content, "   Chế độ xử lý lệnh đang lỗ:",
            {
                "wait_be": "Chờ hồi về Entry",
                "close_if_strong": "Cắt khi đảo mạnh",
                "tighten_sl": "Siết SL 50%",
            },
            "wait_be",
            help_key="rev_loss_mode"
        )

        # Phân nhóm: Chốt lời chùm lệnh & Thời gian giữ lệnh
        tk.Label(
            sec3.content, text="─── CHỐT LỜI CHÙM LỆNH & THỜI GIAN ───",
            bg=PANEL_BG, fg=GOLD_ACCENT, font=("Segoe UI", 7, "bold"), pady=4
        ).pack(fill=tk.X)
        self.basket_tp_usd_var = self._add_labeled_entry(sec3.content, "Chốt lời chùm lệnh ($, 0=Tắt):", "0", help_key="basket_tp_usd")
        self.basket_cooldown_mins_var = self._add_labeled_entry(sec3.content, "   Nghỉ sau chốt chùm (phút, 0=Tắt):", "15", help_key="basket_cooldown_mins")
        self.stack_half_be_var = tk.BooleanVar(value=False)
        self._add_checkbutton(sec3.content, "Nhồi lệnh: thoát nửa số lệnh giá xấu ở BE+", self.stack_half_be_var, help_key="stack_half_be")
        self.max_hold_mins_var = self._add_labeled_entry(sec3.content, "Giữ lệnh tối đa (phút, 0=Tắt):", "15", help_key="max_hold_mins")
        self.max_hold_action_var = self._add_labeled_combo(
            sec3.content, "   Xử lý khi vượt thời gian:",
            [
                "Dời SL về BE+ (nếu lãi) & Dời TP về hòa (nếu lỗ)",
                "Dời SL về hòa vốn (BE+)",
                "Đóng tất cả (Bất kể Lãi/Lỗ)"
            ],
            "Dời SL về BE+ (nếu lãi) & Dời TP về hòa (nếu lỗ)",
            help_key="max_hold_action"
        )

        # Lựa chọn cũ "Chốt lời (nếu lãi) & Dời TP về hòa (nếu lỗ)" đã bỏ: cấu hình đã lưu tự chuyển sang lựa chọn mới
        def _migrate_hold_action(*_a):
            try:
                if self.max_hold_action_var.get() == "Chốt lời (nếu lãi) & Dời TP về hòa (nếu lỗ)":
                    self.max_hold_action_var.set("Dời SL về BE+ (nếu lãi) & Dời TP về hòa (nếu lỗ)")
            except Exception:
                pass

        self.max_hold_action_var.trace_add("write", _migrate_hold_action)

        # Phân nhóm: Giới hạn Lãi/Lỗ ngày & Cooldown
        tk.Label(
            sec3.content, text="─── GIỚI HẠN LÃI/LỖ TRONG NGÀY ───",
            bg=PANEL_BG, fg=GOLD_ACCENT, font=("Segoe UI", 7, "bold"), pady=4
        ).pack(fill=tk.X)
        self.daily_tp_usd_var = self._add_labeled_entry(sec3.content, "Dừng khi Lãi ngày ($, 0=Tắt):", "0", help_key="daily_tp_usd")
        self.daily_sl_usd_var = self._add_labeled_entry(sec3.content, "Dừng khi Lỗ ngày ($, 0=Tắt):", "0", help_key="daily_sl_usd")
        self.recovery_close_var = tk.BooleanVar(value=False)
        self._add_checkbutton(sec3.content, "Gỡ vốn trong ngày (đóng lệnh lãi ≥ lỗ ròng)", self.recovery_close_var, help_key="recovery_close")
        self.max_consec_loss_var = self._add_labeled_entry(sec3.content, "Thua liên tiếp nghỉ (lệnh, 0=Tắt):", "0", help_key="max_consec_loss")
        self.cooldown_mins_var = self._add_labeled_entry(sec3.content, "Thời gian tạm ngừng (phút):", "30", help_key="cooldown_mins")
        self.fast_sl_cooldown_candles_var = self._add_labeled_entry(sec3.content, "Nghỉ nếu SL nhanh (nến AI, 0=Tắt):", "1", help_key="fast_sl_cooldown_candles")
        self.fast_sl_cooldown_mins_var = self.fast_sl_cooldown_candles_var
        self.fast_sl_same_dir_var = tk.BooleanVar(value=False)
        self._add_checkbutton(sec3.content, "   Chỉ nghỉ chiều vừa SL nhanh (chiều ngược vẫn vào)", self.fast_sl_same_dir_var, help_key="fast_sl_same_dir")
        tk.Button(
            sec3.content, text="↺ Khôi phục mặc định Mục 3 (Quản lý lệnh & Chốt lời)", bg="#161e2e", fg="#94a3b8",
            activebackground="#25334c", activeforeground=GOLD_ACCENT,
            font=("Segoe UI", 7, "bold"), relief=tk.FLAT, pady=2, cursor="hand2",
            command=lambda: self.reset_section_settings(3)
        ).pack(fill=tk.X, pady=(6, 2))

        # Cây thông số cha/con (chỉ giao diện): cha tắt thì con mờ đi
        try:
            self._bind_param_children(self.auto_be_var, [self.be_trigger_r_var])
            self._bind_param_children(self.auto_trail_var, [self.trail_dist_r_var])
            self._bind_param_children(self.auto_rev_close_var, [self.rev_consensus_var, self.rev_be_consensus_var,
                                                                self.rev_min_move_var, self.rev_loss_mode_var])
            self._bind_param_children(self.basket_tp_usd_var, [self.basket_cooldown_mins_var], numeric=True)
            self._bind_param_children(self.max_hold_mins_var, [self.max_hold_action_var], numeric=True)
            self._bind_param_children(self.max_consec_loss_var, [self.cooldown_mins_var], numeric=True)
            self._bind_param_children(self.fast_sl_cooldown_candles_var, [self.fast_sl_same_dir_var], numeric=True)
            self._bind_param_children(self.confirm_entry_pa_var, [self.pa_min_confirmations_var, self.pa_fail_open_var])
        except Exception as _e:
            self._write_file_log(f"[UI] Không dựng được cây thông số: {_e}", is_error=True)

        # Mục 4: 4. Phân tích AI & Mô hình (Mặc định thu gọn)
        sec4 = CollapsibleSection(
            left_panel, "4. Phân tích AI & Mô hình", initially_open=False,
            group_list=self.accordion_sections,
            on_reset=lambda: self.reset_section_settings(4)
        )
        self.engine_var = self._add_labeled_combo(
            sec4.content, "Chế độ chạy:",
            ["Ưu tiên Hugging Face -> GTX 1650", "Chạy trực tiếp máy Local"],
            "Chạy trực tiếp máy Local",
            help_key="engine"
        )
        self.model_var = self._add_labeled_combo(
            sec4.content, "Mô hình:",
            list(MODEL_CONFIGS.keys()),
            "AI-base (102.3M - Mạnh nhất)",
            help_key="model"
        )
        self.hf_space_var = self._add_labeled_entry(sec4.content, "HF Space ID:", "tranvankha2807/Kronos_Xau", entry_width=18, help_key="hf_space")
        self.hf_token_var = self._add_labeled_entry(sec4.content, "HF Token (nếu có):", "", show="*", entry_width=18, help_key="hf_token")
        self.lookback_var = self._add_labeled_entry(sec4.content, "Số nến lịch sử AI (Lookback):", "450", help_key="lookback")
        self.pred_len_var = self._add_labeled_entry(sec4.content, "Số nến dự báo (6 = 30p):", "6", help_key="pred_len")
        self.debug_trace_var = tk.BooleanVar(value=False)
        self._add_checkbutton(sec4.content, "Ghi nhật ký debug theo nến (chỉ để chẩn đoán)", self.debug_trace_var, help_key="debug_trace")
        tk.Button(
            sec4.content, text="↺ Khôi phục mặc định Mục 4 (Phân tích AI & Mô hình)", bg="#161e2e", fg="#94a3b8",
            activebackground="#25334c", activeforeground=GOLD_ACCENT,
            font=("Segoe UI", 7, "bold"), relief=tk.FLAT, pady=2, cursor="hand2",
            command=lambda: self.reset_section_settings(4)
        ).pack(fill=tk.X, pady=(6, 2))

        # Mục 5: 5. Thông báo & Điều khiển Telegram (Mặc định thu gọn)
        sec5 = CollapsibleSection(
            left_panel, "5. Thông báo Telegram", initially_open=False,
            group_list=self.accordion_sections,
            on_reset=lambda: self.reset_section_settings(5)
        )
        self.telegram_enabled_var = tk.BooleanVar(value=False)
        self._add_checkbutton(sec5.content, "Bật thông báo Telegram:", self.telegram_enabled_var, help_key="telegram_enabled")
        self.telegram_bot_token_var = self._add_labeled_entry(sec5.content, "Bot Token:", "", show="*", entry_width=18, help_key="telegram_bot_token")
        self.telegram_chat_id_var = self._add_labeled_entry(sec5.content, "ID nhóm chat (có thể trống):", "", entry_width=18, help_key="telegram_chat_id")
        self.telegram_admin_ids_var = self._add_labeled_entry(sec5.content, "ID người điều khiển:", "", entry_width=18, help_key="telegram_admin_ids")

        # Nút Kiểm tra kết nối Telegram
        test_tg_btn = tk.Button(
            sec5.content, text="⚡ Gửi thử tin nhắn (Test Connection)",
            bg="#1e293b", fg=CYAN_LIVE, activebackground="#334155", activeforeground="#ffffff",
            font=("Segoe UI", 8, "bold"), relief=tk.FLAT, pady=3, cursor="hand2",
            command=self.test_telegram_connection
        )
        test_tg_btn.pack(fill=tk.X, pady=(4, 6))

        self.telegram_notify_open_var = tk.BooleanVar(value=True)
        self._add_checkbutton(sec5.content, "Báo khi vào lệnh mới:", self.telegram_notify_open_var, help_key="telegram_notify_open")
        self.telegram_notify_close_var = tk.BooleanVar(value=True)
        self._add_checkbutton(sec5.content, "Báo khi đóng lệnh (Chốt lời/SL/BE):", self.telegram_notify_close_var, help_key="telegram_notify_close")

        # Hướng dẫn nhanh lấy Token và ID
        tg_hint_lbl = tk.Label(
            sec5.content,
            text="💡 Token: Chat @BotFather lấy mã | ID: Chat @userinfobot",
            bg=PANEL_BG, fg="#64748b", font=("Segoe UI", 7, "italic"), justify=tk.LEFT
        )
        tg_hint_lbl.pack(fill=tk.X, pady=(2, 4))

        tk.Button(
            sec5.content, text="↺ Khôi phục mặc định Mục 5 (Telegram)", bg="#161e2e", fg="#94a3b8",
            activebackground="#25334c", activeforeground=GOLD_ACCENT,
            font=("Segoe UI", 7, "bold"), relief=tk.FLAT, pady=2, cursor="hand2",
            command=lambda: self.reset_section_settings(5)
        ).pack(fill=tk.X, pady=(4, 2))

        # KHỐI LƯU CÀI ĐẶT DUY NHẤT Ở ĐÁY CÙNG MENU TRÁI (DƯỚI CÙNG GIAO DIỆN)
        save_card = tk.Frame(left_panel, bg="#121722", padx=8, pady=6, highlightthickness=1, highlightbackground="#263147")
        save_card.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 4))

        self.save_settings_btn = tk.Button(
            save_card,
            text="💾 LƯU CÀI ĐẶT NGAY (SAVE)",
            bg="#0d7a46",
            fg="#ffffff",
            activebackground="#159f54",
            activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold"),
            relief=tk.FLAT,
            padx=10,
            pady=5,
            cursor="hand2",
            command=self.manual_save_user_settings
        )
        self.save_settings_btn.pack(fill=tk.X, pady=(1, 2))

        # Hàng 2 nút Xuất / Nhập cấu hình tinh gọn, tối giản (Slate thanh lịch, không màu mè)
        io_row = tk.Frame(save_card, bg="#121722")
        io_row.pack(fill=tk.X, pady=(2, 2))

        self.export_settings_btn = tk.Button(
            io_row,
            text="📤 Xuất Cấu Hình",
            bg="#1e293b",
            fg="#cbd5e1",
            activebackground="#334155",
            activeforeground="#ffffff",
            font=("Segoe UI", 8, "bold"),
            relief=tk.FLAT,
            padx=6,
            pady=3,
            cursor="hand2",
            command=self.export_settings_file
        )
        self.export_settings_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))

        self.import_settings_btn = tk.Button(
            io_row,
            text="📥 Nhập Cấu Hình",
            bg="#1e293b",
            fg="#cbd5e1",
            activebackground="#334155",
            activeforeground="#ffffff",
            font=("Segoe UI", 8, "bold"),
            relief=tk.FLAT,
            padx=6,
            pady=3,
            cursor="hand2",
            command=self.import_settings_file
        )
        self.import_settings_btn.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(2, 0))

        self.save_defaults_btn = tk.Button(
            save_card,
            text="Lưu cấu hình mặc định",
            bg="#1e293b",
            fg="#fbbf24",
            activebackground="#334155",
            activeforeground="#fde68a",
            font=("Segoe UI", 8, "bold"),
            relief=tk.FLAT,
            padx=6,
            pady=3,
            cursor="hand2",
            command=self.save_current_as_defaults
        )
        self.save_defaults_btn.pack(fill=tk.X, pady=(2, 2))

        self.save_status_lbl = tk.Label(
            save_card,
            text="✓ Tự động lưu: ĐÃ BẬT (Sẵn sàng)",
            bg="#121722",
            fg="#10b981",
            font=("Consolas", 7, "bold"),
            anchor="center"
        )
        self.save_status_lbl.pack(fill=tk.X, pady=(2, 0))

        # Kiểm tra thông số nhập (chỉ giao diện, không đổi luật giao dịch): ô sai tô đỏ, ô cần lưu ý tô cam, lý do hiện ngay dưới đây
        self.param_issue_lbl = tk.Label(
            save_card, text="", bg="#121722", fg="#ff6b6b", font=("Segoe UI", 7, "bold"),
            justify=tk.LEFT, anchor="w", wraplength=300
        )
        self._install_param_validation()
        self._apply_params_lock()  # khởi động ở trạng thái KHÓA

        # BẢNG THÔNG TIN PHIÊN & TÓM TẮT AI NẰM Ở GIỮA MENU TRÁI (CO GIÃN TỰ NHIÊN)
        info_card = tk.Frame(left_panel, bg="#131822", padx=10, pady=8, highlightthickness=1, highlightbackground="#263147")
        info_card.pack(fill=tk.BOTH, expand=True, pady=(4, 4))

        info_top_row = tk.Frame(info_card, bg="#131822")
        info_top_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(
            info_top_row, text="THÔNG TIN PHIÊN & TÓM TẮT AI",
            bg="#131822", fg=GOLD_ACCENT, font=("Segoe UI", 9, "bold"), anchor="w"
        ).pack(side=tk.LEFT)
        tk.Button(
            info_top_row, text="📂 Mở Log", bg="#232d3f", fg=CYAN_LIVE,
            activebackground=GOLD_ACCENT, activeforeground="#000000",
            font=("Segoe UI", 7, "bold"), relief=tk.FLAT, padx=6, pady=1, cursor="hand2",
            command=self.open_log_folder
        ).pack(side=tk.RIGHT)

        self.quick_info_lbl = tk.Label(
            info_card,
            text=(
                "• Mã giao dịch: XAUUSDm (M5)\n"
                "• Giá Live: Đang tải...\n"
                "• Lệnh đang mở: 0 lệnh\n"
                "• Lãi/Lỗ nổi: 0.00 USD\n"
                "──────────────────────────\n"
                "• Trạng thái AI: Đang khởi tạo...\n"
                "• Xu hướng AI: ---\n"
                "• Mục tiêu 6 nến: ---\n"
                "• SL / TP gợi ý: ---"
            ),
            bg="#131822", fg=TEXT_MAIN, font=("Consolas", 8), justify=tk.LEFT, anchor="nw"
        )
        self.quick_info_lbl.pack(fill=tk.BOTH, expand=True)

        # Hộp Tín hiệu bên Cột phải (Tích hợp nút Reload % bo tròn & Công tắc gạt START/STOP ở góc phải)
        sig_box = tk.Frame(right_panel, bg=PANEL_BG, padx=12, pady=6)
        sig_box.pack(fill=tk.X, pady=(0, 4))
        self.live_sig_box = sig_box  # ẩn khi mở màn hình Backtest (Backtest có dòng tín hiệu riêng)

        sig_right = tk.Frame(sig_box, bg=PANEL_BG)
        sig_right.pack(side=tk.RIGHT, padx=(12, 0), anchor="center")

        # 1. Thanh hiển thị Tiến Trình AI (Chỉ hiển thị tiến độ tự động theo đúng khung thời gian cài đặt, không bấm quét tay)
        reload_col = tk.Frame(sig_right, bg=PANEL_BG)
        reload_col.pack(side=tk.LEFT, padx=(0, 10))
        tk.Label(
            reload_col, text="TIẾN TRÌNH AI", bg=PANEL_BG, fg=TEXT_MUTED,
            font=("Segoe UI", 7, "bold")
        ).pack(pady=(0, 2))
        self.reload_canvas = tk.Canvas(
            reload_col, width=118, height=32, bg=PANEL_BG, highlightthickness=0
        )
        self.reload_canvas.pack()
        self._draw_reload_pill()

        # 2. Công tắc gạt (Toggle Switch) bo tròn mềm mại cho START / STOP
        switch_col = tk.Frame(sig_right, bg=PANEL_BG)
        switch_col.pack(side=tk.LEFT)
        tk.Label(
            switch_col, text="TỰ ĐỘNG TRADE (AUTO)", bg=PANEL_BG, fg=TEXT_MUTED,
            font=("Segoe UI", 7, "bold")
        ).pack(pady=(0, 2))
        self.toggle_switch_canvas = tk.Canvas(
            switch_col, width=118, height=32, bg=PANEL_BG, highlightthickness=0, cursor="hand2"
        )
        self.toggle_switch_canvas.pack()
        self.toggle_switch_canvas.bind("<Button-1>", lambda e: self.toggle_bot_start_stop())
        self._draw_toggle_switch()

        sig_left = tk.Frame(sig_box, bg=PANEL_BG)
        sig_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.signal_title_lbl = tk.Label(
            sig_left, text="⏳ ĐANG TỰ ĐỘNG CHẠY PHÂN TÍCH AI LẦN ĐẦU...",
            bg=PANEL_BG, fg=GOLD_ACCENT, font=("Segoe UI", 11, "bold"), anchor="w"
        )
        self.signal_title_lbl.pack(fill=tk.X)

        self.signal_detail_lbl = tk.Label(
            sig_left,
            text="💡 Rê chuột: Crosshair | Lăn chuột: Co giãn nến | Kéo chuột: Di chuyển | Chuột phải / Nháy đúp: Reset.",
            bg=PANEL_BG, fg=TEXT_MAIN, font=("Consolas", 8), justify=tk.LEFT, anchor="w"
        )
        self.signal_detail_lbl.pack(fill=tk.X, pady=(2, 0))

        # THANH TRẠNG THÁI HOẠT ĐỘNG BOT CHUYÊN DỤNG (BOT STATUS STRIP - CANH GIỮA, CHỮ ĐỎ, CHIỀU CAO CỐ ĐỊNH TRÁNH GIẬT KHUNG)
        self.bot_status_frame = tk.Frame(
            sig_left, bg="#121824", padx=8, pady=3,
            highlightthickness=1, highlightbackground="#121824", height=24
        )
        self.bot_status_frame.pack(fill=tk.X, pady=(4, 0))
        self.bot_status_frame.pack_propagate(False)

        # Giữ đối tượng label để tương thích ngược nếu có hàm tham chiếu, không pack để chữ canh giữa hoàn hảo 100%
        self.bot_status_icon_lbl = tk.Label(self.bot_status_frame, text="", bg="#121824")
        self.bot_countdown_lbl = tk.Label(self.bot_status_frame, text="", bg="#121824")

        self.bot_status_text_lbl = tk.Label(
            self.bot_status_frame,
            text="",
            bg="#121824", fg=RED_DOWN, font=("Segoe UI", 9, "bold"),
            anchor="center", justify="center"
        )
        self.bot_status_text_lbl.pack(fill=tk.BOTH, expand=True)

        # Menu chuột phải trên biểu đồ kiểu TradingView
        self.chart_context_menu = tk.Menu(
            self.root, tearoff=0, bg="#19202c", fg=TEXT_MAIN,
            activebackground=GOLD_ACCENT, activeforeground="#000000",
            font=("Segoe UI", 9, "bold")
        )
        self.chart_context_menu.add_command(label="🔄 Reset biểu đồ (Auto-Fit)", command=self.reset_chart_view)
        self.chart_context_menu.add_separator()
        self.chart_context_menu.add_command(label="➕ Mở rộng nến (Phóng to ngang)", command=lambda: self.adjust_chart_horizontal_scale(0.75))
        self.chart_context_menu.add_command(label="➖ Thu hẹp nến (Xem nhiều nến hơn)", command=lambda: self.adjust_chart_horizontal_scale(1.35))

        # BỘ CHIA KHUNG KÉO GIÃN LINH HOẠT GIỮA BIỂU ĐỒ VÀ BẢNG LỆNH (VERTICAL PANEDWINDOW)
        self.right_paned = tk.PanedWindow(
            right_panel,
            orient=tk.VERTICAL,
            bg="#222c3c",
            bd=0,
            sashwidth=6,
            sashpad=1,
            sashrelief=tk.RAISED,
            sashcursor="size_ns",
            opaqueresize=False
        )
        self.right_paned.pack(fill=tk.BOTH, expand=True)

        # Biểu đồ Nến tương tác trực tiếp bằng chuột + Thanh công cụ chọn nhanh Khung M1/M5/M15/H1
        # Khung chứa: biểu đồ live HOẶC màn hình Backtest riêng (khi mở tab Backtest); đổi qua lại không đụng bot live
        self.chart_holder = tk.Frame(self.right_paned, bg=PANEL_BG)
        chart_frame = tk.Frame(self.chart_holder, bg=PANEL_BG)
        chart_frame.pack(fill=tk.BOTH, expand=True)
        self.live_chart_frame = chart_frame

        chart_toolbar = tk.Frame(chart_frame, bg="#131822", padx=10, pady=4, highlightthickness=1, highlightbackground="#232d3f")
        chart_toolbar.pack(fill=tk.X)

        tk.Label(
            chart_toolbar, text="XEM BIỂU ĐỒ:", bg="#131822", fg=TEXT_MUTED,
            font=("Segoe UI", 8, "bold")
        ).pack(side=tk.LEFT, padx=(0, 6))

        self.tf_quick_btns = {}
        for tf_opt in ("M1", "M5", "M15", "H1"):
            b = tk.Button(
                chart_toolbar, text=tf_opt,
                bg="#222c3c", fg=TEXT_MAIN,
                activebackground=GOLD_ACCENT, activeforeground="#000000",
                font=("Segoe UI", 8, "bold"), relief=tk.FLAT, padx=10, pady=1, cursor="hand2",
                command=lambda t=tf_opt: self.switch_chart_timeframe_quick(t)
            )
            b.pack(side=tk.LEFT, padx=2)
            self.tf_quick_btns[tf_opt] = b

        self.ai_overlay_btn = tk.Button(
            chart_toolbar, text="🧠 Dự Báo AI: BẬT",
            bg=GOLD_ACCENT, fg="#000000",
            activebackground="#ffd54f", activeforeground="#000000",
            font=("Segoe UI", 8, "bold"), relief=tk.FLAT, padx=8, pady=1, cursor="hand2",
            command=self.toggle_ai_chart_overlay
        )
        self.ai_overlay_btn.pack(side=tk.LEFT, padx=(8, 2))

        tk.Label(
            chart_toolbar, text="(Gộp chung Lệnh Live & Dự báo AI trên cùng biểu đồ)",
            bg="#131822", fg="#64748b", font=("Segoe UI", 7, "italic")
        ).pack(side=tk.LEFT, padx=(6, 0))

        self.ohlc_hover_lbl = tk.Label(
            chart_toolbar, text="Rê chuột lên nến để xem giá OHLC & Thời gian",
            bg="#131822", fg=CYAN_LIVE, font=("Consolas", 8, "bold"), anchor="e"
        )
        self.ohlc_hover_lbl.pack(side=tk.RIGHT, padx=(6, 0))

        self.fig = Figure(figsize=(9, 4.4), dpi=100, facecolor=PANEL_BG)
        self.ax = self.fig.add_subplot(111)
        self._style_axes()

        self.canvas = FigureCanvasTkAgg(self.fig, master=chart_frame)
        self.canvas_widget = self.canvas.get_tk_widget()
        self.canvas_widget.pack(fill=tk.BOTH, expand=True)

        self.canvas.mpl_connect("scroll_event", self._on_mouse_scroll)
        self.canvas.mpl_connect("button_press_event", self._on_mouse_press)
        self.canvas.mpl_connect("button_release_event", self._on_mouse_release)
        self.canvas.mpl_connect("motion_notify_event", self._on_mouse_motion)
        self.canvas.mpl_connect("resize_event", lambda e: setattr(self, "_chart_bg_bbox", None))

        # KHU VỰC DƯỚI CÙNG: BẢNG ĐIỀU KHIỂN ĐA TAB GỌN GÀNG (XAUUSD | BTCUSD | LỊCH SỬ | BACKTEST)
        # Đã gộp Bảng 6 Nến Dự Báo AI & Nhật Ký Hệ Thống trực tiếp vào trong từng Tab XAUUSD và BTCUSD!
        self.bottom_notebook = ttk.Notebook(self.right_paned)

        # TAB 0 (Mặc định khi mở App): XAUUSD (BIỂU ĐỒ, LỆNH REAL-TIME, DỰ BÁO AI & NHẬT KÝ)
        pos_tab_xau = tk.Frame(self.bottom_notebook, bg=PANEL_BG)
        self.bottom_notebook.add(pos_tab_xau, text="XAUUSD (BIỂU ĐỒ & LỆNH REAL-TIME)")
        self._build_positions_tab_ui(pos_tab_xau, "XAUUSD")

        # TAB 1: BTCUSD (BIỂU ĐỒ, LỆNH REAL-TIME, DỰ BÁO AI & NHẬT KÝ)
        pos_tab_btc = tk.Frame(self.bottom_notebook, bg=PANEL_BG)
        self.bottom_notebook.add(pos_tab_btc, text="₿ BTCUSD (BIỂU ĐỒ & LỆNH REAL-TIME)")
        self._build_positions_tab_ui(pos_tab_btc, "BTCUSD")

        # Mặc định khởi tạo trỏ vào Tab XAUUSD
        self.pos_tree = self.pos_tree_by_profile["XAUUSD"]
        self.pos_summary_lbl = self.pos_summary_lbl_by_profile["XAUUSD"]
        self.tree = self.pred_tree_by_profile["XAUUSD"]
        self.pred_table_title_lbl = self.pred_title_by_profile["XAUUSD"]
        self.log_text = self.log_text_by_profile["XAUUSD"]

        # TAB 2: LỊCH SỬ GIAO DỊCH & XUẤT BÁO CÁO EXCEL
        history_tab = tk.Frame(self.bottom_notebook, bg=PANEL_BG)
        self.bottom_notebook.add(history_tab, text="📜 LỊCH SỬ & XUẤT EXCEL")
        self._build_history_tab_ui(history_tab)

        # TAB 3: KIỂM THỬ CHIẾN LƯỢC (BACKTEST TICK-LEVEL ENGINE) - KHÓA QUẢN TRỊ VIÊN
        self.backtest_tab = tk.Frame(self.bottom_notebook, bg=PANEL_BG)
        self.bottom_notebook.add(self.backtest_tab, text="🔒 BACKTEST (ADMIN)")
        self._build_backtest_tab_ui(self.backtest_tab)

        self.bottom_notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # Đưa Khung Biểu Đồ và Bảng Lệnh Đa Tab vào PanedWindow kéo giãn linh hoạt
        self.right_paned.add(self.chart_holder, minsize=180, stretch="always")
        self.right_paned.add(self.bottom_notebook, minsize=120, stretch="always")

        self.right_paned.bind("<ButtonRelease-1>", self._on_sash_released, add="+")
        self.right_paned.bind("<Double-Button-1>", self._on_paned_double_click, add="+")
        self.root.after(150, self._init_right_paned_sash)

    def _build_positions_tab_ui(self, pos_tab: tk.Frame, profile_key: str):
        """Dựng giao diện hợp nhất (All-in-One) cho từng Tab (XAUUSD / BTCUSD): Bảng lệnh Real-Time + Bảng 6 Nến Dự Báo AI & Nhật ký Log."""
        pos_top_bar = tk.Frame(pos_tab, bg=PANEL_BG, pady=4, padx=6)
        pos_top_bar.pack(fill=tk.X)

        tk.Button(
            pos_top_bar, text="⛶ Mở Rộng", bg="#1f293d", fg=TEXT_MAIN,
            activebackground=GOLD_ACCENT, activeforeground="#000000",
            font=("Segoe UI", 8, "bold"), relief=tk.FLAT, padx=6, pady=2, cursor="hand2",
            command=self.toggle_bottom_panel_expand
        ).pack(side=tk.RIGHT, padx=(4, 2))

        side_toggle_btn = tk.Button(
            pos_top_bar, text="📋 Ẩn/Hiện Nhật Ký & AI", bg="#1e2c44", fg=CYAN_LIVE,
            activebackground=GOLD_ACCENT, activeforeground="#000000",
            font=("Segoe UI", 8, "bold"), relief=tk.FLAT, padx=7, pady=2, cursor="hand2",
            command=lambda pk=profile_key: self._toggle_profile_side_drawer(pk)
        )
        side_toggle_btn.pack(side=tk.RIGHT, padx=3)
        self.side_toggle_btn_by_profile[profile_key] = side_toggle_btn

        tk.Button(
            pos_top_bar, text="🔥 ĐÓNG TẤT CẢ (CLOSE ALL)", bg=RED_DOWN, fg="#ffffff",
            font=("Segoe UI", 8, "bold"), relief=tk.FLAT, padx=8, pady=2,
            command=self.close_all_positions
        ).pack(side=tk.RIGHT, padx=3)

        tk.Button(
            pos_top_bar, text="❌ Đóng Lệnh Chọn", bg="#8c2d3b", fg="#ffffff",
            font=("Segoe UI", 8, "bold"), relief=tk.FLAT, padx=8, pady=2,
            command=self.close_selected_position
        ).pack(side=tk.RIGHT, padx=3)

        tk.Button(
            pos_top_bar, text="🛡️ Dời SL về Entry", bg="#235c46", fg="#ffffff",
            font=("Segoe UI", 8, "bold"), relief=tk.FLAT, padx=8, pady=2,
            command=self.breakeven_all_positions
        ).pack(side=tk.RIGHT, padx=3)

        summary_lbl = tk.Label(
            pos_top_bar, text=f"Đang tải danh sách lệnh {profile_key} từ MT5...",
            bg=PANEL_BG, fg=CYAN_LIVE, font=("Consolas", 9, "bold")
        )
        summary_lbl.pack(side=tk.LEFT)

        # Bộ chia ngang (Horizontal PanedWindow): Trái = Bảng Lệnh Real-Time | Phải = Nhật Ký Hệ Thống & 6 Nến Dự Báo AI
        horiz_paned = tk.PanedWindow(
            pos_tab, orient=tk.HORIZONTAL, bg="#1e293b", bd=0, sashwidth=5, sashrelief=tk.RAISED, opaqueresize=True
        )
        horiz_paned.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 4))
        self.side_paned_by_profile[profile_key] = horiz_paned

        # 1. KHUNG BÊN TRÁI: BẢNG QUẢN LÝ LỆNH REAL-TIME & LỆNH ĐÃ CHỐT TRONG NGÀY
        tree_container = tk.Frame(horiz_paned, bg=PANEL_BG)

        pos_cols = ("ticket", "time", "duration", "symbol", "type", "volume", "open_price", "current_price", "sl", "tp", "profit", "status")
        pos_headers = {
            "ticket": "Mã Lệnh (Ticket)",
            "time": "Giờ",
            "duration": "Thời gian giữ",
            "symbol": "Mã",
            "type": "Loại",
            "volume": "Lot",
            "open_price": "Giá Vào (Entry)",
            "current_price": "Giá Hiện Tại / Đóng",
            "sl": "SL (Cắt lỗ)",
            "tp": "TP (Chốt lời)",
            "profit": "Lãi / Lỗ (USD)",
            "status": "Trạng thái Bảo vệ / Kết quả"
        }
        pos_widths = {
            "ticket": 80, "time": 90, "duration": 85, "symbol": 65, "type": 45, "volume": 42,
            "open_price": 80, "current_price": 85, "sl": 70, "tp": 70, "profit": 85, "status": 155
        }

        tree = ttk.Treeview(tree_container, columns=pos_cols, show="headings", height=5)
        scrollbar = ttk.Scrollbar(tree_container, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        for c in pos_cols:
            tree.heading(c, text=pos_headers[c])
            if c == "status":
                # Cột trạng thái chứa câu dài: rộng hơn, canh trái, không bị co nhỏ khi cửa sổ hẹp
                tree.column(c, width=320, minwidth=260, anchor=tk.W)
            else:
                tree.column(c, width=pos_widths[c], anchor=tk.CENTER)

        tree.tag_configure("profit_pos", foreground=GREEN_UP)
        tree.tag_configure("profit_neg", foreground=RED_DOWN)
        tree.tag_configure("closed_pos", foreground="#34d399")
        tree.tag_configure("closed_neg", foreground="#f87171")
        tree.tag_configure("closed_be", foreground="#94a3b8")

        tree.bind("<<TreeviewSelect>>", lambda e, pk=profile_key: self._on_pos_tree_select(pk))
        tree.bind("<Button-1>", lambda e, pk=profile_key: self._on_pos_tree_click(e, pk), add="+")
        tree.bind("<Escape>", self._deselect_all_orders_and_reset)

        self.pos_tree_by_profile[profile_key] = tree
        self.pos_summary_lbl_by_profile[profile_key] = summary_lbl

        # 2. KHUNG BÊN PHẢI (TÍCH HỢP CHUNG): NHẬT KÝ HỆ THỐNG & BẢNG 6 NẾN DỰ BÁO AI
        side_drawer = tk.Frame(horiz_paned, bg="#131822", width=430, highlightthickness=1, highlightbackground="#232d3f")
        self.side_drawer_by_profile[profile_key] = side_drawer

        side_hdr = tk.Frame(side_drawer, bg="#131822", padx=4, pady=2)
        side_hdr.pack(fill=tk.X)

        btn_log_mode = tk.Button(
            side_hdr, text="📋 Nhật Ký Hệ Thống", bg=CYAN_LIVE, fg="#000000",
            font=("Segoe UI", 7, "bold"), relief=tk.FLAT, padx=6, pady=1, cursor="hand2",
            command=lambda pk=profile_key: self._switch_profile_side_mode(pk, "LOG")
        )
        btn_log_mode.pack(side=tk.LEFT, padx=(0, 3))

        btn_ai_mode = tk.Button(
            side_hdr, text="📊 6 Nến Dự Báo AI", bg="#222c3c", fg=TEXT_MAIN,
            font=("Segoe UI", 7, "bold"), relief=tk.FLAT, padx=6, pady=1, cursor="hand2",
            command=lambda pk=profile_key: self._switch_profile_side_mode(pk, "AI_TABLE")
        )
        btn_ai_mode.pack(side=tk.LEFT, padx=(0, 4))
        self.side_mode_btns_by_profile[profile_key] = (btn_log_mode, btn_ai_mode)

        tk.Button(
            side_hdr, text="📂 Thư Mục Log", bg="#232d3f", fg=TEXT_MAIN,
            activebackground=GOLD_ACCENT, activeforeground="#000000",
            font=("Segoe UI", 7, "bold"), relief=tk.FLAT, padx=5, pady=1, cursor="hand2",
            command=self.open_log_folder
        ).pack(side=tk.RIGHT, padx=(2, 0))
        tk.Button(
            side_hdr, text="⚠️ Log Lỗi", bg="#5c232d", fg="#ffffff",
            activebackground=RED_DOWN, activeforeground="#ffffff",
            font=("Segoe UI", 7, "bold"), relief=tk.FLAT, padx=5, pady=1, cursor="hand2",
            command=self.open_error_log_file
        ).pack(side=tk.RIGHT)

        side_body = tk.Frame(side_drawer, bg="#131822")
        side_body.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 3))

        # Khung con A: Nhật ký Log
        log_sub = tk.Frame(side_body, bg="#131822")
        log_sub.pack(fill=tk.BOTH, expand=True)
        log_txt = tk.Text(log_sub, bg="#0f141d", fg="#b7bdc6", font=("Consolas", 8), relief=tk.FLAT)
        log_txt.pack(fill=tk.BOTH, expand=True)
        self.log_text_by_profile[profile_key] = log_txt

        # Khung con B: Bảng 6 nến dự báo AI
        ai_sub = tk.Frame(side_body, bg="#131822")
        pred_lbl = tk.Label(
            ai_sub,
            text=f"📌 6 NẾN DỰ BÁO AI: {profile_key}",
            bg="#131822", fg=GOLD_ACCENT, font=("Segoe UI", 7, "bold"), anchor="w", padx=4, pady=1
        )
        pred_lbl.pack(fill=tk.X)
        self.pred_title_by_profile[profile_key] = pred_lbl

        cols_ai = ("timestamps", "open", "high", "low", "close")
        pred_tree = ttk.Treeview(ai_sub, columns=cols_ai, show="headings", height=5)
        headers_ai = {
            "timestamps": "Thời gian",
            "open": "Open",
            "high": "High (Đỉnh)",
            "low": "Low (Đáy)",
            "close": "Close (Đóng)"
        }
        for c_ai in cols_ai:
            pred_tree.heading(c_ai, text=headers_ai[c_ai])
            pred_tree.column(c_ai, width=105 if c_ai == "timestamps" else 68, anchor=tk.CENTER)
        pred_tree.pack(fill=tk.BOTH, expand=True)
        pred_tree.bind("<Button-1>", lambda e, pt=pred_tree: "break" if pt.identify_region(e.x, e.y) == "heading" else None)
        pred_tree.bind("<Escape>", self._deselect_all_orders_and_reset)
        self.pred_tree_by_profile[profile_key] = pred_tree

        self.side_sub_frames_by_profile[profile_key] = (log_sub, ai_sub)

        # Mặc định ẩn khung Nhật Ký & 6 Nến Dự Báo AI khi khởi động để Bảng Quản Lý Lệnh rộng tối đa (bấm nút '📋 Ẩn/Hiện Nhật Ký & AI' khi cần xem)
        horiz_paned.add(tree_container, minsize=460, stretch="always")

    def _switch_profile_side_mode(self, profile_key: str, mode: str):
        """Chuyển đổi giữa xem Nhật Ký Hệ Thống và Bảng 6 Nến Dự Báo AI ngay trong khung phụ của Tab đang mở."""
        subs = getattr(self, "side_sub_frames_by_profile", {}).get(profile_key)
        btns = getattr(self, "side_mode_btns_by_profile", {}).get(profile_key)
        if not subs or not btns:
            return
        log_sub, ai_sub = subs
        btn_log, btn_ai = btns
        if mode == "AI_TABLE":
            log_sub.pack_forget()
            ai_sub.pack(fill=tk.BOTH, expand=True)
            btn_ai.config(bg=GOLD_ACCENT, fg="#000000")
            btn_log.config(bg="#222c3c", fg=TEXT_MAIN)
        else:
            ai_sub.pack_forget()
            log_sub.pack(fill=tk.BOTH, expand=True)
            btn_log.config(bg=CYAN_LIVE, fg="#000000")
            btn_ai.config(bg="#222c3c", fg=TEXT_MAIN)

    def _toggle_profile_side_drawer(self, profile_key: str):
        """Ẩn hoặc hiện khung Nhật Ký & 6 Nến Dự Báo AI bên phải bảng lệnh để không gian bảng lệnh rộng tối đa khi cần."""
        paned = getattr(self, "side_paned_by_profile", {}).get(profile_key)
        drawer = getattr(self, "side_drawer_by_profile", {}).get(profile_key)
        if not paned or not drawer:
            return
        try:
            panes = [str(p) for p in paned.panes()]
            if str(drawer) in panes:
                paned.forget(drawer)
            else:
                paned.add(drawer, minsize=280, stretch="never")
                paned.update_idletasks()
                total_w = paned.winfo_width()
                if total_w > 600:
                    try:
                        paned.sash_place(0, max(460, total_w - 430), 0)
                    except Exception:
                        pass
        except Exception:
            pass

    def toggle_ai_chart_overlay(self):
        """Bật/Tắt hiển thị chùm 8 kịch bản & 6 nến dự báo AI ngay trên biểu đồ Real-Time của Tab hiện tại."""
        self.show_ai_chart_overlay = not getattr(self, "show_ai_chart_overlay", True)
        if hasattr(self, "ai_overlay_btn") and self.ai_overlay_btn.winfo_exists():
            if self.show_ai_chart_overlay:
                self.ai_overlay_btn.config(text="🧠 Dự Báo AI: BẬT", bg=GOLD_ACCENT, fg="#000000")
            else:
                self.ai_overlay_btn.config(text="🧠 Dự Báo AI: TẮT", bg="#222c3c", fg=TEXT_MAIN)
        sym = self.symbol_var.get().strip()
        tf = self.tf_var.get().strip()
        self._refresh_realtime_chart(sym, tf, force_full_redraw=True)

    def _get_selected_tickets_for_profile(self, profile_key: str = None) -> set:
        """Lấy danh sách mã ticket (int) các lệnh đang được người dùng nhấp chọn trong bảng vị thế."""
        pk = profile_key or self.active_profile_key
        tree = getattr(self, "pos_tree_by_profile", {}).get(pk, getattr(self, "pos_tree", None))
        if tree is None:
            return set()
        selected = set()
        try:
            for item_id in tree.selection():
                vals = tree.item(item_id, "values")
                if vals and len(vals) > 0:
                    raw_ticket = str(vals[0]).replace("#", "").strip()
                    if raw_ticket.isdigit():
                        selected.add(int(raw_ticket))
        except Exception:
            pass
        return selected

    def _on_pos_tree_select(self, profile_key: str = None):
        """Xử lý sự kiện khi người dùng chọn lệnh trong danh sách phía dưới: làm nổi bật lệnh tương ứng trên biểu đồ."""
        pk = profile_key or self.active_profile_key
        if pk == self.active_profile_key:
            sel_tickets = self._get_selected_tickets_for_profile(pk)
            if sel_tickets:
                # Nếu có ticket lệnh đã đóng được chọn -> kích hoạt auto-focus vào cây nến khớp lệnh
                for t in sel_tickets:
                    cd = self._get_closed_deal_info(t)
                    if cd is not None:
                        self._selected_deal_ticket_focus = t
                        self.saved_ylim = None
                        break
            else:
                self._selected_deal_ticket_focus = None
                self._active_viewing_closed_deal = None
            sym = self.symbol_var.get().strip() or DEFAULT_PROFILE_SETTINGS.get(pk, {}).get("symbol", "BTCUSDm" if pk == "BTCUSD" else "XAUUSDm")
            tf = self.tf_var.get().strip() or "M1"
            self._refresh_realtime_chart(sym, tf, force_full_redraw=True)

    def _on_pos_tree_click(self, event, profile_key: str = None):
        """
        1. Nếu người dùng nhấp vào hàng tiêu đề (heading), chặn hoàn toàn hiệu ứng nút bấm.
        2. Nếu người dùng nhấp vào khoảng trống ngoài các dòng lệnh, tự động bỏ chọn để biểu đồ trở về hiển thị bình thường.
        """
        pk = profile_key or self.active_profile_key
        tree = getattr(self, "pos_tree_by_profile", {}).get(pk, getattr(self, "pos_tree", None))
        if tree is not None:
            region = tree.identify_region(event.x, event.y)
            if region == "heading":
                return "break"
            clicked_row = tree.identify_row(event.y)
            if not clicked_row and tree.selection():
                tree.selection_remove(tree.selection())
                self._on_pos_tree_select(pk)

    def _deselect_all_orders_and_reset(self, event=None):
        """Khi người dùng nhấn phím ESC: Bỏ chọn dòng lệnh đang được tô sáng và đưa biểu đồ trở về trạng thái ban đầu."""
        pk = self.active_profile_key
        tree = getattr(self, "pos_tree_by_profile", {}).get(pk, getattr(self, "pos_tree", None))
        cleared = False
        if tree is not None and tree.winfo_exists():
            sel = tree.selection()
            if sel:
                tree.selection_remove(sel)
                cleared = True
            tree.focus("")

        for p_key, t_widget in getattr(self, "pos_tree_by_profile", {}).items():
            if t_widget is not None and t_widget != tree and t_widget.winfo_exists():
                s = t_widget.selection()
                if s:
                    t_widget.selection_remove(s)
                    t_widget.focus("")
                    if p_key == pk:
                        cleared = True

        self._selected_deal_ticket_focus = None
        self.reset_chart_view()

    def _build_history_tab_ui(self, hist_tab: tk.Frame):
        """Dựng giao diện Lịch Sử Giao Dịch: Lọc theo Hôm nay / Khoảng ngày qua Lịch Pop-up & Xuất Excel."""
        today_iso = datetime.now(tz=VN_TZ).strftime("%Y-%m-%d")
        self.hist_start_var = tk.StringVar(value=today_iso)
        self.hist_end_var = tk.StringVar(value=today_iso)
        self.hist_symbol_var = tk.StringVar(value="Tất cả")
        self.hist_include_manual_var = tk.BooleanVar(value=False)
        self._cached_hist_deals = []

        hist_top_bar = tk.Frame(hist_tab, bg=PANEL_BG, pady=4, padx=6)
        hist_top_bar.pack(fill=tk.X)

        # Cụm bên trái: Lọc ngày bắt đầu, ngày kết thúc và xem lịch sử
        left_ctrl = tk.Frame(hist_top_bar, bg=PANEL_BG)
        left_ctrl.pack(side=tk.LEFT, fill=tk.Y)

        # Khoảng ngày: 1 nút "Từ -> Đến", bấm mở Lịch Lãi/Lỗ (mỗi ngày ghi lãi xanh / lỗ đỏ, bấm 2 ngày để chọn khoảng)
        self.hist_range_btn = tk.Button(
            left_ctrl, text="", bg="#131822", fg=TEXT_MAIN, activebackground="#232d3f", activeforeground=GOLD_ACCENT,
            font=("Segoe UI", 9, "bold"), relief=tk.FLAT, padx=10, pady=2, cursor="hand2",
            highlightthickness=1, highlightbackground="#2b3648", command=self._open_pnl_calendar
        )
        self.hist_range_btn.pack(side=tk.LEFT, padx=(0, 10))

        tk.Label(left_ctrl, text="Mã:", bg=PANEL_BG, fg=TEXT_MUTED, font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT, padx=(0, 2))
        sym_cb = ttk.Combobox(left_ctrl, textvariable=self.hist_symbol_var, values=["Tất cả", "XAUUSD", "BTCUSD"], width=8, state="readonly")
        sym_cb.pack(side=tk.LEFT, padx=(0, 4))

        cb_manual = tk.Checkbutton(
            left_ctrl, text="Gồm lệnh tay", variable=self.hist_include_manual_var,
            bg=PANEL_BG, fg=TEXT_MAIN, selectcolor="#131822", activebackground=PANEL_BG, activeforeground=GOLD_ACCENT,
            font=("Segoe UI", 8, "bold"), cursor="hand2", command=self._refresh_history_tab
        )
        cb_manual.pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(
            left_ctrl, text="⟳ Làm mới", bg="#232d3f", fg=CYAN_LIVE, activebackground=CYAN_LIVE, activeforeground="#000000",
            font=("Segoe UI", 8, "bold"), relief=tk.FLAT, padx=8, pady=2, cursor="hand2",
            command=self._refresh_history_tab
        ).pack(side=tk.LEFT, padx=(0, 6))

        # Cụm bên phải: Nút chọn nhanh (Hôm Nay, Hôm Qua, 7 Ngày, 30 Ngày) & Nút Xuất Excel
        right_ctrl = tk.Frame(hist_top_bar, bg=PANEL_BG)
        right_ctrl.pack(side=tk.RIGHT, fill=tk.Y)

        tk.Button(
            right_ctrl, text="⛶ Mở Rộng", bg="#1f293d", fg=TEXT_MAIN,
            activebackground=GOLD_ACCENT, activeforeground="#000000",
            font=("Segoe UI", 8, "bold"), relief=tk.FLAT, padx=6, pady=2, cursor="hand2",
            command=self.toggle_bottom_panel_expand
        ).pack(side=tk.RIGHT, padx=(4, 0))

        tk.Button(
            right_ctrl, text="📊 XUẤT EXCEL", bg="#107c41", fg="#ffffff",
            font=("Segoe UI", 8, "bold"), relief=tk.FLAT, padx=10, pady=2, cursor="hand2",
            command=self.export_history_to_excel
        ).pack(side=tk.RIGHT, padx=4)

        # Cụm nút chọn nhanh ĐÃ ẨN (người dùng yêu cầu 19/11/2026): chọn ngày bằng nút khoảng ngày/lịch.
        # Vẫn tạo nút (không pack) để phần tô sáng nút đang chọn không lỗi.
        quick_seg = tk.Frame(right_ctrl, bg="#2b3648", padx=1, pady=1)
        self._hist_quick_btns = {}
        for q_key, q_txt, q_fn in (
            ("today", "Hôm nay", self._set_history_today),
            ("yesterday", "Hôm qua", self._set_history_yesterday),
            ("7d", "7 ngày", self._set_history_7days),
            ("30d", "30 ngày", self._set_history_30days),
            ("month", "Tháng này", self._set_history_this_month),
        ):
            q_btn = tk.Button(
                quick_seg, text=q_txt, bg="#19202c", fg=TEXT_MUTED, activebackground=GOLD_ACCENT, activeforeground="#000000",
                font=("Segoe UI", 8, "bold"), relief=tk.FLAT, bd=0, padx=9, pady=2, cursor="hand2", command=q_fn
            )
            q_btn.pack(side=tk.LEFT, padx=(1 if self._hist_quick_btns else 0, 0))
            self._hist_quick_btns[q_key] = q_btn
        for _v in (self.hist_start_var, self.hist_end_var):
            _v.trace_add("write", lambda *_a: self._update_hist_range_btn())
        self._update_hist_range_btn()

        # Thanh tổng kết số liệu: các ô chỉ số gọn (tiêu đề mờ + số đậm, lãi xanh / lỗ đỏ)
        summary_bar = tk.Frame(hist_tab, bg="#131822", padx=6, pady=3)
        summary_bar.pack(fill=tk.X, padx=6, pady=(0, 3))
        self.hist_kpi_lbls = {}
        for k_key, k_title in (("total", "Lệnh"), ("win", "Thắng ≥1R"), ("be", "Hòa/BE+"), ("loss", "Thua"),
                               ("wr", "Win rate"), ("pnl", "Lãi/Lỗ ròng"), ("pf", "PF")):
            k_card = tk.Frame(summary_bar, bg="#19202c", padx=8, pady=1)
            k_card.pack(side=tk.LEFT, padx=(0, 5))
            tk.Label(k_card, text=k_title, bg="#19202c", fg=TEXT_MUTED, font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(0, 5))
            k_val = tk.Label(k_card, text="---", bg="#19202c", fg=TEXT_MAIN, font=("Consolas", 9, "bold"))
            k_val.pack(side=tk.LEFT)
            self.hist_kpi_lbls[k_key] = k_val
        self.hist_summary_lbl = tk.Label(
            summary_bar, text="Đang tải lịch sử giao dịch...",
            bg="#131822", fg=TEXT_MUTED, font=("Segoe UI", 8, "bold"), anchor="e"
        )
        self.hist_summary_lbl.pack(side=tk.RIGHT)

        # Bảng Treeview lịch sử
        hist_container = tk.Frame(hist_tab, bg=PANEL_BG)
        hist_container.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 4))

        hist_cols = ("ticket", "time", "duration", "symbol", "type", "volume", "open_price", "close_price", "sl", "tp", "profit", "r_ratio", "category", "status")
        hist_headers = {
            "ticket": "Mã Lệnh",
            "time": "Giờ Vào → Ra",
            "duration": "Thời Gian",
            "symbol": "Mã",
            "type": "Loại",
            "volume": "Lot",
            "open_price": "Giá Vào",
            "close_price": "Giá Đóng",
            "sl": "SL",
            "tp": "TP",
            "profit": "Lãi Ròng ($)",
            "r_ratio": "Tỷ Lệ R",
            "category": "Phân Loại",
            "status": "Trạng Thái / Kết Quả"
        }
        hist_widths = {
            "ticket": 85, "time": 105, "duration": 80, "symbol": 70, "type": 50, "volume": 45,
            "open_price": 85, "close_price": 85, "sl": 75, "tp": 75, "profit": 90,
            "r_ratio": 70, "category": 85, "status": 170
        }

        self.hist_tree = ttk.Treeview(hist_container, columns=hist_cols, show="headings", height=5)
        hist_sb = ttk.Scrollbar(hist_container, orient="vertical", command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=hist_sb.set)
        hist_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.hist_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        for c in hist_cols:
            self.hist_tree.heading(c, text=hist_headers[c])
            self.hist_tree.column(c, width=hist_widths[c], anchor=tk.CENTER)

        self.hist_tree.tag_configure("tag_win", foreground=GREEN_UP)
        self.hist_tree.tag_configure("tag_loss", foreground=RED_DOWN)
        self.hist_tree.tag_configure("tag_be", foreground=GOLD_ACCENT)

        self.hist_tree.bind("<Button-1>", lambda e: "break" if self.hist_tree.identify_region(e.x, e.y) == "heading" else None)
        self.hist_tree.bind("<Double-1>", self._on_hist_tree_double_click)

    def _on_hist_tree_double_click(self, event=None):
        """Khi người dùng nháy đúp vào một lệnh trong Tab Lịch Sử: Chuyển sang Tab biểu đồ tương ứng và soi nến khớp lệnh."""
        try:
            sel = self.hist_tree.selection()
            if not sel:
                return
            vals = self.hist_tree.item(sel[0], "values")
            if not vals:
                return
            ticket = int(str(vals[0]).replace("#", "").strip())
            sym = str(vals[3]).strip()
            target_pk = "BTCUSD" if "BTC" in sym.upper() else "XAUUSD"
            target_tab_idx = 0 if target_pk == "XAUUSD" else 1
            self.bottom_notebook.select(target_tab_idx)
            self._switch_active_profile(target_pk)
            tree = self.pos_tree_by_profile.get(target_pk)
            if tree and tree.winfo_exists():
                for item in tree.get_children():
                    row_vals = tree.item(item, "values")
                    if row_vals and str(row_vals[0]).replace("#", "").strip().isdigit():
                        if int(str(row_vals[0]).replace("#", "").strip()) == ticket:
                            tree.selection_set(item)
                            tree.see(item)
                            break
            self._selected_deal_ticket_focus = ticket
            self.saved_ylim = None
            self._refresh_realtime_chart(force_full_redraw=True)
            self.show_toast("Soi Nến Khớp Lệnh", f"🔍 Đang xem lại nến khớp lệnh #{ticket} trên biểu đồ {target_pk}")
        except Exception as e:
            self.log_error("HIST_TREE_DOUBLE_CLICK", e, show_in_ui=False)

    def _open_calendar_popup(self, target_var: tk.StringVar):
        """Mở cửa sổ Pop-up Lịch chọn ngày và cập nhật vào biến StringVar đích."""
        DatePickerDialog(self.root, initial_date=target_var.get(), on_date_selected=target_var.set)

    # =========================================================================
    # ── TAB 4: MODULE KIỂM THỬ CHIẾN LƯỢC TICK-LEVEL (BACKTEST ADMIN) ────────
    # =========================================================================
    def _build_backtest_tab_ui(self, parent: tk.Frame):
        """Dựng giao diện Tab Kiểm thử chiến lược Backtest Tick-Level với khóa bảo vệ Quản trị viên (Admin)."""
        parent.configure(bg=PANEL_BG)

        # 1. Khung làm việc chính (Mặc định ẩn khi chưa mở khóa)
        self.bt_main_frame = tk.Frame(parent, bg=PANEL_BG)

        # 2. Khung màn hình khóa Admin (Mặc định che phủ ban đầu)
        self.bt_lock_frame = tk.Frame(parent, bg="#0b0e14")
        self.bt_lock_frame.pack(fill=tk.BOTH, expand=True)

        self._build_backtest_lock_screen()
        self._build_backtest_workspace()

    def _build_backtest_lock_screen(self):
        """Màn hình khóa Admin sang trọng với biểu tượng ổ khóa, làm mờ tính năng khi chưa nhập 'admin'."""
        card = tk.Frame(self.bt_lock_frame, bg="#131822", padx=30, pady=24, highlightthickness=1, highlightbackground=GOLD_ACCENT)
        card.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        tk.Label(card, text="🔒", bg="#131822", fg=GOLD_ACCENT, font=("Segoe UI", 32)).pack(pady=(0, 4))
        tk.Label(
            card, text="TÍNH NĂNG KIỂM THỬ CHIẾN LƯỢC (BACKTEST)",
            bg="#131822", fg=GOLD_ACCENT, font=("Segoe UI", 12, "bold")
        ).pack(pady=(0, 4))
        tk.Label(
            card, text="Chức năng chạy mô phỏng nến & Tick-Level chuyên sâu chỉ dành riêng cho Quản trị viên (Admin).\nVui lòng nhập mật khẩu Quản trị để mở khóa:",
            bg="#131822", fg="#9aa4b2", font=("Segoe UI", 8), justify=tk.CENTER
        ).pack(pady=(0, 14))

        input_box = tk.Frame(card, bg="#131822")
        input_box.pack(pady=4)

        tk.Label(input_box, text="Mật khẩu Admin:", bg="#131822", fg=TEXT_MAIN, font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 8))
        self.bt_pwd_entry = tk.Entry(
            input_box, textvariable=self.bt_pass_var, show="•", width=18,
            bg="#1c2433", fg="#ffffff", font=("Segoe UI", 10, "bold"), insertbackground=GOLD_ACCENT, relief=tk.FLAT
        )
        self.bt_pwd_entry.pack(side=tk.LEFT, ipady=3)
        self.bt_pwd_entry.bind("<Return>", lambda e: self._unlock_backtest_admin())

        btn_unlock = tk.Button(
            card, text="🔑 MỞ KHÓA ADMIN", bg=GOLD_ACCENT, fg="#000000",
            activebackground="#ffd700", activeforeground="#000000",
            font=("Segoe UI", 9, "bold"), relief=tk.FLAT, padx=18, pady=5, cursor="hand2",
            command=self._unlock_backtest_admin
        )
        btn_unlock.pack(pady=(12, 4))

        self.bt_pwd_err_lbl = tk.Label(
            card, text="", bg="#131822", fg=RED_DOWN, font=("Segoe UI", 8, "bold")
        )
        self.bt_pwd_err_lbl.pack()

    def _focus_backtest_password(self):
        if hasattr(self, "bt_pwd_entry") and self.bt_pwd_entry.winfo_exists():
            self.bt_pwd_entry.focus_set()

    def _unlock_backtest_admin(self):
        entered = str(self.bt_pass_var.get()).strip()
        if entered == "admin":
            self._backtest_unlocked = True
            self.bt_pass_var.set("")
            self.bt_pwd_err_lbl.config(text="")
            self.bt_lock_frame.pack_forget()
            self.bt_main_frame.pack(fill=tk.BOTH, expand=True)
            try:
                if self.bottom_notebook.index(self.bottom_notebook.select()) == 3:
                    self._bt_set_workspace_visible(True)
            except Exception:
                pass
            if hasattr(self, "bottom_notebook") and hasattr(self, "backtest_tab"):
                self.bottom_notebook.tab(self.backtest_tab, text="🧪 KIỂM THỬ CHIẾN LƯỢC (BACKTEST)")
            self.show_toast("Đã mở khóa Quản trị viên (Admin Mode)!", is_error=False, duration_ms=2500)
            self.log("[BACKTEST] Đã xác thực thành công quyền Quản trị viên (Admin).")
        else:
            self.bt_pwd_err_lbl.config(text="❌ Sai mật khẩu quản trị viên! Vui lòng thử lại.")
            self.bt_pwd_entry.config(bg="#3d1f24")
            self.root.after(800, lambda: self.bt_pwd_entry.config(bg="#1c2433") if hasattr(self, "bt_pwd_entry") and self.bt_pwd_entry.winfo_exists() else None)

    def _lock_backtest_admin(self):
        self._backtest_unlocked = False
        self._bt_cancel_flag = True
        self.bt_main_frame.pack_forget()
        self.bt_lock_frame.pack(fill=tk.BOTH, expand=True)
        try:
            self._bt_set_workspace_visible(False)
        except Exception:
            pass
        if hasattr(self, "bottom_notebook") and hasattr(self, "backtest_tab"):
            self.bottom_notebook.tab(self.backtest_tab, text="🔒 BACKTEST (ADMIN)")
        self.show_toast("Đã khóa lại tính năng Backtest!", is_error=False, duration_ms=2000)

    def _build_backtest_workspace(self):
        """Dựng toàn bộ không gian làm việc kiểm thử chiến lược Backtest."""
        # 1. THANH ĐIỀU KHIỂN & CHỌN NGÀY BẮT ĐẦU / KẾT THÚC
        ctrl_frame = tk.Frame(self.bt_main_frame, bg="#131822", padx=6, pady=4)
        ctrl_frame.pack(fill=tk.X, padx=6, pady=(4, 2))

        tk.Label(ctrl_frame, text="Mã:", bg="#131822", fg=TEXT_MUTED, font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(2, 2))
        self.bt_sym_combo = ttk.Combobox(ctrl_frame, textvariable=self.bt_sym_var, values=["XAUUSDm", "BTCUSDm", "XAUUSD", "BTCUSD"], width=8, state="readonly")
        self.bt_sym_combo.pack(side=tk.LEFT, padx=(0, 6))
        self.bt_sym_combo.bind("<<ComboboxSelected>>", self._on_bt_symbol_changed)

        self.bt_tf_info_lbl = tk.Label(ctrl_frame, text="⚡ Khung: Menu", bg="#131822", fg="#38bdf8", font=("Segoe UI", 8, "bold"))
        self.bt_tf_info_lbl.pack(side=tk.LEFT, padx=(2, 6))

        # Chọn ngày bắt đầu kèm nút mở Lịch Pop-up (DatePickerDialog)
        tk.Label(ctrl_frame, text="Từ ngày:", bg="#131822", fg=GOLD_ACCENT, font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT, padx=(2, 2))
        start_entry = tk.Entry(ctrl_frame, textvariable=self.bt_start_var, width=10, bg="#1c2433", fg="#ffffff", font=("Consolas", 8, "bold"), relief=tk.FLAT)
        start_entry.pack(side=tk.LEFT, ipady=1)
        start_entry.bind("<Double-Button-1>", lambda e: self._open_calendar_popup(self.bt_start_var))
        tk.Button(
            ctrl_frame, text="📅", bg="#232d3f", fg=GOLD_ACCENT, relief=tk.FLAT, font=("Segoe UI", 8),
            padx=4, pady=0, cursor="hand2", command=lambda: self._open_calendar_popup(self.bt_start_var)
        ).pack(side=tk.LEFT, padx=(2, 6))

        # Chọn ngày kết thúc kèm nút mở Lịch Pop-up
        tk.Label(ctrl_frame, text="Đến ngày:", bg="#131822", fg=GOLD_ACCENT, font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT, padx=(2, 2))
        end_entry = tk.Entry(ctrl_frame, textvariable=self.bt_end_var, width=10, bg="#1c2433", fg="#ffffff", font=("Consolas", 8, "bold"), relief=tk.FLAT)
        end_entry.pack(side=tk.LEFT, ipady=1)
        end_entry.bind("<Double-Button-1>", lambda e: self._open_calendar_popup(self.bt_end_var))
        tk.Button(
            ctrl_frame, text="📅", bg="#232d3f", fg=GOLD_ACCENT, relief=tk.FLAT, font=("Segoe UI", 8),
            padx=4, pady=0, cursor="hand2", command=lambda: self._open_calendar_popup(self.bt_end_var)
        ).pack(side=tk.LEFT, padx=(2, 6))

        tk.Label(ctrl_frame, text="Vốn ($):", bg="#131822", fg=TEXT_MUTED, font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(2, 2))
        tk.Entry(ctrl_frame, textvariable=self.bt_capital_var, width=6, bg="#1c2433", fg="#ffffff", font=("Consolas", 8), relief=tk.FLAT).pack(side=tk.LEFT, ipady=1, padx=(0, 4))

        tk.Label(ctrl_frame, text="Lot:", bg="#131822", fg=TEXT_MUTED, font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(2, 2))
        tk.Entry(ctrl_frame, textvariable=self.bt_lot_var, width=5, bg="#1c2433", fg="#ffffff", font=("Consolas", 8), relief=tk.FLAT).pack(side=tk.LEFT, ipady=1, padx=(0, 6))

        # Chế độ dữ liệu Tick 1s vs Nến chuẩn
        tk.Label(ctrl_frame, text="Dữ liệu:", bg="#131822", fg=TEXT_MUTED, font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(2, 2))
        self.bt_prec_combo = ttk.Combobox(
            ctrl_frame, textvariable=self.bt_data_precision_var,
            values=["⚡ Nến Tick 1s (S1)", "📊 Nến Thường (M1/M5)"],
            width=15, state="readonly"
        )
        self.bt_prec_combo.pack(side=tk.LEFT, padx=(0, 6))

        # Checkbutton Bật Não AI Kronos
        self.bt_ai_chk = tk.Checkbutton(
            ctrl_frame, text="🧠 Não AI Kronos", variable=self.bt_use_ai_var,
            bg="#131822", fg=GOLD_ACCENT, selectcolor="#0b0e14",
            activebackground="#131822", activeforeground=GOLD_ACCENT,
            font=("Segoe UI", 8, "bold"), cursor="hand2"
        )
        self.bt_ai_chk.pack(side=tk.LEFT, padx=(0, 4))

        # Nhóm nút bấm tác vụ
        tk.Button(
            ctrl_frame, text="🔒 Khóa", bg="#232d3f", fg=TEXT_MUTED,
            font=("Segoe UI", 8), relief=tk.FLAT, padx=6, pady=1, cursor="hand2",
            command=self._lock_backtest_admin
        ).pack(side=tk.RIGHT, padx=2)

        tk.Button(
            ctrl_frame, text="🗑️ Xóa Lệnh", bg="#3a2026", fg="#ff7b88",
            activebackground="#ff4b60", activeforeground="#ffffff",
            font=("Segoe UI", 8, "bold"), relief=tk.FLAT, padx=6, pady=1, cursor="hand2",
            command=self._clear_backtest_data
        ).pack(side=tk.RIGHT, padx=2)

        tk.Button(
            ctrl_frame, text="💾 Áp Dụng Vào Bot Live", bg="#163832", fg="#00e676",
            activebackground="#00e676", activeforeground="#000000",
            font=("Segoe UI", 8, "bold"), relief=tk.FLAT, padx=7, pady=1, cursor="hand2",
            command=self._apply_bt_settings_to_menu
        ).pack(side=tk.RIGHT, padx=3)

        tk.Button(
            ctrl_frame, text="⏹ Dừng", bg="#4a1e24", fg="#ff4b60",
            font=("Segoe UI", 8, "bold"), relief=tk.FLAT, padx=7, pady=1, cursor="hand2",
            command=self._stop_backtest_worker
        ).pack(side=tk.RIGHT, padx=2)

        tk.Button(
            ctrl_frame, text="▶ BẮT ĐẦU TEST", bg=GREEN_UP, fg="#000000",
            activebackground="#00ff88", activeforeground="#000000",
            font=("Segoe UI", 8, "bold"), relief=tk.FLAT, padx=9, pady=1, cursor="hand2",
            command=self._start_backtest_worker
        ).pack(side=tk.RIGHT, padx=3)

        tk.Button(
            ctrl_frame, text="🔄 Lấy từ Menu", bg="#1e2c44", fg=CYAN_LIVE,
            font=("Segoe UI", 8, "bold"), relief=tk.FLAT, padx=7, pady=1, cursor="hand2",
            command=self._copy_menu_settings_to_bt
        ).pack(side=tk.RIGHT, padx=2)

        # 2. THANH TRẠNG THÁI & TIẾN ĐỘ KIỂM THỬ (PROGRESS BAR)
        status_box = tk.Frame(self.bt_main_frame, bg="#0e131b", padx=8, pady=2)
        status_box.pack(fill=tk.X, padx=6, pady=(0, 2))

        self.bt_status_lbl = tk.Label(
            status_box, text="Sẵn sàng kiểm thử. Chọn khoảng ngày và nhấn '▶ BẮT ĐẦU TEST'.",
            bg="#0e131b", fg=TEXT_MUTED, font=("Consolas", 8), anchor="w"
        )
        self.bt_status_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.bt_progress_canvas = tk.Canvas(status_box, width=160, height=8, bg="#1c2433", highlightthickness=0)
        self.bt_progress_canvas.pack(side=tk.RIGHT, padx=(4, 0))
        self.bt_progress_bar = self.bt_progress_canvas.create_rectangle(0, 0, 0, 8, fill=GREEN_UP, outline="")

        tk.Button(
            status_box, text="🎬 Xem phát lại", bg="#24203a", fg="#c4b5fd", font=("Segoe UI", 8, "bold"),
            relief=tk.FLAT, padx=7, pady=0, cursor="hand2", command=self._open_bt_player
        ).pack(side=tk.RIGHT, padx=(4, 4))
        tk.Button(
            status_box, text="⏯ Tạm dừng / Phát tiếp", bg="#3a3220", fg="#ffd54f", font=("Segoe UI", 8, "bold"),
            relief=tk.FLAT, padx=7, pady=0, cursor="hand2", command=self._bt_toggle_pause
        ).pack(side=tk.RIGHT, padx=(4, 0))
        if not hasattr(self, "bt_engine_var"):
            self.bt_engine_var = tk.StringVar(value=self.BT_LIVE_ENGINE_LABEL)
        eng_cb = ttk.Combobox(
            status_box, textvariable=self.bt_engine_var, values=[self.BT_LIVE_ENGINE_LABEL, self.BT_LEGACY_ENGINE_LABEL],
            width=25, state="readonly"
        )
        eng_cb.pack(side=tk.RIGHT, padx=(4, 2))
        eng_cb.bind("<<ComboboxSelected>>", lambda e: self._bt_on_engine_changed())
        tk.Label(status_box, text="Engine:", bg="#0e131b", fg=TEXT_MUTED, font=("Segoe UI", 8)).pack(side=tk.RIGHT)
        tk.Button(
            status_box, text="⚙️ Thông số Backtest", bg="#3a2a0b", fg=GOLD_ACCENT,
            font=("Segoe UI", 8, "bold"), relief=tk.FLAT, padx=7, pady=0, cursor="hand2",
            command=self._open_bt_settings_window
        ).pack(side=tk.RIGHT, padx=(4, 8))
        self._bt_on_engine_changed()

        # 3. DÃY 6 THẺ KPI SCORECARDS CHỈ SỐ TÀI CHÍNH
        kpi_frame = tk.Frame(self.bt_main_frame, bg=PANEL_BG)
        kpi_frame.pack(fill=tk.X, padx=6, pady=(1, 3))
        for col_idx in range(6):
            kpi_frame.columnconfigure(col_idx, weight=1, uniform="kpi")

        self.bt_kpi_total_lbl = self._create_kpi_card(kpi_frame, 0, "TỔNG LỆNH", "0 Lệnh", GOLD_ACCENT)
        self.bt_kpi_winrate_lbl = self._create_kpi_card(kpi_frame, 1, "WIN RATE (%)", "0.0%", GREEN_UP)
        self.bt_kpi_pnl_lbl = self._create_kpi_card(kpi_frame, 2, "LÃI RÒNG ($)", "+0.00 $", CYAN_LIVE)
        self.bt_kpi_pf_lbl = self._create_kpi_card(kpi_frame, 3, "PROFIT FACTOR", "0.00", GOLD_ACCENT)
        self.bt_kpi_dd_lbl = self._create_kpi_card(kpi_frame, 4, "MAX DRAWDOWN", "-0.00$", RED_DOWN)
        self.bt_kpi_maxwin_lbl = self._create_kpi_card(kpi_frame, 5, "LÃI TB / LỆNH", "+0.00 $", TEXT_MAIN)

        # 4. CHIA 2 NỬA: BẢNG LỆNH BÊN TRÁI (60%) & ĐỒ THỊ EQUITY CURVE BÊN PHẢI (40%)
        paned = tk.PanedWindow(self.bt_main_frame, orient=tk.HORIZONTAL, bg="#1a2230", sashwidth=4)
        paned.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 4))

        # Nửa Trái: Bảng chi tiết toàn bộ các lệnh mô phỏng
        left_tbl_frame = tk.Frame(paned, bg=PANEL_BG)
        paned.add(left_tbl_frame, minsize=350, stretch="always")

        cols = ("ticket", "in_time", "out_time", "duration", "type", "lot", "in_price", "out_price", "pnl", "reason")
        headers = {
            "ticket": "Mã Lệnh", "in_time": "Giờ Vào", "out_time": "Giờ Đóng", "duration": "Thời Gian",
            "type": "Loại", "lot": "Lot", "in_price": "Giá Vào", "out_price": "Giá Ra",
            "pnl": "Lãi Ròng ($)", "reason": "Lý Do Đóng"
        }
        widths = {
            "ticket": 65, "in_time": 95, "out_time": 95, "duration": 70,
            "type": 45, "lot": 45, "in_price": 75, "out_price": 75,
            "pnl": 85, "reason": 130
        }

        self.bt_tree = ttk.Treeview(left_tbl_frame, columns=cols, show="headings", height=5)
        sb_y = ttk.Scrollbar(left_tbl_frame, orient="vertical", command=self.bt_tree.yview)
        self.bt_tree.configure(yscrollcommand=sb_y.set)
        sb_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.bt_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        for c in cols:
            self.bt_tree.heading(c, text=headers[c])
            self.bt_tree.column(c, width=widths[c], anchor=tk.CENTER)

        self.bt_tree.tag_configure("tag_win", foreground=GREEN_UP)
        self.bt_tree.tag_configure("tag_loss", foreground=RED_DOWN)
        self.bt_tree.tag_configure("tag_be", foreground=GOLD_ACCENT)

        # Nửa Phải: Đồ thị đường cong vốn tài khoản (Equity Curve)
        right_chart_frame = tk.Frame(paned, bg="#0b0e14", highlightthickness=1, highlightbackground="#1b2435")
        paned.add(right_chart_frame, minsize=260, stretch="always")

        chart_hdr = tk.Frame(right_chart_frame, bg="#131822", padx=6, pady=2)
        chart_hdr.pack(fill=tk.X)
        tk.Label(
            chart_hdr, text="📈 ĐỒ THỊ TĂNG TRƯỞNG VỐN (EQUITY CURVE)",
            bg="#131822", fg=CYAN_LIVE, font=("Segoe UI", 8, "bold")
        ).pack(side=tk.LEFT)

        self.bt_chart_canvas = tk.Canvas(right_chart_frame, bg="#0b0e14", highlightthickness=0)
        self.bt_chart_canvas.pack(fill=tk.BOTH, expand=True)
        self.bt_chart_canvas.bind("<Configure>", lambda e: self._draw_equity_curve(self._bt_equity_points))
        self._update_bt_tf_display()

    def _create_kpi_card(self, parent: tk.Frame, col_idx: int, title: str, init_val: str, val_color: str) -> tk.Label:
        """Tạo thẻ hiển thị chỉ số tài chính dạng hộp nhỏ."""
        box = tk.Frame(parent, bg="#131822", padx=4, pady=2, highlightthickness=1, highlightbackground="#1c2433")
        box.grid(row=0, column=col_idx, sticky="nsew", padx=2)
        tk.Label(box, text=title, bg="#131822", fg=TEXT_MUTED, font=("Segoe UI", 7, "bold")).pack()
        lbl = tk.Label(box, text=init_val, bg="#131822", fg=val_color, font=("Segoe UI", 9, "bold"))
        lbl.pack()
        return lbl

    def _update_bt_tf_display(self):
        """Cập nhật nhãn hiển thị khung thời gian theo cấu hình chiến lược trên thanh Backtest."""
        if hasattr(self, "bt_tf_info_lbl") and self.bt_tf_info_lbl.winfo_exists():
            sym = self.bt_sym_var.get().strip()
            pk = "BTCUSD" if "BTC" in sym.upper() else "XAUUSD"
            cfg = self._get_bt_cfg(pk)
            ana_tf = str(cfg.get("analysis_tf", "M15")).strip()
            scalp_tf = str(cfg.get("tf", "M5")).strip()
            desc = f"{ana_tf}" if ana_tf == scalp_tf else f"{ana_tf} ➔ {scalp_tf}"
            self.bt_tf_info_lbl.config(text=f"⚡ Khung: {desc} (Backtest {pk})")

    def _copy_menu_settings_to_bt(self):
        """Chép TOÀN BỘ thông số chiến lược từ Menu Live sang cấu hình Backtest riêng của mã đang chọn (không đổi cấu hình Live)."""
        sym = self.bt_sym_var.get().strip()
        is_btc = "BTC" in sym.upper()
        target_pk = "BTCUSD" if is_btc else "XAUUSD"
        cfg = self._get_profile_cfg(target_pk)
        cfg_sym = str(cfg.get("symbol", sym)).strip()
        cfg_lot = str(cfg.get("lot", "0.01" if is_btc else "0.02")).strip()

        if not isinstance(getattr(self, "bt_settings", None), dict):
            self._load_bt_settings()
        self.bt_settings[target_pk] = self._bt_filter_cfg(cfg)
        self._save_bt_settings()

        if cfg_sym:
            self.bt_sym_var.set(cfg_sym)
        if cfg_lot:
            self.bt_lot_var.set(cfg_lot)

        self._update_bt_tf_display()
        self._refresh_bt_settings_window()
        self.show_toast(f"Đã chép toàn bộ thông số Menu Live [{target_pk}] sang Backtest!", is_error=False, duration_ms=2500)

    def _on_bt_symbol_changed(self, event=None):
        """Khi người dùng chọn mã tiền tệ ở Backtest (BTC / XAU): Cập nhật thông số Backtest tương ứng mà KHÔNG hoán đổi Menu Bot Live."""
        sym = self.bt_sym_var.get().strip()
        is_btc = "BTC" in sym.upper()
        target_pk = "BTCUSD" if is_btc else "XAUUSD"

        # Tự động nạp thông số lot tham khảo từ profile mà không chuyển đổi Profile Live
        cfg = self._get_profile_cfg(target_pk)
        lot = str(cfg.get("lot", "0.01" if is_btc else "0.02")).strip()
        if lot:
            self.bt_lot_var.set(lot)

        self._update_bt_tf_display()
        self.show_toast(f"Backtest đã chọn [{sym}] (Lot tham khảo: {lot})", is_error=False, duration_ms=2500)
        self._refresh_bt_settings_window()

    def _apply_bt_settings_to_menu(self):
        """Chép cấu hình Backtest (mọi thông số khác Live + Mã + Lot) sang Menu Bot Live của đúng mã đang test.
        Có popup xác nhận liệt kê từng thông số thay đổi; không bấm YES thì Live giữ nguyên."""
        sym = self.bt_sym_var.get().strip()
        is_btc = "BTC" in sym.upper()
        target_pk = "BTCUSD" if is_btc else "XAUUSD"
        test_lot = self.bt_lot_var.get().strip()

        # Thông số Backtest sai / mâu thuẫn (ô tô đỏ): không chép sang bot live, yêu cầu nhập lại
        try:
            bt_cfg_chk = self._get_bt_cfg(target_pk)
            bt_keys = set(self.bt_settings.get(target_pk, {})) | {"lot"}
            bt_errs = [it["msg"] for it in validate_param_values(bt_cfg_chk)
                       if it["level"] == "error" and bt_keys & set(it["keys"])]
        except Exception:
            bt_errs = []
        if bt_errs:
            messagebox.showwarning(
                "Thông số Backtest chưa hợp lệ",
                "Chưa áp dụng vào Bot Live. Vui lòng nhập lại các ô tô đỏ trong bảng Thông số Backtest:\n\n"
                + "\n".join(f"• {m}" for m in bt_errs[:8]),
                parent=self.root,
            )
            return

        changes = self._bt_collect_changes(target_pk)
        if not changes:
            self.show_toast(f"Cấu hình Backtest giống hệt Bot Live [{target_pk}], không có gì để áp dụng.", is_error=False, duration_ms=2500)
            return
        smap = self._get_settings_vars_map()
        lines = []
        for k, old, new in changes[:20]:
            lbl = self._bt_param_spec(k, smap.get(k))["label"]
            lines.append(f"  • {lbl}: {self._bt_show_val(k, old, smap.get(k))} → {self._bt_show_val(k, new, smap.get(k))}")
        if len(changes) > 20:
            lines.append(f"  ... và {len(changes) - 20} thông số khác")

        # Hiển thị popup xác nhận an toàn để người dùng không bao giờ bấm nhầm làm thay đổi cấu hình Live
        confirm = messagebox.askyesno(
            "Xác Nhận Áp Dụng Sang Bot Live",
            f"⚠️ CẢNH BÁO GHI ĐÈ BOT LIVE [{target_pk}]:\n\n"
            f"Sẽ đổi {len(changes)} thông số của bot live:\n" + "\n".join(lines) + "\n\n"
            f"Thao tác này ghi đè cấu hình Live ngay lập tức!\n"
            f"(YES = áp dụng vào Live, NO = hủy bỏ).",
            parent=self.root
        )
        if not confirm:
            self.show_toast("Đã hủy bỏ thao tác áp dụng vào Bot Live.", is_error=False, duration_ms=2000)
            return

        # 1. Đảm bảo Menu đang trỏ vào đúng profile đích
        if self.active_profile_key != target_pk:
            self._switch_active_profile(target_pk)

        # 2. Cập nhật các biến giao diện & lưu vào profile
        smap = self._get_settings_vars_map()
        for k, _old, new in changes:
            var = smap.get(k)
            if var is None:
                continue
            try:
                var.set(self._bt_to_bool(new) if isinstance(var, tk.BooleanVar) else str(new))
            except Exception as e:
                self._write_file_log(f"[BACKTEST] Không áp dụng được {k}={new}: {e}", is_error=True)

        # Đồng bộ ngược vào profile context, lưu xuống ổ cứng và áp dụng ngay cho Backend AI
        self._sync_self_to_active_ctx(include_ai_state=False)
        self.save_user_settings()
        self.apply_live_settings_and_rescan(profile_key=target_pk, source_label="Áp dụng từ Backtest")
        self._update_bt_tf_display()
        self.show_toast(f"✓ Đã áp dụng {len(changes)} thông số Backtest vào Bot Live [{target_pk}]!", is_error=False, duration_ms=3000)
        self.log(f"[BACKTEST] Đã áp dụng {len(changes)} thông số Backtest vào cấu hình Live [{target_pk}]: "
                 + ", ".join(f"{k}={new}" for k, _o, new in changes[:30]))

    # ── CẤU HÌNH BACKTEST RIÊNG (tách khỏi cấu hình bot live) ──────────────────────────────
    # Sửa thông số để kiểm thử KHÔNG được làm đổi bot live: backtest đọc self.bt_settings (lưu ở backtest_settings.json),
    # "Lấy từ Menu" chép live -> backtest, "Áp Dụng Vào Bot Live" chép backtest -> live (có xác nhận).
    BT_SETTINGS_FILE_NAME = "backtest_settings.json"
    # Khóa không thuộc cấu hình backtest: bí mật (token), kết nối/thông báo, không ảnh hưởng kết quả, hoặc lấy từ thanh Backtest (Mã, Lot)
    BT_EXCLUDED_KEYS = frozenset({
        "hf_token", "hf_space", "engine", "telegram_enabled", "telegram_bot_token", "telegram_chat_id", "telegram_admin_ids",
        "telegram_notify_open", "telegram_notify_close", "mt5_path", "magic_number", "ai_threads", "debug_trace",
        "fast_sl_cooldown_mins", "symbol", "lot",
    })
    BT_SECRET_KEYS = ("hf_token", "telegram_bot_token", "telegram_chat_id")
    # Khóa đã ẩn trên menu live thì cũng ẩn trong bảng backtest và không chép sang live
    BT_HIDDEN_KEYS = frozenset({"trend_filter", "structure_conflict_mode"})

    def _bt_settings_path(self) -> str:
        return os.path.join(SETTINGS_DIR, self.BT_SETTINGS_FILE_NAME)

    def _bt_filter_cfg(self, cfg: dict) -> dict:
        return {k: v for k, v in dict(cfg or {}).items() if k not in self.BT_EXCLUDED_KEYS}

    @staticmethod
    def _bt_to_bool(v) -> bool:
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    @staticmethod
    def _bt_norm(v) -> str:
        """Chuẩn hóa giá trị để so sánh backtest với live (True/'True'/'1' như nhau, '1.0' == '1')."""
        if isinstance(v, bool):
            return "1" if v else "0"
        t = "" if v is None else str(v).strip()
        if t.lower() in ("true", "false"):
            return "1" if t.lower() == "true" else "0"
        try:
            return repr(float(t))
        except ValueError:
            return t

    def _load_bt_settings(self) -> dict:
        """Nạp cấu hình backtest riêng từ ổ đĩa (không chứa token). Lỗi đọc -> bắt đầu trống (chép từ live khi cần)."""
        self.bt_settings = {}
        try:
            path = self._bt_settings_path()
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for pk in ("XAUUSD", "BTCUSD"):
                        if isinstance(data.get(pk), dict):
                            self.bt_settings[pk] = self._bt_filter_cfg(data[pk])
        except Exception as e:
            self._write_file_log(f"[BACKTEST] Không đọc được cấu hình backtest ({e}) -> chép lại từ Live khi cần.", is_error=True)
        return self.bt_settings

    def _save_bt_settings(self):
        try:
            os.makedirs(SETTINGS_DIR, exist_ok=True)
            path = self._bt_settings_path()
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({pk: self._bt_filter_cfg(v) for pk, v in self.bt_settings.items()}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as e:
            self._write_file_log(f"[BACKTEST] Không lưu được cấu hình backtest: {e}", is_error=True)

    def _get_bt_cfg(self, pk: str) -> dict:
        """Cấu hình dùng cho backtest của profile pk = cấu hình live làm nền (khóa mới chưa có trong bản backtest)
        phủ bằng cấu hình backtest riêng; Mã/Lot lấy theo thanh Backtest. Lần đầu chưa có bản backtest -> chép từ live.
        KHÔNG bao giờ ghi vào cấu hình live; bỏ token."""
        if not isinstance(getattr(self, "bt_settings", None), dict):
            self._load_bt_settings()
        live = self._get_profile_cfg(pk)
        if pk not in self.bt_settings:
            self.bt_settings[pk] = self._bt_filter_cfg(live)
            self._save_bt_settings()
        cfg = dict(live)
        cfg.update(self.bt_settings[pk])
        for k in self.BT_SECRET_KEYS:
            cfg.pop(k, None)
        try:
            sym = self.bt_sym_var.get().strip()
            if sym and (("BTC" in sym.upper()) == (pk == "BTCUSD")):
                cfg["symbol"] = sym
                lot = self.bt_lot_var.get().strip()
                if lot:
                    cfg["lot"] = lot
        except Exception:
            pass
        return cfg

    def _bt_collect_changes(self, pk: str) -> list:
        """Danh sách (khóa, giá trị live, giá trị backtest) khác nhau, gồm Mã/Lot của thanh Backtest. Bỏ khóa ẩn/khóa không thuộc backtest."""
        bt_cfg = self._get_bt_cfg(pk)
        live_cfg = self._get_profile_cfg(pk)
        smap = self._get_settings_vars_map()
        changes = []
        for k in list(self.bt_settings.get(pk, {}).keys()) + ["symbol", "lot"]:
            if k not in smap or k in self.BT_HIDDEN_KEYS or (k in self.BT_EXCLUDED_KEYS and k not in ("symbol", "lot")):
                continue
            new = bt_cfg.get(k)
            if new is None or str(new).strip() == "":
                continue
            if self._bt_norm(new) != self._bt_norm(live_cfg.get(k)):
                changes.append((k, live_cfg.get(k), new))
        return changes

    def _bt_param_spec(self, key: str, live_var) -> dict:
        """Mô tả 1 ô thông số (nhãn, kiểu, lựa chọn, nút ?) lấy từ chính ô trên menu live để bảng backtest giống menu."""
        meta = self.__dict__.get("_param_meta", {}).get(str(live_var)) if live_var is not None else None
        meta = dict(meta or {})
        guide = PARAM_HELP_GUIDE.get(key, {})
        label = str(meta.get("label") or guide.get("title") or key).strip().rstrip(":").strip()
        typ = meta.get("type")
        if typ is None:
            typ = "bool" if isinstance(live_var, tk.BooleanVar) else "entry"
        help_key = meta.get("help_key") or (key if key in PARAM_HELP_GUIDE else None)
        return {"type": typ, "label": label, "values": list(meta.get("values") or []),
                "code_labels": dict(meta.get("code_labels") or {}), "help_key": help_key}

    def _bt_show_val(self, key: str, v, live_var=None) -> str:
        spec = self._bt_param_spec(key, live_var)
        if spec["type"] == "bool":
            return "BẬT" if self._bt_to_bool(v) else "TẮT"
        if spec["type"] == "coded":
            return str(spec["code_labels"].get(str(v), v))
        return str(v)

    def _refresh_bt_settings_window(self):
        win = getattr(self, "_bt_cfg_win", None)
        try:
            if win is not None and win.winfo_exists():
                self._open_bt_settings_window()
        except Exception:
            pass

    def _open_bt_settings_window(self):
        """Bảng thông số RIÊNG cho Backtest (giống Menu Mục 1-4). Sửa ở đây chỉ đổi cấu hình backtest, không đổi bot live."""
        sym = self.bt_sym_var.get().strip()
        pk = "BTCUSD" if "BTC" in sym.upper() else "XAUUSD"
        old = getattr(self, "_bt_cfg_win", None)
        geo = None
        try:
            if old is not None and old.winfo_exists():
                geo = old.geometry()
                old.destroy()
        except Exception:
            pass
        cfg = self._get_bt_cfg(pk)
        smap = self._get_settings_vars_map()

        win = tk.Toplevel(self.root)
        self._bt_cfg_win = win
        win.title(f"Thông số Backtest [{pk}] - riêng, không ảnh hưởng bot live")
        win.configure(bg=PANEL_BG)
        win.geometry(geo or "540x700")

        hdr = tk.Frame(win, bg="#2a1d0b", padx=8, pady=6)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text=f"⚙️ CẤU HÌNH BACKTEST [{pk}]", bg="#2a1d0b", fg=GOLD_ACCENT,
                 font=("Segoe UI", 10, "bold"), anchor="w").pack(fill=tk.X)
        tk.Label(hdr, text="Sửa ở đây chỉ dùng cho kiểm thử, KHÔNG làm thay đổi bot live.\n"
                           "Mã và Lot lấy theo thanh Backtest. Dùng 'Áp Dụng Vào Bot Live' khi thông số đã ổn.",
                 bg="#2a1d0b", fg=TEXT_MAIN, font=("Segoe UI", 8), justify="left", anchor="w").pack(fill=tk.X)

        btns = tk.Frame(win, bg=PANEL_BG, pady=4)
        btns.pack(fill=tk.X, padx=8)
        tk.Button(btns, text="🔄 Lấy từ Menu Live", bg="#1e2c44", fg=CYAN_LIVE, font=("Segoe UI", 8, "bold"),
                  relief=tk.FLAT, padx=7, cursor="hand2", command=self._copy_menu_settings_to_bt).pack(side=tk.LEFT, padx=2)
        tk.Button(btns, text="💾 Áp Dụng Vào Bot Live", bg="#163832", fg="#00e676", font=("Segoe UI", 8, "bold"),
                  relief=tk.FLAT, padx=7, cursor="hand2", command=self._apply_bt_settings_to_menu).pack(side=tk.LEFT, padx=2)
        tk.Button(btns, text="Đóng", bg="#232d3f", fg=TEXT_MUTED, font=("Segoe UI", 8),
                  relief=tk.FLAT, padx=7, cursor="hand2", command=win.destroy).pack(side=tk.RIGHT, padx=2)
        btns2 = tk.Frame(win, bg=PANEL_BG)
        btns2.pack(fill=tk.X, padx=8, pady=(0, 4))
        tk.Button(btns2, text="💾 Lưu ra file...", bg="#2a2440", fg="#c4b5fd", font=("Segoe UI", 8, "bold"),
                  relief=tk.FLAT, padx=7, cursor="hand2", command=lambda: self._bt_export_settings_file(pk)).pack(side=tk.LEFT, padx=2)
        tk.Button(btns2, text="📂 Mở file...", bg="#2a2440", fg="#c4b5fd", font=("Segoe UI", 8, "bold"),
                  relief=tk.FLAT, padx=7, cursor="hand2", command=lambda: self._bt_import_settings_file(pk)).pack(side=tk.LEFT, padx=2)
        tk.Label(btns2, text="giữ lại / nạp lại bộ thông số Backtest", bg=PANEL_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 7, "italic")).pack(side=tk.LEFT, padx=6)
        # Lý do ô sai (tô đỏ) / cần lưu ý (tô cam) của bảng Backtest; không có vấn đề thì ẩn
        issue_box = tk.Frame(win, bg=PANEL_BG)
        issue_box.pack(fill=tk.X, padx=10)
        self._bt_issue_lbl = tk.Label(issue_box, text="", bg=PANEL_BG, fg="#ff6b6b", font=("Segoe UI", 8, "bold"),
                                      justify=tk.LEFT, anchor="w", wraplength=500)

        outer = tk.Frame(win, bg=PANEL_BG)
        outer.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(outer, bg=PANEL_BG, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        body = tk.Frame(canvas, bg=PANEL_BG)
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        body_id = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(body_id, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        win.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        self._bt_form_vars = {}
        self._bt_form_widgets = {}
        for sec_num in (1, 2, 3, 4):
            title, keys = self.SETTINGS_SECTION_MAP[sec_num]
            shown = [k for k in keys if k in smap and k not in self.BT_EXCLUDED_KEYS and k not in self.BT_HIDDEN_KEYS]
            if not shown:
                continue
            tk.Label(body, text=title, bg="#161e2e", fg=GOLD_ACCENT, font=("Segoe UI", 9, "bold"),
                     anchor="w", padx=6, pady=3).pack(fill=tk.X, padx=4, pady=(8, 2))
            for key in shown:
                try:
                    self._bt_add_form_row(body, pk, key, smap.get(key), cfg.get(key))
                except Exception as e:
                    self._write_file_log(f"[BACKTEST] Không dựng được ô {key}: {e}", is_error=True)
        self._schedule_param_validation("bt", lambda: self._validate_bt_form(pk))

    BT_EXPORT_KIND = "kronos_backtest_settings"

    def _bt_dialog_parent(self):
        """Cửa sổ cha cho hộp chọn file: bảng Thông số Backtest nếu còn mở, không thì cửa sổ chính."""
        win = getattr(self, "_bt_cfg_win", None)
        try:
            if win is not None and win.winfo_exists():
                return win
        except Exception:
            pass
        return getattr(self, "root", None)

    def _bt_export_settings_file(self, pk: str):
        """Lưu bộ thông số Backtest của pk ra file .json (KHÔNG có token) để giữ lại / dùng lại sau.
        Cùng dạng file cấu hình của app ({"profiles": {pk: {...}}}) nên dùng được với tools/live_replay.py --config."""
        try:
            cfg = self._get_bt_cfg(pk)
            for k in self.BT_SECRET_KEYS + self.MACHINE_LOCAL_KEYS:  # không token, không thông số riêng máy
                cfg.pop(k, None)
            parent = self._bt_dialog_parent()
            file_path = filedialog.asksaveasfilename(
                parent=parent, title=f"Lưu cấu hình Backtest [{pk}] ra file", defaultextension=".json",
                initialfile=f"backtest_{pk}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")])
            if not file_path:
                return
            data = {"kind": self.BT_EXPORT_KIND, "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "last_active_tab": pk, "profiles": {pk: cfg}}
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            self.log(f"[BACKTEST] Đã lưu cấu hình Backtest [{pk}] ra file: {file_path}")
            self.show_toast(f"Đã lưu cấu hình Backtest: {os.path.basename(file_path)}", is_error=False, duration_ms=2500)
        except Exception as e:
            self._write_file_log(f"[BACKTEST] Lỗi lưu file cấu hình Backtest: {e}", is_error=True)
            self.show_toast(f"Lỗi lưu file cấu hình Backtest: {e}", is_error=True, duration_ms=3500)

    def _bt_import_settings_file(self, pk: str):
        """Nạp bộ thông số [pk] từ file .json vào cấu hình Backtest: file đã lưu bằng nút 'Lưu ra file' HOẶC file cấu hình
        xuất từ menu live. Chỉ thay cấu hình BACKTEST (không đụng bot live), bỏ token và các khóa không thuộc backtest."""
        try:
            parent = self._bt_dialog_parent()
            file_path = filedialog.askopenfilename(parent=parent, title=f"Mở file cấu hình cho Backtest [{pk}]",
                                                   filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")])
            if not file_path:
                return
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            prof = None
            if isinstance(data, dict):
                profs = data.get("profiles")
                if isinstance(profs, dict):
                    prof = profs.get(pk)
                elif "analysis_tf" in data or "consensus" in data:  # file chỉ có 1 bộ thông số
                    prof = data
            if not isinstance(prof, dict) or not prof:
                self.show_toast(f"File không có cấu hình [{pk}]: {os.path.basename(file_path)}", is_error=True, duration_ms=3500)
                return
            new = {k: v for k, v in self._bt_filter_cfg(prof).items() if k not in self.BT_SECRET_KEYS and k not in self.BT_HIDDEN_KEYS}
            if not messagebox.askyesno(
                    "Nạp cấu hình Backtest",
                    f"Nạp {len(new)} thông số [{pk}] từ file:\n{os.path.basename(file_path)}\n\n"
                    f"Chỉ thay cấu hình BACKTEST, KHÔNG thay đổi bot live.", parent=parent):
                return
            self._get_bt_cfg(pk)  # bảo đảm đã nạp cấu hình backtest hiện có
            self.bt_settings[pk] = new
            self._save_bt_settings()
            lot = prof.get("lot")
            if lot not in (None, "") and hasattr(self, "bt_lot_var"):
                self.bt_lot_var.set(str(lot))
            try:
                self._update_bt_tf_display()
            except Exception:
                pass
            self._refresh_bt_settings_window()
            self.log(f"[BACKTEST] Đã nạp {len(new)} thông số Backtest [{pk}] từ file: {file_path}")
            self.show_toast(f"Đã nạp cấu hình Backtest từ {os.path.basename(file_path)}", is_error=False, duration_ms=2500)
        except Exception as e:
            self._write_file_log(f"[BACKTEST] Lỗi nạp file cấu hình Backtest: {e}", is_error=True)
            self.show_toast(f"Lỗi nạp file cấu hình Backtest: {e}", is_error=True, duration_ms=3500)

    def _bt_add_form_row(self, parent, pk: str, key: str, live_var, value):
        spec = self._bt_param_spec(key, live_var)
        row = tk.Frame(parent, bg=PANEL_BG)
        row.pack(fill=tk.X, padx=10, pady=1)

        def _store(v):
            self.bt_settings.setdefault(pk, {})[key] = v
            self._save_bt_settings()
            if key in ("analysis_tf", "tf"):
                self._update_bt_tf_display()
            self._schedule_param_validation("bt", lambda: self._validate_bt_form(pk))

        if spec["type"] == "bool":
            var = tk.BooleanVar(value=self._bt_to_bool(value))
            tk.Checkbutton(row, text=spec["label"], variable=var, bg=PANEL_BG, fg=TEXT_MAIN, selectcolor=BG_DARK,
                           activebackground=PANEL_BG, font=("Segoe UI", 8, "bold"), anchor="w",
                           command=lambda: _store(bool(var.get()))).pack(side=tk.LEFT)
            if spec["help_key"] and spec["help_key"] in PARAM_HELP_GUIDE:
                self._create_help_badge(row, spec["help_key"])
        else:
            lbl = tk.Frame(row, bg=PANEL_BG)
            lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
            tk.Label(lbl, text=spec["label"] + ":", bg=PANEL_BG, fg=TEXT_MAIN, font=("Segoe UI", 8), anchor="w").pack(side=tk.LEFT)
            if spec["help_key"] and spec["help_key"] in PARAM_HELP_GUIDE:
                self._create_help_badge(lbl, spec["help_key"])
            if spec["type"] in ("coded", "combo"):
                if spec["type"] == "coded":
                    label_of = spec["code_labels"]
                    code_of = {v: k for k, v in label_of.items()}
                    shown_vals = list(label_of.values())
                    var = tk.StringVar(value=label_of.get(str(value), str(value)))
                    to_store = lambda: code_of.get(var.get(), var.get())
                else:
                    shown_vals = [str(x) for x in spec["values"]]
                    var = tk.StringVar(value="" if value is None else str(value))
                    to_store = lambda: var.get()
                width = max(12, min(max((len(x) for x in shown_vals), default=12) + 1, 30))
                cb = ttk.Combobox(row, textvariable=var, values=shown_vals, state="readonly", font=("Segoe UI", 8), width=width)
                cb.pack(side=tk.RIGHT)
                cb.bind("<<ComboboxSelected>>", lambda e: _store(to_store()))
            else:
                var = tk.StringVar(value="" if value is None else str(value))
                ent = tk.Entry(row, textvariable=var, bg="#10141d", fg=TEXT_MAIN, insertbackground=TEXT_MAIN, relief=tk.FLAT,
                               font=("Consolas", 8), width=12, justify="center")
                ent.pack(side=tk.RIGHT)
                self.__dict__.setdefault("_bt_form_widgets", {})[key] = ent
                var.trace_add("write", lambda *_a: _store(var.get().strip()))
        self._bt_form_vars[key] = var

    def _clear_backtest_data(self):
        """Xóa trắng toàn bộ dữ liệu bảng lệnh, biểu đồ và chỉ số KPI để chuẩn bị chạy lượt test mới."""
        self._bt_cancel_flag = True
        self._bt_trades_cache = []
        self._bt_equity_points = []

        if hasattr(self, "bt_tree") and self.bt_tree.winfo_exists():
            for item in self.bt_tree.get_children():
                self.bt_tree.delete(item)

        if hasattr(self, "bt_kpi_total_lbl") and self.bt_kpi_total_lbl.winfo_exists():
            self.bt_kpi_total_lbl.config(text="0 Lệnh", fg=GOLD_ACCENT)
        if hasattr(self, "bt_kpi_winrate_lbl") and self.bt_kpi_winrate_lbl.winfo_exists():
            self.bt_kpi_winrate_lbl.config(text="0.0%", fg=GREEN_UP)
        if hasattr(self, "bt_kpi_pnl_lbl") and self.bt_kpi_pnl_lbl.winfo_exists():
            self.bt_kpi_pnl_lbl.config(text="+0.00 $", fg=CYAN_LIVE)
        if hasattr(self, "bt_kpi_pf_lbl") and self.bt_kpi_pf_lbl.winfo_exists():
            self.bt_kpi_pf_lbl.config(text="0.00", fg=GOLD_ACCENT)
        if hasattr(self, "bt_kpi_dd_lbl") and self.bt_kpi_dd_lbl.winfo_exists():
            self.bt_kpi_dd_lbl.config(text="-0.00$", fg=RED_DOWN)
        if hasattr(self, "bt_kpi_maxwin_lbl") and self.bt_kpi_maxwin_lbl.winfo_exists():
            self.bt_kpi_maxwin_lbl.config(text="+0.00 $", fg=TEXT_MAIN)

        self._draw_equity_curve([])
        self._update_bt_status("Đã xóa sạch dữ liệu. Sẵn sàng cho lượt kiểm thử mới.", 0)
        self.show_toast("Đã xóa trắng dữ liệu Backtest!", is_error=False, duration_ms=2000)

    def _update_bt_status(self, msg: str, pct: int):
        """Cập nhật thanh tiến độ và thông điệp trạng thái."""
        if hasattr(self, "bt_status_lbl") and self.bt_status_lbl.winfo_exists():
            self.bt_status_lbl.config(text=msg)
        if hasattr(self, "bt_progress_canvas") and self.bt_progress_canvas.winfo_exists():
            w = max(int(160 * (min(max(pct, 0), 100) / 100.0)), 0)
            self.bt_progress_canvas.coords(self.bt_progress_bar, 0, 0, w, 8)

    def _start_backtest_worker(self):
        """Khởi động luồng ngầm chạy mô phỏng Backtest không đơ giật UI."""
        if getattr(self, "_bt_worker_running", False):
            self.show_toast("Kiểm thử đang chạy, vui lòng đợi!", is_error=True)
            return

        if self._bt_engine_is_live():
            self._start_live_engine_backtest()
            return

        self._bt_cancel_flag = False
        self._bt_worker_running = True
        self._reset_backtest_view_for_run()
        try:
            self._bt_ws_message("⚡ Engine cũ: không có màn hình chạy trực tiếp, kết quả hiện ở bảng lệnh bên dưới.\n"
                                "Chọn engine '🧠 Code Live' để xem bot chạy theo thời gian như thật.")
        except Exception:
            pass
        self._update_bt_status("Đang khởi tạo môi trường kiểm thử...", 2)

        def _runner():
            try:
                self._run_backtest_simulation()
            except Exception as e:
                self.root.after(0, lambda: self._update_bt_status(f"Lỗi kiểm thử: {str(e)}", 0))
                self.root.after(0, lambda: self.show_toast(f"Lỗi Backtest: {str(e)}", is_error=True, duration_ms=4000))
            finally:
                self._bt_worker_running = False

        self._bt_worker_thread = threading.Thread(target=_runner, daemon=True)
        self._bt_worker_thread.start()

    def _stop_backtest_worker(self):
        """Dừng tiến trình kiểm thử."""
        if getattr(self, "_bt_proc", None) is not None:
            self._bt_request_cancel_live()
            return
        if getattr(self, "_bt_worker_running", False):
            self._bt_cancel_flag = True
            self._update_bt_status("Đang hủy tiến trình kiểm thử...", 0)
            self.show_toast("Đang dừng Backtest...", is_error=False, duration_ms=2000)

    # ── BACKTEST BẰNG CODE LIVE (TIẾN TRÌNH RIÊNG) + TRÌNH PHÁT LẠI ─────────────────────────
    # Engine "Code Live" chạy tools/live_replay (CHÍNH code bot live trên tick quá khứ, sàn giả lập) ở tiến trình con:
    # live_replay thay mt5/time/datetime cấp module nên KHÔNG được chạy chung tiến trình với bot live.
    # Tiến trình con: độ ưu tiên thấp, ít luồng AI; báo tiến độ qua file progress.jsonl, kết quả result.json + player.npz.
    BT_LIVE_ENGINE_LABEL = "🧠 Code Live (giống bot live)"
    BT_LEGACY_ENGINE_LABEL = "⚡ Engine cũ (nhanh, khác live)"
    BT_RUNS_KEEP = 10

    def _bt_on_engine_changed(self):
        """Engine Code Live luôn dùng tick thật + AI: làm mờ 2 ô chỉ dùng cho engine cũ (Dữ liệu, Não AI)."""
        live = self._bt_engine_is_live()
        for name, on_state in (("bt_prec_combo", "readonly"), ("bt_ai_chk", "normal")):
            w = getattr(self, name, None)
            try:
                if w is not None and w.winfo_exists():
                    w.config(state="disabled" if live else on_state)
            except Exception:
                pass

    def _bt_engine_is_live(self) -> bool:
        try:
            return not str(self.bt_engine_var.get()).startswith("⚡")
        except Exception:
            return True

    @staticmethod
    def _bt_child_threads() -> int:
        """Số luồng AI cho tiến trình backtest: chừa CPU cho bot live (máy từng bị Kernel-Power 41)."""
        cpu = os.cpu_count() or 4
        return max(1, min(2, cpu // 4))

    @staticmethod
    def _bt_build_replay_command(job_path: str) -> tuple:
        """Lệnh khởi chạy tiến trình backtest. Bản .exe (launcher + core_engine.bin): chạy lại chính chương trình với biến môi trường
        KRONOS_REPLAY_JOB -> code bot thấy biến này thì chạy backtest rồi thoát (không mở giao diện). Từ mã nguồn: tools/live_replay.py --job."""
        env = dict(os.environ)
        env.pop("KRONOS_REPLAY_JOB", None)
        env["PYTHONIOENCODING"] = "utf-8"  # tiến trình con in chữ Việt: tránh UnicodeEncodeError cp1252
        env["PYTHONUTF8"] = "1"
        main_mod = sys.modules.get("__main__")
        if getattr(sys, "frozen", False):
            env["KRONOS_REPLAY_JOB"] = job_path
            return [sys.executable], env
        if getattr(main_mod, "_CONTAINER_RAW_DICT", None) and getattr(main_mod, "__file__", None):
            env["KRONOS_REPLAY_JOB"] = job_path
            return [sys.executable, os.path.abspath(main_mod.__file__)], env
        tool = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "live_replay.py")
        if not os.path.exists(tool):
            raise FileNotFoundError(f"Không thấy công cụ phát lại: {tool}")
        return [sys.executable, "-u", tool, "--job", job_path], env

    def _bt_cleanup_old_runs(self):
        """Giữ BT_RUNS_KEEP lượt backtest gần nhất trong thư mục backtest_runs (chỉ xóa thư mục run_* do app tạo)."""
        try:
            root_dir = os.path.join(SETTINGS_DIR, "backtest_runs")
            runs = sorted(d for d in os.listdir(root_dir) if d.startswith("run_") and os.path.isdir(os.path.join(root_dir, d)))
            for d in runs[:-self.BT_RUNS_KEEP]:
                shutil.rmtree(os.path.join(root_dir, d), ignore_errors=True)
        except Exception:
            pass

    def _start_live_engine_backtest(self):
        """Chạy backtest bằng CODE LIVE ở tiến trình riêng theo cấu hình Backtest (không đọc menu live)."""
        sym = self.bt_sym_var.get().strip()
        pk = "BTCUSD" if "BTC" in sym.upper() else "XAUUSD"
        start_s, end_s = self.bt_start_var.get().strip(), self.bt_end_var.get().strip()
        try:
            d0, d1 = datetime.strptime(start_s, "%Y-%m-%d"), datetime.strptime(end_s, "%Y-%m-%d")
            if d1 < d0:
                raise ValueError("'Đến ngày' phải sau 'Từ ngày'")
        except ValueError as e:
            self.show_toast(f"Ngày không hợp lệ ({e}). Nhập dạng YYYY-MM-DD.", is_error=True, duration_ms=3500)
            return
        try:
            capital = float(self.bt_capital_var.get() or 1000)
        except ValueError:
            capital = 1000.0
        cfg = self._get_bt_cfg(pk)
        for k in self.BT_SECRET_KEYS:
            cfg.pop(k, None)
        threads = self._bt_child_threads()
        run_dir = os.path.join(SETTINGS_DIR, "backtest_runs", datetime.now().strftime("run_%Y%m%d_%H%M%S"))
        try:
            os.makedirs(run_dir, exist_ok=True)
            job_path = os.path.join(run_dir, "job.json")
            with open(job_path, "w", encoding="utf-8") as f:
                json.dump({"profile": pk, "symbol": sym, "cfg": cfg, "start": start_s, "end": end_s, "capital": capital,
                           "threads": threads, "seed": 1, "out_dir": run_dir}, f, ensure_ascii=False, indent=1, default=str)
            cmd, env = self._bt_build_replay_command(job_path)
            flags = (0x08000000 | 0x00004000) if os.name == "nt" else 0  # CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS
            self._bt_stderr_fh = open(os.path.join(run_dir, "stderr.txt"), "w", encoding="utf-8")
            self._bt_proc = subprocess.Popen(cmd, env=env, cwd=os.getcwd(), stdin=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL, stderr=self._bt_stderr_fh, creationflags=flags)
        except Exception as e:
            self._bt_proc = None
            self._update_bt_status(f"Lỗi khởi chạy backtest code live: {e}", 0)
            self.show_toast(f"Không khởi chạy được backtest: {e}", is_error=True, duration_ms=4000)
            self._write_file_log(f"[BACKTEST] Lỗi khởi chạy tiến trình backtest: {e}", is_error=True)
            return
        self._bt_run_dir = run_dir
        self._bt_prog_pos = 0
        self._bt_last_event = {}
        self._bt_live_meta = {"pk": pk, "sym": sym, "capital": capital, "start": start_s, "end": end_s, "threads": threads}
        self._bt_ev_pos = 0
        self._bt_worker_running = True
        self._bt_cancel_flag = False
        self._reset_backtest_view_for_run()
        try:
            self._bt_goto_workspace()
            self.bt_ws_title_lbl.config(text=f"🧪 MÀN HÌNH BACKTEST — {sym} | {start_s} → {end_s} | chạy CODE LIVE theo cấu hình Backtest "
                                             f"(tách riêng, không ảnh hưởng bot live)")
            self._bt_ws_message("⏳ Đang nạp dữ liệu nến + tick từ MT5 (chỉ đọc)...\n"
                                "Nạp xong, biểu đồ sẽ chạy theo thời gian như thật (tua được 1x...600x).")
        except Exception as e:
            self._write_file_log(f"[BACKTEST] Lỗi mở màn hình Backtest: {e}", is_error=True)
        self._update_bt_status(f"🧠 Đang chạy CODE LIVE {start_s} → {end_s} (tiến trình riêng, {threads} luồng AI, ưu tiên thấp)...", 1)
        self.log(f"[BACKTEST] Bắt đầu backtest bằng CODE LIVE [{pk} {sym}] {start_s} → {end_s} | thư mục: {run_dir}")
        self._bt_cleanup_old_runs()
        self.root.after(250, self._poll_live_bt)

    def _bt_request_cancel_live(self):
        run_dir = getattr(self, "_bt_run_dir", None)
        try:
            if run_dir:
                with open(os.path.join(run_dir, "cancel"), "w", encoding="utf-8") as f:
                    f.write("1")
        except Exception:
            pass
        self._update_bt_status("Đang dừng backtest code live...", 0)
        self.show_toast("Đang dừng Backtest...", is_error=False, duration_ms=2000)
        self._bt_cancel_deadline = time.time() + 8.0  # quá hạn mà tiến trình chưa tự dừng -> buộc dừng

    def _bt_kill_child(self):
        proc = getattr(self, "_bt_proc", None)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def _bt_read_progress_events(self) -> list:
        """Đọc các dòng tiến độ MỚI (chỉ dòng hoàn chỉnh) từ progress.jsonl của lượt đang chạy."""
        events = []
        path = os.path.join(getattr(self, "_bt_run_dir", "") or "", "progress.jsonl")
        try:
            if os.path.exists(path):
                with open(path, "rb") as f:
                    f.seek(self._bt_prog_pos)
                    data = f.read()
                cut = data.rfind(b"\n")
                if cut >= 0:
                    self._bt_prog_pos += cut + 1
                    for line in data[:cut + 1].splitlines():
                        if line.strip():
                            try:
                                events.append(json.loads(line.decode("utf-8")))
                            except Exception:
                                pass
        except Exception:
            pass
        return events

    def _poll_live_bt(self):
        proc = getattr(self, "_bt_proc", None)
        if proc is None:
            return
        for ev in self._bt_read_progress_events():
            self._bt_last_event = ev
            typ = ev.get("type")
            if typ == "start":
                prm = ev.get("params", {})
                self.log(f"[BACKTEST] {ev.get('note', '')} | Thông số chính: " + ", ".join(f"{k}={v}" for k, v in prm.items()))
            elif typ == "progress":
                pct = int(ev.get("pct", 0))
                mins = float(ev.get("mins", 0.0))
                eta = f" | còn ~{mins * (100 - pct) / pct:.0f} phút" if pct >= 3 else ""
                stage = ev.get("stage")
                when = datetime.fromtimestamp(float(ev.get("t", 0)), VN_TZ).strftime("%d/%m %H:%M") if ev.get("t") else ""
                txt = stage or (f"🧠 Code live: {pct}% | đang ở {when} | {ev.get('closed', 0)} lệnh đã đóng | "
                                f"{ev.get('analyses', 0)} lần AI | {mins:.1f} phút{eta}")
                self._update_bt_status(txt, max(pct, 1))
            elif typ == "data_ready":
                self._bt_on_data_ready(ev)
        self._bt_feed_live_events()
        deadline = getattr(self, "_bt_cancel_deadline", None)
        if deadline and time.time() > deadline and proc.poll() is None:
            self._bt_kill_child()
        if proc.poll() is None:
            self.root.after(250, self._poll_live_bt)
            return
        self._bt_feed_live_events()  # sự kiện cuối cùng trước khi tiến trình thoát
        _p = getattr(self, "_bt_player", None)
        if _p is not None and getattr(self, "_bt_player_run", None) == self._bt_run_dir and _p.live and not _p.finished:
            # Tính xong: bỏ giới hạn mốc đã tính. Dừng/lỗi giữa chừng: chỉ phát tới mốc đã tính được.
            _p.finish(None if os.path.exists(os.path.join(self._bt_run_dir, "result.json")) else _p.frontier)
        # Tiến trình đã kết thúc
        try:
            self._bt_stderr_fh.close()
        except Exception:
            pass
        self._bt_proc = None
        self._bt_worker_running = False
        self._bt_cancel_deadline = None
        run_dir = self._bt_run_dir
        res_path = os.path.join(run_dir, "result.json")
        last = getattr(self, "_bt_last_event", {}) or {}
        if os.path.exists(res_path):
            try:
                with open(res_path, "r", encoding="utf-8") as f:
                    res = json.load(f)
                self._bt_show_live_result(res, run_dir)
                return
            except Exception as e:
                last = {"type": "error", "msg": f"Không đọc được kết quả: {e}"}
        if last.get("type") == "cancelled" or os.path.exists(os.path.join(run_dir, "cancel")):
            self._update_bt_status("⏹ Đã dừng backtest code live.", 0)
            return
        msg = last.get("msg") or ""
        if not msg:
            try:
                with open(os.path.join(run_dir, "stderr.txt"), "r", encoding="utf-8", errors="replace") as f:
                    msg = f.read()[-400:].strip()
            except Exception:
                msg = ""
        msg = msg or f"tiến trình thoát mã {proc.returncode}"
        self._update_bt_status(f"❌ Lỗi backtest code live: {msg[:160]}", 0)
        self.show_toast(f"Lỗi Backtest: {msg[:120]}", is_error=True, duration_ms=5000)
        self._write_file_log(f"[BACKTEST] Lỗi backtest code live: {msg} | {last.get('tb', '')}", is_error=True)

    def _bt_show_live_result(self, res: dict, run_dir: str):
        """Đổ kết quả backtest code live vào bảng lệnh + thẻ KPI + biểu đồ vốn có sẵn của tab Backtest."""
        capital = float(res.get("capital", 1000.0))
        is_btc = "BTC" in str(res.get("symbol", "")).upper()
        contract = 1.0 if is_btc else 100.0
        trades, eq_curve = self._bt_rows_from_closed(res.get("closed", []), capital, contract)
        self._bt_last_result = res
        self._bt_last_run_dir = run_dir
        p = getattr(self, "_bt_player", None)
        follow = p is not None and not getattr(p, "_closed", True) and getattr(self, "_bt_player_run", None) == run_dir
        if follow:
            # Bảng lệnh + thẻ KPI chạy theo thời điểm đang phát trên màn hình Backtest (_bt_view_loop);
            # báo cáo tổng kết (log + CSV) vẫn ghi 1 lần cho cả lượt
            self._bt_table_n = -1
            try:
                self._emit_backtest_summary(trades, capital)
            except Exception as e:
                self.log_error("BACKTEST_SUMMARY", e, show_in_ui=False)
        else:
            self._bt_table_n = len(trades)
            self._bt_trades_cache = trades
            self._bt_equity_points = eq_curve
            self._render_backtest_results(trades, eq_curve, capital)
        try:
            self.bt_tree.bind("<Double-1>", self._on_bt_tree_double_click)
        except Exception:
            pass
        sm = res.get("summary", {})
        n_err = int(res.get("n_errors", 0))
        err_tag = f" | ⚠️ {n_err} lỗi trong code bot (xem log.txt)" if n_err else ""
        self._update_bt_status(
            f"✅ Xong (code live): {sm.get('n', len(trades))} lệnh | lãi ròng {float(sm.get('net', 0.0)):+.2f}$ | "
            f"{res.get('n_analyses', 0)} lần AI | {float(res.get('minutes', 0.0)):.1f} phút{err_tag} — "
            + ("bảng lệnh chạy theo thời gian trên biểu đồ (⏭ Cuối để xem hết), " if follow else "")
            + "bấm đúp 1 lệnh để tua biểu đồ tới lệnh đó", 100)
        self.log(f"[BACKTEST] Xong backtest CODE LIVE: {sm.get('n', 0)} lệnh, lãi ròng {float(sm.get('net', 0.0)):+.2f}$, "
                 f"PF {sm.get('pf', 0)}, sụt vốn {sm.get('max_dd', 0)}$, lỗi code bot {n_err}. Log: {os.path.join(run_dir, 'log.txt')}")

    def _on_bt_tree_select(self, event=None):
        """Bấm 1 lệnh trong sổ lệnh Backtest -> tô lệnh đó lên biểu đồ màn hình Backtest (tạm dừng, như tab live)."""
        try:
            p = getattr(self, "_bt_player", None)
            sel = self.bt_tree.selection()
            if p is None or getattr(p, "_closed", True) or not sel:
                return
            no = str(self.bt_tree.item(sel[0], "values")[0]).lstrip("#")
            tr = self._bt_trades_cache[int(no) - 1]
            if tr.get("mt5_ticket") is not None:
                self._bt_set_workspace_visible(True)
                p.focus_trade(tr.get("mt5_ticket"))
        except Exception:
            pass

    def _bt_toggle_pause(self):
        """Nút ⏸ Tạm dừng / ▶ Phát tiếp ở thanh Backtest: dừng/chạy biểu đồ để kiểm tra (tiến trình tính vẫn chạy nền)."""
        p = getattr(self, "_bt_player", None)
        if p is None or getattr(p, "_closed", True):
            self.show_toast("Chưa có biểu đồ Backtest đang chạy.", is_error=True, duration_ms=2500)
            return
        self._bt_set_workspace_visible(True)
        p.toggle_play()

    def _on_bt_tree_double_click(self, event=None):
        try:
            sel = self.bt_tree.selection()
            if not sel:
                return
            no = str(self.bt_tree.item(sel[0], "values")[0]).lstrip("#")
            tr = self._bt_trades_cache[int(no) - 1]
            self._open_bt_player(ticket=tr.get("mt5_ticket"))
        except Exception:
            pass

    # ───── Màn hình Backtest riêng (khung biểu đồ phía trên, giống tab Vàng/BTC) ─────
    def _bt_ensure_workspace(self):
        """Màn hình Backtest ở khung biểu đồ phía trên (tạo 1 lần, nằm cùng chỗ với biểu đồ live)."""
        ws = getattr(self, "bt_ws_frame", None)
        if ws is not None and ws.winfo_exists():
            return ws
        ws = tk.Frame(self.chart_holder, bg="#131722")
        bar = tk.Frame(ws, bg="#2a1f3d", padx=10, pady=3)
        bar.pack(fill=tk.X)
        self.bt_ws_title_lbl = tk.Label(
            bar, text="🧪 MÀN HÌNH BACKTEST (tách riêng, không ảnh hưởng bot live)", bg="#2a1f3d", fg="#d1c4e9",
            font=("Segoe UI", 9, "bold"), anchor="w")
        self.bt_ws_title_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.bt_ws_body = tk.Frame(ws, bg="#131722")
        self.bt_ws_body.pack(fill=tk.BOTH, expand=True)
        self.bt_ws_placeholder = tk.Label(
            self.bt_ws_body, bg="#131722", fg="#b2b5be", font=("Segoe UI", 11), justify=tk.CENTER,
            text="Chọn ngày rồi bấm ▶ BẮT ĐẦU TEST (engine 🧠 Code Live).\n"
                 "Nạp xong dữ liệu, biểu đồ sẽ chạy theo thời gian như thật: tín hiệu AI, nến dự báo, vùng canh, lệnh, nhật ký.\n"
                 "Chọn tốc độ tua 1x...600x hoặc 'Tối đa'.")
        self.bt_ws_placeholder.pack(expand=True)
        self.bt_ws_frame = ws
        return ws

    def _bt_workspace_shown(self) -> bool:
        """Màn hình Backtest đang chiếm khung biểu đồ phía trên (biểu đồ live đang ẩn)."""
        try:
            ws = getattr(self, "bt_ws_frame", None)
            return bool(ws is not None and ws.winfo_exists() and ws.winfo_manager())
        except Exception:
            return False

    def _bt_set_workspace_visible(self, show: bool):
        """Đổi khung phía trên giữa biểu đồ live và màn hình Backtest (không dừng/đổi gì của bot live)."""
        if getattr(self, "chart_holder", None) is None or getattr(self, "live_chart_frame", None) is None:
            return
        ws = getattr(self, "bt_ws_frame", None)
        sb = getattr(self, "live_sig_box", None)
        if show:
            ws = self._bt_ensure_workspace()
            if not ws.winfo_manager():
                self.live_chart_frame.pack_forget()
                if sb is not None:
                    sb.pack_forget()
                ws.pack(fill=tk.BOTH, expand=True)
        else:
            if ws is not None and ws.winfo_exists() and ws.winfo_manager():
                ws.pack_forget()
            if not self.live_chart_frame.winfo_manager():
                self.live_chart_frame.pack(fill=tk.BOTH, expand=True)
            if sb is not None and not sb.winfo_manager():
                sb.pack(fill=tk.X, pady=(0, 4), before=self.right_paned)

    def _bt_goto_workspace(self):
        try:
            if self.bottom_notebook.index(self.bottom_notebook.select()) != 3:
                self.bottom_notebook.select(self.backtest_tab)
        except Exception:
            pass
        self._bt_set_workspace_visible(True)

    def _bt_close_ws_player(self):
        p = getattr(self, "_bt_player", None)
        if p is not None:
            try:
                p.close()
            except Exception:
                pass
        self._bt_player = None
        self._bt_player_run = None

    def _bt_ws_message(self, text: str):
        """Thông báo giữa màn hình Backtest (khi chưa có biểu đồ)."""
        self._bt_ensure_workspace()
        self._bt_close_ws_player()
        self.bt_ws_placeholder.config(text=text)
        if not self.bt_ws_placeholder.winfo_manager():
            self.bt_ws_placeholder.pack(expand=True)

    def _bt_make_ws_player(self, run_dir: str, start_utc: float, end_utc: float, contract: float, tf: str, live: bool,
                           closed=(), sltp=()):
        """Dựng trình phát lại NHÚNG vào màn hình Backtest từ player.npz của lượt chạy."""
        from trading_modules.replay_player import ReplayPlayer
        with np.load(os.path.join(run_dir, "player.npz")) as z:
            data = {k: z[k] for k in ("sec", "o", "h", "l", "c")}
        self._bt_ensure_workspace()
        self._bt_close_ws_player()
        try:
            self.bt_ws_placeholder.pack_forget()
        except Exception:
            pass
        tf = tf if tf in ("M1", "M5", "M15") else "M1"
        self._bt_player = ReplayPlayer(self.root, data, list(closed), list(sltp), float(start_utc), float(end_utc),
                                       contract=float(contract), tf=tf, parent=self.bt_ws_body, live=live, speed=60)
        self._bt_player_run = run_dir
        self._bt_table_n = -1
        try:
            self.bt_tree.bind("<<TreeviewSelect>>", self._on_bt_tree_select)
            self.bt_tree.bind("<Double-1>", self._on_bt_tree_double_click)
        except Exception:
            pass
        if not getattr(self, "_bt_view_loop_on", False):
            self._bt_view_loop_on = True
            self.root.after(400, self._bt_view_loop)
        return self._bt_player

    @staticmethod
    def _bt_read_jsonl(path: str, pos: int) -> tuple:
        """Đọc các dòng JSON MỚI (chỉ dòng hoàn chỉnh) từ vị trí pos. Trả (danh sách, vị trí mới)."""
        out = []
        try:
            if os.path.exists(path):
                with open(path, "rb") as f:
                    f.seek(pos)
                    data = f.read()
                cut = data.rfind(b"\n")
                if cut >= 0:
                    pos += cut + 1
                    for line in data[:cut + 1].splitlines():
                        if line.strip():
                            try:
                                out.append(json.loads(line.decode("utf-8")))
                            except Exception:
                                pass
        except Exception:
            pass
        return out, pos

    def _bt_feed_live_events(self):
        """Đưa sự kiện mới của lượt đang chạy (events.jsonl) vào biểu đồ trực tiếp."""
        p = getattr(self, "_bt_player", None)
        run_dir = getattr(self, "_bt_run_dir", None)
        if p is None or not run_dir or getattr(self, "_bt_player_run", None) != run_dir:
            return
        evs, self._bt_ev_pos = self._bt_read_jsonl(os.path.join(run_dir, "events.jsonl"), getattr(self, "_bt_ev_pos", 0))
        if evs:
            p.add_events(evs)

    def _bt_on_data_ready(self, ev: dict):
        """Tiến trình con đã nạp xong nến/tick -> dựng biểu đồ trực tiếp và bắt đầu chạy theo thời gian."""
        meta = getattr(self, "_bt_live_meta", {}) or {}
        contract = float(ev.get("contract", 100.0) or 100.0)
        self._bt_view_meta = {"capital": float(meta.get("capital", 1000.0)), "contract": contract}
        self._bt_ev_pos = 0
        try:
            p = self._bt_make_ws_player(self._bt_run_dir, float(ev["start"]), float(ev["end"]), contract,
                                        str(ev.get("tf") or "M1").strip(), live=True)
            p.play()
        except Exception as e:
            self._bt_ws_message(f"Không dựng được biểu đồ trực tiếp: {e}")
            self._write_file_log(f"[BACKTEST] Lỗi dựng biểu đồ trực tiếp: {e}", is_error=True)

    def _bt_view_loop(self):
        """Đồng bộ bảng lệnh + thẻ KPI theo thời điểm đang phát (khi đang chạy trực tiếp)."""
        p = getattr(self, "_bt_player", None)
        if p is None or getattr(p, "_closed", True):
            self._bt_view_loop_on = False
            return
        try:
            self._bt_sync_table_to_cursor()
        except Exception as e:
            if not getattr(self, "_bt_sync_err_logged", False):
                self._bt_sync_err_logged = True
                self._write_file_log(f"[BACKTEST] Lỗi đồng bộ bảng lệnh theo biểu đồ: {e}", is_error=True)
        self.root.after(400, self._bt_view_loop)

    def _bt_sync_table_to_cursor(self):
        """Bảng lệnh + thẻ KPI chỉ gồm lệnh đã đóng TỚI thời điểm đang phát trên màn hình Backtest (như thật),
        cả khi đang tính, đã tính xong hay xem lại. Muốn xem hết: nút ⏭ Cuối."""
        p = self._bt_player
        n = p.n_closed_at_cursor()
        if n == getattr(self, "_bt_table_n", -1):
            return
        self._bt_table_n = n
        meta = getattr(self, "_bt_view_meta", {}) or {}
        capital = float(meta.get("capital", 1000.0))
        trades, eq = self._bt_rows_from_closed(p._closed_sorted[:n], capital, p.contract)
        self._bt_trades_cache = trades
        self._bt_equity_points = eq
        self._render_backtest_results(trades, eq, capital, final=False)

    def _bt_rows_from_closed(self, closed: list, capital: float, contract: float) -> tuple:
        """Lệnh đã đóng (định dạng live_replay) -> dòng bảng lệnh + đường vốn của tab Backtest."""
        closed = sorted(closed, key=lambda c: float(c.get("close_ms", 0)))
        trades, equity, eq_curve = [], float(capital), [(0, float(capital))]
        for i, c in enumerate(closed):
            o_dt = datetime.fromtimestamp(float(c["open_ms"]) / 1000.0, VN_TZ)
            x_dt = datetime.fromtimestamp(float(c["close_ms"]) / 1000.0, VN_TZ)
            pnl = round(float(c.get("profit", 0.0)), 2)
            equity += pnl
            vol = float(c.get("volume", 0.0))
            sl0 = float(c.get("sl0", 0.0) or 0.0)
            trades.append({
                "ticket": f"#{i + 1:04d}", "date": o_dt.strftime("%Y-%m-%d"),
                "risk_usd": round(abs(float(c["price_open"]) - sl0) * vol * contract, 2) if sl0 > 0 else 0.0,
                "cost_spread": 0.0, "cost_kind": "LIVE_REPLAY",
                "in_time": o_dt.strftime("%d/%m %H:%M:%S"), "out_time": x_dt.strftime("%d/%m %H:%M:%S"),
                "duration": self._format_duration(int((float(c["close_ms"]) - float(c["open_ms"])) / 1000.0)),
                "type": c.get("type", ""), "lot": f"{vol:.2f}", "in_price": f"{float(c['price_open']):.2f}",
                "out_price": f"{float(c['price_close']):.2f}", "pnl": pnl,
                "reason": c.get("reason_label") or c.get("reason", ""), "mt5_ticket": c.get("ticket"),
            })
            eq_curve.append((len(trades), round(equity, 2)))
        return trades, eq_curve

    def _open_bt_player(self, ticket=None):
        """Xem lại lượt backtest code live trên màn hình Backtest (khung phía trên). ticket: tua tới lệnh đó."""
        p = getattr(self, "_bt_player", None)
        if p is not None and not getattr(p, "_closed", True):
            self._bt_goto_workspace()
            if ticket is not None:
                p.jump_to_trade(ticket)
            else:
                p.go_start()
                p.play()
            return
        res = getattr(self, "_bt_last_result", None)
        run_dir = getattr(self, "_bt_last_run_dir", None)
        if not res or not run_dir:
            self.show_toast("Chưa có kết quả để phát lại: chạy Backtest bằng engine '🧠 Code Live' trước.", is_error=True, duration_ms=3500)
            return
        try:
            self._bt_goto_workspace()
            is_btc = "BTC" in str(res.get("symbol", "")).upper()
            contract = 1.0 if is_btc else 100.0
            self._bt_view_meta = {"capital": float(res.get("capital", 1000.0)), "contract": contract}
            p = self._bt_make_ws_player(run_dir, float(res["start_utc"]), float(res["end_utc"]), contract,
                                        str(res.get("tf") or "M1").strip(), live=False, closed=res.get("closed", []),
                                        sltp=res.get("sltp", []))
            self._bt_table_n = -1  # bảng lệnh chạy theo thời điểm đang phát
            evs, _pos = self._bt_read_jsonl(os.path.join(run_dir, "events.jsonl"), 0)
            p.add_events([e for e in evs if e.get("type") in ("ai", "zone", "log")])
            if ticket is not None:
                p.jump_to_trade(ticket)
            else:
                p.play()
        except Exception as e:
            self.show_toast(f"Không mở được trình phát lại: {e}", is_error=True, duration_ms=4000)
            self._write_file_log(f"[BACKTEST] Lỗi mở trình phát lại: {e}", is_error=True)

    def _fetch_or_load_ticks(self, sym: str, start_dt: datetime, end_dt: datetime):
        """
        Tải dòng tick thực tế từ MetaTrader 5 với đệm múi giờ và lưu cache nhị phân trong APPDATA/Kronos_MT5_Bot/tick_cache
        """
        tick_cache_dir = os.path.join(SETTINGS_DIR, "tick_cache")
        try:
            os.makedirs(tick_cache_dir, exist_ok=True)
        except Exception:
            pass

        start_ts = int(start_dt.timestamp())
        end_ts = int(end_dt.timestamp())
        cache_file = os.path.join(tick_cache_dir, f"ticks_{sym}_{start_ts}_{end_ts}.npz")

        if os.path.exists(cache_file):
            try:
                data = np.load(cache_file)
                return data["ticks"]
            except Exception:
                pass

        if not MT5_AVAILABLE:
            return None

        with self._mt5_lock:
            query_from = datetime.fromtimestamp(start_ts - 7200)
            query_to = datetime.fromtimestamp(end_ts + 7200)
            ticks = mt5.copy_ticks_range(sym, query_from, query_to, mt5.COPY_TICKS_ALL)

        if ticks is not None and len(ticks) > 0:
            try:
                np.savez_compressed(cache_file, ticks=ticks)
            except Exception:
                pass

        return ticks

    def _resample_ticks_to_s1(self, ticks):
        """Ghép tick MT5 thành nến 1 giây (S1). Logic ở trading_modules/tick_resample.py."""
        return resample_ticks_to_s1(ticks)

    def _evaluate_bt_ai_candle(
        self,
        predictor,
        df_rates_ana: pd.DataFrame,
        idx_ana: int,
        cfg: dict,
        def_cfg: dict,
        ana_tf_str: str,
        is_btc: bool,
        atr: float,
        df_rates_scalp: Optional[pd.DataFrame] = None,
        idx_scalp: int = 0,
        scalp_tf_str: str = "M5",
        cur_sec: Optional[int] = None,
    ) -> Optional[dict]:
        """
        Đánh giá tín hiệu AI trong Backtest chuẩn xác 100% đồng nhất với Não Live:
        - Sử dụng đúng số nến lookback cài đặt (mặc định 450 nến).
        - Sử dụng đúng số kịch bản num_scenarios (mặc định 8 kịch bản).
        - Tự động điều chỉnh nhiệt độ thích ứng (Dynamic Temperature & Top-P theo khung M1/M5).
        - Chấm điểm xác suất Transformer (Bayesian Log-Likelihood) + Đồng thuận quỹ đạo đa bước.
        - Lọc bẫy giá phân tán kịch bản (max_dispersion).
        - Đồng bộ 100% Cấu Trúc Thị Trường Zero-Lag (BOS/CHoCH) từ live:
          + Lấy 60 nến scalp đã đóng hoàn toàn (không nhìn trước tương lai).
          + Tính ATR chuẩn: max(atr_scalp, atr_ana * 0.65).
          + Áp dụng trend_filter: macro_dir == -1 chặn BUY, macro_dir == 1 chặn SELL.
        """
        try:
            lookback = min(max(int(cfg.get("lookback", def_cfg.get("lookback", 450))), 50), 450)
            num_scenarios = int(cfg.get("scenarios", def_cfg.get("scenarios", 8)))
            min_consensus = float(cfg.get("consensus", def_cfg.get("consensus", 75)))
            min_move = float(cfg.get("min_move", def_cfg.get("min_move", 60.0 if is_btc else 1.2)))
            max_dispersion = float(cfg.get("max_dispersion", def_cfg.get("max_dispersion", 2.5)))
            sl_atr = float(cfg.get("sl_atr", def_cfg.get("sl_atr", 1.5)))
            sl_buffer = float(cfg.get("sl_buffer", def_cfg.get("sl_buffer", 20.0 if is_btc else 0.5)))
            bt_use_live_structure = bool(cfg.get("bt_use_live_structure", True))
            trend_filter_on = bool(cfg.get("trend_filter", def_cfg.get("trend_filter", True)))

            sub_df = df_rates_ana.iloc[max(0, idx_ana - lookback):idx_ana].copy().reset_index(drop=True)
            if len(sub_df) < 30:
                return None

            sub_scalp_df = None
            if df_rates_scalp is not None and idx_scalp > 0:
                sub_scalp_df = df_rates_scalp.iloc[max(0, idx_scalp - 60):idx_scalp].copy().reset_index(drop=True)

            # Đồng bộ công thức ATR và trích xuất Cấu Trúc Zero-Lag y hệt bản Live
            macro_dir = 0
            macro_ctx = {"macro_dir": 0, "structure_reason": "Cân bằng / Tích lũy"}
            if bt_use_live_structure and sub_scalp_df is not None and len(sub_scalp_df) >= 15:
                atr_scalp = self.calculate_atr(sub_scalp_df, 14)
                atr_ana = self.calculate_atr(sub_df, 14) if ana_tf_str != scalp_tf_str else atr_scalp
                atr = max(atr_scalp, atr_ana * 0.65)
                macro_ctx = self._analyze_scalping_micro_macro_context(sub_df, sub_scalp_df, atr)
                macro_dir = macro_ctx.get("macro_dir", 0)
            # THỬ NGHIỆM (mặc định tắt): thay xu hướng SMC/BOS bằng N nến Heikin Ashi đã đóng cùng màu (khung phân tích)
            try:
                _ha_n = int(float(cfg.get("exp_ha_trend", 0) or 0))
            except (ValueError, TypeError):
                _ha_n = 0
            if _ha_n > 0:
                _ha_df = sub_df
                if str(cfg.get("exp_ha_src", "ana")).strip().lower() == "scalp" and sub_scalp_df is not None and len(sub_scalp_df) >= _ha_n + 2:
                    _ha_df = sub_scalp_df  # THỬ NGHIỆM: xu hướng HA trên khung scalp thay vì khung phân tích
                _hc = heikin_ashi_colors(_ha_df["open"], _ha_df["high"], _ha_df["low"], _ha_df["close"])[-_ha_n:]
                macro_dir = 1 if all(x == 1 for x in _hc) else (-1 if all(x == -1 for x in _hc) else 0)
                macro_ctx = {"macro_dir": macro_dir, "structure_reason": f"Heikin Ashi {_ha_n} nến"}

            x_ts = sub_df["timestamps"].copy().reset_index(drop=True)
            dt_step = x_ts.iloc[-1] - x_ts.iloc[-2] if len(x_ts) >= 2 else pd.Timedelta(minutes=5)
            y_ts = pd.Series([x_ts.iloc[-1] + dt_step * (k + 1) for k in range(6)])

            dyn_t, dyn_top_p, regime_tag = self._compute_adaptive_sampling_params(sub_df, ana_tf_str)
            # Cache kịch bản AI thô: chỉ tools/run_backtest.py gắn vào (live/UI: None -> chạy AI như cũ)
            _sc_cache = getattr(self, "_bt_scenario_cache", None)
            scenarios = None
            _sc_key = None
            if _sc_cache is not None:
                try:
                    _sc_key = _sc_cache.make_key(predictor, sub_df, y_ts, num_scenarios, dyn_t, dyn_top_p)
                    scenarios = _sc_cache.get(_sc_key, y_ts)
                except Exception:
                    scenarios, _sc_key = None, None
            if scenarios is None:
                scenarios = self._fast_predict_scenarios(
                    predictor=predictor,
                    df_in=sub_df,
                    x_timestamp=x_ts,
                    y_timestamp=y_ts,
                    pred_len=6,
                    num_scenarios=num_scenarios,
                    T=dyn_t,
                    top_k=0,
                    top_p=dyn_top_p
                )
                if scenarios and _sc_cache is not None and _sc_key is not None:
                    try:
                        _sc_cache.put(_sc_key, scenarios)
                    except Exception:
                        pass
            if not scenarios:
                return None

            last_c = float(sub_df["close"].iloc[-1])
            # Chấm điểm kịch bản (dùng chung với live): module trading_modules/scenario_scoring.py
            sc = score_scenarios(scenarios, last_c, atr, macro_ctx)
            bull_cnt, bear_cnt, total_scen = sc["bull_count"], sc["bear_count"], sc["total"]
            b_pct, s_pct = sc["bull_pct"], sc["bear_pct"]
            avg_end_close, exp_m = sc["avg_end_close"], sc["expected_move"]
            scenario_std, dispersion_r, best_idx = sc["scenario_std"], sc["dispersion_r"], sc["best_idx"]
            dir_pct, ai_dom_dir, confidence_pct = sc["dir_pct"], sc["ai_dom_dir"], sc["confidence_pct"]
            if max_dispersion > 0.0 and dispersion_r > max_dispersion:
                return None

            mean_df = scenarios[best_idx]
            pred_hi = float(mean_df["high"].max())
            pred_lo = float(mean_df["low"].min())

            spread_est = float(cfg.get("max_spread", def_cfg.get("max_spread", 30.0 if is_btc else 0.35))) * 0.5
            slippage_est = max(spread_est * 0.5, 1.0 if is_btc else 0.05)
            try:
                k_factor = float(cfg.get("expected_move_k", def_cfg.get("expected_move_k", 3.0)))
            except (ValueError, TypeError):
                k_factor = 3.0
            min_effective_move = max(min_move, k_factor * (spread_est + slippage_est))

            base_atr_sl = max(atr * sl_atr, 8.0 if is_btc else 0.5)

            use_confidence_gate = bool(cfg.get("use_confidence_gate", False))
            is_bull_consensus = (confidence_pct >= min_consensus and b_pct > s_pct) if use_confidence_gate else (b_pct >= min_consensus)
            is_bear_consensus = (confidence_pct >= min_consensus and s_pct > b_pct) if use_confidence_gate else (s_pct >= min_consensus)

            sig_dir = None
            if is_bull_consensus and exp_m >= min_effective_move:
                # Cổng quyết định Cấu Trúc Zero-Lag (y hệt live): Chặn BUY nếu Cấu trúc đang Giảm (-1)
                if bt_use_live_structure and trend_filter_on and macro_dir == -1:
                    sig_dir = None
                else:
                    sig_dir = "BUY"
                    raw_sl_d = max(base_atr_sl, max(last_c - pred_lo, 0.0)) + sl_buffer
                    if str(cfg.get("sl_swing", "0")) in ("1", "true", "True"):  # THỬ NGHIỆM (mặc định tắt)
                        _swd = nearest_swing_dist(sub_df, "BUY", last_c)
                        if _swd is not None:
                            raw_sl_d = max(8.0 if is_btc else 0.5, _swd) + sl_buffer
                    sl_p = last_c - raw_sl_d
            elif is_bear_consensus and (-exp_m) >= min_effective_move:
                # Cổng quyết định Cấu Trúc Zero-Lag (y hệt live): Chặn SELL nếu Cấu trúc đang Tăng (+1)
                if bt_use_live_structure and trend_filter_on and macro_dir == 1:
                    sig_dir = None
                else:
                    sig_dir = "SELL"
                    raw_sl_d = max(base_atr_sl, max(pred_hi - last_c, 0.0)) + sl_buffer
                    if str(cfg.get("sl_swing", "0")) in ("1", "true", "True"):  # THỬ NGHIỆM (mặc định tắt)
                        _swd = nearest_swing_dist(sub_df, "SELL", last_c)
                        if _swd is not None:
                            raw_sl_d = max(8.0 if is_btc else 0.5, _swd) + sl_buffer
                    sl_p = last_c + raw_sl_d

            if sig_dir is not None:
                return {
                    "signal": sig_dir,
                    "sl_price": sl_p,
                    "atr": atr,
                    "mean_df": mean_df,
                    "macro_dir": macro_dir,
                    "break_type": macro_ctx.get("break_type", "none"),
                    "timeframe_conflict": macro_ctx.get("timeframe_conflict", False),
                    "structure_reason": macro_ctx.get("structure_reason", "Cân bằng")
                }
        except Exception:
            pass
        return None

    def _run_backtest_simulation(self):
        """Động cơ mô phỏng giao dịch quá khứ chuẩn xác theo nến lịch sử hoặc nến Tick 1s và đệm giật giá Exness."""
        sym = self.bt_sym_var.get().strip()
        start_s = self.bt_start_var.get().strip()
        end_s = self.bt_end_var.get().strip()
        is_s1_mode = "S1" in self.bt_data_precision_var.get()
        use_ai = bool(self.bt_use_ai_var.get())

        try:
            init_cap = float(self.bt_capital_var.get() or 1000)
        except Exception:
            init_cap = 1000.0
        try:
            lot = float(self.bt_lot_var.get() or 0.02)
        except Exception:
            lot = 0.02

        try:
            start_d = datetime.strptime(start_s, "%Y-%m-%d").date()
            end_d = datetime.strptime(end_s, "%Y-%m-%d").date()
        except Exception:
            self.root.after(0, lambda: self.show_toast("Định dạng ngày không hợp lệ (YYYY-MM-DD)!", is_error=True))
            return

        if start_d > end_d:
            start_d, end_d = end_d, start_d

        start_dt = datetime(start_d.year, start_d.month, start_d.day, 0, 0, 0, tzinfo=VN_TZ)
        end_dt = datetime(end_d.year, end_d.month, end_d.day, 23, 59, 59, tzinfo=VN_TZ)

        is_btc = "BTC" in sym.upper()
        pk = "BTCUSD" if is_btc else "XAUUSD"
        # Cấu hình backtest RIÊNG (không phải menu live) khi chạy từ tab Backtest của app; app giả (tools/run_backtest.py, test) dùng _get_profile_cfg
        _bt_cfg_getter = getattr(self, "_get_bt_cfg", None)
        cfg = _bt_cfg_getter(pk) if (callable(_bt_cfg_getter) and "bt_sym_var" in self.__dict__) else self._get_profile_cfg(pk)
        def_cfg = DEFAULT_PROFILE_SETTINGS.get(pk, {})

        ana_tf_str = str(cfg.get("analysis_tf", self.analysis_tf_var.get() if hasattr(self, "analysis_tf_var") else "M15")).strip() or "M15"
        scalp_tf_str = str(cfg.get("tf", self.tf_var.get() if hasattr(self, "tf_var") else "M5")).strip() or "M5"

        sl_atr = float(cfg.get("sl_atr", def_cfg.get("sl_atr", "1.5")))
        sl_buffer = float(cfg.get("sl_buffer", def_cfg.get("sl_buffer", "20.0" if is_btc else "0.5")))
        max_sl_price = float(cfg.get("max_sl_price", def_cfg.get("max_sl_price", "350.0" if is_btc else "5.0")))
        rr = float(cfg.get("rr", def_cfg.get("rr", "2.0")))
        auto_be = bool(cfg.get("auto_be", def_cfg.get("auto_be", True)))
        be_trigger_r = float(cfg.get("be_trigger_r", def_cfg.get("be_trigger_r", "1.0")))
        be_plus_price = float(cfg.get("be_plus_price", def_cfg.get("be_plus_price", "5.0" if is_btc else "0.2")))
        auto_trail = bool(cfg.get("auto_trail", def_cfg.get("auto_trail", True)))
        trail_dist_r = float(cfg.get("trail_dist_r", def_cfg.get("trail_dist_r", "0.35")))
        max_hold_mins = int(float(cfg.get("max_hold_mins", def_cfg.get("max_hold_mins", "0"))))
        # Khóa THỬ NGHIỆM (chỉ backtest chế độ nến, mặc định tắt; đặt bằng --set exp_*=...)
        try:
            exp_min_sl = float(cfg.get("exp_min_sl", 0) or 0)
            exp_confirm = str(cfg.get("exp_confirm", "0")) in ("1", "true", "True")
            exp_runner = str(cfg.get("exp_runner", "0")) in ("1", "true", "True")
            exp_daystop = int(float(cfg.get("exp_daystop", 0) or 0))
            exp_realfill = str(cfg.get("exp_realfill", "0")) in ("1", "true", "True")
            exp_sr = float(cfg.get("exp_sr", 0) or 0)  # >0: bỏ lệnh khi cản (đỉnh/đáy swing) cách entry < exp_sr x ATR
            exp_trail_neg = str(cfg.get("exp_trail_neg", "0")) in ("1", "true", "True")  # chỉ bám SL (trailing) khi PnL ngày đang âm
            exp_ha_exit = int(float(cfg.get("exp_ha_exit", 0) or 0))  # >0: bỏ TP/trailing, gồng tới khi N nến HA (khung vào lệnh) ngược màu (sau BE)
        except Exception:
            exp_ha_exit = 0
            exp_min_sl, exp_confirm, exp_runner, exp_daystop, exp_realfill, exp_sr, exp_trail_neg = 0.0, False, False, 0, False, 0.0, False
        _ha_cols = None
        min_consensus = float(cfg.get("consensus", def_cfg.get("consensus", "75")))
        min_move = float(cfg.get("min_move", def_cfg.get("min_move", "60.0" if is_btc else "1.2")))
        model_choice = str(cfg.get("model", def_cfg.get("model", "AI-base (102.3M - Mạnh nhất)")))
        contract_size = 1.0 if is_btc else 100.0

        tf_map = {
            "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1
        } if MT5_AVAILABLE else {}
        mt5_ana_tf = tf_map.get(ana_tf_str, getattr(mt5, "TIMEFRAME_M15", 15))
        mt5_scalp_tf = tf_map.get(scalp_tf_str, getattr(mt5, "TIMEFRAME_M5", 5))

        interval_map = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600}
        interval_sec = interval_map.get(scalp_tf_str, 300)

        # Nạp nến lùi trước start theo SỐ NẾN cần (đủ lookback phân tích + nến scalp/ATR/cấu trúc), không theo 72 giờ cố định.
        # Nến trước start chỉ làm ngữ cảnh: vòng mô phỏng chỉ đánh giá AI/mở lệnh từ nến (giây) đầu tiên >= start.
        start_ts = int(start_dt.timestamp())
        try:
            bt_lookback = min(max(int(float(cfg.get("lookback", def_cfg.get("lookback", 450)))), 50), 450)
        except (ValueError, TypeError):
            bt_lookback = 450
        warmup_sec = max((bt_lookback + 10) * interval_map.get(ana_tf_str, 300),
                         80 * interval_map.get(scalp_tf_str, 300))
        # x1.5 bù cuối tuần/nghỉ phiên, +3 ngày đệm; tối đa 10 ngày lịch
        warmup_sec = min(int(warmup_sec * 1.5) + 3 * 86400, 10 * 86400)

        tf_desc = f"{ana_tf_str}" if ana_tf_str == scalp_tf_str else f"{ana_tf_str} ➔ {scalp_tf_str}"
        self.root.after(0, lambda: self._update_bt_status(f"Đang tải dữ liệu {sym} ({tf_desc}) từ MetaTrader 5...", 5))

        rates_ana = None
        rates_scalp = None
        if MT5_AVAILABLE:
            with self._mt5_lock:
                query_from = datetime.fromtimestamp(start_ts - warmup_sec)
                query_to = datetime.fromtimestamp(int(end_dt.timestamp()) + 7200)
                rates_ana = mt5.copy_rates_range(sym, mt5_ana_tf, query_from, query_to)
                if ana_tf_str == scalp_tf_str:
                    rates_scalp = rates_ana
                else:
                    rates_scalp = mt5.copy_rates_range(sym, mt5_scalp_tf, query_from, query_to)

        if rates_ana is None or len(rates_ana) < 30 or rates_scalp is None or len(rates_scalp) < 30:
            self.root.after(0, lambda: self.show_toast(f"Không lấy được nến lịch sử của {sym} ({tf_desc}) trong khoảng ngày này!", is_error=True))
            self.root.after(0, lambda: self._update_bt_status("Kiểm thử thất bại: Không đủ nến lịch sử từ MT5.", 0))
            return

        # Chuẩn hóa bảng nến lịch sử thành DataFrame chuẩn với đầy đủ các cột phục vụ Não AI và phân tích kỹ thuật
        df_rates_ana = pd.DataFrame(rates_ana)
        if "timestamps" not in df_rates_ana.columns:
            df_rates_ana["timestamps"] = pd.to_datetime(df_rates_ana["time"], unit="s", utc=True).dt.tz_convert(VN_TZ)
        if "volume" not in df_rates_ana.columns:
            df_rates_ana["volume"] = df_rates_ana["tick_volume"].astype(float) if "tick_volume" in df_rates_ana.columns else 1.0
        rate_ana_times = df_rates_ana["time"].values

        df_rates_scalp = pd.DataFrame(rates_scalp)
        if "timestamps" not in df_rates_scalp.columns:
            df_rates_scalp["timestamps"] = pd.to_datetime(df_rates_scalp["time"], unit="s", utc=True).dt.tz_convert(VN_TZ)
        if "volume" not in df_rates_scalp.columns:
            df_rates_scalp["volume"] = df_rates_scalp["tick_volume"].astype(float) if "tick_volume" in df_rates_scalp.columns else 1.0
        rate_scalp_times = df_rates_scalp["time"].values

        n_ana_before = int(np.searchsorted(rate_ana_times, start_ts, side="left"))
        n_scalp_before = int(np.searchsorted(rate_scalp_times, start_ts, side="left"))
        if n_ana_before < bt_lookback or n_scalp_before < 60:
            self.root.after(0, lambda a=n_ana_before, sc=n_scalp_before: self.log(
                f"⚠️ [BACKTEST] Ngữ cảnh trước {start_s} chưa đủ: {a}/{bt_lookback} nến {ana_tf_str}, {sc}/60 nến {scalp_tf_str} "
                f"(giới hạn nạp 10 ngày lịch hoặc dữ liệu sàn) -> các lệnh đầu kỳ dùng ngữ cảnh ngắn hơn live."))

        # Nạp AI Predictor nếu người dùng bật Não AI Kronos
        predictor = None
        if use_ai:
            self.root.after(0, lambda: self._update_bt_status(f"Đang chuẩn bị Não AI Kronos ({model_choice})...", 10))
            try:
                predictor = self.get_local_predictor(model_choice)
            except Exception as pe:
                self.root.after(0, lambda: self.log(f"⚠️ [BACKTEST] Không nạp được model {model_choice} ({str(pe)}), chuyển sang thuật toán kỹ thuật."))
                use_ai = False

        s1_bars = []
        if is_s1_mode:
            self.root.after(0, lambda: self._update_bt_status(f"Đang tải dòng Tick thực tế của {sym} từ máy chủ Exness...", 12))
            ticks = self._fetch_or_load_ticks(sym, start_dt, end_dt)
            if ticks is not None and len(ticks) > 50:
                self.root.after(0, lambda: self._update_bt_status(f"Đã nạp {len(ticks):,} ticks. Đang ghép thành nến 1 giây (S1)...", 18))
                s1_bars = self._resample_ticks_to_s1(ticks)
            else:
                self.root.after(0, lambda: self.log(f"ℹ️ [BACKTEST] Không lấy được đủ ticks cho {sym}, tự động fallback về nến MT5."))
                is_s1_mode = False

        trades = []
        equity = init_cap
        equity_curve = [(0, equity)]
        bt_ai_cache = {}

        # ── KỊCH BẢN 1: MÔ PHỎNG NẾN TICK 1 GIÂY (S1 PRECISION) ───────────────
        if is_s1_mode and len(s1_bars) > 50:
            total_s1 = len(s1_bars)
            self.root.after(0, lambda: self._update_bt_status(f"Đang mô phỏng {total_s1:,} nến 1 giây (Canh vùng AI {ana_tf_str})...", 22))
            # Cổng ngày: chỉ bắt đầu từ giây đầu tiên >= start (tick trước start chỉ là đệm, không đánh giá AI/mở lệnh)
            s1_idx = int(np.searchsorted(np.fromiter((x[0] for x in s1_bars), dtype=np.int64, count=total_s1), start_ts, side="left"))
            active_bt_zone = None
            last_ana_idx = -1

            while s1_idx < total_s1 - 1 and not self._bt_cancel_flag:
                pct = 22 + int((s1_idx / total_s1) * 75)
                if s1_idx % 800 == 0:
                    cur_pnl = sum(t["pnl"] for t in trades)
                    pnl_tag = f" | Lãi: {cur_pnl:+,.2f} $" if trades else ""
                    self.root.after(0, lambda p=pct, i=s1_idx, n=len(trades), pt=pnl_tag: self._update_bt_status(f"Đang mô phỏng giây {i:,}/{total_s1:,} ({n} lệnh{pt})...", p))

                cur_sec = s1_bars[s1_idx][0]
                cur_time = datetime.fromtimestamp(cur_sec, tz=VN_TZ)

                # 1. Chỉ gọi AI khi có cây nến Khung Phân Tích (ana_tf, VD: M5) mới đóng cửa
                idx_ana = int(np.searchsorted(rate_ana_times, cur_sec, side="right")) - 1
                if idx_ana >= 50 and idx_ana != last_ana_idx:
                    last_ana_idx = idx_ana
                    active_bt_zone = None

                    # Trừ 1: searchsorted(right) đếm cả nến scalp ĐANG HÌNH THÀNH (open <= cur_sec); chỉ lấy nến đã đóng như live (chống lookahead)
                    idx_scalp = int(np.searchsorted(rate_scalp_times, cur_sec, side="right")) - 1
                    atr = 20.0 if is_btc else 1.5
                    if idx_scalp >= 20:
                        sub_recent = df_rates_scalp.iloc[max(0, idx_scalp - 14):idx_scalp]
                        highs = sub_recent["high"].values
                        lows = sub_recent["low"].values
                        closes = sub_recent["close"].values
                        trs = [max(highs[j] - lows[j], abs(highs[j] - closes[j-1]), abs(lows[j] - closes[j-1])) for j in range(1, len(highs))]
                        atr = float(np.mean(trs)) if trs else (20.0 if is_btc else 1.5)

                    if use_ai and predictor is not None:
                        if idx_ana in bt_ai_cache:
                            active_bt_zone = bt_ai_cache[idx_ana]
                        else:
                            # Nhường quyền ưu tiên số 1 cho Bot Live nếu đang quét AI thời gian thực
                            while (
                                getattr(self, "is_analyzing", False)
                                or self.profile_ctx.get("XAUUSD", {}).get("is_analyzing", False)
                                or self.profile_ctx.get("BTCUSD", {}).get("is_analyzing", False)
                            ) and not self._bt_cancel_flag:
                                time.sleep(0.05)

                            active_bt_zone = self._evaluate_bt_ai_candle(
                                predictor, df_rates_ana, idx_ana, cfg, def_cfg, ana_tf_str, is_btc, atr,
                                df_rates_scalp=df_rates_scalp, idx_scalp=idx_scalp, scalp_tf_str=scalp_tf_str, cur_sec=cur_sec
                            )
                            bt_ai_cache[idx_ana] = dict(active_bt_zone) if active_bt_zone else None
                            time.sleep(0.005)
                    elif not use_ai:
                        if idx_scalp >= 14:
                            recent = df_rates_scalp.iloc[max(0, idx_scalp - 14):idx_scalp]
                            closes = recent["close"].values
                            highs = recent["high"].values
                            lows = recent["low"].values
                            ma_short = np.mean(closes[-3:])
                            ma_long = np.mean(closes[-10:])
                            swing_high = max(highs[-10:-1])
                            swing_low = min(lows[-10:-1])
                            c_close = closes[-1]
                            raw_sl_d = max(atr * sl_atr + sl_buffer, 8.0 if is_btc else 0.5)
                            if c_close > swing_high and ma_short > ma_long:
                                active_bt_zone = {"signal": "BUY", "sl_price": c_close - raw_sl_d, "atr": atr}
                            elif c_close < swing_low and ma_short < ma_long:
                                active_bt_zone = {"signal": "SELL", "sl_price": c_close + raw_sl_d, "atr": atr}

                # 2. Python canh giá từng giây (S1) về đúng vùng Entry/SL-TP của AI để vào lệnh
                signal = None
                if active_bt_zone is not None:
                    z_sig = active_bt_zone["signal"]
                    z_sl = active_bt_zone["sl_price"]
                    cur_p = s1_bars[s1_idx][5] if z_sig == "BUY" else s1_bars[s1_idx][4]
                    cur_sl_d = (cur_p - z_sl) if z_sig == "BUY" else (z_sl - cur_p)
                    if cur_sl_d <= 0.0:
                        # Giá đã phá qua SL của AI -> Hủy vùng canh của chu kỳ M5 hiện tại
                        active_bt_zone = None
                    elif (z_ai_d := ((cur_p - ((z_sl + sl_buffer) if z_sig == "BUY" else (z_sl - sl_buffer))) if z_sig == "BUY" else (((z_sl + sl_buffer) if z_sig == "BUY" else (z_sl - sl_buffer)) - cur_p))) > 0.0 and (max_sl_price <= 0.0 or z_ai_d <= max_sl_price):
                        # Vùng vào neo theo SL AI gốc (= SL thật ± Giá an toàn), SL thật giữ nguyên
                        signal = z_sig
                        sl_price = z_sl
                        entry_price = cur_p
                        one_r_dist = max(cur_sl_d, 8.0 if is_btc else 0.5)
                        tp_mult = max(rr, 2.0) if rr > 0 else 2.0
                        tp_price = (entry_price + one_r_dist * tp_mult) if z_sig == "BUY" else (entry_price - one_r_dist * tp_mult)
                        active_bt_zone = None

                if signal is not None:
                    be_moved = False

                    out_price = entry_price
                    exit_reason = "Đóng cuối phiên"
                    close_sec_idx = s1_idx + 1

                    for f_idx in range(s1_idx + 1, total_s1):
                        close_sec_idx = f_idx
                        fb = s1_bars[f_idx]
                        fb_time = datetime.fromtimestamp(fb[0], tz=VN_TZ)
                        hold_mins = (fb_time - cur_time).total_seconds() / 60.0

                        if signal == "BUY":
                            bar_h, bar_l, bar_c = fb[2], fb[3], fb[4]      # giá BID
                        else:
                            bar_h, bar_l, bar_c = fb[5], fb[6], fb[7]      # giá ASK
                        sl_price, be_moved, ex_price, ex_reason = step_position(
                            signal, entry_price, sl_price, tp_price, one_r_dist, be_moved, bar_h, bar_l, bar_c, hold_mins,
                            auto_be, auto_trail, be_trigger_r, trail_dist_r, be_plus_price, max_hold_mins)
                        if ex_price is not None:
                            out_price, exit_reason = ex_price, ex_reason
                            break

                    if signal == "BUY":
                        net_pnl = round((out_price - entry_price) * lot * contract_size, 2)
                    else:
                        net_pnl = round((entry_price - out_price) * lot * contract_size, 2)

                    equity += net_pnl
                    out_time = datetime.fromtimestamp(s1_bars[min(close_sec_idx, total_s1 - 1)][0], tz=VN_TZ)
                    dur_secs = max(1, int((out_time - cur_time).total_seconds()))
                    dur_str = self._format_duration(dur_secs)

                    trade_record = {
                        "ticket": f"#{len(trades)+1:04d}",
                        "date": cur_time.strftime("%Y-%m-%d"),
                        "risk_usd": round(one_r_dist * lot * contract_size, 2),
                        "cost_spread": round(float(s1_bars[s1_idx][8]) * lot * contract_size, 2),
                        "cost_kind": "S1_EMBEDDED",
                        "in_time": cur_time.strftime("%d/%m %H:%M:%S"),
                        "out_time": out_time.strftime("%d/%m %H:%M:%S"),
                        "duration": dur_str,
                        "type": signal,
                        "lot": f"{lot:.2f}",
                        "in_price": f"{entry_price:.2f}",
                        "out_price": f"{out_price:.2f}",
                        "pnl": net_pnl,
                        "reason": exit_reason
                    }
                    trades.append(trade_record)
                    equity_curve.append((len(trades), round(equity, 2)))
                    self._bt_trades_cache = trades
                    self._bt_equity_points = equity_curve
                    self.root.after(0, lambda tr=trade_record, trs=list(trades), eq=list(equity_curve), ic=init_cap: self._push_live_bt_trade(tr, trs, eq, ic))
                    s1_idx = max(s1_idx + 1, close_sec_idx)
                else:
                    s1_idx += 1

        # ── KỊCH BẢN 2: MÔ PHỎNG NẾN TIÊU CHUẨN (M1 / M5) ─────────────────────
        else:
            rates = rates_scalp
            total_bars = len(rates)
            self.root.after(0, lambda: self._update_bt_status(f"Đã nạp {total_bars} nến ({scalp_tf_str}). Đang mô phỏng canh vùng AI {ana_tf_str}...", 20))
            # Cổng ngày: chỉ bắt đầu từ nến đầu tiên >= start (nến trước start chỉ là ngữ cảnh, không đánh giá AI/mở lệnh)
            bar_idx = max(20, int(np.searchsorted(rate_scalp_times, start_ts, side="left")))
            active_bt_zone = None
            last_ana_idx = -1

            while bar_idx < total_bars - 1 and not self._bt_cancel_flag:
                pct = 20 + int((bar_idx / total_bars) * 75)
                if bar_idx % 40 == 0:
                    cur_pnl = sum(t["pnl"] for t in trades)
                    pnl_tag = f" | Lãi: {cur_pnl:+,.2f} $" if trades else ""
                    self.root.after(0, lambda p=pct, b=bar_idx, n=len(trades), pt=pnl_tag: self._update_bt_status(f"Đang mô phỏng nến {b}/{total_bars} ({scalp_tf_str} | {n} lệnh{pt})...", p))

                window = rates[bar_idx-14:bar_idx]
                closes = [r[4] for r in window]
                highs = [r[2] for r in window]
                lows = [r[3] for r in window]

                trs = []
                for j in range(1, len(window)):
                    h = highs[j]
                    l = lows[j]
                    pc = closes[j-1]
                    tr = max(h - l, abs(h - pc), abs(l - pc))
                    trs.append(tr)
                atr = float(np.mean(trs)) if trs else (20.0 if is_btc else 1.5)

                cur_bar = rates[bar_idx]
                cur_sec = int(cur_bar[0])
                cur_time = datetime.fromtimestamp(cur_sec, tz=VN_TZ)
                cur_high = float(cur_bar[2])
                cur_low = float(cur_bar[3])
                cur_close = float(cur_bar[4])

                idx_ana = int(np.searchsorted(rate_ana_times, cur_sec, side="right")) - 1
                if idx_ana >= 50 and idx_ana != last_ana_idx:
                    last_ana_idx = idx_ana
                    active_bt_zone = None
                    if use_ai and predictor is not None:
                        if idx_ana in bt_ai_cache:
                            active_bt_zone = bt_ai_cache[idx_ana]
                        else:
                            while (
                                getattr(self, "is_analyzing", False)
                                or self.profile_ctx.get("XAUUSD", {}).get("is_analyzing", False)
                                or self.profile_ctx.get("BTCUSD", {}).get("is_analyzing", False)
                            ) and not self._bt_cancel_flag:
                                time.sleep(0.05)

                            active_bt_zone = self._evaluate_bt_ai_candle(
                                predictor, df_rates_ana, idx_ana, cfg, def_cfg, ana_tf_str, is_btc, atr,
                                df_rates_scalp=df_rates_scalp, idx_scalp=bar_idx, scalp_tf_str=scalp_tf_str, cur_sec=cur_sec
                            )
                            bt_ai_cache[idx_ana] = dict(active_bt_zone) if active_bt_zone else None
                            time.sleep(0.005)
                    elif not use_ai:
                        ma_short = np.mean(closes[-3:])
                        ma_long = np.mean(closes[-10:])
                        swing_high = max(highs[-10:-1])
                        swing_low = min(lows[-10:-1])
                        raw_sl_d = max(atr * sl_atr + sl_buffer, 8.0 if is_btc else 0.5)
                        if cur_close > swing_high and ma_short > ma_long:
                            active_bt_zone = {"signal": "BUY", "sl_price": cur_close - raw_sl_d, "atr": atr}
                        elif cur_close < swing_low and ma_short < ma_long:
                            active_bt_zone = {"signal": "SELL", "sl_price": cur_close + raw_sl_d, "atr": atr}

                signal = None
                _zone_bak = active_bt_zone
                if active_bt_zone is not None:
                    z_sig = active_bt_zone["signal"]
                    z_sl = active_bt_zone["sl_price"]
                    # Vùng vào neo theo SL AI gốc (= SL thật ± Giá an toàn); hủy vùng khi nến ĐÓNG CỬA vượt SL AI (khớp live)
                    z_ai = (z_sl + sl_buffer) if z_sig == "BUY" else (z_sl - sl_buffer)
                    limit_p = (z_ai + max_sl_price) if (z_sig == "BUY" and max_sl_price > 0) else ((z_ai - max_sl_price) if (z_sig == "SELL" and max_sl_price > 0) else cur_close)
                    if (z_sig == "BUY" and cur_close <= z_ai) or (z_sig == "SELL" and cur_close >= z_ai):
                        active_bt_zone = None
                    elif z_sig == "BUY" and (max_sl_price <= 0.0 or cur_low <= limit_p):
                        signal = "BUY"
                        entry_price = min(cur_close, limit_p) if max_sl_price > 0.0 else cur_close
                        if exp_realfill and max_sl_price > 0.0:
                            entry_price = min(float(cur_bar[1]), limit_p)  # lệnh chờ khớp đúng giá vùng (hoặc giá mở nếu gap)
                        sl_price = z_sl
                        one_r_dist = max(entry_price - sl_price, 8.0 if is_btc else 0.5)
                        tp_mult = max(rr, 2.0) if rr > 0 else 2.0
                        tp_price = entry_price + one_r_dist * tp_mult
                        active_bt_zone = None
                    elif z_sig == "SELL" and (max_sl_price <= 0.0 or cur_high >= limit_p):
                        signal = "SELL"
                        entry_price = max(cur_close, limit_p) if max_sl_price > 0.0 else cur_close
                        if exp_realfill and max_sl_price > 0.0:
                            entry_price = max(float(cur_bar[1]), limit_p)
                        sl_price = z_sl
                        one_r_dist = max(sl_price - entry_price, 8.0 if is_btc else 0.5)
                        tp_mult = max(rr, 2.0) if rr > 0 else 2.0
                        tp_price = entry_price - one_r_dist * tp_mult
                        active_bt_zone = None

                # Cổng THỬ NGHIỆM (mặc định tắt): SL tối thiểu / xác nhận nến / dừng ngày sau N lệnh thua liên tiếp
                if signal is not None and (exp_min_sl > 0.0 or exp_confirm or exp_daystop > 0):
                    if exp_min_sl > 0.0 and abs(entry_price - sl_price) < exp_min_sl:
                        signal = None
                    elif exp_daystop > 0:
                        _today = [t for t in trades if t["date"] == cur_time.strftime("%Y-%m-%d")]
                        _streak = 0
                        for _t in reversed(_today):
                            if _t["pnl"] < 0:
                                _streak += 1
                            else:
                                break
                        if _streak >= exp_daystop:
                            signal = None
                    if signal is not None and exp_confirm:
                        _co = float(cur_bar[1])
                        # Xác nhận SAU khi nến đóng (không nhìn trước): vào ở GIÁ ĐÓNG nến xác nhận, SL giữ nguyên,
                        # và khoảng cách tới SL không được vượt Max SL (nếu không -> bỏ, tiếp tục canh vùng).
                        _bad = (signal == "BUY" and cur_close <= _co) or (signal == "SELL" and cur_close >= _co)
                        _d = (cur_close - sl_price) if signal == "BUY" else (sl_price - cur_close)
                        if _bad or _d <= 0.0 or (max_sl_price > 0.0 and _d > max_sl_price):
                            signal = None
                            active_bt_zone = _zone_bak
                        else:
                            entry_price = cur_close
                            one_r_dist = max(_d, 8.0 if is_btc else 0.5)
                            _tm = max(rr, 2.0) if rr > 0 else 2.0
                            tp_price = (entry_price + one_r_dist * _tm) if signal == "BUY" else (entry_price - one_r_dist * _tm)

                # Cổng THỬ NGHIỆM hỗ trợ/kháng cự (mặc định tắt): chỉ dùng nến ĐÃ ĐÓNG trước nến hiện tại (không nhìn trước)
                if signal is not None and exp_sr > 0.0:
                    try:
                        _sl = rates[max(0, bar_idx - 120):bar_idx]
                        _h = [float(r[2]) for r in _sl]
                        _l = [float(r[3]) for r in _sl]
                        _res = [_h[i] for i in range(2, len(_h) - 2) if _h[i] > max(_h[i-2], _h[i-1], _h[i+1], _h[i+2])]
                        _sup = [_l[i] for i in range(2, len(_l) - 2) if _l[i] < min(_l[i-2], _l[i-1], _l[i+1], _l[i+2])]
                        if signal == "BUY":
                            _near = [x - entry_price for x in _res if x > entry_price]
                        else:
                            _near = [entry_price - x for x in _sup if x < entry_price]
                        if _near and min(_near) < exp_sr * atr:
                            signal = None
                    except Exception:
                        pass

                if signal is not None:
                    be_moved = False
                    out_price = entry_price
                    exit_reason = "Đóng cuối phiên"
                    _trail_eff = auto_trail
                    if exp_trail_neg:  # THỬ NGHIỆM: ngày đang thắng/hòa -> tắt trailing; ngày đang thua -> bật
                        _trail_eff = auto_trail and sum(t["pnl"] for t in trades if t["date"] == cur_time.strftime("%Y-%m-%d")) < 0
                    close_bar_idx = bar_idx + 1
                    if exp_ha_exit > 0:
                        _trail_eff = False
                        if _ha_cols is None:
                            _ha_cols = heikin_ashi_colors([r[1] for r in rates], [r[2] for r in rates], [r[3] for r in rates], [r[4] for r in rates])
                    _ha_opp = 0

                    for f_idx in range(bar_idx + 1, total_bars):
                        close_bar_idx = f_idx
                        f_bar = rates[f_idx]
                        f_high = float(f_bar[2])
                        f_low = float(f_bar[3])
                        f_close = float(f_bar[4])
                        f_time = datetime.fromtimestamp(int(f_bar[0]), tz=VN_TZ)
                        hold_mins = (f_time - cur_time).total_seconds() / 60.0

                        sl_price, be_moved, ex_price, ex_reason = step_position(
                            signal, entry_price, sl_price, tp_price, one_r_dist, be_moved, f_high, f_low, f_close, hold_mins,
                            auto_be, _trail_eff, be_trigger_r, trail_dist_r, be_plus_price, max_hold_mins, runner_no_tp=(exp_runner or exp_ha_exit > 0))
                        if ex_price is not None:
                            out_price, exit_reason = ex_price, ex_reason
                            break
                        if exp_ha_exit > 0 and be_moved:  # THỬ NGHIỆM: gồng lãi theo Heikin Ashi (nến f_idx đã đóng)
                            _opp = -1 if signal == "BUY" else 1
                            _ha_opp = _ha_opp + 1 if _ha_cols[f_idx] == _opp else 0
                            if _ha_opp >= exp_ha_exit:
                                out_price, exit_reason = f_close, f"Heikin Ashi đổi màu ({exp_ha_exit} nến)"
                                break

                    spread_pts = 6.4 if is_btc else 0.20
                    if signal == "BUY":
                        pnl_raw = (out_price - entry_price) * lot * contract_size
                    else:
                        pnl_raw = (entry_price - out_price) * lot * contract_size

                    spread_cost = spread_pts * lot * contract_size
                    net_pnl = round(pnl_raw - spread_cost, 2)
                    equity += net_pnl

                    out_time = datetime.fromtimestamp(int(rates[min(close_bar_idx, total_bars - 1)][0]), tz=VN_TZ)
                    dur_secs = max(60, int((out_time - cur_time).total_seconds()))
                    dur_str = self._format_duration(dur_secs)

                    trade_record = {
                        "ticket": f"#{len(trades)+1:04d}",
                        "date": cur_time.strftime("%Y-%m-%d"),
                        "risk_usd": round(one_r_dist * lot * contract_size, 2),
                        "cost_spread": round(spread_cost, 2),
                        "cost_kind": "CANDLE_EXPLICIT",
                        "in_time": cur_time.strftime("%d/%m %H:%M"),
                        "out_time": out_time.strftime("%d/%m %H:%M"),
                        "duration": dur_str,
                        "type": signal,
                        "lot": f"{lot:.2f}",
                        "in_price": f"{entry_price:.2f}",
                        "out_price": f"{out_price:.2f}",
                        "pnl": net_pnl,
                        "reason": exit_reason
                    }
                    trades.append(trade_record)
                    equity_curve.append((len(trades), round(equity, 2)))
                    self._bt_trades_cache = trades
                    self._bt_equity_points = equity_curve
                    self.root.after(0, lambda tr=trade_record, trs=list(trades), eq=list(equity_curve), ic=init_cap: self._push_live_bt_trade(tr, trs, eq, ic))
                    bar_idx = max(bar_idx + 1, close_bar_idx)
                else:
                    bar_idx += 1

        self._bt_trades_cache = trades
        self._bt_equity_points = equity_curve

        self.root.after(0, lambda: self._render_backtest_results(trades, equity_curve, init_cap))

    def _reset_backtest_view_for_run(self):
        """Reset sạch bảng lệnh, đồ thị và thẻ chỉ số trước khi bắt đầu lượt kiểm thử mới."""
        self._bt_trades_cache = []
        self._bt_equity_points = []
        if hasattr(self, "bt_tree") and self.bt_tree.winfo_exists():
            for item in self.bt_tree.get_children():
                self.bt_tree.delete(item)

        if hasattr(self, "bt_kpi_total_lbl") and self.bt_kpi_total_lbl.winfo_exists():
            self.bt_kpi_total_lbl.config(text="0 Lệnh", fg=GOLD_ACCENT)
        if hasattr(self, "bt_kpi_winrate_lbl") and self.bt_kpi_winrate_lbl.winfo_exists():
            self.bt_kpi_winrate_lbl.config(text="0.0%", fg=GREEN_UP)
        if hasattr(self, "bt_kpi_pnl_lbl") and self.bt_kpi_pnl_lbl.winfo_exists():
            self.bt_kpi_pnl_lbl.config(text="+0.00 $", fg=CYAN_LIVE)
        if hasattr(self, "bt_kpi_pf_lbl") and self.bt_kpi_pf_lbl.winfo_exists():
            self.bt_kpi_pf_lbl.config(text="0.00", fg=GOLD_ACCENT)
        if hasattr(self, "bt_kpi_dd_lbl") and self.bt_kpi_dd_lbl.winfo_exists():
            self.bt_kpi_dd_lbl.config(text="-0.00$", fg=RED_DOWN)
        if hasattr(self, "bt_kpi_maxwin_lbl") and self.bt_kpi_maxwin_lbl.winfo_exists():
            self.bt_kpi_maxwin_lbl.config(text="+0.00 $", fg=TEXT_MAIN)

        self._draw_equity_curve([])

    def _push_live_bt_trade(self, t: dict, current_trades: list, current_equity: list, init_cap: float):
        """Cập nhật tức thì lệnh vừa khớp, chỉ số tài chính Scorecards và đồ thị vốn lên giao diện để người dùng theo dõi trực quan theo thời gian thực."""
        # 1. Chèn lệnh vừa khớp vào hàng đầu tiên của Treeview (newest at top)
        if hasattr(self, "bt_tree") and self.bt_tree.winfo_exists():
            pnl_val = t["pnl"]
            tag = "tag_win" if pnl_val > 0.05 else ("tag_loss" if pnl_val < -0.05 else "tag_be")
            row_vals = (
                t["ticket"], t["in_time"], t["out_time"], t["duration"],
                t["type"], t["lot"], t["in_price"], t["out_price"],
                f"{pnl_val:+.2f} $", t["reason"]
            )
            self.bt_tree.insert("", 0, values=row_vals, tags=(tag,))

        # 2. Tính toán lại nhanh các chỉ số Scorecards
        total = len(current_trades)
        wins = sum(1 for tr in current_trades if tr["pnl"] > 0.05)
        losses = sum(1 for tr in current_trades if tr["pnl"] < -0.05)
        bes = total - wins - losses
        win_rate = (wins / max(total, 1)) * 100.0 if total > 0 else 0.0

        total_pnl = sum(tr["pnl"] for tr in current_trades)
        win_sum = sum(tr["pnl"] for tr in current_trades if tr["pnl"] > 0)
        loss_sum = abs(sum(tr["pnl"] for tr in current_trades if tr["pnl"] < 0))
        pf = (win_sum / max(loss_sum, 0.01)) if loss_sum > 0 else (99.0 if win_sum > 0 else 0.0)
        avg_trade = (total_pnl / max(total, 1)) if total > 0 else 0.0

        # Tính Drawdown tức thời
        peak = init_cap
        max_dd = 0.0
        max_dd_pct = 0.0
        for _, eq in current_equity:
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_dd:
                max_dd = dd
                max_dd_pct = (dd / peak) * 100.0 if peak > 0 else 0.0

        # Cập nhật ngay 6 thẻ Scorecards trên giao diện
        if hasattr(self, "bt_kpi_total_lbl") and self.bt_kpi_total_lbl.winfo_exists():
            self.bt_kpi_total_lbl.config(text=f"{total} Lệnh ({wins}W - {losses}L - {bes}BE)")
        if hasattr(self, "bt_kpi_winrate_lbl") and self.bt_kpi_winrate_lbl.winfo_exists():
            self.bt_kpi_winrate_lbl.config(text=f"{win_rate:.1f}%", fg=GREEN_UP if win_rate >= 55.0 else (GOLD_ACCENT if win_rate >= 45.0 else RED_DOWN))
        if hasattr(self, "bt_kpi_pnl_lbl") and self.bt_kpi_pnl_lbl.winfo_exists():
            self.bt_kpi_pnl_lbl.config(text=f"{total_pnl:+,.2f} $", fg=GREEN_UP if total_pnl >= 0 else RED_DOWN)
        if hasattr(self, "bt_kpi_pf_lbl") and self.bt_kpi_pf_lbl.winfo_exists():
            self.bt_kpi_pf_lbl.config(text=f"{pf:.2f}", fg=GREEN_UP if pf >= 1.5 else (GOLD_ACCENT if pf >= 1.0 else RED_DOWN))
        if hasattr(self, "bt_kpi_dd_lbl") and self.bt_kpi_dd_lbl.winfo_exists():
            self.bt_kpi_dd_lbl.config(text=f"-{max_dd:.2f}$ ({max_dd_pct:.1f}%)")
        if hasattr(self, "bt_kpi_maxwin_lbl") and self.bt_kpi_maxwin_lbl.winfo_exists():
            self.bt_kpi_maxwin_lbl.config(text=f"{avg_trade:+,.2f} $/lệnh", fg=GREEN_UP if avg_trade >= 0 else RED_DOWN)

        # 3. Vẽ lại đồ thị đường cong tăng trưởng vốn (Equity Curve) trực tiếp theo thời gian thực
        if len(current_equity) >= 2:
            self._draw_equity_curve(current_equity)

    def _render_backtest_results(self, trades: list, equity_curve: list, init_cap: float, final: bool = True):
        """Xuất toàn bộ kết quả lên Treeview bảng lệnh, 6 thẻ Scorecard và biểu đồ Canvas."""
        # 1. Đổ dữ liệu vào Treeview
        if hasattr(self, "bt_tree") and self.bt_tree.winfo_exists():
            for item in self.bt_tree.get_children():
                self.bt_tree.delete(item)

            for t in reversed(trades):
                pnl_val = t["pnl"]
                tag = "tag_win" if pnl_val > 0.05 else ("tag_loss" if pnl_val < -0.05 else "tag_be")
                row_vals = (
                    t["ticket"], t["in_time"], t["out_time"], t["duration"],
                    t["type"], t["lot"], t["in_price"], t["out_price"],
                    f"{pnl_val:+.2f} $", t["reason"]
                )
                self.bt_tree.insert("", tk.END, values=row_vals, tags=(tag,))

        # 2. Tính toán chỉ số tài chính Scorecards
        total = len(trades)
        wins = sum(1 for t in trades if t["pnl"] > 0.05)
        losses = sum(1 for t in trades if t["pnl"] < -0.05)
        bes = total - wins - losses
        win_rate = (wins / max(total, 1)) * 100.0 if total > 0 else 0.0

        total_pnl = sum(t["pnl"] for t in trades)
        win_sum = sum(t["pnl"] for t in trades if t["pnl"] > 0)
        loss_sum = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
        pf = (win_sum / max(loss_sum, 0.01)) if loss_sum > 0 else (99.0 if win_sum > 0 else 0.0)
        avg_trade = (total_pnl / max(total, 1)) if total > 0 else 0.0

        # Tính Max Drawdown
        peak = init_cap
        max_dd = 0.0
        max_dd_pct = 0.0
        for _, eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_dd:
                max_dd = dd
                max_dd_pct = (dd / peak) * 100.0 if peak > 0 else 0.0

        # 3. Cập nhật nhãn Scorecards
        if hasattr(self, "bt_kpi_total_lbl"):
            self.bt_kpi_total_lbl.config(text=f"{total} Lệnh ({wins}W - {losses}L - {bes}BE)")
        if hasattr(self, "bt_kpi_winrate_lbl"):
            self.bt_kpi_winrate_lbl.config(text=f"{win_rate:.1f}%", fg=GREEN_UP if win_rate >= 55.0 else (GOLD_ACCENT if win_rate >= 45.0 else RED_DOWN))
        if hasattr(self, "bt_kpi_pnl_lbl"):
            self.bt_kpi_pnl_lbl.config(text=f"{total_pnl:+,.2f} $", fg=GREEN_UP if total_pnl >= 0 else RED_DOWN)
        if hasattr(self, "bt_kpi_pf_lbl"):
            self.bt_kpi_pf_lbl.config(text=f"{pf:.2f}", fg=GREEN_UP if pf >= 1.5 else (GOLD_ACCENT if pf >= 1.0 else RED_DOWN))
        if hasattr(self, "bt_kpi_dd_lbl"):
            self.bt_kpi_dd_lbl.config(text=f"-{max_dd:.2f}$ ({max_dd_pct:.1f}%)")
        if hasattr(self, "bt_kpi_maxwin_lbl"):
            self.bt_kpi_maxwin_lbl.config(text=f"{avg_trade:+,.2f} $/lệnh", fg=GREEN_UP if avg_trade >= 0 else RED_DOWN)

        # 4. Cập nhật thanh tiến độ và vẽ biểu đồ Equity Curve
        if not final:  # đang xem trực tiếp: chỉ cập nhật bảng/thẻ/đường vốn theo thời điểm đang phát
            self._draw_equity_curve(equity_curve)
            return
        self._update_bt_status(f"✓ Kiểm thử hoàn tất: {total} lệnh | Win Rate: {win_rate:.1f}% | Lãi ròng: {total_pnl:+,.2f} USD.", 100)
        self._draw_equity_curve(equity_curve)

        # 5. Báo cáo tổng kết nâng cao + xuất CSV (chỉ đọc kết quả, không ảnh hưởng logic vào/thoát lệnh)
        try:
            self._emit_backtest_summary(trades, init_cap)
        except Exception as e:
            self.log_error("BACKTEST_SUMMARY", e, show_in_ui=False)

    def _emit_backtest_summary(self, trades: list, init_cap: float):
        """Ghi báo cáo tổng kết backtest vào log và xuất CSV danh sách lệnh vào SETTINGS_DIR/backtests."""
        for line in format_backtest_report(build_backtest_report(trades, init_cap)):
            self.log(line)
        if not trades:
            return
        sym = re.sub(r"[^A-Za-z0-9_.-]", "_", self.bt_sym_var.get().strip() or "SYM")
        start_s = self.bt_start_var.get().strip()
        end_s = self.bt_end_var.get().strip()
        fname = f"backtest_{sym}_{start_s}_{end_s}_{datetime.now().strftime('%H%M%S')}.csv"
        path = export_backtest_csv(trades, os.path.join(SETTINGS_DIR, "backtests", fname))
        self.log(f"💾 Đã xuất danh sách lệnh backtest: {path}")

    def _draw_equity_curve(self, points: list):
        """Vẽ biểu đồ đường cong tăng trưởng vốn (Equity Curve) trực quan trên Tkinter Canvas."""
        if not hasattr(self, "bt_chart_canvas") or not self.bt_chart_canvas.winfo_exists():
            return
        c = self.bt_chart_canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 60 or h < 60:
            return

        if not points or len(points) < 2:
            c.create_text(w / 2, h / 2, text="Chưa có dữ liệu đồ thị Backtest", fill=TEXT_MUTED, font=("Segoe UI", 9))
            return

        pad_left = 65
        pad_right = 25
        pad_top = 25
        pad_bottom = 25

        plot_w = w - pad_left - pad_right
        plot_h = h - pad_top - pad_bottom

        vals = [p[1] for p in points]
        min_v = min(vals)
        max_v = max(vals)
        if max_v == min_v:
            max_v += 10.0
            min_v -= 10.0

        v_range = max_v - min_v
        n_pts = len(points)

        # Khung nền đồ thị
        c.create_rectangle(pad_left, pad_top, pad_left + plot_w, pad_top + plot_h, outline="#1f293d", fill="#0e121a")

        # Lưới ngang & nhãn giá trị
        for g_idx in range(5):
            gy = pad_top + plot_h * (g_idx / 4.0)
            gv = max_v - v_range * (g_idx / 4.0)
            c.create_line(pad_left, gy, pad_left + plot_w, gy, fill="#182030", dash=(2, 2))
            c.create_text(pad_left - 8, gy, text=f"${gv:,.0f}", fill=TEXT_MUTED, font=("Consolas", 7), anchor="e")

        # Tọa độ các điểm trên Canvas
        coords = []
        for i, (tr_idx, val) in enumerate(points):
            x = pad_left + plot_w * (i / max(n_pts - 1, 1))
            y = pad_top + plot_h * (1.0 - (val - min_v) / v_range)
            coords.append((x, y))

        # Đổ bóng đa giác bên dưới đường cong
        poly_coords = [coords[0][0], pad_top + plot_h]
        for x, y in coords:
            poly_coords.extend([x, y])
        poly_coords.extend([coords[-1][0], pad_top + plot_h])
        fill_color = "#102a24" if vals[-1] >= vals[0] else "#2a1518"
        c.create_polygon(poly_coords, fill=fill_color, outline="")

        # Vẽ đường cong tăng trưởng vốn Neon
        line_color = "#00e676" if vals[-1] >= vals[0] else "#ff4b60"
        for i in range(len(coords) - 1):
            c.create_line(coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1], fill=line_color, width=2, smooth=True)

        # Chấm tròn điểm đầu và điểm cuối
        c.create_oval(coords[0][0] - 3, coords[0][1] - 3, coords[0][0] + 3, coords[0][1] + 3, fill=GOLD_ACCENT, outline="")
        c.create_oval(coords[-1][0] - 4, coords[-1][1] - 4, coords[-1][0] + 4, coords[-1][1] + 4, fill=line_color, outline="#ffffff")
        c.create_text(coords[-1][0], coords[-1][1] - 12, text=f"${vals[-1]:,.2f}", fill=line_color, font=("Consolas", 8, "bold"))

    def _init_right_paned_sash(self):
        """Đặt vị trí ban đầu cho thanh kéo giãn giữa Biểu đồ và Bảng lệnh."""
        if not hasattr(self, "right_paned") or self.right_paned is None or not self.right_paned.winfo_exists():
            return
        try:
            self.root.update_idletasks()
            total_h = self.right_paned.winfo_height()
            if total_h > 350:
                saved_h = getattr(self, "_saved_bottom_panel_h", 240)
                if not isinstance(saved_h, (int, float)) or saved_h < 120 or saved_h > total_h - 180:
                    saved_h = 240
                target_sash_y = max(180, total_h - int(saved_h))
                self.right_paned.sash_place(0, 0, target_sash_y)
        except Exception:
            pass

    def _on_sash_released(self, event=None):
        """Lưu lại chiều cao bảng lệnh khi người dùng thả chuột kéo thanh splitter."""
        if not hasattr(self, "bottom_notebook") or self.bottom_notebook is None or not self.bottom_notebook.winfo_exists():
            return
        try:
            h = self.bottom_notebook.winfo_height()
            if h >= 120:
                self._saved_bottom_panel_h = h
                self.save_user_settings()
        except Exception:
            pass

    def _on_paned_double_click(self, event):
        """Bắt sự kiện nhấp đúp vào thanh phân cách để mở rộng / thu gọn nhanh."""
        if not hasattr(self, "right_paned") or self.right_paned is None or not self.right_paned.winfo_exists():
            return
        try:
            ident = self.right_paned.identify(event.x, event.y)
            if ident and len(ident) > 1 and ident[1] == "sash":
                self.toggle_bottom_panel_expand()
        except Exception:
            pass

    def toggle_bottom_panel_expand(self):
        """Chuyển đổi nhanh giữa chiều cao chuẩn (~240px) và chiều cao mở rộng (~480px) của Bảng lệnh."""
        if not hasattr(self, "right_paned") or self.right_paned is None or not self.right_paned.winfo_exists():
            return
        try:
            total_h = self.right_paned.winfo_height()
            if total_h <= 300:
                return
            cur_nb_h = self.bottom_notebook.winfo_height()
            if cur_nb_h < 320:
                target_nb_h = min(total_h - 180, max(460, int(total_h * 0.58)))
            else:
                target_nb_h = 240
            target_sash_y = max(180, total_h - target_nb_h)
            self.right_paned.sash_place(0, 0, target_sash_y)
            self._saved_bottom_panel_h = target_nb_h
            self.save_user_settings()
        except Exception as e:
            self.log_error("TOGGLE_PANEL_EXPAND", e)

    def _set_history_today(self):
        t_str = datetime.now(tz=VN_TZ).strftime("%Y-%m-%d")
        self.hist_start_var.set(t_str)
        self.hist_end_var.set(t_str)
        self._refresh_history_tab()

    def _set_history_yesterday(self):
        y_str = (datetime.now(tz=VN_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
        self.hist_start_var.set(y_str)
        self.hist_end_var.set(y_str)
        self._refresh_history_tab()

    def _set_history_7days(self):
        now_dt = datetime.now(tz=VN_TZ)
        self.hist_start_var.set((now_dt - timedelta(days=7)).strftime("%Y-%m-%d"))
        self.hist_end_var.set(now_dt.strftime("%Y-%m-%d"))
        self._refresh_history_tab()

    def _set_history_30days(self):
        now_dt = datetime.now(tz=VN_TZ)
        self.hist_start_var.set((now_dt - timedelta(days=30)).strftime("%Y-%m-%d"))
        self.hist_end_var.set(now_dt.strftime("%Y-%m-%d"))
        self._refresh_history_tab()

    def _set_history_this_month(self):
        now_dt = datetime.now(tz=VN_TZ)
        self.hist_start_var.set(now_dt.replace(day=1).strftime("%Y-%m-%d"))
        self.hist_end_var.set(now_dt.strftime("%Y-%m-%d"))
        self._refresh_history_tab()

    def _hist_quick_ranges(self) -> dict:
        """Khoảng ngày của các nút chọn nhanh (giống các hàm _set_history_*)."""
        now_dt = datetime.now(tz=VN_TZ)
        t = now_dt.strftime("%Y-%m-%d")
        y = (now_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        return {
            "today": (t, t), "yesterday": (y, y),
            "7d": ((now_dt - timedelta(days=7)).strftime("%Y-%m-%d"), t),
            "30d": ((now_dt - timedelta(days=30)).strftime("%Y-%m-%d"), t),
            "month": (now_dt.replace(day=1).strftime("%Y-%m-%d"), t),
        }

    def _update_hist_range_btn(self):
        """Ghi khoảng ngày lên nút lịch và tô vàng nút chọn nhanh đang khớp (chỉ giao diện)."""
        try:
            s_iso, e_iso = self.hist_start_var.get().strip(), self.hist_end_var.get().strip()
            try:
                s_txt = datetime.strptime(s_iso, "%Y-%m-%d").strftime("%d/%m/%Y")
                e_txt = datetime.strptime(e_iso, "%Y-%m-%d").strftime("%d/%m/%Y")
                txt = f"📅  {s_txt}" if s_iso == e_iso else f"📅  {s_txt}  →  {e_txt}"
            except ValueError:
                txt = f"📅  {s_iso} → {e_iso}"
            if hasattr(self, "hist_range_btn"):
                self.hist_range_btn.config(text=txt + "  ▾")
            active = next((k for k, rg in self._hist_quick_ranges().items() if rg == (s_iso, e_iso)), None)
            for k, b in getattr(self, "_hist_quick_btns", {}).items():
                b.config(bg=(GOLD_ACCENT if k == active else "#19202c"), fg=("#000000" if k == active else TEXT_MUTED))
        except Exception:
            pass

    def _hist_filter_args(self):
        """(symbol, profile_key, magic_scope) theo ô Mã và 'Gồm lệnh tay' của tab Lịch Sử."""
        sym_choice = self.hist_symbol_var.get().strip()
        include_manual = getattr(self, "hist_include_manual_var", None)
        is_manual = include_manual.get() if include_manual is not None else False
        sym_filter = None
        pk_hist = None
        if is_manual:
            magic_scope = "ALL"
            if sym_choice == "XAUUSD":
                sym_filter = getattr(self, "symbol_xau", "XAUUSDm")
            elif sym_choice == "BTCUSD":
                sym_filter = getattr(self, "symbol_btc", "BTCUSDm")
        else:
            if sym_choice == "XAUUSD":
                sym_filter = getattr(self, "symbol_xau", "XAUUSDm")
                pk_hist = "XAUUSD"
                magic_scope = "PROFILE"
            elif sym_choice == "BTCUSD":
                sym_filter = getattr(self, "symbol_btc", "BTCUSDm")
                pk_hist = "BTCUSD"
                magic_scope = "PROFILE"
            else:
                magic_scope = "BOT_ALL"
        return sym_filter, pk_hist, magic_scope

    def _hist_fetch_daily_pnl(self, start_iso: str, end_iso: str) -> dict:
        """Lãi/lỗ theo ngày cho Lịch (cùng bộ lọc với bảng; không ghi bộ đệm dùng chung của luật giao dịch)."""
        sym_filter, pk_hist, magic_scope = self._hist_filter_args()
        deals = self._get_closed_deals_by_range(
            symbol=sym_filter, start_date_str=start_iso, end_date_str=end_iso, use_cache=False,
            profile_key=pk_hist, magic_scope=magic_scope, store_cache=False,
        )
        return daily_pnl(deals)

    def _open_pnl_calendar(self):
        """Mở Lịch Lãi/Lỗ: chọn khoảng ngày (bấm 2 ngày) rồi Áp dụng -> nạp lại bảng Lịch Sử."""
        def _apply(s_iso, e_iso):
            self.hist_start_var.set(s_iso)
            self.hist_end_var.set(e_iso)
            self._refresh_history_tab()
        try:
            PnlCalendarDialog(self.root, self.hist_start_var.get(), self.hist_end_var.get(),
                              fetch_daily=self._hist_fetch_daily_pnl, on_apply=_apply,
                              today=datetime.now(tz=VN_TZ).date())
        except Exception as e:
            self.log_error("PNL_CALENDAR", e, show_in_ui=False)

    def _refresh_history_tab(self):
        """Truy vấn MT5 và nạp danh sách lệnh đóng theo khung thời gian đã chọn vào bảng Lịch Sử."""
        if not hasattr(self, "hist_tree") or not self.hist_tree.winfo_exists():
            return
        start_s = self.hist_start_var.get().strip()
        end_s = self.hist_end_var.get().strip()
        sym_choice = self.hist_symbol_var.get().strip()

        sym_filter, pk_hist, magic_scope = self._hist_filter_args()

        deals = self._get_closed_deals_by_range(
            symbol=sym_filter,
            start_date_str=start_s,
            end_date_str=end_s,
            use_cache=False,
            profile_key=pk_hist,
            magic_scope=magic_scope,
        )
        self._cached_hist_deals = deals

        for item in self.hist_tree.get_children():
            self.hist_tree.delete(item)

        total_deals = len(deals)
        wins = sum(1 for d in deals if d.get("category") == "WIN")
        losses = sum(1 for d in deals if d.get("category") == "LOSS")
        bes = sum(1 for d in deals if d.get("category") == "BE+")
        net_sum = sum(d.get("net_pnl", 0.0) for d in deals)
        gross_profit = sum(d.get("net_pnl", 0.0) for d in deals if d.get("net_pnl", 0.0) > 0)
        gross_loss = abs(sum(d.get("net_pnl", 0.0) for d in deals if d.get("net_pnl", 0.0) < 0))
        pf = (gross_profit / gross_loss) if gross_loss > 0 else (99.9 if gross_profit > 0 else 0.0)

        valid_trades = wins + losses
        win_rate = (wins / valid_trades * 100.0) if valid_trades > 0 else 0.0
        wr_str = f"{win_rate:.1f}%" if valid_trades > 0 else "---"

        kpi = getattr(self, "hist_kpi_lbls", {})
        if kpi:
            pnl_col = GREEN_UP if net_sum > 0 else (RED_DOWN if net_sum < 0 else TEXT_MAIN)
            for k_key, k_txt, k_col in (
                ("total", str(total_deals), TEXT_MAIN), ("win", str(wins), GREEN_UP), ("be", str(bes), GOLD_ACCENT),
                ("loss", str(losses), RED_DOWN), ("wr", wr_str, TEXT_MAIN), ("pnl", f"{net_sum:+,.2f} $", pnl_col),
                ("pf", (f"{pf:.2f}" if total_deals else "---"), TEXT_MAIN),
            ):
                if k_key in kpi:
                    kpi[k_key].config(text=k_txt, fg=k_col if total_deals else TEXT_MUTED)
            self.hist_summary_lbl.config(text=("" if total_deals else "Không có lệnh đóng trong khoảng này"), fg=TEXT_MUTED)
        else:
            self.hist_summary_lbl.config(
                text=f"Tổng: {total_deals} lệnh | Win Rate: {wr_str} | Tổng Lãi/Lỗ: {net_sum:+.2f}$ | PF: {pf:.2f}",
                fg=(GREEN_UP if net_sum >= 0 else RED_DOWN) if total_deals > 0 else GOLD_ACCENT)

        for d in deals:
            cat = d.get("category", "BE+")
            tag = "tag_win" if cat == "WIN" else ("tag_loss" if cat == "LOSS" else "tag_be")
            self.hist_tree.insert(
                "", tk.END,
                values=(
                    str(d["ticket"]),
                    d["time_display"],
                    d.get("duration", "---"),
                    d["symbol"],
                    d["type"],
                    f"{d['volume']:.2f}",
                    f"{d['open_price']:.2f}" if d["open_price"] > 0 else "---",
                    f"{d['close_price']:.2f}",
                    f"{d.get('sl', 0.0):.2f}" if d.get('sl', 0.0) > 0 else "---",
                    f"{d.get('tp', 0.0):.2f}" if d.get('tp', 0.0) > 0 else "---",
                    f"{d['net_pnl']:+.2f} $",
                    d.get("r_ratio_str", "---"),
                    cat,
                    d["status"]
                ),
                tags=(tag,)
            )

    def export_history_to_excel(self):
        """Xuất danh sách lệnh trong khung thời gian đang chọn ra file Excel (.xlsx / .csv)."""
        deals = getattr(self, "_cached_hist_deals", [])
        if not deals:
            messagebox.showwarning("Thông Báo", "Không có dữ liệu lệnh nào trong khung thời gian đã chọn để xuất file!")
            return

        start_s = self.hist_start_var.get().strip().replace("-", "")
        end_s = self.hist_end_var.get().strip().replace("-", "")
        default_fn = f"Lich_Su_Trading_AI_PRO_{start_s}_{end_s}.xlsx"

        file_path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Lưu File Báo Cáo Lịch Sử Giao Dịch",
            initialfile=default_fn,
            filetypes=[("Excel Workbook (*.xlsx)", "*.xlsx"), ("CSV UTF-8 (*.csv)", "*.csv"), ("Tất cả tập tin", "*.*")]
        )
        if not file_path:
            return

        rows = []
        for idx, d in enumerate(deals, start=1):
            rows.append({
                "STT": idx,
                "Mã Lệnh (Ticket)": d["ticket"],
                "Ngày": d.get("date", "---"),
                "Giờ Vào": d.get("open_time", "---"),
                "Giờ Đóng": d.get("close_time", "---"),
                "Thời Gian Giữ": d.get("duration", "---"),
                "Cặp Giao Dịch": d["symbol"],
                "Loại Lệnh": d["type"],
                "Khối Lượng (Lot)": d["volume"],
                "Giá Vào (Entry)": d["open_price"],
                "Giá Đóng (Close)": d["close_price"],
                "Cắt Lỗ (SL)": d.get("sl", 0.0),
                "Chốt Lời (TP)": d.get("tp", 0.0),
                "Lãi Ròng Net ($)": round(d["net_pnl"], 2),
                "Tỷ Lệ R": d.get("r_ratio_str", "---"),
                "Phân Loại": d.get("category", "---"),
                "Trạng Thái / Kết Quả": d.get("status", "---"),
            })

        try:
            df_exp = pd.DataFrame(rows)
            if file_path.lower().endswith(".csv"):
                df_exp.to_csv(file_path, index=False, encoding="utf-8-sig")
            else:
                try:
                    df_exp.to_excel(file_path, index=False, engine="openpyxl")
                except Exception:
                    if not file_path.lower().endswith(".csv"):
                        file_path = file_path.rsplit(".", 1)[0] + ".csv"
                    df_exp.to_csv(file_path, index=False, encoding="utf-8-sig")

            self.log(f"📊 Đã xuất file báo cáo lịch sử thành công ({len(deals)} lệnh): {file_path}")
            messagebox.showinfo(
                "Xuất File Thành Công",
                f"Đã xuất thành công {len(deals)} lệnh vào file:\n{file_path}\n\nBạn có thể mở trực tiếp bằng Microsoft Excel!"
            )
        except Exception as e:
            self.log_error("EXPORT_EXCEL", e)
            messagebox.showerror("Lỗi Xuất File", f"Không thể lưu file báo cáo:\n{e}")

    # =========================================================================
    # TỰ ĐỘNG LƯU & KHÔI PHỤC CÀI ĐẶT NGƯỜI DÙNG THEO TỪNG TAB (XAUUSD / BTCUSD)
    # =========================================================================
    @staticmethod
    def _format_duration(duration_seconds: int) -> str:
        """Định dạng thời gian giữ lệnh thành chuỗi hiển thị trực quan (VD: 45s, 12p 30s, 1h 15p)."""
        if duration_seconds < 0:
            duration_seconds = 0
        if duration_seconds < 60:
            return f"{duration_seconds}s"
        m = duration_seconds // 60
        s = duration_seconds % 60
        if m < 60:
            return f"{m}p {s:02d}s"
        h = m // 60
        rem_m = m % 60
        return f"{h}h {rem_m:02d}p"

    def _get_settings_vars_map(self) -> dict:
        return {
            "symbol": self.symbol_var,
            "analysis_tf": self.analysis_tf_var,
            "tf": self.tf_var,
            "lot": self.lot_var,
            "max_pos": self.max_pos_var,
            "magic_number": self.magic_number_var,
            "mt5_path": self.mt5_path_var,
            "ai_threads": self.ai_threads_var,
            "scenarios": self.scenarios_var,
            "consensus": self.consensus_var,
            "max_dispersion": self.max_dispersion_var,
            "min_move": self.min_move_var,
            "expected_move_k": self.expected_move_k_var,
            "max_spread": self.max_spread_var,
            "sl_atr": self.sl_atr_var,
            "sl_buffer": self.sl_buffer_var,
            "max_sl_price": self.max_sl_price_var,
            "rr": self.rr_var,
            "min_entry_spacing": self.min_entry_spacing_var,
            "entry_mode": self.entry_mode_var,
            "allow_opposite_entry": self.allow_opposite_entry_var,
            "one_order_per_candle": self.one_order_per_candle_var,
            "trend_filter": self.trend_filter_var,
            "confirm_entry_pa": self.confirm_entry_pa_var,
            "pa_fail_open": self.pa_fail_open_var,
            "pa_min_confirmations": self.pa_min_confirmations_var,
            "structure_conflict_mode": self.structure_conflict_mode_var,
            "use_confidence_gate": self.use_confidence_gate_var,
            "debug_trace": self.debug_trace_var,
            "recovery_close": self.recovery_close_var,
            "stack_half_be": self.stack_half_be_var,
            "structure_direction": self.structure_direction_var,
            "blocked_trading_windows": self.blocked_trading_windows_var,
            "auto_be": self.auto_be_var,
            "be_trigger_r": self.be_trigger_r_var,
            "auto_trail": self.auto_trail_var,
            "trail_dist_r": self.trail_dist_r_var,
            "auto_rev_close": self.auto_rev_close_var,
            "rev_consensus": self.rev_consensus_var,
            "rev_be_consensus": self.rev_be_consensus_var,
            "rev_min_move": self.rev_min_move_var,
            "be_plus_price": self.be_plus_price_var,
            "rev_loss_mode": self.rev_loss_mode_var,
            "basket_tp_usd": self.basket_tp_usd_var,
            "basket_cooldown_mins": self.basket_cooldown_mins_var,
            "max_hold_mins": self.max_hold_mins_var,
            "max_hold_action": self.max_hold_action_var,
            "daily_tp_usd": self.daily_tp_usd_var,
            "daily_sl_usd": self.daily_sl_usd_var,
            "max_consec_loss": self.max_consec_loss_var,
            "cooldown_mins": self.cooldown_mins_var,
            "fast_sl_cooldown_candles": self.fast_sl_cooldown_candles_var,
            "fast_sl_cooldown_mins": self.fast_sl_cooldown_candles_var,
            "fast_sl_same_dir": self.fast_sl_same_dir_var,
            "engine": self.engine_var,
            "model": self.model_var,
            "hf_space": self.hf_space_var,
            "hf_token": self.hf_token_var,
            "lookback": self.lookback_var,
            "pred_len": self.pred_len_var,
            "realtime_chart": self.realtime_chart_var,
            "telegram_enabled": self.telegram_enabled_var,
            "telegram_bot_token": self.telegram_bot_token_var,
            "telegram_chat_id": self.telegram_chat_id_var,
            "telegram_admin_ids": self.telegram_admin_ids_var,
            "telegram_notify_open": self.telegram_notify_open_var,
            "telegram_notify_close": self.telegram_notify_close_var,
        }

    def show_toast(self, message: str, is_error: bool = False, duration_ms: int = 2000):
        """Hiển thị thông báo Toast tinh gọn ở góc trên bên phải cửa sổ Bot, tự động ẩn sau 2 giây (không chặn giao diện, Thread-Safe)."""
        if threading.current_thread() is not threading.main_thread():
            self._ui_after(0, lambda: self.show_toast(message, is_error, duration_ms))
            return
        try:
            if getattr(self, "_active_toast", None) is not None:
                try:
                    self._active_toast.destroy()
                except Exception:
                    pass
                self._active_toast = None

            toast = tk.Toplevel(self.root)
            self._active_toast = toast
            toast.overrideredirect(True)
            toast.attributes("-topmost", True)

            bg_color = "#1e293b"  # Slate tối thanh lịch, chuyên nghiệp, không màu mè
            border_color = "#ef4444" if is_error else "#10b981"
            fg_color = "#fca5a5" if is_error else "#6ee7b7"

            frame = tk.Frame(
                toast,
                bg=bg_color,
                padx=14,
                pady=8,
                highlightthickness=1,
                highlightbackground=border_color
            )
            frame.pack(fill=tk.BOTH, expand=True)

            icon_str = "⚠️" if is_error else "✓"
            tk.Label(
                frame,
                text=f"{icon_str}  {message}",
                bg=bg_color,
                fg=fg_color,
                font=("Segoe UI", 9, "bold"),
                anchor="w"
            ).pack(side=tk.LEFT)

            toast.update_idletasks()
            rx = self.root.winfo_rootx()
            ry = self.root.winfo_rooty()
            rw = self.root.winfo_width()
            tw = toast.winfo_width()

            pos_x = rx + rw - tw - 25
            pos_y = ry + 45
            toast.geometry(f"+{max(0, pos_x)}+{max(0, pos_y)}")

            self.root.after(duration_ms, lambda t=toast: self._dismiss_toast(t))
        except Exception as e:
            self.log_error("SHOW_TOAST", e, show_in_ui=False)

    def _dismiss_toast(self, toast):
        try:
            if toast and toast.winfo_exists():
                toast.destroy()
            if getattr(self, "_active_toast", None) == toast:
                self._active_toast = None
        except Exception:
            pass

    def _get_ai_scan_config_sig(self, profile_key: str = None) -> tuple:
        """Lấy chữ ký các thông số ảnh hưởng đến phân tích AI của một Tab để chỉ quét lại khi người dùng thực sự đổi khung/thông số AI."""
        pk = profile_key or self.active_profile_key
        cfg = self._get_profile_cfg(pk)
        scalp_tf = str(cfg.get("tf", "M5")).strip() or "M5"
        ana_tf = str(cfg.get("analysis_tf", scalp_tf)).strip() or scalp_tf
        return (
            str(cfg.get("symbol", pk)).strip(),
            ana_tf,
            str(cfg.get("model", "")).strip(),
            str(cfg.get("engine", "")).strip(),
            str(cfg.get("lookback", "450")).strip(),
            str(cfg.get("pred_len", "6")).strip(),
            str(cfg.get("scenarios", "8")).strip(),
            str(cfg.get("consensus", "75")).strip(),
            str(cfg.get("max_dispersion", "2.5")).strip(),
            str(cfg.get("min_move", "1.2")).strip(),
            str(cfg.get("sl_atr", "1.5")).strip(),
            str(cfg.get("sl_buffer", "0.5")).strip(),
            str(cfg.get("max_sl_price", "5.0")).strip(),
            str(cfg.get("rr", "2.0")).strip(),
            str(cfg.get("trend_filter", True)).strip(),
        )

    def apply_live_settings_and_rescan(self, profile_key: str = None, source_label: str = "Menu Cài Đặt", force_rescan: bool = False):
        """
        Đồng bộ ngay lập tức toàn bộ thông số Cài đặt trên giao diện vào bộ nhớ điều hành của Bot,
        cập nhật biểu đồ/vùng canh giá và CHỈ gửi Request cho AI phân tích lại nếu Khung Phân Tích AI hoặc thông số AI thực sự thay đổi.
        """
        pk = profile_key or self.active_profile_key
        self._sync_self_to_active_ctx(include_ai_state=False)
        cfg = self._get_profile_cfg(pk)
        ctx = self.profile_ctx.get(pk, {})

        # Đồng bộ khung xem biểu đồ nếu người dùng đổi Khung phân tích hoặc Khung vào lệnh
        scalp_tf = str(cfg.get("tf", "M5")).strip() or "M5"
        ana_tf = str(cfg.get("analysis_tf", scalp_tf)).strip() or scalp_tf
        if hasattr(self, "chart_view_tf_by_profile"):
            cur_view = self.chart_view_tf_by_profile.get(pk)
            if cur_view not in (scalp_tf, ana_tf):
                self.chart_view_tf_by_profile[pk] = scalp_tf
        self._update_forecast_tab_header()
        self._update_bt_tf_display()

        self._last_drawn_chart_key = None
        self._last_drawn_pos_sig = None

        new_ai_sig = self._get_ai_scan_config_sig(pk)
        prev_ai_sig = ctx.get("_last_ai_scan_config_sig")
        need_ai_rescan = force_rescan or (prev_ai_sig != new_ai_sig) or (ctx.get("cached_mean_df") is None)

        if need_ai_rescan:
            ctx["_last_ai_scan_config_sig"] = new_ai_sig
            ctx["last_ana_candle_time"] = None
            if not ctx.get("is_analyzing", False):
                self.enqueue_ai_scan(profile_key=pk, reason="AUTO_CANDLE")
            self.log(
                f"⚡ [ÁP DỤNG CÀI ĐẶT - {pk}] Đã nạp cấu hình mới từ {source_label} "
                f"(Phân tích AI: {ana_tf} -> Vào lệnh: {scalp_tf} | Lot: {cfg.get('lot')} | Max SL: {cfg.get('max_sl_price')}$ | RR: {cfg.get('rr')}R) -> Đồng bộ theo khung {ana_tf}!"
            )
        else:
            self._refresh_realtime_chart(force_full_redraw=True)
            if source_label != "Đóng Menu Cài Đặt":
                self.log(
                    f"✓ [CÀI ĐẶT TRỰC TIẾP - {pk}] Đã áp dụng ngay ({source_label}): "
                    f"Phân tích AI [{ana_tf}] -> Vào lệnh [{scalp_tf}] | Lot: {cfg.get('lot')} | Max SL: {cfg.get('max_sl_price')}$ | RR: {cfg.get('rr')}R (Giữ nguyên chu kỳ quét tự động [{ana_tf}])."
                )

    def manual_save_user_settings(self):
        """Người dùng nhấn nút Lưu cài đặt: Ghi ra file, áp dụng ngay vào luồng Live + gọi lại Backend AI mà không cần restart app."""
        # Có ô nhập sai / mâu thuẫn (tô đỏ): không lưu & áp dụng, yêu cầu nhập lại (chỉ giao diện)
        try:
            errs = self._validate_menu_settings()
        except Exception:
            errs = []
        if errs:
            if hasattr(self, "save_status_lbl"):
                self.save_status_lbl.config(text=f"⛔ Chưa lưu: {len(errs)} ô nhập sai (tô đỏ)", fg="#ef4444")
            self.log(f"[CÀI ĐẶT] ⛔ Chưa lưu: {len(errs)} thông số chưa hợp lệ -> " + " | ".join(errs[:5]))
            try:
                messagebox.showwarning(
                    "Thông số chưa hợp lệ",
                    "Chưa lưu cài đặt. Vui lòng nhập lại các ô tô đỏ:\n\n" + "\n".join(f"• {m}" for m in errs[:8])
                    + (f"\n• ... và {len(errs) - 8} lỗi khác" if len(errs) > 8 else ""),
                    parent=self.root,
                )
            except Exception:
                pass
            return
        saved_ok = self.save_user_settings(show_feedback=True)
        now_str = datetime.now().strftime("%H:%M:%S")
        if saved_ok:
            self.apply_live_settings_and_rescan(profile_key=self.active_profile_key, source_label="Nút Lưu Cài Đặt")
            self.show_toast(f"Đã lưu & Áp dụng ngay cài đặt [{self.active_profile_key}]!", is_error=False, duration_ms=2200)
        else:
            self.log(f"[CÀI ĐẶT] ⚠️ Không thể lưu cài đặt ({now_str}).")
            self.show_toast("Không thể lưu file cài đặt!", is_error=True, duration_ms=3000)

    # Thông số riêng từng máy: KHÔNG chép khi Xuất / Nhập file cấu hình (người dùng yêu cầu 19/11, tránh đè dữ liệu máy khác):
    # Magic, đường dẫn MT5, số luồng AI (Mục 1) và toàn bộ Mục 5 Telegram (mỗi máy giữ bot / nhóm Telegram riêng)
    MACHINE_LOCAL_KEYS = ("magic_number", "mt5_path", "ai_threads",
                          "telegram_enabled", "telegram_bot_token", "telegram_chat_id", "telegram_admin_ids", "telegram_notify_open", "telegram_notify_close")
    # Trạng thái riêng từng máy (tab đang mở, chiều cao bảng dưới, bot đang chạy tự động để bật lại sau cập nhật): cũng không chép
    MACHINE_LOCAL_STATE_KEYS = ("last_active_tab", "bottom_panel_height", "auto_running_profiles")
    # Cấu hình mặc định do người dùng đặt (nút "Lưu cấu hình mặc định"): file riêng, KHÔNG gồm khóa riêng máy và token
    USER_DEFAULTS_FILE_NAME = "user_defaults.json"
    USER_DEFAULTS_EXCLUDED = frozenset(MACHINE_LOCAL_KEYS) | {"hf_token"}

    @classmethod
    def _strip_machine_local(cls, data: dict) -> dict:
        """Bản sao cấu hình đã bỏ thông số / trạng thái riêng từng máy (ở cấp ngoài và trong từng profiles)."""
        drop = set(cls.MACHINE_LOCAL_KEYS) | set(cls.MACHINE_LOCAL_STATE_KEYS)
        out = {k: v for k, v in data.items() if k not in drop}
        profs = data.get("profiles")
        if isinstance(profs, dict):
            out["profiles"] = {pk: ({k: v for k, v in p.items() if k not in cls.MACHINE_LOCAL_KEYS} if isinstance(p, dict) else p)
                               for pk, p in profs.items()}
        return out

    @classmethod
    def _merge_machine_local(cls, imported: dict, local: dict) -> dict:
        """Cấu hình nhập từ file, nhưng thông số / trạng thái riêng máy lấy theo máy này (local = cài đặt đang lưu của máy này).
        Máy này chưa có giá trị thì bỏ khóa (nạp sẽ dùng mặc định), không bao giờ lấy giá trị của máy khác."""
        out = cls._strip_machine_local(imported)
        local = local if isinstance(local, dict) else {}
        for k in cls.MACHINE_LOCAL_KEYS + cls.MACHINE_LOCAL_STATE_KEYS:
            if k in local:
                out[k] = local[k]
        lprofs = local.get("profiles") if isinstance(local.get("profiles"), dict) else {}
        oprofs = out.get("profiles") if isinstance(out.get("profiles"), dict) else {}
        for pk, p in oprofs.items():
            lp = lprofs.get(pk)
            if isinstance(p, dict) and isinstance(lp, dict):
                for k in cls.MACHINE_LOCAL_KEYS:
                    if k in lp:
                        p[k] = lp[k]
        return out

    def export_settings_file(self):
        """Xuất toàn bộ cấu hình 2 Tab ra file .json để sao lưu hoặc đồng bộ sang máy khác/VPS."""
        try:
            self.save_user_settings(show_feedback=False)
            default_name = f"kronos_settings_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            file_path = filedialog.asksaveasfilename(
                parent=self.root,
                title="Xuất File Cấu Hình",
                defaultextension=".json",
                initialfile=default_name,
                filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")]
            )
            if not file_path:
                return
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = {
                    "last_active_tab": self.active_profile_key,
                    "profiles": dict(self.profile_settings)
                }
            data = self._strip_machine_local(data)  # không xuất Magic / đường dẫn MT5 / số luồng AI / trạng thái riêng máy
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            self.log(f"[CẤU HÌNH] Đã xuất file cài đặt thành công: {os.path.basename(file_path)}")
            self.show_toast(f"Đã xuất cấu hình: {os.path.basename(file_path)}", is_error=False, duration_ms=2500)
        except Exception as e:
            self.log_error("EXPORT_SETTINGS", e)
            self.show_toast(f"Lỗi xuất file: {e}", is_error=True, duration_ms=3000)

    def import_settings_file(self):
        """Nhập file .json từ máy khác vào, cập nhật trực tiếp lên giao diện và lưu vào máy."""
        try:
            file_path = filedialog.askopenfilename(
                parent=self.root,
                title="Chọn File Cấu Hình Cần Nhập",
                filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")]
            )
            if not file_path:
                return

            with open(file_path, "r", encoding="utf-8") as f:
                imported_data = json.load(f)

            if not isinstance(imported_data, dict):
                raise ValueError("Định dạng file không hợp lệ (không phải đối tượng JSON).")

            # Giữ Magic / đường dẫn MT5 / số luồng AI / trạng thái riêng của MÁY NÀY (file từ máy khác không được đè)
            self.save_user_settings(show_feedback=False)
            local_data = {}
            if os.path.exists(SETTINGS_FILE):
                try:
                    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                        local_data = json.load(f)
                except Exception:
                    local_data = {}
            imported_data = self._merge_machine_local(imported_data, local_data)

            os.makedirs(SETTINGS_DIR, exist_ok=True)
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(imported_data, f, ensure_ascii=False, indent=2)

            self.load_user_settings()
            self.apply_live_settings_and_rescan(profile_key=self.active_profile_key, source_label="Nhập File Cấu Hình")
            now_str = datetime.now().strftime("%H:%M:%S")
            if hasattr(self, "save_status_lbl"):
                self.save_status_lbl.config(
                    text=f"✓ Đã nạp từ file lúc {now_str}",
                    fg="#10b981"
                )
            self.log(f"[CẤU HÌNH] Đã nhập và áp dụng thành công cấu hình từ: {os.path.basename(file_path)}")
            self.show_toast("Đã nhập & Áp dụng cấu hình thành công!", is_error=False, duration_ms=2500)
        except Exception as e:
            self.log_error("IMPORT_SETTINGS", e)
            self.show_toast(f"Lỗi nhập file: {e}", is_error=True, duration_ms=3000)

    def save_user_settings(self, *args, show_feedback: bool = False) -> bool:
        """Lưu toàn bộ cài đặt của cả 2 Tab (XAUUSD & BTCUSD) và ghi nhớ Tab đang mở (last_active_tab)."""
        if self._loading_settings:
            return False
        try:
            os.makedirs(SETTINGS_DIR, exist_ok=True)
            cur_data = {k: v.get() for k, v in self._get_settings_vars_map().items()}
            self.profile_settings[self.active_profile_key] = dict(cur_data)

            # Lấy HF Token và Space ID từ UI (nếu người dùng xóa thì lưu rỗng, không tự ý khôi phục token cũ)
            token_val = str(self.hf_token_var.get()).strip()
            space_val = str(self.hf_space_var.get()).strip()

            # Đồng bộ các thông tin xác thực & kết nối chung (HF Token, HF Space) cho cả 2 tab
            for pk in ("XAUUSD", "BTCUSD"):
                if pk in self.profile_settings:
                    self.profile_settings[pk]["hf_token"] = token_val
                    self.profile_settings[pk]["hf_space"] = space_val
                    self.profile_settings[pk]["mt5_path"] = str(self.mt5_path_var.get()).strip()
                    self.profile_settings[pk]["ai_threads"] = str(self.ai_threads_var.get()).strip() or "Auto"

            payload = dict(self.profile_settings.get(self.active_profile_key, cur_data))
            payload["hf_token"] = token_val
            payload["hf_space"] = space_val
            payload["last_active_tab"] = self.active_profile_key
            payload["bottom_panel_height"] = int(getattr(self, "_saved_bottom_panel_h", 240))
            payload["auto_running_profiles"] = [
                pk for pk in ("XAUUSD", "BTCUSD")
                if self.profile_ctx.get(pk, {}).get("auto_loop_running", False)
                or (pk == self.active_profile_key and getattr(self, "auto_loop_running", False))
            ]
            payload["profiles"] = {
                "XAUUSD": dict(self.profile_settings["XAUUSD"]),
                "BTCUSD": dict(self.profile_settings["BTCUSD"]),
            }
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

            now_str = datetime.now().strftime("%H:%M:%S")
            if hasattr(self, "save_status_lbl"):
                self.save_status_lbl.config(
                    text=f"✓ Đã lưu ({self.active_profile_key}) lúc {now_str}",
                    fg="#10b981"
                )
            return True
        except Exception as e:
            now_str = datetime.now().strftime("%H:%M:%S")
            if hasattr(self, "save_status_lbl"):
                self.save_status_lbl.config(
                    text=f"⚠️ Lỗi lưu file: {e} ({now_str})",
                    fg="#ef4444"
                )
            return False

    def load_user_settings(self):
        if not os.path.exists(SETTINGS_FILE):
            self.active_profile_key = "XAUUSD"
            self._sync_active_ctx_to_self()
            return
        try:
            self._loading_settings = True
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data.get("profiles"), dict):
                for pk in ("XAUUSD", "BTCUSD"):
                    saved_p = data["profiles"].get(pk, {})
                    if isinstance(saved_p, dict):
                        merged = dict(DEFAULT_PROFILE_SETTINGS[pk])
                        for k, val in saved_p.items():
                            if k in merged:
                                if k == "model" and isinstance(val, str) and val.startswith("Kronos-"):
                                    val = val.replace("Kronos-", "AI-", 1)
                                elif k == "engine" and isinstance(val, str) and "trực tiếp" in val:
                                    val = "Chạy trực tiếp máy Local"
                                merged[k] = val
                        self.profile_settings[pk] = merged
            else:
                # Tự động chuyển đổi (Migration) từ file cài đặt đơn cũ sang 2 hồ sơ độc lập XAUUSD & BTCUSD
                shared_keys = (
                    "lot", "max_pos", "scenarios", "consensus", "max_dispersion", "sl_atr", "rr",
                    "auto_be", "be_trigger_r", "auto_trail", "trail_dist_r",
                    "auto_rev_close", "rev_consensus", "rev_be_consensus", "rev_min_move", "be_plus_price",
                    "daily_tp_usd", "daily_sl_usd", "max_consec_loss", "cooldown_mins", "fast_sl_cooldown_candles",
                    "engine", "model", "hf_space", "hf_token", "lookback", "pred_len", "realtime_chart",
                    "telegram_enabled", "telegram_bot_token", "telegram_chat_id", "telegram_admin_ids", "telegram_notify_open", "telegram_notify_close"
                )
                for pk in ("XAUUSD", "BTCUSD"):
                    for sk in shared_keys:
                        if sk in data:
                            val = data[sk]
                            if sk == "model" and isinstance(val, str) and val.startswith("Kronos-"):
                                val = val.replace("Kronos-", "AI-", 1)
                            elif sk == "engine" and isinstance(val, str) and "trực tiếp" in val:
                                val = "Chạy trực tiếp máy Local"
                            self.profile_settings[pk][sk] = val

                sym_old = str(data.get("symbol", "XAUUSDm")).strip()
                sym_specific_keys = ("symbol", "analysis_tf", "tf", "min_move", "max_spread", "sl_buffer", "max_sl_price", "be_plus_price")
                if "BTC" in sym_old.upper():
                    for k in sym_specific_keys:
                        if k in data:
                            self.profile_settings["BTCUSD"][k] = data[k]
                else:
                    for k in sym_specific_keys:
                        if k in data:
                            self.profile_settings["XAUUSD"][k] = data[k]
                    # Nếu trước đó người dùng thử nghiệm thông số SL tối đa của BTC (>= 50 giá) trên mã Vàng -> Tách riêng cho BTCUSD và trả Vàng về 5.0 giá
                    try:
                        if float(self.profile_settings["XAUUSD"].get("max_sl_price", 5.0)) >= 50.0:
                            self.profile_settings["BTCUSD"]["max_sl_price"] = str(self.profile_settings["XAUUSD"]["max_sl_price"])
                            self.profile_settings["XAUUSD"]["max_sl_price"] = "5.0"
                    except Exception:
                        pass

            # Tự động chuyển đổi thông số Nghỉ SL nhanh từ phút cũ sang đơn vị số nến AI
            for pk in ("XAUUSD", "BTCUSD"):
                if pk in self.profile_settings:
                    pcfg = self.profile_settings[pk]
                    if "fast_sl_cooldown_candles" not in pcfg:
                        old_val = pcfg.get("fast_sl_cooldown_mins")
                        if old_val is not None:
                            try:
                                old_m = float(old_val)
                                ana_tf = pcfg.get("analysis_tf", "M15")
                                tf_m = 15 if ana_tf == "M15" else (1 if ana_tf == "M1" else 5)
                                pcfg["fast_sl_cooldown_candles"] = str(max(1, int(round(old_m / tf_m)))) if old_m > 0 else "0"
                            except Exception:
                                pcfg["fast_sl_cooldown_candles"] = "1"
                        else:
                            pcfg["fast_sl_cooldown_candles"] = "1"
                    # Lọc cấu trúc BOS/CHoCH đã ẩn khỏi giao diện -> luôn TẮT (cấu hình cũ có thể đang lưu True)
                    pcfg["trend_filter"] = False

            # Trích xuất và đồng bộ HF Token cho cả 2 Tab nếu có lưu
            token_saved = ""
            for pk in ("XAUUSD", "BTCUSD"):
                t = str(self.profile_settings.get(pk, {}).get("hf_token", "")).strip()
                if t:
                    token_saved = t
                    break
            if not token_saved and isinstance(data, dict):
                token_saved = str(data.get("hf_token", "")).strip()

            if token_saved:
                for pk in ("XAUUSD", "BTCUSD"):
                    if pk in self.profile_settings:
                        self.profile_settings[pk]["hf_token"] = token_saved

            # Trích xuất và đồng bộ Telegram Token, Chat ID & trạng thái cho cả 2 Tab nếu có lưu
            tg_token_saved = ""
            tg_chat_id_saved = ""
            tg_enabled_saved = False
            for pk in ("XAUUSD", "BTCUSD"):
                t_tok = str(self.profile_settings.get(pk, {}).get("telegram_bot_token", "")).strip()
                t_cid = str(self.profile_settings.get(pk, {}).get("telegram_chat_id", "")).strip()
                t_en = bool(self.profile_settings.get(pk, {}).get("telegram_enabled", False))
                if t_tok:
                    tg_token_saved = t_tok
                if t_cid:
                    tg_chat_id_saved = t_cid
                if t_en:
                    tg_enabled_saved = True
            if tg_token_saved or tg_chat_id_saved:
                for pk in ("XAUUSD", "BTCUSD"):
                    if pk in self.profile_settings:
                        if tg_token_saved:
                            self.profile_settings[pk]["telegram_bot_token"] = tg_token_saved
                        if tg_chat_id_saved:
                            self.profile_settings[pk]["telegram_chat_id"] = tg_chat_id_saved
                        self.profile_settings[pk]["telegram_enabled"] = tg_enabled_saved

            # Khôi phục chính xác Tab người dùng đang mở trước khi tắt App (XAUUSD hoặc BTCUSD)
            last_tab = data.get("last_active_tab", "XAUUSD") if isinstance(data, dict) else "XAUUSD"
            if last_tab not in ("XAUUSD", "BTCUSD"):
                last_tab = "XAUUSD"
            self.active_profile_key = last_tab
            self._sync_active_ctx_to_self()

            if hasattr(self, "chart_view_tf_by_profile"):
                for pk in ("XAUUSD", "BTCUSD"):
                    self.chart_view_tf_by_profile[pk] = str(self.profile_settings.get(pk, {}).get("tf", "M5")).strip()
            if hasattr(self, "tf_quick_btns"):
                self._highlight_active_tf_btn(self.chart_view_tf_by_profile.get(last_tab, "M5"))

            if hasattr(self, "bottom_notebook"):
                try:
                    self.bottom_notebook.select(1 if last_tab == "BTCUSD" else 0)
                except Exception:
                    pass

            if isinstance(data, dict) and "bottom_panel_height" in data:
                try:
                    self._saved_bottom_panel_h = int(data["bottom_panel_height"])
                except Exception:
                    self._saved_bottom_panel_h = 240

            if ("--after-update" in sys.argv) and isinstance(data, dict) and isinstance(data.get("auto_running_profiles"), list):
                self._pending_restore_running_profiles = [
                    pk for pk in data["auto_running_profiles"] if pk in ("XAUUSD", "BTCUSD")
                ]

            self.log(f"Đã khôi phục hồ sơ cài đặt riêng cho Tab {last_tab} (XAUUSD & BTCUSD độc lập).")
            if token_saved:
                self.log(f"🔑 Hugging Face Token đã sẵn sàng ({token_saved[:5]}...{token_saved[-4:]}).")
        except Exception as e:
            self.log(f"⚠️ Lỗi đọc file cài đặt user_settings.json: {e}")
        finally:
            self._loading_settings = False
            self.save_user_settings()

    # Danh sách khóa cấu hình theo từng Mục của menu (nút Khôi phục mặc định + bảng thông số Backtest dùng chung)
    SETTINGS_SECTION_MAP = {
        1: ("Mục 1: Cài đặt cơ bản", ["symbol", "analysis_tf", "tf", "lot", "max_pos", "magic_number", "mt5_path", "ai_threads"]),
        2: ("Mục 2: Bộ lọc Entry & Nhồi lệnh", ["scenarios", "consensus", "max_dispersion", "min_move", "expected_move_k", "max_spread", "sl_atr", "sl_buffer", "max_sl_price", "rr", "min_entry_spacing", "entry_mode", "allow_opposite_entry", "one_order_per_candle", "trend_filter", "confirm_entry_pa", "pa_fail_open", "pa_min_confirmations", "structure_conflict_mode", "use_confidence_gate", "blocked_trading_windows", "structure_direction"]),
        3: ("Mục 3: Quản lý lệnh & Chốt lời", ["auto_be", "be_trigger_r", "auto_trail", "trail_dist_r", "auto_rev_close", "rev_consensus", "rev_be_consensus", "rev_min_move", "be_plus_price", "rev_loss_mode", "basket_tp_usd", "basket_cooldown_mins", "max_hold_mins", "max_hold_action", "daily_tp_usd", "daily_sl_usd", "max_consec_loss", "cooldown_mins", "fast_sl_cooldown_candles", "fast_sl_same_dir", "recovery_close", "stack_half_be"]),
        4: ("Mục 4: Phân tích AI & Mô hình", ["engine", "model", "hf_space", "hf_token", "lookback", "pred_len", "debug_trace"]),
        5: ("Mục 5: Thông báo & Điều khiển Telegram", ["telegram_enabled", "telegram_bot_token", "telegram_chat_id", "telegram_admin_ids", "telegram_notify_open", "telegram_notify_close"]),
    }

    def _get_effective_defaults(self, pk: str) -> dict:
        """Mặc định đang dùng cho nút 'Khôi phục mặc định': mặc định gốc trong code, được ghi đè bởi cấu hình mặc định
        người dùng đã lưu (user_defaults.json, nút 'Lưu cấu hình mặc định'). Lỗi đọc file -> dùng mặc định gốc."""
        base = dict(DEFAULT_PROFILE_SETTINGS.get(pk, DEFAULT_PROFILE_SETTINGS["XAUUSD"]))
        try:
            with open(os.path.join(SETTINGS_DIR, self.USER_DEFAULTS_FILE_NAME), "r", encoding="utf-8") as f:
                data = json.load(f)
            ud = data.get(pk) if isinstance(data, dict) else None
            if isinstance(ud, dict):
                for k, v in ud.items():
                    if k in base and k not in self.USER_DEFAULTS_EXCLUDED:
                        base[k] = v
        except Exception:
            pass
        return base

    def save_current_as_defaults(self):
        """Nút 'Lưu cấu hình mặc định': lấy thông số ĐANG HIỂN THỊ của tab hiện tại làm mặc định mới
        (các nút 'Khôi phục mặc định' sẽ trả về cấu hình này thay vì giá trị cứng trong code). Không đổi bot đang chạy."""
        if getattr(self, "_params_locked", False):
            self.show_toast("Đang khóa: bấm ổ khóa để mở trước khi lưu mặc định", is_error=True, duration_ms=2500)
            return
        try:
            errs = self._validate_menu_settings()
        except Exception:
            errs = []
        if errs:
            self.show_toast(f"Chưa lưu mặc định: còn {len(errs)} ô nhập sai (tô đỏ)", is_error=True, duration_ms=3000)
            return
        pk = self.active_profile_key
        try:
            if not messagebox.askyesno(
                "Lưu cấu hình mặc định",
                f"Lấy thông số hiện tại của tab {pk} làm CẤU HÌNH MẶC ĐỊNH?\n\n"
                "Các nút 'Khôi phục mặc định' sẽ trả về cấu hình này (không gồm Magic, đường dẫn MT5, số luồng AI, token và Telegram).",
                parent=self.root,
            ):
                return
        except Exception:
            return
        smap = self._get_settings_vars_map()
        cur = {}
        for k in DEFAULT_PROFILE_SETTINGS.get(pk, DEFAULT_PROFILE_SETTINGS["XAUUSD"]):
            if k in smap and k not in self.USER_DEFAULTS_EXCLUDED:
                try:
                    cur[k] = smap[k].get()
                except Exception:
                    pass
        path = os.path.join(SETTINGS_DIR, self.USER_DEFAULTS_FILE_NAME)
        try:
            os.makedirs(SETTINGS_DIR, exist_ok=True)
            data = {}
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    data = {}
            except Exception:
                data = {}
            data[pk] = cur
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as e:
            self.log(f"[CÀI ĐẶT] ⚠️ Không lưu được cấu hình mặc định: {e}")
            self.show_toast("Không thể lưu cấu hình mặc định!", is_error=True, duration_ms=3000)
            return
        self.log(f"[CÀI ĐẶT] Đã lưu thông số hiện tại làm cấu hình mặc định cho tab {pk} ({len(cur)} thông số).")
        self.show_toast(f"Đã lưu cấu hình mặc định [{pk}]", is_error=False, duration_ms=2200)

    def toggle_params_lock(self):
        """Ổ khóa ở tiêu đề menu: khóa -> thông số mờ, không sửa được; mở khóa -> sửa như bình thường (chỉ giao diện)."""
        self._params_locked = not getattr(self, "_params_locked", True)
        self._apply_params_lock()
        try:
            self.show_toast("Đã MỞ KHÓA: có thể chỉnh thông số" if not self._params_locked else "Đã KHÓA thông số",
                            is_error=False, duration_ms=1500)
        except Exception:
            pass

    def _apply_params_lock(self):
        """Khóa/mở khóa mọi ô thông số của menu (làm mờ khi khóa). Giá trị không đổi; đặt giá trị bằng code vẫn hoạt động."""
        locked = bool(getattr(self, "_params_locked", False))
        dim = "#5b6475"

        def _labels(w):
            out = []
            for c in w.winfo_children():
                if isinstance(c, tk.Label):
                    out.append(c)
                out.extend(_labels(c))
            return out

        seen = set()
        for _k, pair in list(self.__dict__.get("_param_rows", {}).items()):
            try:
                row, w = pair
                if id(w) in seen:
                    continue
                seen.add(id(w))
                if isinstance(w, ttk.Combobox):
                    w.config(state=("disabled" if locked else "readonly"))
                elif isinstance(w, tk.Entry):
                    w.config(state=(tk.DISABLED if locked else tk.NORMAL),
                             disabledbackground="#1a1f2b", disabledforeground="#8b95a8")
                else:
                    # Ô tích: chữ giữ màu thường để vẫn đọc được, chỉ không bấm được
                    w.config(state=(tk.DISABLED if locked else tk.NORMAL), disabledforeground=str(w.cget("fg")))
            except Exception:
                pass
        # Nút khôi phục mặc định + nút Lưu cấu hình mặc định: khóa theo ổ khóa
        try:
            btns = [getattr(self, "save_defaults_btn", None)]

            def _find_btns(w):
                for c in w.winfo_children():
                    if isinstance(c, tk.Button) and "Khôi phục" in str(c.cget("text")):
                        btns.append(c)
                    _find_btns(c)

            for sec in getattr(self, "accordion_sections", []):
                _find_btns(sec.content)
            for bt in btns:
                if bt is not None:
                    bt.config(state=(tk.DISABLED if locked else tk.NORMAL), disabledforeground=dim)
        except Exception:
            pass
        # Thông số con: mở khóa xong phải trả về đúng trạng thái theo cha (cha tắt -> con vẫn mờ)
        for fn in list(self.__dict__.get("_param_syncs", [])):
            try:
                fn()
            except Exception:
                pass
        try:
            self.params_lock_btn.config(text=("🔒" if locked else "🔓"), fg=(GOLD_ACCENT if locked else "#22c55e"))
        except Exception:
            pass

    def reset_section_settings(self, section_num: int):
        """Khôi phục các thông số của một mục cụ thể về giá trị mặc định tối ưu theo Tab đang chọn."""
        if getattr(self, "_params_locked", False):
            self.show_toast("Đang khóa: bấm ổ khóa để mở trước khi khôi phục mặc định", is_error=True, duration_ms=2500)
            return
        section_map = self.SETTINGS_SECTION_MAP
        if section_num not in section_map:
            return

        sec_title, keys = section_map[section_num]
        pk = self.active_profile_key
        defaults = self._get_effective_defaults(pk)
        smap = self._get_settings_vars_map()
        cur_token = str(self.hf_token_var.get()).strip()
        cur_tg_token = str(getattr(self, "telegram_bot_token_var", tk.StringVar()).get()).strip()
        cur_tg_chat = str(getattr(self, "telegram_chat_id_var", tk.StringVar()).get()).strip()

        self._loading_settings = True
        try:
            for k in keys:
                if k == "hf_token" and cur_token:
                    continue  # Không xóa token bảo mật của người dùng khi reset
                if k == "telegram_bot_token" and cur_tg_token:
                    continue  # Không xóa Telegram bot token khi reset
                if k == "telegram_chat_id" and cur_tg_chat:
                    continue  # Không xóa Telegram chat ID khi reset
                if k == "telegram_admin_ids" and str(getattr(self, "telegram_admin_ids_var", tk.StringVar()).get()).strip():
                    continue  # Không xóa ID người điều khiển khi reset
                if k in smap and k in defaults:
                    smap[k].set(defaults[k])
                    if pk in self.profile_settings:
                        self.profile_settings[pk][k] = defaults[k]
        finally:
            self._loading_settings = False

        self.save_user_settings()
        self.apply_live_settings_and_rescan(profile_key=pk, source_label=f"Khôi phục {sec_title}")
        self.log(f"[CÀI ĐẶT] Đã khôi phục thông số mặc định tối ưu cho {sec_title} ({pk}).")

    def reset_all_settings(self):
        """Khôi phục toàn bộ thông số của Tab đang chọn về cấu hình mặc định tối ưu."""
        pk = self.active_profile_key
        if getattr(self, "_params_locked", False):
            self.show_toast("Đang khóa: bấm ổ khóa để mở trước khi khôi phục mặc định", is_error=True, duration_ms=2500)
            return
        if not messagebox.askyesno(
            "Xác nhận khôi phục",
            f"Bạn có chắc chắn muốn khôi phục TẤT CẢ thông số cài đặt của tab {pk} về giá trị mặc định tối ưu?"
        ):
            return

        defaults = self._get_effective_defaults(pk)
        smap = self._get_settings_vars_map()
        cur_token = str(self.hf_token_var.get()).strip()
        cur_tg_token = str(getattr(self, "telegram_bot_token_var", tk.StringVar()).get()).strip()
        cur_tg_chat = str(getattr(self, "telegram_chat_id_var", tk.StringVar()).get()).strip()

        self._loading_settings = True
        try:
            for k, val in defaults.items():
                if k == "hf_token" and cur_token:
                    continue  # Không xóa token bảo mật cá nhân khi reset thông số giao dịch
                if k == "telegram_bot_token" and cur_tg_token:
                    continue  # Không xóa Telegram bot token khi reset
                if k == "telegram_chat_id" and cur_tg_chat:
                    continue  # Không xóa Telegram chat ID khi reset
                if k == "telegram_admin_ids" and str(getattr(self, "telegram_admin_ids_var", tk.StringVar()).get()).strip():
                    continue  # Không xóa ID người điều khiển khi reset
                if k in smap:
                    smap[k].set(val)
                    if pk in self.profile_settings:
                        self.profile_settings[pk][k] = val
        finally:
            self._loading_settings = False

        self.save_user_settings()
        self.apply_live_settings_and_rescan(profile_key=pk, source_label="Khôi phục toàn bộ mặc định")
        self.log(f"[CÀI ĐẶT] Đã khôi phục TOÀN BỘ thông số mặc định tối ưu cho tab {pk}.")

    # =========================================================================
    # ── HỆ THỐNG THÔNG BÁO & ĐIỀU KHIỂN TELEGRAM TỪ XA QUA ĐIỆN THOẠI ────────
    # =========================================================================
    def _telegram_api_call(self, token: str, method: str, data: dict = None, timeout: int = 15) -> Optional[dict]:
        """Gọi Telegram Bot API trực tiếp qua urllib chuẩn, 0 phụ thuộc thư viện ngoài, tối ưu VPS."""
        clean_token = (token or "").strip()
        if not clean_token:
            return None
        url = f"https://api.telegram.org/bot{clean_token}/{method}"
        headers = {
            "User-Agent": "Trading-AI-PRO-Bot",
            "Content-Type": "application/json",
        }
        # Tin Telegram chỉ chữ thuần (người dùng yêu cầu 19/11/2026): bỏ icon ở chữ tin + nút bấm, giữ chấm 🟢/🔴
        try:
            data = self._tg_strip_icons_payload(data) if data else data
        except Exception:
            pass
        req_body = json.dumps(data).encode("utf-8") if data else None
        req = urllib.request.Request(url, data=req_body, headers=headers)
        ctx = None
        try:
            import ssl
            ctx = ssl._create_unverified_context()
        except Exception:
            pass
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return None

    # Icon/emoji bị bỏ khỏi tin Telegram; giữ lại 🟢 (U+1F7E2) và 🔴 (U+1F534). Không đụng →, ≤, ≥, •, ·, ─.
    _TG_ICON_RE = re.compile(
        "(?:(?![\U0001F7E2\U0001F534])[\U0001F000-\U0001FAFF☀-➿⬀-⯿⌚⌛⌨⏏"
        "⏩-⏺▪▫▶◀◻-◾ℹ〰〽㊗㊙️‍⃣])+ ?"
    )

    @classmethod
    def _tg_plain_text(cls, text):
        """Bỏ icon khỏi chữ gửi Telegram (giữ 🟢/🔴). Nếu bỏ xong rỗng thì trả nguyên văn."""
        if not isinstance(text, str) or not text:
            return text
        out = cls._TG_ICON_RE.sub("", text)
        out = "\n".join(ln.rstrip() for ln in out.split("\n")).strip()
        return out or text

    def _tg_strip_icons_payload(self, data: dict) -> dict:
        """Bản sao payload Telegram với 'text'/'caption' và chữ trên nút inline đã bỏ icon."""
        if not isinstance(data, dict):
            return data
        out = dict(data)
        for k in ("text", "caption"):
            if isinstance(out.get(k), str):
                out[k] = self._tg_plain_text(out[k])
        rm = out.get("reply_markup")
        if isinstance(rm, dict) and isinstance(rm.get("inline_keyboard"), list):
            rm = dict(rm)
            rm["inline_keyboard"] = [
                [dict(b, text=self._tg_plain_text(b.get("text"))) if isinstance(b, dict) else b for b in row]
                if isinstance(row, list) else row
                for row in rm["inline_keyboard"]
            ]
            out["reply_markup"] = rm
        return out

    def _tg_admin_id_list(self) -> list:
        """ID tài khoản Telegram cá nhân được BẬT/TẮT bot (ô 'ID người điều khiển', cách nhau dấu phẩy/khoảng trắng), giữ thứ tự."""
        try:
            raw = str(getattr(self, "telegram_admin_ids_var", None).get() if getattr(self, "telegram_admin_ids_var", None) else "")
        except Exception:
            raw = ""
        out = []
        for x in re.split(r"[\s,;]+", raw.strip()):
            if re.fullmatch(r"-?\d+", x or "") and x not in out:
                out.append(x)
        return out

    def _tg_admin_ids(self) -> set:
        return set(self._tg_admin_id_list())

    def _tg_target_chat_id(self) -> str:
        """Nơi bot gửi tin: 'ID nhóm chat' nếu có; để trống thì gửi riêng cho ID người điều khiển ĐẦU TIÊN; không có cả hai -> ''."""
        try:
            cid = str(getattr(self, "telegram_chat_id_var", None).get() if getattr(self, "telegram_chat_id_var", None) else "").strip()
        except Exception:
            cid = ""
        if cid:
            return cid
        admins = self._tg_admin_id_list()
        return admins[0] if admins else ""

    def _tg_sender_allowed(self, chat_id: str, from_id: str, msg_chat: str) -> bool:
        """Nhận tin/nút: từ đúng chat đã cài (riêng hoặc nhóm), hoặc chat riêng của người điều khiển."""
        if msg_chat and msg_chat == chat_id:
            return True
        return bool(from_id) and msg_chat == from_id and from_id in self._tg_admin_ids()

    def _tg_can_control(self, chat_id: str, from_id: str) -> bool:
        """Quyền BẬT/TẮT bot (người dùng yêu cầu 19/11/2026: chỉ chủ bot):
        - Có điền 'ID người điều khiển': chỉ các ID đó.
        - Chưa điền: Chat ID là chat riêng (số dương) thì như cũ (chính chủ chat đó); Chat ID là nhóm (số âm) thì KHÔNG ai."""
        admins = self._tg_admin_ids()
        if admins:
            return bool(from_id) and from_id in admins
        return not str(chat_id).startswith("-") and (not from_id or from_id == chat_id)

    def _tg_deny_control_text(self, chat_id: str) -> str:
        if self._tg_admin_ids():
            return "Chỉ chủ bot được bật/tắt bot giao dịch."
        return "Chưa điền 'ID người điều khiển' (Mục 5) nên trong nhóm không ai được bật/tắt bot."

    def _tg_capture_chart_png(self, timeout: float = 10.0):
        """Chụp biểu đồ chính (PNG bytes) để gửi Telegram cùng phân tích AI.
        Luôn chụp khung SCALP (Khung vào lệnh, vd M1: vùng canh, SL/TP AI, lệnh đang mở; nến dự báo AI chỉ có ở khung phân tích).
        Màn hình đang xem khung khác thì vẽ TẠM khung scalp để chụp rồi trả lại khung + thu phóng cũ ngay trong cùng lượt.
        Gọi từ luồng nền: việc vẽ chạy trên luồng Tk chính (matplotlib không an toàn đa luồng), luồng gọi chờ tối đa timeout giây.
        Ẩn tạm đường chữ thập của chuột khi chụp. Ảnh rộng tối đa ~1280px (Telegram hiển thị tối đa cỡ này).
        Lỗi/hết giờ -> None (chỉ gửi chữ như cũ)."""
        fig = getattr(self, "fig", None)
        root = getattr(self, "root", None)
        if fig is None or root is None:
            return None
        box = {}
        done = threading.Event()

        def _grab():
            views = getattr(self, "chart_view_tf_by_profile", None)
            pk = getattr(self, "active_profile_key", None)
            cur_view = views.get(pk) if isinstance(views, dict) else None
            try:
                ana_tf = str(self.tf_var.get()).strip()  # khung SCALP (vào lệnh), người dùng chọn 19/11/2026: nhìn dễ hơn
            except Exception:
                ana_tf = ""
            switch = bool(cur_view and ana_tf and cur_view != ana_tf)
            zoom_saved = (getattr(self, "user_zoomed", False), getattr(self, "saved_xlim", None), getattr(self, "saved_ylim", None))
            try:
                import io
                if switch:
                    views[pk] = ana_tf
                    self.user_zoomed, self.saved_xlim, self.saved_ylim = False, None, None
                    self._last_drawn_chart_key = None
                    self._refresh_realtime_chart(force_full_redraw=True)
                cross = [a for a in (getattr(self, n, None) for n in
                                     ("cross_vline", "cross_hline", "cross_price_annot", "cross_time_annot")) if a is not None]
                vis = [a.get_visible() for a in cross]
                for a in cross:
                    a.set_visible(False)
                try:
                    w_in = float(fig.get_size_inches()[0]) or 9.0
                    dpi = min(max(1280.0 / w_in, float(fig.dpi)), float(fig.dpi) * 1.5)
                    buf = io.BytesIO()
                    fig.savefig(buf, format="png", dpi=dpi, facecolor=fig.get_facecolor())
                    box["png"] = buf.getvalue()
                finally:
                    for a, v in zip(cross, vis):
                        a.set_visible(v)
            except Exception as e:
                box["err"] = str(e)
            finally:
                if switch:
                    try:
                        views[pk] = cur_view
                        self.user_zoomed, self.saved_xlim, self.saved_ylim = zoom_saved
                        self._last_drawn_chart_key = None
                        self._refresh_realtime_chart(force_full_redraw=True)
                    except Exception:
                        pass
                try:
                    self._request_canvas_redraw(full=True)
                except Exception:
                    pass
                done.set()

        if threading.current_thread() is threading.main_thread():
            _grab()
        else:
            try:
                root.after(0, _grab)
            except Exception:
                return None
            if not done.wait(timeout):
                self._tg_diag_log(f"Chụp biểu đồ quá {timeout:g}s (luồng giao diện bận) -> chỉ gửi chữ")
                return None
        if box.get("err"):
            self._tg_diag_log(f"Chụp biểu đồ lỗi: {box['err']} -> chỉ gửi chữ")
        return box.get("png")

    def _tg_diag_log(self, msg: str):
        """Log chẩn đoán Telegram, tối đa 1 dòng/60 giây, không bao giờ ném lỗi."""
        try:
            now = time.monotonic()
            if now - float(getattr(self, "_tg_diag_last_ts", -1e9)) >= 60.0:
                self._tg_diag_last_ts = now
                self.log(f"⚠️ [Telegram] {msg}")
        except Exception:
            pass

    def _telegram_send_photo(self, token: str, chat_id: str, png: bytes, caption: str = "", timeout: int = 25,
                             reply_markup: dict = None) -> Optional[dict]:
        """Gửi ảnh PNG lên Telegram (sendPhoto, multipart/form-data qua urllib, không thư viện ngoài).
        reply_markup: nút bấm gắn dưới ảnh (đã bỏ icon như tin chữ)."""
        clean_token = (token or "").strip()
        if not clean_token or not chat_id or not png:
            return None
        import uuid
        boundary = "KronosTg" + uuid.uuid4().hex
        crlf = "\r\n"

        def _field(name, val):
            return (f"--{boundary}{crlf}Content-Disposition: form-data; name=\"{name}\"{crlf}{crlf}{val}{crlf}").encode("utf-8")

        body = _field("chat_id", str(chat_id))
        cap = self._tg_plain_text(caption or "")
        if cap:
            body += _field("caption", cap[:1024])
        if reply_markup:
            try:
                rm = self._tg_strip_icons_payload({"reply_markup": reply_markup}).get("reply_markup")
            except Exception:
                rm = reply_markup
            body += _field("reply_markup", json.dumps(rm, ensure_ascii=False))
        body += (f"--{boundary}{crlf}Content-Disposition: form-data; name=\"photo\"; filename=\"chart.png\"{crlf}"
                 f"Content-Type: image/png{crlf}{crlf}").encode("utf-8") + png + f"{crlf}--{boundary}--{crlf}".encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{clean_token}/sendPhoto", data=body,
            headers={"User-Agent": "Trading-AI-PRO-Bot", "Content-Type": f"multipart/form-data; boundary={boundary}"})
        ctx = None
        try:
            import ssl
            ctx = ssl._create_unverified_context()
        except Exception:
            pass
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    def _telegram_send_ai_report(self, token: str, chat_id: str):
        """Nút 'Phân tích AI' / lệnh /ai: gửi ẢNH biểu đồ khung scalp kèm chữ phân tích + nút bấm
        trong CÙNG 1 tin (nhanh hơn: 1 lần gửi thay vì 2). Chữ dài quá giới hạn chú thích ảnh (1024 ký tự) thì gửi ảnh rồi tin chữ.
        Chụp/gửi ảnh lỗi thì vẫn gửi tin chữ như cũ."""
        report = self._generate_telegram_ai_report()
        try:
            png = self._tg_capture_chart_png()
            if png:
                if len(self._tg_plain_text(report) or "") <= 1024:
                    res = self._telegram_send_photo(token, chat_id, png, report,
                                                    reply_markup=self._get_telegram_inline_keyboard())
                    if isinstance(res, dict) and res.get("ok"):
                        self._tg_track_kb_message(token, chat_id, res)
                        return
                    self._tg_diag_log("Gửi ảnh biểu đồ thất bại -> gửi tin chữ")
                else:
                    pk = str(getattr(self, "active_profile_key", "XAUUSD") or "XAUUSD")
                    name = "VÀNG" if pk == "XAUUSD" else ("BTC" if pk == "BTCUSD" else pk)
                    self._telegram_send_photo(token, chat_id, png, f"Biểu đồ {name} lúc {datetime.now(tz=VN_TZ).strftime('%H:%M:%S')}")
        except Exception:
            pass
        self._telegram_send_with_keyboard(token, chat_id, report)

    def _get_telegram_inline_keyboard(self) -> dict:
        """
        Tạo bàn phím nút bấm trực tiếp gắn dưới tin nhắn (Inline Keyboard).
        Người dùng chạm vào nút là tác động trực tiếp lên công tắc phần mềm mà KHÔNG gửi tin nhắn rác vào hộp chat.
        Tự động hiển thị trạng thái ĐANG CHẠY / TẠM DỪNG riêng biệt của Vàng (XAU) và Bitcoin (BTC).
        """
        xau_running = bool(self.profile_ctx.get("XAUUSD", {}).get("auto_loop_running", False))
        btc_running = bool(self.profile_ctx.get("BTCUSD", {}).get("auto_loop_running", False))

        xau_text = "🟡 VÀNG: ⏸ DỪNG" if xau_running else "🟡 VÀNG: ▶️ BẬT"
        btc_text = "🟠 BTC: ⏸ DỪNG" if btc_running else "🟠 BTC: ▶️ BẬT"

        return {
            "inline_keyboard": [
                [
                    {"text": "📊 Báo cáo", "callback_data": "tg_stats"},
                    {"text": "🧠 Phân tích AI", "callback_data": "tg_ai"}
                ],
                [
                    {"text": xau_text, "callback_data": "tg_toggle_xau"},
                    {"text": btc_text, "callback_data": "tg_toggle_btc"}
                ],
                [
                    {"text": "⏸️ Dừng Cả 2", "callback_data": "tg_stop_all"},
                    {"text": "▶️ Bật Cả 2", "callback_data": "tg_start_all"}
                ]
            ]
        }

    def _telegram_send_with_keyboard(self, token: str, chat_id: str, text: str, reply_markup: dict = None, timeout: int = 15):
        """Gửi tin kèm nút bấm rồi gỡ nút ở tin có nút trước đó: nút chỉ nằm dưới tin MỚI NHẤT (khung chat gọn).
        Trả về kết quả sendMessage như _telegram_api_call."""
        res = self._telegram_api_call(token, "sendMessage", {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": reply_markup or self._get_telegram_inline_keyboard(),
        }, timeout=timeout)
        return self._tg_track_kb_message(token, chat_id, res)

    def _tg_track_kb_message(self, token: str, chat_id: str, res):
        """Ghi nhận tin vừa gửi có nút; gỡ nút ở tin có nút trước đó (nút chỉ nằm dưới tin mới nhất). Trả lại res."""
        try:
            new_id = int(res["result"]["message_id"]) if isinstance(res, dict) and res.get("ok") else None
        except Exception:
            new_id = None
        lock = getattr(self, "_tg_kb_lock", None)
        if new_id is None or lock is None:
            return res
        with lock:
            old_id = getattr(self, "_tg_last_kb_msg_id", None)
            self._tg_last_kb_msg_id = new_id
        if old_id and old_id != new_id:
            try:
                self._telegram_api_call(token, "editMessageReplyMarkup", {
                    "chat_id": chat_id, "message_id": old_id, "reply_markup": {"inline_keyboard": []},
                }, timeout=10)
            except Exception:
                pass
        return res

    def _telegram_dispatcher_loop(self):
        """Luồng ngầm gửi tin nhắn từ hàng đợi ra Telegram hoàn toàn bất đồng bộ (0 giật lag UI / 0 nghẽn MT5)."""
        while not getattr(self, "_app_closing", False):
            try:
                item = self._telegram_queue.get(timeout=2.0)
            except queue.Empty:
                continue

            if item is None or getattr(self, "_app_closing", False):
                break

            msg_text, reply_markup = item
            token = str(getattr(self, "telegram_bot_token_var", tk.StringVar()).get()).strip()
            chat_id = self._tg_target_chat_id()  # ID nhóm chat, trống -> gửi riêng ID người điều khiển

            if token and chat_id:
                try:
                    self._telegram_send_with_keyboard(token, chat_id, msg_text, reply_markup=reply_markup, timeout=12)
                except Exception:
                    pass  # không để luồng gửi tin chết vì 1 tin lỗi

    def _telegram_polling_worker(self):
        """Luồng ngầm lắng nghe lệnh điều khiển từ xa từ điện thoại người dùng qua Telegram (Long-Polling)."""
        offset = None
        cleaned_webhook_token = None
        while not getattr(self, "_app_closing", False):
            try:
                en_var = getattr(self, "telegram_enabled_var", None)
                if not (en_var and en_var.get()):
                    time.sleep(3)
                    continue

                token = str(getattr(self, "telegram_bot_token_var", tk.StringVar()).get()).strip()
                chat_id = self._tg_target_chat_id()  # ID nhóm chat, trống -> gửi riêng ID người điều khiển
                if not token or not chat_id:
                    time.sleep(3)
                    continue

                # Khi khởi động hoặc đổi token: Luôn xóa bỏ triệt để webhook cũ/bên thứ 3
                # để giải phóng xung đột getUpdates và chặn đứng các bot lạ tự ý phản hồi
                if cleaned_webhook_token != token:
                    del_res = self._telegram_api_call(token, "deleteWebhook", {"drop_pending_updates": True}, timeout=15)
                    if del_res and del_res.get("ok"):
                        self.log("[Telegram] Đã gỡ bỏ webhook cũ và kích hoạt chế độ lắng nghe độc quyền cho Trading AI PRO.")
                    cleaned_webhook_token = token
                    offset = None
                    # Tên bot: chỉ làm theo lệnh /lệnh@tên_bot gửi ĐÚNG bot này (nhóm có nhiều bot)
                    try:
                        me = self._telegram_api_call(token, "getMe", None, timeout=15)
                        self._tg_bot_username = str(((me or {}).get("result") or {}).get("username") or "").strip().lower()
                    except Exception:
                        self._tg_bot_username = ""

                payload = {"timeout": 15}
                if offset is not None:
                    payload["offset"] = offset

                res = self._telegram_api_call(token, "getUpdates", payload, timeout=25)
                if not res or not res.get("ok"):
                    time.sleep(3)
                    continue

                updates = res.get("result", [])
                for upd in updates:
                    offset = max(offset or 0, upd.get("update_id", 0) + 1)

                    # 1. Bắt tương tác từ nút bấm trực tiếp (Inline Keyboard Callback Query)
                    cq = upd.get("callback_query")
                    if cq and isinstance(cq, dict):
                        # Nút dưới tin trong nhóm: chat của tin = nhóm, người bấm = tài khoản cá nhân -> xét quyền theo người bấm
                        cq_from = str((cq.get("from") or {}).get("id", ""))
                        cq_chat = str(((cq.get("message") or {}).get("chat") or {}).get("id", ""))
                        if self._tg_sender_allowed(chat_id, cq_from, cq_chat or cq_from):
                            self._handle_telegram_callback(cq, chat_id, token)
                        continue

                    # 2. Bắt tin nhắn văn bản (Lệnh gõ tay /start, /stats, /menu...)
                    msg = upd.get("message") or upd.get("channel_post")
                    if not msg or not isinstance(msg, dict):
                        continue

                    sender = str(msg.get("chat", {}).get("id", ""))
                    from_id = str((msg.get("from") or {}).get("id", ""))
                    # Lá chắn an ninh: Chỉ chấp nhận tin từ đúng Chat ID đã đăng ký, hoặc chat riêng của người điều khiển
                    if not self._tg_sender_allowed(chat_id, from_id, sender):
                        continue

                    text = str(msg.get("text", "")).strip()
                    if text:
                        self._handle_telegram_command(text, chat_id, token, from_id=from_id, in_group=sender.startswith("-"))

            except Exception:
                time.sleep(3)

    def _handle_telegram_callback(self, cq: dict, chat_id: str, token: str):
        """Xử lý sự kiện khi người dùng bấm trực tiếp vào nút Inline Keyboard trên điện thoại."""
        cq_id = str(cq.get("id", ""))
        data = str(cq.get("data", "")).strip()
        msg_obj = cq.get("message") or {}
        msg_id = msg_obj.get("message_id")
        # Nút BẬT/TẮT: chỉ người điều khiển (chủ bot); nút xem (Báo cáo, Phân tích AI) ai trong chat cũng bấm được
        cq_from = str((cq.get("from") or {}).get("id", ""))
        if data in ("tg_toggle_xau", "tg_toggle_btc", "tg_stop_all", "tg_start_all") and not self._tg_can_control(chat_id, cq_from):
            self._telegram_api_call(token, "answerCallbackQuery", {
                "callback_query_id": cq_id, "text": self._tg_deny_control_text(chat_id), "show_alert": True})
            return

        if data == "tg_stats":
            self._telegram_api_call(token, "answerCallbackQuery", {
                "callback_query_id": cq_id,
                "text": "📊 Đang cập nhật báo cáo...",
                "show_alert": False
            })
            report = self._generate_telegram_stats_report()
            self._telegram_send_with_keyboard(token, chat_id, report)

        elif data == "tg_ai":
            self._telegram_api_call(token, "answerCallbackQuery", {
                "callback_query_id": cq_id,
                "text": "🧠 Đang lấy phân tích AI mới nhất...",
                "show_alert": False
            })
            self._telegram_send_ai_report(token, chat_id)

        elif data == "tg_toggle_xau":
            xau_running = bool(self.profile_ctx.get("XAUUSD", {}).get("auto_loop_running", False))
            if xau_running:
                self.root.after(0, lambda: self.stop_auto_scan("XAUUSD"))
                toast = "⏸️ Đã DỪNG Tự Động Trade Vàng (XAUUSD)!"
            else:
                self.root.after(0, lambda: self.start_auto_scan("XAUUSD"))
                toast = "🟢 Đã BẬT Tự Động Trade Vàng (XAUUSD)!"

            self._telegram_api_call(token, "answerCallbackQuery", {
                "callback_query_id": cq_id,
                "text": toast,
                "show_alert": False
            })
            if msg_id:
                time.sleep(0.2)
                self._telegram_api_call(token, "editMessageReplyMarkup", {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "reply_markup": self._get_telegram_inline_keyboard()
                })

        elif data == "tg_toggle_btc":
            btc_running = bool(self.profile_ctx.get("BTCUSD", {}).get("auto_loop_running", False))
            if btc_running:
                self.root.after(0, lambda: self.stop_auto_scan("BTCUSD"))
                toast = "⏸️ Đã DỪNG Tự Động Trade Bitcoin (BTCUSD)!"
            else:
                self.root.after(0, lambda: self.start_auto_scan("BTCUSD"))
                toast = "🟢 Đã BẬT Tự Động Trade Bitcoin (BTCUSD)!"

            self._telegram_api_call(token, "answerCallbackQuery", {
                "callback_query_id": cq_id,
                "text": toast,
                "show_alert": False
            })
            if msg_id:
                time.sleep(0.2)
                self._telegram_api_call(token, "editMessageReplyMarkup", {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "reply_markup": self._get_telegram_inline_keyboard()
                })

        elif data == "tg_stop_all":
            self.root.after(0, lambda: self.stop_auto_scan("XAUUSD"))
            self.root.after(0, lambda: self.stop_auto_scan("BTCUSD"))
            self._telegram_api_call(token, "answerCallbackQuery", {
                "callback_query_id": cq_id,
                "text": "⏸️ ĐÃ DỪNG TẤT CẢ (Vàng & Bitcoin)!",
                "show_alert": False
            })
            if msg_id:
                time.sleep(0.2)
                self._telegram_api_call(token, "editMessageReplyMarkup", {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "reply_markup": self._get_telegram_inline_keyboard()
                })

        elif data == "tg_start_all":
            self.root.after(0, lambda: self.start_auto_scan("XAUUSD"))
            self.root.after(0, lambda: self.start_auto_scan("BTCUSD"))
            self._telegram_api_call(token, "answerCallbackQuery", {
                "callback_query_id": cq_id,
                "text": "🟢 ĐÃ BẬT TẤT CẢ (Vàng & Bitcoin)!",
                "show_alert": False
            })
            if msg_id:
                time.sleep(0.2)
                self._telegram_api_call(token, "editMessageReplyMarkup", {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "reply_markup": self._get_telegram_inline_keyboard()
                })

    def _handle_telegram_command(self, text: str, chat_id: str, token: str, from_id: str = None, in_group: bool = False):
        """Xử lý lệnh từ điện thoại gửi về qua tin nhắn văn bản.
        from_id: ID người gửi (None = gọi cũ, coi như chủ chat riêng). in_group: tin trong nhóm."""
        norm_txt = text.lower().strip()
        # A31: chỉ khớp NGUYÊN tin nhắn; bỏ hậu tố @tên_bot của dạng /lệnh@bot (Telegram trong nhóm/chat)
        if norm_txt.startswith("/"):
            norm_txt, _, target_bot = norm_txt.partition("@")
            norm_txt = norm_txt.strip()
            own_bot = str(getattr(self, "_tg_bot_username", "") or "").lower()
            if target_bot.strip() and own_bot and target_bot.strip() != own_bot:
                return  # lệnh gửi cho bot KHÁC trong nhóm
        now_str = datetime.now(tz=VN_TZ).strftime("%H:%M:%S")
        from_id = chat_id if from_id is None else str(from_id)
        control_cmds = ("tạm dừng", "/pause", "/stop", "bật bot", "chạy", "/resume", "/run", "/start_bot")
        if norm_txt in control_cmds and not self._tg_can_control(chat_id, from_id):
            self._telegram_api_call(token, "sendMessage", {"chat_id": chat_id, "text": self._tg_deny_control_text(chat_id)})
            return

        if norm_txt in ("/start", "/help", "/menu"):
            welcome = (
                "🎛️ [TRADING AI PRO] BẢNG ĐIỀU KHIỂN TỪ XA\n"
                "────────────────────────\n"
                "Hệ thống đang kết nối trực tuyến. Bạn có thể điều khiển trực tiếp trạng thái chạy của bot hoặc xem thống kê qua các nút bấm bên dưới:"
            )
            # Gỡ bỏ phím Reply Keyboard cũ ở đáy màn hình
            self._telegram_api_call(token, "sendMessage", {
                "chat_id": chat_id,
                "text": "🔄 Đã kích hoạt chế độ phím bấm trực tiếp (Inline Buttons).",
                "reply_markup": {"remove_keyboard": True}
            })
            self._telegram_send_with_keyboard(token, chat_id, welcome)

        elif norm_txt in ("thống kê", "/stats", "/status"):
            report = self._generate_telegram_stats_report()
            self._telegram_send_with_keyboard(token, chat_id, report)

        elif norm_txt in ("/ai", "phân tích", "phân tích ai"):
            self._telegram_send_ai_report(token, chat_id)

        elif norm_txt in ("tạm dừng", "/pause", "/stop"):
            self.root.after(0, lambda: self.stop_auto_scan("XAUUSD"))
            self.root.after(0, lambda: self.stop_auto_scan("BTCUSD"))
            resp = (
                "⏸️ [TRADING AI PRO] ĐÃ TẠM DỪNG TẤT CẢ BOT\n"
                f"⏰ Thời gian: {now_str}\n"
                "Trạng thái: ĐÃ TẮT TỰ ĐỘNG VÀO LỆNH (Vàng & Bitcoin).\n"
                "Các lệnh đang mở vẫn được bảo vệ SL/TP và Trailing Stop bình thường."
            )
            self._telegram_send_with_keyboard(token, chat_id, resp)

        elif norm_txt in ("bật bot", "chạy", "/resume", "/run", "/start_bot"):
            self.root.after(0, lambda: self.start_auto_scan("XAUUSD"))
            self.root.after(0, lambda: self.start_auto_scan("BTCUSD"))
            resp = (
                "🟢 [TRADING AI PRO] ĐÃ BẬT TẤT CẢ BOT HOẠT ĐỘNG\n"
                f"⏰ Thời gian: {now_str}\n"
                "Trạng thái: ĐANG CHẠY (RUNNING - Vàng & Bitcoin).\n"
                "Bot đã sẵn sàng quét tín hiệu AI và vào lệnh tự động."
            )
            self._telegram_send_with_keyboard(token, chat_id, resp)

        elif not in_group:
            # Tin nhắn thường (không phải đúng một lệnh): KHÔNG kích hoạt gì, chỉ gợi ý lệnh hợp lệ (trong nhóm thì im lặng, tránh spam)
            self._telegram_api_call(token, "sendMessage", {
                "chat_id": chat_id,
                "text": "ℹ️ Lệnh hợp lệ (gõ đúng nguyên lệnh): /menu, /stats (hoặc 'thống kê'), /ai (hoặc 'phân tích'), /pause (hoặc 'tạm dừng'), /resume (hoặc 'bật bot', 'chạy').",
            })

    def _generate_telegram_ai_report(self) -> str:
        """Tin cho nút '🧠 Phân tích AI' / lệnh /ai: kết quả phân tích AI mới nhất của từng mã (dòng tín hiệu, dự báo,
        gợi ý Entry/SL/TP như trên app) + vùng đang canh. Chỉ đọc dữ liệu đã có, không gọi AI/MT5."""
        parts = []
        for pk, name in (("XAUUSD", "VÀNG"), ("BTCUSD", "BTC")):
            ctx = (getattr(self, "profile_ctx", None) or {}).get(pk) or {}
            info = ctx.get("_tg_ai_info")
            if not isinstance(info, dict):
                continue
            try:
                running = bool(ctx.get("auto_loop_running", False))
                lines = [f"🧠 {name} · AI {info.get('tf', '')} lúc {info.get('ts', '')} · AUTO {'BẬT' if running else 'TẮT'}",
                         str(ctx.get("_sig_title") or "").strip()]
                for ln in str(ctx.get("_last_detail_prefix") or "").splitlines():
                    if ln.strip():
                        lines.append(re.sub(r"\s{2,}", " ", ln).strip())
                pend = ctx.get("pending_pullback_order")
                if isinstance(pend, dict) and pend.get("zone_watch") and pend.get("action") in ("BUY", "SELL") and pend.get("sl_ai_price"):
                    eff = float(pend.get("effective_max_sl") or 0.0)
                    if eff > 0.0:
                        is_buy = pend["action"] == "BUY"
                        zone = float(pend["sl_ai_price"]) + (eff if is_buy else -eff)
                        lines.append(f"⏳ Đang canh vùng {pend['action']} {'≤' if is_buy else '≥'} {zone:.2f}")
                parts.append("\n".join(x for x in lines if x))
            except Exception:
                continue
        if not parts:
            return "🧠 Chưa có kết quả phân tích AI (bot vừa khởi động hoặc chưa phân tích nến nào)."
        return "\n\n".join(parts)

    def _generate_telegram_stats_report(self) -> str:
        """Tạo chuỗi báo cáo chi tiết tài khoản và tách biệt thống kê cho từng cặp Vàng (XAU) và Bitcoin (BTC)."""
        now_dt = datetime.now(tz=VN_TZ)
        time_str = now_dt.strftime("%H:%M:%S")
        date_str = now_dt.strftime("%d/%m/%Y")

        acc_id = "Chưa kết nối"
        balance = 0.0
        equity = 0.0
        open_positions = []
        if MT5_AVAILABLE:
            try:
                with self._mt5_lock:
                    ai = mt5.account_info()
                    if ai:
                        acc_id = str(ai.login)
                        balance = float(ai.balance)
                        equity = float(ai.equity)
                    open_positions = list(mt5.positions_get() or [])
            except Exception:
                pass

        xau_running = bool(self.profile_ctx.get("XAUUSD", {}).get("auto_loop_running", False))
        btc_running = bool(self.profile_ctx.get("BTCUSD", {}).get("auto_loop_running", False))
        xau_st = "🟢 ĐANG CHẠY" if xau_running else "⏸️ TẠM DỪNG"
        btc_st = "🟢 ĐANG CHẠY" if btc_running else "⏸️ TẠM DỪNG"

        cfg_xau = self._get_profile_cfg("XAUUSD")
        sym_xau = str(cfg_xau.get("symbol", "XAUUSD")).strip()
        pnl_xau, _, _ = self._get_today_deals_stats(symbol=sym_xau)
        deals_xau = self._get_today_closed_deals(symbol=sym_xau)
        w_xau = sum(1 for d in deals_xau if d.get("category") == "WIN")
        l_xau = sum(1 for d in deals_xau if d.get("category") == "LOSS")
        be_xau = sum(1 for d in deals_xau if d.get("category") in ("BE", "BE+"))
        tot_xau = len(deals_xau)
        wr_xau = (w_xau / max(tot_xau, 1)) * 100.0 if tot_xau > 0 else 0.0

        cfg_btc = self._get_profile_cfg("BTCUSD")
        sym_btc = str(cfg_btc.get("symbol", "BTCUSD")).strip()
        pnl_btc, _, _ = self._get_today_deals_stats(symbol=sym_btc)
        deals_btc = self._get_today_closed_deals(symbol=sym_btc)
        w_btc = sum(1 for d in deals_btc if d.get("category") == "WIN")
        l_btc = sum(1 for d in deals_btc if d.get("category") == "LOSS")
        be_btc = sum(1 for d in deals_btc if d.get("category") in ("BE", "BE+"))
        tot_btc = len(deals_btc)
        wr_btc = (w_btc / max(tot_btc, 1)) * 100.0 if tot_btc > 0 else 0.0

        floating_profit = sum(float(p.profit) for p in open_positions) if open_positions else 0.0
        total_pnl = pnl_xau + pnl_btc + floating_profit

        lines = [
            "📊 [TRADING AI PRO] BÁO CÁO TOÀN DIỆN",
            f"⏰ Thời gian: {time_str} ({date_str})",
            "────────────────────────",
            f"💳 Tài khoản MT5: {acc_id}",
            f"💰 Số dư (Balance): {balance:,.2f} USD",
            f"📈 Vốn (Equity): {equity:,.2f} USD",
            f"💵 Tổng Lãi/Lỗ hôm nay: {total_pnl:+,.2f} USD",
            "────────────────────────",
            f"🟡 VÀNG ({sym_xau}): {xau_st}",
            f"   • Lãi ngày: {pnl_xau:+,.2f} USD",
            f"   • Win Rate: {wr_xau:.1f}% ({w_xau}W - {l_xau}L - {be_xau}BE)",
            "",
            f"🟠 BITCOIN ({sym_btc}): {btc_st}",
            f"   • Lãi ngày: {pnl_btc:+,.2f} USD",
            f"   • Win Rate: {wr_btc:.1f}% ({w_btc}W - {l_btc}L - {be_btc}BE)",
            "────────────────────────",
            f"⚡ Lệnh đang mở: {len(open_positions)} vị thế (Lãi thả nổi: {floating_profit:+,.2f}$)",
        ]
        if open_positions:
            for p in open_positions[:6]:
                t_str = "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL"
                lines.append(f"   • #{p.ticket}: {t_str} {p.symbol} {p.volume:.2f}L (P/L: {p.profit:+,.2f}$)")
            if len(open_positions) > 6:
                lines.append(f"   ... và còn {len(open_positions) - 6} lệnh khác.")

        return "\n".join(lines)

    def _telegram_notify_order_opened(self, ticket: int, symbol: str, action: str, lot: float, price: float, sl: float, tp: float, profile_key: str):
        """Gửi thông báo mở lệnh mới lên Telegram."""
        if not bool(getattr(self, "telegram_enabled_var", None) and self.telegram_enabled_var.get()):
            return
        if not bool(getattr(self, "telegram_notify_open_var", None) and self.telegram_notify_open_var.get()):
            return

        now_str = datetime.now(tz=VN_TZ).strftime("%H:%M:%S")
        sl_dist = abs(price - sl)
        tp_dist = abs(tp - price)
        rr_val = tp_dist / max(sl_dist, 0.01)

        ctx = self.profile_ctx.get(profile_key, {})
        # Đồng thuận THẬT của lần phân tích gần nhất (ghi lúc phân tích xong); trước đây luôn hiện ngưỡng cài đặt
        ai_consensus = ctx.get("cached_consensus")
        ai_str = f" · AI {ai_consensus}%" if ai_consensus not in (None, "") else ""

        sym_tag = "VÀNG" if "XAU" in symbol.upper() or "GOLD" in symbol.upper() else symbol

        # Tin gọn 2 dòng (người dùng chọn 19/11/2026); nút bấm do hàm gửi gắn vào tin mới nhất
        msg = (
            f"🚀 {sym_tag} {action} {lot:.2f} @ {price:.2f} · {now_str}\n"
            f"SL {sl:.2f} (-{sl_dist:.2f}) · TP {tp:.2f} (+{tp_dist:.2f} · {rr_val:.1f}R){ai_str} · #{ticket}"
        )
        self._telegram_queue.put((msg, None))

    def _telegram_notify_order_closed(self, cd: dict, day_pnl: float = None):
        """Gửi thông báo đóng lệnh (chốt lời / cắt lỗ / hòa vốn) lên Telegram.
        day_pnl: lãi/lỗ đã chốt hôm nay của profile tính từ CÙNG danh sách lệnh đã đóng (có lệnh vừa đóng);
        trước đây lấy từ bộ đệm 10 giây nên có lúc chưa tính lệnh vừa đóng (vd 19/11 12:34 báo +21.82$ thay vì ~+3.6$)."""
        if not bool(getattr(self, "telegram_enabled_var", None) and self.telegram_enabled_var.get()):
            return
        if not bool(getattr(self, "telegram_notify_close_var", None) and self.telegram_notify_close_var.get()):
            return

        category = cd.get("category", "")
        # Chỉ giữ chấm xanh/đỏ (người dùng yêu cầu 19/11/2026): thắng 🟢, thua 🔴, hòa vốn không chấm
        if category == "WIN":
            icon = "🟢"
        elif category in ("BE", "BE+"):
            icon = ""
        else:
            icon = "🔴"

        net_pnl = float(cd.get("net_pnl", 0.0))
        r_str = cd.get("r_ratio_str", "")
        r_suffix = f" ({r_str})" if r_str else ""
        dur_str = cd.get("duration", "---")
        status_str = str(cd.get("status", "Đã đóng"))
        open_p = cd.get("open_price", 0.0)
        close_p = cd.get("close_price", 0.0)
        symbol = str(cd.get("symbol", "")).strip()

        if day_pnl is None:
            cfg = self._get_profile_cfg(self.active_profile_key)
            sym = str(cfg.get("symbol", "")).strip()
            day_pnl, _, _ = self._get_today_deals_stats(symbol=sym)

        sym_tag = "VÀNG" if "XAU" in symbol.upper() or "GOLD" in symbol.upper() else symbol
        # "ĐÃ CHỐT TP (13:10:11)" -> "CHỐT TP · 13:10:11"
        m_st = re.match(r"^\s*(?:ĐÃ\s+)?(.*?)\s*\((\d{1,2}:\d{2}:\d{2})\)\s*$", status_str)
        status_short = f"{m_st.group(1)} · {m_st.group(2)}" if m_st else status_str

        # Tin gọn 2 dòng (người dùng chọn 19/11/2026); nút bấm do hàm gửi gắn vào tin mới nhất
        msg = (
            f"{icon + ' ' if icon else ''}{sym_tag} {cd.get('type')} {net_pnl:+.2f}${r_suffix} · {status_short}\n"
            f"{open_p:.2f} → {close_p:.2f} · giữ {dur_str} · Ngày {float(day_pnl):+.2f}$ · #{cd.get('ticket')}"
        )
        self._telegram_queue.put((msg, None))

    def _telegram_fresh_closed_deals(self, pk: str, symbol: str, positions, closed_deals: list) -> list:
        """Telegram báo đóng lệnh nhanh (~1-2 giây thay vì tới 15 giây): lệnh bot vừa biến mất khỏi danh sách đang mở mà
        danh sách lệnh đã đóng (bộ đệm 15 giây) chưa có -> đọc lịch sử MỚI, tối đa 1 lần/giây, chờ tối đa 20 giây.
        KHÔNG ghi vào bộ đệm dùng chung (store_cache=False): luật SL nhanh/chuỗi thua giữ nguyên thời điểm như cũ."""
        try:
            ctx = self.profile_ctx[pk]
            now_m = time.monotonic()
            cur_open = {int(p.ticket) for p in (positions or [])}
            prev_open = ctx.get("_tg_open_tickets")
            ctx["_tg_open_tickets"] = cur_open
            waiting = ctx.setdefault("_tg_wait_close", {})
            if prev_open:
                for t in prev_open - cur_open:
                    waiting[t] = now_m + 20.0
            known = {int(c.get("ticket") or 0) for c in (closed_deals or [])}
            sent = getattr(self, "_telegram_sent_deal_tickets", set())
            for t in list(waiting):
                if t in known or t in sent or t in cur_open or waiting[t] < now_m:
                    waiting.pop(t, None)
            if not waiting or now_m - float(ctx.get("_tg_fresh_ts", 0.0)) < 1.0:
                return closed_deals
            ctx["_tg_fresh_ts"] = now_m
            today_s = datetime.now(tz=VN_TZ).strftime("%Y-%m-%d")
            fresh = self._get_closed_deals_by_range(symbol=symbol, start_date_str=today_s, end_date_str=today_s,
                                                    use_cache=False, profile_key=pk, store_cache=False)
            return fresh if fresh else closed_deals
        except Exception:
            return closed_deals

    def test_telegram_connection(self):
        """Gửi tin nhắn thử nghiệm kiểm tra tính hợp lệ của Bot Token & Chat ID."""
        token = str(getattr(self, "telegram_bot_token_var", tk.StringVar()).get()).strip()
        chat_id = self._tg_target_chat_id()  # ID nhóm chat, trống -> gửi riêng ID người điều khiển
        if not token or not chat_id:
            self.show_toast("Vui lòng nhập Bot Token và ID nhóm chat (hoặc ID người điều khiển)!", is_error=True, duration_ms=3000)
            return

        def _worker():
            # 1. Tự động dọn sạch webhook bên thứ 3 (nếu có)
            self._telegram_api_call(token, "deleteWebhook", {"drop_pending_updates": True}, timeout=15)

            # 2. Xóa bỏ phím reply keyboard cũ ở đáy màn hình
            self._telegram_api_call(token, "sendMessage", {
                "chat_id": chat_id,
                "text": "🔄 Đang dọn sạch bàn phím cũ và khởi tạo nút bấm trực tiếp...",
                "reply_markup": {"remove_keyboard": True}
            }, timeout=10)

            # 3. Gửi thông báo kết nối thành công kèm nút bấm Inline Keyboard
            now_str = datetime.now(tz=VN_TZ).strftime("%H:%M:%S (%d/%m/%Y)")
            msg = (
                "✅ [TRADING AI PRO] KẾT NỐI THÀNH CÔNG\n"
                f"⏰ Thời gian: {now_str}\n"
                "────────────────────────\n"
                "• Hệ thống MT5 đã liên kết thành công với Telegram của bạn.\n"
                "• Chế độ nút bấm trực tiếp (Inline Buttons) đã được kích hoạt.\n"
                "• Bấm nút bên dưới để điều khiển bật/tắt bot hoặc xem thống kê:"
            )
            res = self._telegram_send_with_keyboard(token, chat_id, msg, timeout=10)
            if res and res.get("ok"):
                self.root.after(0, lambda: self.show_toast("Đã gửi tin nhắn thử thành công đến Telegram!", is_error=False, duration_ms=3500))
                self.log("✅ [Telegram] Đã gửi tin nhắn thử nghiệm thành công tới Telegram!")
            else:
                err_desc = res.get("description", "Không rõ nguyên nhân") if isinstance(res, dict) else "Lỗi kết nối mạng hoặc sai Token/Chat ID"
                self.root.after(0, lambda: self.show_toast(f"Lỗi gửi Telegram: {err_desc}", is_error=True, duration_ms=4500))
                self.log(f"❌ [Telegram] Lỗi gửi tin nhắn thử: {err_desc}")

        threading.Thread(target=_worker, daemon=True).start()

    def _bind_auto_save_settings(self):
        for var in self._get_settings_vars_map().values():
            var.trace_add("write", self.save_user_settings)
        self.tf_var.trace_add("write", self._on_scalp_timeframe_changed)
        self.analysis_tf_var.trace_add("write", self._on_analysis_timeframe_changed)
        self.root.bind_all("<Button-1>", self._on_global_click_outside_menu, add="+")
        self.root.bind_all("<Button-3>", self._on_global_click_outside_menu, add="+")

    def _on_global_click_outside_menu(self, event):
        """Khi Menu đang sổ ra, nếu click chuột ở bất kỳ chỗ nào bên ngoài Menu thì tự động thu Menu vào."""
        if not self.left_menu_visible or self._drawer_anim_job is not None:
            return
        w = getattr(event, "widget", None)
        if w is self.menu_toggle_btn or w is self.drawer_close_btn:
            return
        w_str = str(w).lower()
        if "popdown" in w_str or "combobox" in w_str:
            return
        curr = w
        while curr is not None:
            if curr is self.left_panel:
                return
            curr = getattr(curr, "master", None)
        try:
            lx = self.left_panel.winfo_rootx()
            ly = self.left_panel.winfo_rooty()
            lw = self.left_panel.winfo_width()
            lh = self.left_panel.winfo_height()
            if lx <= event.x_root <= lx + lw and ly <= event.y_root <= ly + lh:
                return
        except Exception:
            pass
        self.toggle_left_menu()

    def _highlight_active_tf_btn(self, active_tf: str):
        if not hasattr(self, "tf_quick_btns"):
            return
        for tf_k, btn in self.tf_quick_btns.items():
            if tf_k == active_tf:
                btn.config(bg=GOLD_ACCENT, fg="#000000")
            else:
                btn.config(bg="#222c3c", fg=TEXT_MAIN)

    def switch_chart_timeframe_quick(self, tf_opt: str):
        """
        Đổi nhanh khung thời gian hiển thị nến trên biểu đồ (CHỈ ĐỂ XEM BIỂU ĐỒ - VIEW ONLY).
        Hoàn toàn ĐỘC LẬP: KHÔNG làm thay đổi Khung phân tích AI hay Khung vào lệnh trong Menu Cài đặt,
        và KHÔNG kích hoạt phân tích lại AI.
        """
        pk = self.active_profile_key
        if not hasattr(self, "chart_view_tf_by_profile"):
            self.chart_view_tf_by_profile = {}
        self.chart_view_tf_by_profile[pk] = tf_opt
        self.user_zoomed = False
        self.saved_xlim = None
        self.saved_ylim = None
        if pk in self.profile_ctx:
            self.profile_ctx[pk]["user_zoomed"] = False
            self.profile_ctx[pk]["saved_xlim"] = None
            self.profile_ctx[pk]["saved_ylim"] = None

        self._highlight_active_tf_btn(tf_opt)

        cfg = self._get_profile_cfg(pk)
        symbol = str(cfg.get("symbol", pk)).strip()
        ana_tf = str(cfg.get("analysis_tf", "M15")).strip()
        scalp_tf = str(cfg.get("tf", "M5")).strip()

        try:
            chart_bars = min(max(int(self.lookback_var.get() or 450), 400), 500)
        except ValueError:
            chart_bars = 450

        # Tải nến theo khung người dùng muốn xem và vẽ lên biểu đồ
        try:
            df_view = self.fetch_mt5_candles(symbol, tf_opt, chart_bars, include_forming=True)
            if df_view is not None:
                show_fan = (getattr(self, "show_ai_chart_overlay", True) and tf_opt == ana_tf)
                self._draw_candlestick_chart(
                    df_hist=df_view,
                    scenarios=self.cached_scenarios if show_fan else None,
                    mean_df=self.cached_mean_df if show_fan else None,
                    action=self.cached_action,
                    sl_price=self.cached_sl,
                    tp_price=self.cached_tp,
                    symbol=symbol,
                    tf=tf_opt,
                    has_forming_candle=True
                )
        except Exception as e:
            self.log(f"⚠️ [{pk}] Chưa tải được nến khung [{tf_opt}] do MT5 đang mất kết nối: {e}")

        self.log(f"👁️ [{pk}] Đang xem biểu đồ khung [{tf_opt}]. (Cài đặt AI: Phân tích [{ana_tf}] -> Vào lệnh [{scalp_tf}]).")

    def _on_scalp_timeframe_changed(self, *args):
        """Khi người dùng đổi Khung vào lệnh (Scalp): Chỉ cập nhật khung canh lệnh và biểu đồ, KHÔNG kích hoạt quét lại AI."""
        if self._loading_settings:
            return
        pk = self.active_profile_key
        self._sync_self_to_active_ctx(include_ai_state=False)
        scalp_tf = self.tf_var.get().strip() or "M5"
        ana_tf = self.analysis_tf_var.get().strip() or scalp_tf
        if not hasattr(self, "chart_view_tf_by_profile"):
            self.chart_view_tf_by_profile = {}
        self.chart_view_tf_by_profile[pk] = scalp_tf
        self._highlight_active_tf_btn(scalp_tf)
        self._update_forecast_tab_header()
        self._update_bt_tf_display()
        self._last_drawn_chart_key = None
        self._refresh_realtime_chart(force_full_redraw=True)
        self.log(f"🎯 [{pk}] Đã đổi Khung vào lệnh (Scalp) sang [{scalp_tf}] (Giữ nguyên chu kỳ phân tích AI trên khung [{ana_tf}]).")

    def _on_analysis_timeframe_changed(self, *args):
        """Khi người dùng đổi Khung phân tích AI: Đồng bộ sang khung thời gian phân tích mới và chỉ quét 1 lần cho khung mới."""
        if self._loading_settings:
            return
        pk = self.active_profile_key
        self.user_zoomed = False
        self.saved_xlim = None
        self.saved_ylim = None
        if pk in self.profile_ctx:
            self.profile_ctx[pk]["user_zoomed"] = False
            self.profile_ctx[pk]["saved_xlim"] = None
            self.profile_ctx[pk]["saved_ylim"] = None
            self.profile_ctx[pk]["last_ana_candle_time"] = None
        self.apply_live_settings_and_rescan(profile_key=pk, source_label="Đổi Khung Phân Tích AI")

    def toggle_left_menu(self):
        """Trượt mượt mà (Slide-Over Animation) cột Menu bên trái mà không làm giật hay vẽ lại biểu đồ."""
        if self._drawer_anim_job is not None:
            try:
                self.root.after_cancel(self._drawer_anim_job)
            except Exception:
                pass
            self._drawer_anim_job = None

        self.left_menu_visible = not self.left_menu_visible
        if self.left_menu_visible:
            self.left_panel.lift()
            self.menu_toggle_btn.config(bg="#32415c", fg="#ffffff")
            target_x = 0
        else:
            self.menu_toggle_btn.config(bg="#252f42", fg="#ffffff")
            target_x = -self.drawer_width - 5
            self.save_user_settings()
            self.apply_live_settings_and_rescan(profile_key=self.active_profile_key, source_label="Đóng Menu Cài Đặt")

        self._animate_drawer_step(target_x)

    def _animate_drawer_step(self, target_x: int):
        diff = target_x - self.drawer_x
        if abs(diff) <= 4:
            self.drawer_x = target_x
            self.left_panel.place(x=self.drawer_x, y=0, relheight=1.0, width=self.drawer_width)
            self._drawer_anim_job = None
            return
        # Nội suy mượt kiểu Ease-Out (nhanh lúc đầu, êm dần lúc cuối)
        step = int(diff * 0.42)
        if step == 0:
            step = 4 if diff > 0 else -4
        self.drawer_x += step
        self.left_panel.place(x=self.drawer_x, y=0, relheight=1.0, width=self.drawer_width)
        self._drawer_anim_job = self.root.after(12, self._animate_drawer_step, target_x)

    def _register_active_instance_lock(self):
        """Ghi nhận PID và Handle cửa sổ của phiên đang chạy để chống mở trùng hoặc chạy ngầm 2 App."""
        try:
            os.makedirs(SETTINGS_DIR, exist_ok=True)
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            info = {
                "pid": os.getpid(),
                "hwnd": int(hwnd),
                "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            with open(INSTANCE_LOCK_FILE, "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _release_active_instance_lock(self):
        """Giải phóng file khóa phiên chạy khi đóng ứng dụng."""
        try:
            if os.path.exists(INSTANCE_LOCK_FILE):
                with open(INSTANCE_LOCK_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("pid") == os.getpid():
                    os.remove(INSTANCE_LOCK_FILE)
        except Exception:
            pass

    def _on_close_window(self):
        self._app_closing = True
        try:
            self._bt_kill_child()
        except Exception:
            pass
        # Chốt an toàn: dù bước dọn dẹp nào bị treo (vd. MT5 không phản hồi), tiến trình vẫn tự thoát sau 8s, không chạy ngầm
        try:
            _wd = threading.Timer(8.0, os._exit, args=(0,))
            _wd.daemon = True
            _wd.start()
        except Exception:
            pass
        try:
            self.root.withdraw()
        except Exception:
            pass
        try:
            self.save_user_settings()
        except Exception:
            pass
        self.auto_loop_running = False
        for pk in self.profile_ctx:
            self.profile_ctx[pk]["auto_loop_running"] = False
            self._auto_worker_token[pk] = self._auto_worker_token.get(pk, 0) + 1
        try:
            self._ai_queue.put_nowait(None)
        except Exception:
            pass
        try:
            if MT5_AVAILABLE and self._mt5_lock.acquire(timeout=3.0):  # không chờ mãi nếu luồng khác đang kẹt trong lệnh MT5
                try:
                    mt5.shutdown()
                finally:
                    self._mt5_lock.release()
        except Exception:
            pass
        self._write_file_log("Đã đóng ứng dụng an toàn và giải phóng toàn bộ tiến trình chạy ngầm.")
        self._release_active_instance_lock()
        self._cleanup_workspace_junk()
        try:
            self.root.destroy()
        except Exception:
            pass
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        os._exit(0)

    def _on_tab_changed(self, event):
        selected_idx = self.bottom_notebook.index(self.bottom_notebook.select())
        try:
            self._bt_set_workspace_visible(selected_idx == 3 and bool(getattr(self, "_backtest_unlocked", False)))
        except Exception as e:
            self._write_file_log(f"[BACKTEST] Lỗi đổi màn hình Backtest/biểu đồ live: {e}", is_error=True)
        if selected_idx == 0:
            self.chart_mode = "LIVE_POSITIONS"
            self._switch_active_profile("XAUUSD")
        elif selected_idx == 1:
            self.chart_mode = "LIVE_POSITIONS"
            self._switch_active_profile("BTCUSD")
        elif selected_idx == 2:
            # Tab 2: LỊCH SỬ GIAO DỊCH & XUẤT EXCEL
            self._refresh_history_tab()
        elif selected_idx == 3:
            # Tab 3: KIỂM THỬ CHIẾN LƯỢC (BACKTEST)
            if not getattr(self, "_backtest_unlocked", False):
                self._focus_backtest_password()
            else:
                self._update_bt_tf_display()

        self.user_zoomed = False
        self.saved_xlim = None
        self.saved_ylim = None
        pk = self.active_profile_key
        ana_tf = self.analysis_tf_var.get().strip()
        scalp_tf = self.tf_var.get().strip()
        view_tf = getattr(self, "chart_view_tf_by_profile", {}).get(pk, scalp_tf)
        active_tf = view_tf
        self._highlight_active_tf_btn(active_tf)
        if active_tf == scalp_tf and self.cached_df_hist is not None:
            active_df = self.cached_df_hist
        elif active_tf == ana_tf and self.cached_df_analysis is not None:
            active_df = self.cached_df_analysis
        else:
            try:
                chart_bars = min(max(int(self.lookback_var.get() or 450), 400), 500)
            except ValueError:
                chart_bars = 450
            active_df = self.fetch_mt5_candles(self.symbol_var.get().strip(), active_tf, chart_bars, include_forming=True)

        if active_df is not None:
            show_fan = (getattr(self, "show_ai_chart_overlay", True) and active_tf == ana_tf)
            self._draw_candlestick_chart(
                active_df,
                self.cached_scenarios if show_fan else None,
                self.cached_mean_df if show_fan else None,
                self.cached_action, self.cached_sl, self.cached_tp,
                self.symbol_var.get().strip(), active_tf, has_forming_candle=True
            )

    # =========================================================================
    # TƯƠNG TÁC BIỂU ĐỒ KIỂU TRADINGVIEW (ZOOM / PAN / X-SCALE / Y-SCALE / CROSSHAIR)
    # =========================================================================
    def reset_chart_view(self):
        self.user_zoomed = False
        self.saved_xlim = None
        self.saved_ylim = None
        self._selected_deal_ticket_focus = None
        self._active_viewing_closed_deal = None
        self._chart_visible_bars = 170
        pk = self.active_profile_key
        ana_tf = self.analysis_tf_var.get().strip()
        scalp_tf = self.tf_var.get().strip()
        view_tf = getattr(self, "chart_view_tf_by_profile", {}).get(pk, scalp_tf)
        active_tf = view_tf
        if active_tf == scalp_tf and self.cached_df_hist is not None:
            active_df = self.cached_df_hist
        elif active_tf == ana_tf and self.cached_df_analysis is not None:
            active_df = self.cached_df_analysis
        else:
            try:
                chart_bars = min(max(int(self.lookback_var.get() or 450), 400), 500)
            except ValueError:
                chart_bars = 450
            active_df = self.fetch_mt5_candles(self.symbol_var.get().strip(), active_tf, chart_bars, include_forming=True)

        if active_df is not None:
            show_fan = (getattr(self, "show_ai_chart_overlay", True) and active_tf == ana_tf)
            self._draw_candlestick_chart(
                active_df,
                self.cached_scenarios if show_fan else None,
                self.cached_mean_df if show_fan else None,
                self.cached_action, self.cached_sl, self.cached_tp,
                self.symbol_var.get().strip(), active_tf, has_forming_candle=True
            )

    def adjust_chart_horizontal_scale(self, factor: float):
        """Thu hẹp (factor > 1) hoặc giãn rộng (factor < 1) số nến hiển thị trên biểu đồ theo chiều ngang."""
        cur_xlim = self.ax.get_xlim()
        right_edge = cur_xlim[1]
        cur_span = max(cur_xlim[1] - cur_xlim[0], 12.0)
        total_x = max(self._chart_total_x, 80)
        new_span = float(np.clip(cur_span * factor, 18.0, total_x + 15.0))
        new_left = max(-2.5, right_edge - new_span)
        self.saved_xlim = (new_left, right_edge)
        self._chart_visible_bars = int(round(new_span))
        # Tự động căn lại trục giá Y vừa khít với vùng nến đang nhìn thấy
        auto_ylim = self._compute_visible_ylim(self.saved_xlim[0], self.saved_xlim[1])
        if auto_ylim is not None:
            self.saved_ylim = auto_ylim
            self.ax.set_ylim(self.saved_ylim)
        else:
            self.saved_ylim = self.ax.get_ylim()
        self.user_zoomed = True
        self.ax.set_xlim(self.saved_xlim)
        self._sync_xlim_dependents(self.saved_xlim)
        self._request_canvas_redraw()

    def _compute_visible_ylim(self, x0: float, x1: float):
        """
        Tính giới hạn trục giá Y (ylim) tự động ôm sát toàn bộ nến, râu nến và giá Live nằm trong vùng hiển thị [x0, x1].
        Đảm bảo giá và râu nến không bao giờ bị đâm xuyên qua trục Y hoặc bị mất khỏi màn hình khi thị trường biến động mạnh.
        """
        hist = self._chart_hist_df
        if hist is None or len(hist) == 0:
            return None
        n_hist = len(hist)
        i_start = max(0, min(n_hist - 1, int(math.floor(x0))))
        i_end = max(i_start, min(n_hist - 1, int(math.ceil(x1))))
        sub = hist.iloc[i_start:i_end + 1]
        if len(sub) == 0:
            return None

        lows = [float(sub["low"].min())]
        highs = [float(sub["high"].max())]

        # CHỈ ôm giá đóng cửa Live và nến cuối cùng khi vùng nhìn đang hiển thị phần đuôi live (x1 >= n_hist - 5)
        if x1 >= n_hist - 5:
            last_row = hist.iloc[-1]
            live_p = float(last_row["close"])
            lows.append(live_p)
            highs.append(live_p)
            lows.append(float(last_row["low"]))
            highs.append(float(last_row["high"]))

        # Nếu đang soi nến của lệnh đã đóng, luôn ôm trọn các mức giá Entry/Exit/SL/TP của lệnh đó
        sel_cd = getattr(self, "_active_viewing_closed_deal", None)
        if sel_cd is not None:
            for k_p in ("open_price", "close_price", "sl", "tp"):
                v_p = float(sel_cd.get(k_p) or 0.0)
                if v_p > 0:
                    lows.append(v_p)
                    highs.append(v_p)

        # Nếu có đường Chờ giá hồi (pending pullback), ôm cả mức giá chờ vào lệnh (khi đang ở vùng live)
        if getattr(self, "pending_pullback_order", None) is not None and x1 >= n_hist - 5:
            p = self.pending_pullback_order
            try:
                max_sl_p = float(p.get("effective_max_sl") or self.max_sl_price_var.get())
            except (ValueError, TypeError):
                max_sl_p = 5.0
            lim_entry = (p["sl_price"] + max_sl_p) if p["action"] == "BUY" else (p["sl_price"] - max_sl_p)
            lows.append(float(lim_entry))
            highs.append(float(lim_entry))

        # Nếu bật lớp phủ Dự Báo AI, luôn ôm cả mức SL/TP đề xuất của AI và kịch bản dự báo (chỉ khi ở vùng live x1 >= n_hist - 5)
        if getattr(self, "show_ai_chart_overlay", True) and x1 >= n_hist - 5:
            if self._chart_mean_df is not None:
                lows.append(float(self._chart_mean_df["low"].min()))
                highs.append(float(self._chart_mean_df["high"].max()))
            if getattr(self, "cached_sl", None) is not None:
                lows.append(float(self.cached_sl))
                highs.append(float(self.cached_sl))
            if getattr(self, "cached_tp", None) is not None:
                lows.append(float(self.cached_tp))
                highs.append(float(self.cached_tp))

        y_min = min(lows)
        y_max = max(highs)
        raw_span = max(y_max - y_min, 0.5)

        # Chừa lề an toàn 10% (tối thiểu 0.8 giá) ở cả đỉnh và đáy để nến không bao giờ chạm sát hay đâm xuyên biên
        pad = max(raw_span * 0.10, 0.8)
        target_y0 = y_min - pad
        target_y1 = y_max + pad

        return (target_y0, target_y1)

    def _sync_xlim_dependents(self, xlim):
        """Cập nhật mật độ nhãn thời gian trục X và vị trí nhãn Entry/SL/TP khi thu phóng hoặc kéo biểu đồ."""
        all_times = self._chart_all_times
        if all_times:
            i0 = max(0, int(math.floor(xlim[0])))
            i1 = min(len(all_times) - 1, int(math.ceil(xlim[1])))
            vis_count = max(1, i1 - i0 + 1)
            step = max(1, vis_count // 10)
            tick_indices = list(range(i0, i1 + 1, step))
            self.ax.set_xticks(tick_indices)
            self.ax.set_xticklabels([all_times[k] for k in tick_indices], color=TEXT_MAIN)

        if self._pos_text_artists:
            span = max(xlim[1] - xlim[0], 10.0)
            base_x = xlim[0] + span * 0.018
            col_step = span * 0.18
            for txt_artist, col_idx in self._pos_text_artists:
                try:
                    _, y_pos = txt_artist.get_position()
                    txt_artist.set_position((base_x + col_idx * col_step, y_pos))
                except Exception:
                    pass

    def _get_candle_countdown_str(self, candle_ts, tf_str: str, symbol: str = None) -> str:
        """Tính chuỗi thời gian đếm ngược (phút:giây) cho đến khi đóng cây nến hiện tại (chuẩn TradingView)."""
        if candle_ts is None:
            return "00:00"
        try:
            if hasattr(candle_ts, "timestamp"):
                c_ts = int(candle_ts.timestamp())
            elif isinstance(candle_ts, (int, float)):
                c_ts = int(candle_ts)
            else:
                c_ts = int(pd.to_datetime(candle_ts).timestamp())
        except Exception:
            return "00:00"

        tf_map = {
            "M1": 60,
            "M5": 300,
            "M15": 900,
            "M30": 1800,
            "H1": 3600,
            "H4": 14400,
            "D1": 86400,
        }
        dur = tf_map.get(str(tf_str).upper().strip(), 60)

        # Lấy mốc thời gian hiện tại (ưu tiên broker server time từ tick nếu có)
        now_ts = int(time.time())
        if MT5_AVAILABLE and symbol:
            try:
                with self._mt5_lock:
                    tick = mt5.symbol_info_tick(symbol)
                if tick and getattr(tick, "time", 0) > 0:
                    if abs(now_ts - tick.time) < 600:
                        now_ts = int(tick.time)
            except Exception:
                pass

        elapsed = max(0, now_ts - c_ts)
        if elapsed <= dur:
            rem = dur - elapsed
        else:
            rem = max(0, dur - (elapsed % dur))
            if rem == dur:
                rem = 0

        if rem >= 3600:
            h = rem // 3600
            m = (rem % 3600) // 60
            s = rem % 60
            return f"{h:02d}:{m:02d}:{s:02d}"
        else:
            m = rem // 60
            s = rem % 60
            return f"{m:02d}:{s:02d}"

    def _on_mouse_scroll(self, event):
        """Lăn chuột lên để mở rộng nến (xem rõ chi tiết), lăn chuột xuống để thu hẹp nến (xem bao quát nhiều nến hơn)."""
        if event.inaxes != self.ax:
            return
        scale_factor = 0.80 if event.button == "up" else 1.25
        self.adjust_chart_horizontal_scale(scale_factor)

    def _is_on_price_axis(self, event) -> bool:
        """Kiểm tra xem con trỏ chuột có đang nằm trên vùng Thanh Giá (cột số giá bên phải hoặc bên trái) không."""
        if event.x is None or event.y is None:
            return False
        bbox = self.ax.bbox
        if bbox.width <= 0 or bbox.height <= 0:
            return False
        if not (bbox.y0 - 15 <= event.y <= bbox.y1 + 15):
            return False
        # Bao phủ toàn bộ cột số giá bên phải (từ mép phải đồ thị ra hết lề phải) và cột số giá bên trái
        return (event.x >= bbox.x1 - 18) or (event.x <= bbox.x0 + 18)

    def _is_on_time_axis(self, event) -> bool:
        """Kiểm tra xem con trỏ chuột có đang nằm trên Thanh Thời Gian (trục X phía dưới biểu đồ) không."""
        if event.x is None or event.y is None:
            return False
        bbox = self.ax.bbox
        if bbox.width <= 0 or bbox.height <= 0:
            return False
        if not (bbox.x0 <= event.x <= bbox.x1):
            return False
        return 0 <= event.y <= bbox.y0 + 14

    def _on_mouse_press(self, event):
        # Nếu Menu đang mở mà click chuột vào biểu đồ (ngoài vùng Menu) -> Tự động thu Menu vào
        if self.left_menu_visible and self._drawer_anim_job is None:
            try:
                px = self.root.winfo_pointerx()
                lx = self.left_panel.winfo_rootx()
                lw = self.left_panel.winfo_width()
                if px > lx + lw:
                    self.toggle_left_menu()
            except Exception:
                self.toggle_left_menu()

        # Click chuột phải (Button 3) -> Hiển thị menu "Reset biểu đồ" như TradingView
        if event.button == 3:
            try:
                self.chart_context_menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
            finally:
                self.chart_context_menu.grab_release()
            return

        if event.dblclick:
            self.reset_chart_view()
            return

        if event.button == 1 and event.x is not None and event.y is not None:
            self._drag_start_px = (event.x, event.y)
            self._drag_start_xlim = self.ax.get_xlim()
            self._drag_start_ylim = self.ax.get_ylim()
            if self._is_on_price_axis(event):
                self._drag_mode = "Y_SCALE"
                self.canvas_widget.config(cursor="sb_v_double_arrow")
            elif self._is_on_time_axis(event):
                self._drag_mode = "X_SCALE"
                self.canvas_widget.config(cursor="sb_h_double_arrow")
            elif event.inaxes == self.ax:
                self._drag_mode = "PAN"
                self.canvas_widget.config(cursor="fleur")
            else:
                self._drag_start_px = None
                self._drag_mode = None

    def _on_mouse_release(self, event):
        self._drag_start_px = None
        self._drag_mode = None
        if self._is_on_price_axis(event):
            self.canvas_widget.config(cursor="sb_v_double_arrow")
        elif self._is_on_time_axis(event):
            self.canvas_widget.config(cursor="sb_h_double_arrow")
        else:
            self.canvas_widget.config(cursor="crosshair" if event.inaxes == self.ax else "")

    def _request_canvas_redraw(self, full: bool = True):
        """Gộp các yêu cầu vẽ lại biểu đồ trong khung 16ms (~60 FPS) kèm kỹ thuật Blitting giúp rê chuột Crosshair chỉ mất ~10ms (~100 FPS)."""
        if full:
            self._redraw_full_needed = True
        if self._redraw_pending:
            return
        self._redraw_pending = True
        self.root.after(14, self._flush_canvas_redraw)

    def _flush_canvas_redraw(self):
        self._redraw_pending = False
        full = self._redraw_full_needed or (self._chart_bg_bbox is None)
        self._redraw_full_needed = False
        try:
            cross_arts = (self.cross_vline, self.cross_hline, self.cross_price_annot, self.cross_time_annot)
            if full:
                vis_states = [art.get_visible() if art is not None else False for art in cross_arts]
                for art in cross_arts:
                    if art is not None:
                        art.set_visible(False)
                self.canvas.draw()
                self._chart_bg_bbox = self.canvas.copy_from_bbox(self.fig.bbox)
                any_vis = False
                for art, v in zip(cross_arts, vis_states):
                    if art is not None and v:
                        art.set_visible(True)
                        self.ax.draw_artist(art)
                        any_vis = True
                if any_vis:
                    self.canvas.blit(self.fig.bbox)
            else:
                self.canvas.restore_region(self._chart_bg_bbox)
                for art in cross_arts:
                    if art is not None and art.get_visible():
                        self.ax.draw_artist(art)
                self.canvas.blit(self.fig.bbox)
        except Exception:
            try:
                self.canvas.draw_idle()
            except Exception:
                pass

    def _hide_crosshair(self):
        self._last_hover_xy = None
        changed = False
        for art in (self.cross_vline, self.cross_hline, self.cross_price_annot, self.cross_time_annot):
            if art is not None and art.get_visible():
                art.set_visible(False)
                changed = True
        if changed:
            self._request_canvas_redraw(full=False)

    def _apply_crosshair_xy(self, snap_x: float, ydata: float):
        if self.cross_vline is None or self.cross_hline is None:
            return
        idx = int(round(snap_x))
        self.cross_vline.set_xdata([snap_x, snap_x])
        self.cross_vline.set_visible(True)
        self.cross_hline.set_ydata([ydata, ydata])
        self.cross_hline.set_visible(True)

        if self.cross_price_annot is not None:
            self.cross_price_annot.xy = (1.0, ydata)
            self.cross_price_annot.set_text(f" {ydata:,.2f} ")
            self.cross_price_annot.set_visible(True)

        hist = self._chart_hist_df
        mean_df = self._chart_mean_df
        n_hist = self._chart_n_hist
        pred_gap = int(getattr(self, "_chart_pred_gap", 0))
        pred_start = n_hist + pred_gap

        if hist is not None and 0 <= idx < n_hist:
            r = hist.iloc[idx]
            ts_full = r["timestamps"].strftime("%d/%m %H:%M")
            o, h, l, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
            chg = c - o
            if self.cross_time_annot is not None:
                self.cross_time_annot.xy = (snap_x, 0.0)
                self.cross_time_annot.set_text(f" {ts_full} ")
                self.cross_time_annot.set_visible(True)
            if hasattr(self, "ohlc_hover_lbl"):
                col = GREEN_UP if chg >= 0 else RED_DOWN
                self.ohlc_hover_lbl.config(
                    text=f"🕒 {ts_full}  |  O: {o:.2f}   H: {h:.2f}   L: {l:.2f}   C: {c:.2f} ({chg:+.2f}$)",
                    fg=col
                )
        elif getattr(self, "show_ai_chart_overlay", True) and mean_df is not None and pred_start <= idx < pred_start + len(mean_df):
            r = mean_df.iloc[idx - pred_start]
            ts_full = r["timestamps"].strftime("%d/%m %H:%M")
            o, h, l, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
            chg = c - o
            if self.cross_time_annot is not None:
                self.cross_time_annot.xy = (snap_x, 0.0)
                self.cross_time_annot.set_text(f" AI {ts_full} ")
                self.cross_time_annot.set_visible(True)
            if hasattr(self, "ohlc_hover_lbl"):
                self.ohlc_hover_lbl.config(
                    text=f"[Dự báo AI {ts_full}]  |  O: {o:.2f}   H: {h:.2f}   L: {l:.2f}   C: {c:.2f} ({chg:+.2f}$)",
                    fg=GOLD_ACCENT
                )
        else:
            if self.cross_time_annot is not None:
                self.cross_time_annot.set_visible(False)

    def _update_crosshair(self, event):
        """Hiển thị đường gióng chữ thập nét đứt + nhãn giá tối bên phải + thông số OHLC kiểu TradingView."""
        if event.inaxes != self.ax or event.xdata is None or event.ydata is None:
            self._hide_crosshair()
            return
        xdata = float(event.xdata)
        ydata = float(event.ydata)
        idx = int(round(xdata))
        snap_x = float(idx) if (0 <= idx < self._chart_total_x) else xdata
        self._last_hover_xy = (snap_x, ydata)
        self._apply_crosshair_xy(snap_x, ydata)
        self._request_canvas_redraw(full=False)

    def _on_mouse_motion(self, event):
        now_mono = time.monotonic()
        if (now_mono - getattr(self, "_last_mouse_motion_ts", 0.0)) < 0.020:
            return
        self._last_mouse_motion_ts = now_mono

        if self._drag_start_px is None:
            if self._is_on_price_axis(event):
                self.canvas_widget.config(cursor="sb_v_double_arrow")
                self._hide_crosshair()
            elif self._is_on_time_axis(event):
                self.canvas_widget.config(cursor="sb_h_double_arrow")
                self._hide_crosshair()
            elif event.inaxes == self.ax:
                self.canvas_widget.config(cursor="crosshair")
                self._update_crosshair(event)
            else:
                self.canvas_widget.config(cursor="")
                self._hide_crosshair()
            return

        if event.x is None or event.y is None:
            return

        dx_px = event.x - self._drag_start_px[0]
        dy_px = event.y - self._drag_start_px[1]

        bbox = self.ax.bbox
        if bbox.width <= 0 or bbox.height <= 0:
            return

        if self._drag_mode == "Y_SCALE":
            y0, y1 = self._drag_start_ylim
            y_center = (y0 + y1) * 0.5
            y_span = max(y1 - y0, 0.2)
            scale_factor = float(np.clip(1.006 ** (-dy_px), 0.15, 8.0))
            new_half = (y_span * scale_factor) * 0.5
            self.saved_xlim = self._drag_start_xlim
            self.saved_ylim = (y_center - new_half, y_center + new_half)
            self.user_zoomed = True
            self.ax.set_ylim(self.saved_ylim)
            self._request_canvas_redraw()
        elif self._drag_mode == "X_SCALE":
            x0, x1 = self._drag_start_xlim
            x_span = max(x1 - x0, 12.0)
            total_x = max(self._chart_total_x, 80)
            scale_factor = float(np.clip(1.006 ** (-dx_px), 0.2, 6.0))
            new_span = float(np.clip(x_span * scale_factor, 16.0, total_x + 20.0))
            new_left = max(-3.0, x1 - new_span)
            self.saved_xlim = (new_left, x1)
            self.saved_ylim = self._drag_start_ylim
            self.user_zoomed = True
            self.ax.set_xlim(self.saved_xlim)
            self._sync_xlim_dependents(self.saved_xlim)
            self._request_canvas_redraw()
        elif self._drag_mode == "PAN":
            x_span = self._drag_start_xlim[1] - self._drag_start_xlim[0]
            y_span = self._drag_start_ylim[1] - self._drag_start_ylim[0]

            dx_data = -(dx_px / bbox.width) * x_span
            dy_data = -(dy_px / bbox.height) * y_span

            total_x = max(self._chart_total_x, 80)
            new_x0 = float(np.clip(self._drag_start_xlim[0] + dx_data, -10.0, total_x - 5.0))
            new_x1 = new_x0 + x_span
            self.saved_xlim = (new_x0, new_x1)
            self.saved_ylim = (self._drag_start_ylim[0] + dy_data, self._drag_start_ylim[1] + dy_data)
            self.user_zoomed = True

            self.ax.set_xlim(self.saved_xlim)
            self.ax.set_ylim(self.saved_ylim)
            self._sync_xlim_dependents(self.saved_xlim)
            self._request_canvas_redraw()

    def _hide_hover_note(self, event=None):
        """Ẩn ghi chú nổi khi chuột rời khỏi dấu ?."""
        if hasattr(self, "_hover_note_timer") and self._hover_note_timer:
            try:
                self.root.after_cancel(self._hover_note_timer)
            except Exception:
                pass
            self._hover_note_timer = None

        if hasattr(self, "_active_hover_note") and self._active_hover_note:
            try:
                self._active_hover_note.destroy()
            except Exception:
                pass
            self._active_hover_note = None

    def _show_hover_note(self, widget, help_key):
        """Lên lịch hiển thị ghi chú nổi khi rê chuột vào dấu ?."""
        self._hide_hover_note()
        self._hover_note_timer = self.root.after(80, lambda: self._render_hover_note(widget, help_key))

    def _render_hover_note(self, widget, help_key):
        """Hiển thị bảng ghi chú nổi (như Excel/Google Sheets) chỉ với văn bản thuần túy, không dùng icon/ký tự."""
        guide = PARAM_HELP_GUIDE.get(help_key)
        if not guide or not widget.winfo_exists():
            return

        try:
            note = tk.Toplevel(self.root)
            note.wm_overrideredirect(True)
            note.configure(bg="#182232")
            note.attributes("-topmost", True)

            # Khung ghi chú viền mỏng dạng Excel / Google Sheets
            card = tk.Frame(
                note, bg="#182232", padx=10, pady=8,
                highlightthickness=1, highlightbackground="#3b4d66",
                relief=tk.SOLID, bd=1
            )
            card.pack(fill=tk.BOTH, expand=True)

            # Tiêu đề thông số - văn bản thuần túy không icon
            t_lbl = tk.Label(
                card, text=guide.get("title", help_key),
                bg="#182232", fg="#38bdf8",
                font=("Segoe UI", 9, "bold"), anchor="w"
            )
            t_lbl.pack(fill=tk.X, pady=(0, 4))

            # Phần 1: Ý nghĩa thông số
            m_hdr = tk.Label(
                card, text="Ý nghĩa:",
                bg="#182232", fg="#facc15",
                font=("Segoe UI", 8, "bold"), anchor="w"
            )
            m_hdr.pack(fill=tk.X, pady=(2, 1))

            m_txt = tk.Label(
                card, text=guide.get("what_is_it", ""),
                bg="#182232", fg="#e2e8f0",
                font=("Segoe UI", 8), justify="left", anchor="w",
                wraplength=380
            )
            m_txt.pack(fill=tk.X, pady=(0, 6))

            # Phần 2: Cách cài đặt hiệu quả
            s_hdr = tk.Label(
                card, text="Cách cài đặt hiệu quả:",
                bg="#182232", fg="#4ade80",
                font=("Segoe UI", 8, "bold"), anchor="w"
            )
            s_hdr.pack(fill=tk.X, pady=(2, 1))

            s_txt = tk.Label(
                card, text=guide.get("how_to_set", ""),
                bg="#182232", fg="#cbd5e1",
                font=("Segoe UI", 8), justify="left", anchor="w",
                wraplength=380
            )
            s_txt.pack(fill=tk.X)

            # Tự động tính vị trí hiển thị nổi cạnh dấu [?]
            note.update_idletasks()
            nw = note.winfo_reqwidth()
            nh = note.winfo_reqheight()

            bx = widget.winfo_rootx() + widget.winfo_width() + 8
            by = widget.winfo_rooty() - 4

            sw = widget.winfo_screenwidth()
            sh = widget.winfo_screenheight()

            if bx + nw > sw - 12:
                bx = max(10, widget.winfo_rootx() - nw - 8)
            if by + nh > sh - 35:
                by = max(10, sh - nh - 40)

            note.geometry(f"{nw}x{nh}+{bx}+{by}")
            self._active_hover_note = note

            # Đóng ghi chú khi rê chuột ra ngoài hoặc click
            note.bind("<Leave>", lambda e: self._hide_hover_note())
            note.bind("<ButtonPress>", lambda e: self._hide_hover_note())
        except Exception:
            pass

    def _create_help_badge(self, parent, help_key):
        """Tạo biểu tượng chấm hỏi tròn [?] hiển thị ghi chú Excel/Google Sheet thuần văn bản khi rê chuột."""
        c = tk.Canvas(parent, width=16, height=16, bg=PANEL_BG, highlightthickness=0, cursor="hand2")
        circle = c.create_oval(1, 1, 15, 15, fill="#1c2438", outline="#3b82f6", width=1)
        txt = c.create_text(8, 8, text="?", fill="#38bdf8", font=("Segoe UI", 7, "bold"))

        def _on_enter(e):
            c.itemconfig(circle, fill="#2a3a5c", outline="#eab308")
            c.itemconfig(txt, fill="#facc15")
            self._show_hover_note(c, help_key)

        def _on_leave(e):
            c.itemconfig(circle, fill="#1c2438", outline="#3b82f6")
            c.itemconfig(txt, fill="#38bdf8")
            self._hide_hover_note()

        c.bind("<Enter>", _on_enter)
        c.bind("<Leave>", _on_leave)
        c.bind("<Button-1>", lambda e: self._hide_hover_note())
        c.pack(side=tk.LEFT, padx=(4, 0))
        return c

    # ── KIỂM TRA THÔNG SỐ NHẬP (chỉ giao diện: tô màu ô, chặn nút Lưu / Áp dụng; luật ở trading_modules/param_rules.py) ──
    PARAM_ERR_BG = "#7f1d1d"   # ô sai: nền đỏ
    PARAM_WARN_BG = "#78350f"  # ô cần lưu ý: nền cam
    PARAM_OK_BG = "#10141d"    # màu thường của ô nhập

    def _install_param_validation(self):
        """Mọi ô của Menu: gõ / tích / chọn là kiểm tra lại (gộp vào 1 lượt rảnh của Tk, không chạy theo từng phím)."""
        try:
            smap = self._get_settings_vars_map()
        except Exception:
            return  # không bao giờ làm hỏng việc dựng giao diện
        for var in smap.values():
            try:
                var.trace_add("write", lambda *_a: self._schedule_param_validation())
            except Exception:
                pass
        self._schedule_param_validation()

    def _schedule_param_validation(self, slot: str = "menu", fn=None):
        pending = self.__dict__.setdefault("_param_val_pending", set())
        if slot in pending:
            return
        pending.add(slot)

        def _run():
            pending.discard(slot)
            try:
                (fn or self._validate_menu_settings)()
            except Exception:
                pass
        try:
            self.root.after_idle(_run)
        except Exception:
            pending.discard(slot)

    def _paint_param_issues(self, issues, widget_of: dict):
        """Tô ô nhập: lỗi = nền đỏ, cần lưu ý = nền cam, hợp lệ = màu thường. widget_of: khóa -> ô nhập (tk.Entry).
        Kèm dòng báo ngay DƯỚI ô (giá trị đang nhập + mức hợp lệ) để người dùng thấy ngay chỗ sai."""
        rank = {"error": 2, "warn": 1}
        level_of = {}
        items_of = {}
        for it in issues or []:
            for k in it.get("keys", ()):
                if rank.get(it.get("level"), 0) > rank.get(level_of.get(k), 0):
                    level_of[k] = it.get("level")
                items_of.setdefault(k, []).append(it)
        best = {}  # nhiều khóa dùng chung 1 ô (fast_sl_cooldown_mins) -> lấy mức nặng nhất
        for k, w in (widget_of or {}).items():
            if not isinstance(w, tk.Widget):
                continue
            lv = level_of.get(k)
            cur = best.get(id(w))
            if cur is None:
                best[id(w)] = [w, lv, list(items_of.get(k, []))]
                continue
            if rank.get(lv, 0) > rank.get(cur[1], 0):
                cur[1] = lv
            cur[2].extend(it for it in items_of.get(k, []) if it not in cur[2])
        for w, lv, its in best.values():
            try:
                if not isinstance(w, tk.Entry):
                    pass  # ô chọn / ô tích: không tô nền, chỉ hiện dòng báo dưới ô
                elif lv == "error":
                    w.config(bg=self.PARAM_ERR_BG, fg="#ffffff")
                elif lv == "warn":
                    w.config(bg=self.PARAM_WARN_BG, fg="#ffffff")
                else:
                    w.config(bg=self.PARAM_OK_BG, fg=TEXT_MAIN)
            except Exception:
                pass
            self._show_param_field_hint(w, lv, its)

    def _show_param_field_hint(self, w, level, items):
        """Dòng báo ngay dưới ô nhập sai / cần lưu ý (chỉ giao diện); ô hợp lệ thì ẩn. Không bao giờ làm hỏng giao diện."""
        try:
            hints = self.__dict__.setdefault("_param_hint_lbls", {})
            lbl = hints.get(str(w))
            if level not in ("error", "warn"):
                if lbl is not None and lbl.winfo_exists() and lbl.winfo_manager():
                    lbl.pack_forget()
                return
            row = w.master
            if row is None or row.winfo_manager() != "pack":
                return  # ô không nằm trong dòng xếp bằng pack: chỉ tô màu + báo dưới nút Lưu như cũ
            errs = [it for it in items if it.get("level") == "error"]
            shown = (errs or list(items))[:2]
            text = "\n".join(("⛔ " if it.get("level") == "error" else "⚠️ ") + str(it.get("msg", "")) for it in shown)
            if lbl is None or not lbl.winfo_exists():
                lbl = tk.Label(row.master, text="", bg=row.cget("bg"), font=("Segoe UI", 7),
                               justify=tk.LEFT, anchor="w", wraplength=280)
                hints[str(w)] = lbl
            lbl.config(text=text, fg=("#ff6b6b" if errs else "#fbbf24"))
            if not lbl.winfo_manager():
                lbl.pack(fill=tk.X, after=row, padx=row.pack_info().get("padx", 0), pady=(0, 2))
        except Exception:
            pass

    def _show_param_issue_text(self, lbl, issues, max_lines: int = 4):
        """Ghi lý do lên nhãn lbl (lỗi trước, lưu ý sau); không có vấn đề thì ẩn nhãn."""
        if lbl is None:
            return
        try:
            errs = [it for it in (issues or []) if it.get("level") == "error"]
            ordered = errs + [it for it in (issues or []) if it.get("level") != "error"]
            if not ordered:
                if lbl.winfo_manager():
                    lbl.pack_forget()
                return
            lines = [("⛔ " if it.get("level") == "error" else "⚠️ ") + str(it.get("msg", "")) for it in ordered[:max_lines]]
            if len(ordered) > max_lines:
                lines.append(f"... và {len(ordered) - max_lines} mục khác")
            lbl.config(text="\n".join(lines), fg=("#ff6b6b" if errs else "#fbbf24"))
            if not lbl.winfo_manager():
                lbl.pack(fill=tk.X, pady=(3, 0))
        except Exception:
            pass

    def _validate_menu_settings(self) -> list:
        """Kiểm tra thông số đang nhập trên Menu: tô màu ô, ghi lý do dưới nút Lưu. Trả danh sách câu báo LỖI (chặn nút Lưu)."""
        smap = self._get_settings_vars_map()
        cfg = {}
        for k, v in smap.items():
            try:
                cfg[k] = v.get()
            except Exception:
                cfg[k] = ""
        issues = validate_param_values(cfg)
        if threading.current_thread() is threading.main_thread():
            rows = self.__dict__.get("_param_rows", {})
            self._paint_param_issues(issues, {k: rows[str(v)][1] for k, v in smap.items() if str(v) in rows})
            self._show_param_issue_text(getattr(self, "param_issue_lbl", None), issues)
        return [it["msg"] for it in issues if it["level"] == "error"]

    def _validate_bt_form(self, pk: str) -> list:
        """Kiểm tra cấu hình Backtest đang sửa: tô màu ô trong bảng Thông số Backtest. Trả danh sách câu báo LỖI."""
        shown = set(self.__dict__.get("_bt_form_vars", {}))
        issues = [it for it in validate_param_values(self._get_bt_cfg(pk)) if shown & set(it.get("keys", ()))]
        self._paint_param_issues(issues, dict(self.__dict__.get("_bt_form_widgets", {})))
        self._show_param_issue_text(getattr(self, "_bt_issue_lbl", None), issues, max_lines=5)
        return [it["msg"] for it in issues if it["level"] == "error"]

    def _bind_param_children(self, parent_var, child_vars, numeric: bool = False):
        """Cây thông số (chỉ giao diện): thông số con thụt vào trong; khi thông số cha tắt
        (bỏ tích, hoặc = 0 nếu numeric) thì các con bị làm mờ và khóa. Không đổi giá trị đã lưu."""
        rows = self.__dict__.get("_param_rows", {})
        kids = [rows[str(v)] for v in child_vars if str(v) in rows]
        for row, _w in kids:
            try:
                row.pack_configure(padx=(18, 0))
            except Exception:
                pass

        def _labels(w):
            out = []
            for c in w.winfo_children():
                if isinstance(c, tk.Label):
                    out.append(c)
                out.extend(_labels(c))
            return out

        def _is_on():
            try:
                v = parent_var.get()
                return (float(str(v).strip().replace(",", ".") or 0) > 0) if numeric else bool(v)
            except Exception:
                return True  # đang gõ dở giá trị: không khóa con

        def _sync(*_a):
            parent_on = _is_on()
            on = parent_on and not getattr(self, "_params_locked", False)
            for row, w in kids:
                try:
                    if isinstance(w, ttk.Combobox):
                        w.config(state=("readonly" if on else "disabled"))
                    elif isinstance(w, tk.Entry):
                        w.config(state=(tk.NORMAL if on else tk.DISABLED),
                                 disabledbackground="#1a1f2b", disabledforeground="#5b6475")
                    else:
                        w.config(state=(tk.NORMAL if on else tk.DISABLED))
                    for lbl in _labels(row):
                        lbl.config(fg=(TEXT_MAIN if parent_on else "#5b6475"))
                except Exception:
                    pass

        try:
            parent_var.trace_add("write", _sync)
        except Exception:
            pass
        self.__dict__.setdefault("_param_syncs", []).append(_sync)
        _sync()

    def _add_checkbutton(self, parent, text, var, help_key=None, fg=TEXT_MAIN):
        row = tk.Frame(parent, bg=PANEL_BG)
        row.pack(fill=tk.X, pady=2)
        cb = tk.Checkbutton(
            row, text=text, variable=var, bg=PANEL_BG, fg=fg, selectcolor=BG_DARK,
            activebackground=PANEL_BG, font=("Segoe UI", 8, "bold"), anchor="w",
            command=self.save_user_settings
        )
        cb.pack(side=tk.LEFT)
        if help_key and help_key in PARAM_HELP_GUIDE:
            self._create_help_badge(row, help_key)
        self.__dict__.setdefault("_param_rows", {})[str(var)] = (row, cb)
        self.__dict__.setdefault("_param_meta", {})[str(var)] = {"type": "bool", "label": text, "help_key": help_key}

        # Ô được tích: chữ xanh lá + nền dải nhẹ để nổi bật; bỏ tích: trở về màu thường
        def _sync_cb_style(*_a):
            try:
                on = bool(var.get())
                row_bg = "#123524" if on else PANEL_BG
                row.config(bg=row_bg)
                cb.config(fg=(GREEN_UP if on else fg), bg=row_bg, activebackground=row_bg,
                          activeforeground=(GREEN_UP if on else fg), disabledforeground=(GREEN_UP if on else fg))
            except Exception:
                pass

        try:
            var.trace_add("write", _sync_cb_style)
            _sync_cb_style()
        except Exception:
            pass
        return cb

    def _add_labeled_entry(self, parent, label_text, default_val, show=None, entry_width=10, help_key=None):
        row = tk.Frame(parent, bg=PANEL_BG)
        row.pack(fill=tk.X, pady=2)
        lbl_frame = tk.Frame(row, bg=PANEL_BG)
        lbl_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(lbl_frame, text=label_text, bg=PANEL_BG, fg=TEXT_MAIN, font=("Segoe UI", 8), anchor="w").pack(side=tk.LEFT)
        if help_key and help_key in PARAM_HELP_GUIDE:
            self._create_help_badge(lbl_frame, help_key)

        var = tk.StringVar(value=default_val)
        entry = tk.Entry(
            row, textvariable=var, bg="#10141d", fg=TEXT_MAIN, insertbackground=TEXT_MAIN,
            relief=tk.FLAT, font=("Consolas", 8), show=show, width=entry_width,
            justify="center" if entry_width <= 12 else "left"
        )
        entry.pack(side=tk.RIGHT)
        entry.bind("<FocusOut>", lambda e: self.save_user_settings())
        entry.bind("<Return>", lambda e: self.save_user_settings())
        self.__dict__.setdefault("_param_rows", {})[str(var)] = (row, entry)
        self.__dict__.setdefault("_param_meta", {})[str(var)] = {"type": "entry", "label": label_text, "help_key": help_key}
        return var

    def _add_coded_combo(self, parent, label_text, code_labels: dict, default_code: str, help_key=None):
        """Ô chọn hiển thị nhãn tiếng Việt, còn biến trả về giữ MÃ lưu cấu hình (code đọc mã như cũ).
        code_labels: {mã: nhãn tiếng Việt}. Giá trị lạ (không có trong bảng) được giữ nguyên."""
        code_var = tk.StringVar(value=default_code)
        label_of = dict(code_labels)
        code_of = {v: k for k, v in label_of.items()}
        label_var = self._add_labeled_combo(parent, label_text, list(label_of.values()),
                                            label_of.get(default_code, default_code), help_key=help_key)
        self.__dict__.setdefault("_param_meta", {})[str(code_var)] = {"type": "coded", "label": label_text, "code_labels": dict(label_of), "help_key": help_key}
        rows = self.__dict__.setdefault("_param_rows", {})
        if str(label_var) in rows:
            rows[str(code_var)] = rows[str(label_var)]  # cây cha/con tra theo biến mã
        busy = {"on": False}

        def _from_label(*_a):
            if busy["on"]:
                return
            busy["on"] = True
            try:
                lbl = label_var.get()
                code_var.set(code_of.get(lbl, lbl))
            finally:
                busy["on"] = False

        def _from_code(*_a):
            if busy["on"]:
                return
            busy["on"] = True
            try:
                c = str(code_var.get()).strip()
                label_var.set(label_of.get(c, label_of.get(c.lower(), c)))
            finally:
                busy["on"] = False

        label_var.trace_add("write", _from_label)
        code_var.trace_add("write", _from_code)
        return code_var

    def _add_labeled_combo(self, parent, label_text, values, default_val, combo_width=None, help_key=None):
        row = tk.Frame(parent, bg=PANEL_BG)
        row.pack(fill=tk.X, pady=2)
        lbl_frame = tk.Frame(row, bg=PANEL_BG)
        lbl_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(lbl_frame, text=label_text, bg=PANEL_BG, fg=TEXT_MAIN, font=("Segoe UI", 8), anchor="w").pack(side=tk.LEFT)
        if help_key and help_key in PARAM_HELP_GUIDE:
            self._create_help_badge(lbl_frame, help_key)

        var = tk.StringVar(value=default_val)

        # Tự động tính toán bề rộng combobox nếu có chuỗi dài để không bị che khuất chữ
        max_len = max((len(str(v)) for v in values), default=15) if values else 15
        if combo_width is None:
            calc_w = max(15, min(max_len + 1, 28))
        else:
            calc_w = combo_width

        cb = ttk.Combobox(row, textvariable=var, values=values, state="readonly", font=("Segoe UI", 8), width=calc_w)
        cb.pack(side=tk.RIGHT)
        cb.bind("<<ComboboxSelected>>", lambda e: self.save_user_settings())
        self.__dict__.setdefault("_param_rows", {})[str(var)] = (row, cb)
        self.__dict__.setdefault("_param_meta", {})[str(var)] = {"type": "combo", "label": label_text, "values": list(values or []), "help_key": help_key}

        # Mở rộng danh sách xổ xuống (Popdown) tương ứng với chuỗi dài nhất để đọc trọn vẹn 100% nội dung
        if max_len > 18:
            def _expand_popdown(event=None):
                def _do_resize():
                    try:
                        popdown = cb.tk.eval(f"ttk::combobox::PopdownWindow {cb}")
                        target_w = max(cb.winfo_width(), min(max_len * 8 + 35, 320))
                        cb.tk.eval(f"wm geometry {popdown} {target_w}x[winfo height {popdown}]")
                    except Exception:
                        pass
                cb.after(10, _do_resize)
            cb.bind("<ButtonPress-1>", _expand_popdown, add="+")
            cb.bind("<Down>", _expand_popdown, add="+")

        return var

    def _style_axes(self):
        self.ax.set_facecolor("#131822")
        self.ax.yaxis.set_ticks_position("both")
        self.ax.tick_params(axis="y", labelright=True, labelleft=True, colors=TEXT_MAIN, labelsize=8)
        self.ax.tick_params(axis="x", colors=TEXT_MAIN, labelsize=8)
        for spine in self.ax.spines.values():
            spine.set_color("#2b3548")
        self.ax.grid(True, linestyle="--", alpha=0.2, color="#4a5568")

    # =========================================================================
    # HỆ THỐNG GHI LOG RA FILE (NHẬT KÝ HOẠT ĐỘNG & KIỂM TRA LỖI APP / MT5)
    # =========================================================================
    def _get_daily_log_path(self) -> str:
        date_str = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(LOG_DIR, f"bot_activity_{date_str}.log")

    def _init_file_logger(self):
        """Khởi tạo thư mục lưu Log trong AppData, tự động dọn log cũ > 14 ngày và bắt mọi lỗi ngầm của Python/Tkinter."""
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            # Dọn dẹp các file log cũ quá 14 ngày hoặc cắt bớt nếu file lỗi vượt quá 5MB
            cutoff_ts = time.time() - 14 * 86400
            for fname in os.listdir(LOG_DIR):
                fpath = os.path.join(LOG_DIR, fname)
                if os.path.isfile(fpath) and fname.startswith("bot_activity_") and fname.endswith(".log"):
                    if os.path.getmtime(fpath) < cutoff_ts:
                        try:
                            os.remove(fpath)
                        except Exception:
                            pass
            if os.path.exists(ERROR_LOG_FILE) and os.path.getsize(ERROR_LOG_FILE) > 5 * 1024 * 1024:
                with open(ERROR_LOG_FILE, "rb") as f:
                    f.seek(-2 * 1024 * 1024, os.SEEK_END)
                    tail_bytes = f.read()
                with open(ERROR_LOG_FILE, "wb") as f:
                    f.write(tail_bytes)
        except Exception:
            pass

        def _tk_exception_handler(exc_type, exc_val, exc_tb):
            tb_str = "".join(traceback.format_exception(exc_type, exc_val, exc_tb))
            self._write_file_log(f"[TKINTER_ERROR] {exc_val}\n{tb_str}", is_error=True)

        def _thread_exception_handler(args):
            tb_str = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
            t_name = getattr(args.thread, "name", "BackgroundThread")
            self._write_file_log(f"[THREAD_ERROR - {t_name}] {args.exc_value}\n{tb_str}", is_error=True)

        self.root.report_callback_exception = _tk_exception_handler
        sys.excepthook = _tk_exception_handler
        threading.excepthook = _thread_exception_handler
        self._write_file_log("================================================================================")
        self._write_file_log("🚀 KHỞI ĐỘNG HỆ THỐNG SCALPING ĐA LUỒNG XAUUSD & BTCUSD (MT5 EXNESS)")

    def _write_trace(self, pk: str, record: dict):
        """Ghi 1 dòng JSON vào trace_YYYY-MM-DD.jsonl (chỉ gọi khi bật debug_trace). Lỗi ghi không ảnh hưởng giao dịch."""
        try:
            base = getattr(self, "_trace_dir", None) or os.path.join(LOG_DIR, "trace")
            now = datetime.now()
            rec = {"ts": now.strftime("%Y-%m-%d %H:%M:%S"), "run": getattr(self, "_trace_run", "live"), "profile": pk, **record}
            line = json.dumps(rec, ensure_ascii=False, default=str)
            with self._log_file_lock:
                os.makedirs(base, exist_ok=True)
                with open(os.path.join(base, f"trace_{now.strftime('%Y-%m-%d')}.jsonl"), "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:
            pass

    def _write_file_log(self, text: str, is_error: bool = False):
        """Ghi nhật ký hoạt động và lỗi xuống ổ cứng an toàn đa luồng (Thread-Safe)."""
        ts_ms = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        clean_txt = str(text).rstrip("\r\n")
        log_entry = f"[{ts_ms}] {clean_txt}\n"
        try:
            with self._log_file_lock:
                os.makedirs(LOG_DIR, exist_ok=True)
                daily_path = self._get_daily_log_path()
                with open(daily_path, "a", encoding="utf-8") as f:
                    f.write(log_entry)
                if is_error:
                    with open(ERROR_LOG_FILE, "a", encoding="utf-8") as f_err:
                        f_err.write(log_entry)
        except Exception:
            pass

    def log_error(self, context: str, err: Exception = None, extra_info: str = "", show_in_ui: bool = True):
        """Ghi chi tiết lỗi của App hoặc lỗi giao tiếp MT5 (kèm Traceback & mt5.last_error) vào file app_error.log."""
        mt5_err_str = ""
        if MT5_AVAILABLE:
            try:
                with self._mt5_lock:
                    mt5_err_str = f" | MT5 last_error={mt5.last_error()}"
            except Exception:
                pass
        err_msg = f"❌ [LỖI - {context}] {err if err is not None else ''}{ (' | ' + extra_info) if extra_info else ''}{mt5_err_str}"
        tb_str = traceback.format_exc() if err is not None else ""
        full_err_log = f"{err_msg}\n{tb_str}" if (tb_str and "NoneType: None" not in tb_str) else err_msg
        self._write_file_log(full_err_log, is_error=True)
        if show_in_ui:
            ts = datetime.now().strftime("%H:%M:%S")
            self._ui_after(0, self._append_log, f"[{ts}] {err_msg}\n")

    def open_log_folder(self):
        """Mở trực tiếp thư mục chứa các file Log của App trên Windows Explorer."""
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            daily_path = self._get_daily_log_path()
            if not os.path.exists(daily_path):
                self._write_file_log("Mở thư mục kiểm tra Log.")
            if hasattr(os, "startfile"):
                os.startfile(LOG_DIR)
        except Exception as e:
            self.log_error("OPEN_LOG_FOLDER", e)

    def open_error_log_file(self):
        """Mở trực tiếp file Log lỗi (hoặc file Log hoạt động trong ngày nếu chưa có lỗi nào) bằng trình xem văn bản."""
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            target_file = ERROR_LOG_FILE if (os.path.exists(ERROR_LOG_FILE) and os.path.getsize(ERROR_LOG_FILE) > 0) else self._get_daily_log_path()
            if not os.path.exists(target_file):
                self._write_file_log("Khởi tạo file nhật ký kiểm tra.")
            if hasattr(os, "startfile"):
                os.startfile(target_file)
        except Exception as e:
            self.log_error("OPEN_ERROR_LOG", e)

    def log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        msg_str = str(msg)
        is_err = any(k in msg_str for k in ("❌", "⚠️", "Lỗi", "từ chối", "Không đóng được", "ERROR"))
        self._write_file_log(msg_str, is_error=is_err)
        self._ui_after(0, self._append_log, line)

    def _append_log(self, line: str):
        try:
            log_widgets = list(getattr(self, "log_text_by_profile", {}).values())
            if not log_widgets and hasattr(self, "log_text"):
                log_widgets = [self.log_text]
            for w in log_widgets:
                if w is not None and w.winfo_exists():
                    w.insert("1.0", line)
                    w.delete("150.0", tk.END)
        except Exception:
            pass

    # =========================================================================
    # LUỒNG REAL-TIME 1 GIÂY: CẬP NHẬT GIÁ, ĐẾM NGƯỢC, QUẢN LÝ LỆNH, CHART LIVE
    # =========================================================================
    def _realtime_tick_loop(self):
        if getattr(self, "_app_closing", False):
            return
        # Bảo vệ Luồng Giao Diện Chính (Non-Blocking Lock): Nếu luồng ngầm đang tải dữ liệu MT5 nặng, bỏ qua nhịp này để UI luôn mượt 60 FPS
        if not self._mt5_lock.acquire(blocking=False):
            self.__dict__.setdefault("_pb_cnt", {"busy": 0, "exc": 0})["busy"] += 1
            if not getattr(self, "_app_closing", False):
                self.root.after(1000, self._realtime_tick_loop)
            return
        self._mt5_lock.release()
        try:
            with self._mt5_lock:
                mt5_ok = bool(MT5_AVAILABLE and (mt5.terminal_info() is not None or self._mt5_initialize()))
            if mt5_ok:
                active_pk = self.active_profile_key
                symbol = self.symbol_var.get().strip()
                tf_str = self.tf_var.get().strip()
                ana_tf_str = self.analysis_tf_var.get().strip() or tf_str
                with self._mt5_lock:
                    mt5.symbol_select(symbol, True)
                    acc = mt5.account_info()
                    tick = mt5.symbol_info_tick(symbol)

                self.heartbeat_toggle = not self.heartbeat_toggle
                dot = "🟢" if self.heartbeat_toggle else "🔵"
                now_str = datetime.now().strftime("%H:%M:%S")

                tf_sec_map = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600}
                ana_sec = tf_sec_map.get(ana_tf_str, 300)

                if tick is not None:
                    spread = tick.ask - tick.bid
                    rem_sec = ana_sec - (int(tick.time) % ana_sec)
                    mins, secs = divmod(rem_sec, 60)
                    scan_state = f"CANH VÙNG {tf_str}" if self.auto_loop_running else "CHỜ"
                    tf_clock_tag = f"AI {ana_tf_str}" if ana_tf_str != tf_str else f"Nến {tf_str}"
                    self.live_clock_lbl.config(
                        text=f"{dot} {now_str} | [{active_pk}] Bid:{tick.bid:.2f} Ask:{tick.ask:.2f} (Spr:{spread:.2f}$) | ⏳{tf_clock_tag}: {mins:02d}p:{secs:02d}s [{scan_state}]"
                    )

                if acc is not None:
                    algo_on = self._algo_trading_on(acc)
                    algo_str = "Algo:BẬT" if algo_on else "Algo:TẮT (bật nút Algo Trading trên MT5!)"
                    self.mt5_status_lbl.config(
                        text=f"TK: {acc.login} ({'THẬT' if getattr(acc, 'trade_mode', 0) == 2 else 'DEMO'}) | Dư: {acc.balance:.2f}$ | Eq: {acc.equity:.2f}$ | {algo_str}",
                        fg=GREEN_UP if algo_on else RED_DOWN
                    )

                pos_count, total_profit = self._update_open_positions_and_manage(symbol, profile_key=active_pk)
                self._check_daily_profit_loss_limits(total_profit, tick, profile_key=active_pk)
                if tick is not None:
                    self._check_pending_pullback_order(symbol, tick, profile_key=active_pk)
                    self._pb_heartbeat(active_pk, symbol, tick)
                self._update_left_quick_info(symbol, tf_str, tick, pos_count, total_profit)
                self._update_bot_status_bar(active_pk)
                self._sync_self_to_active_ctx()

                # Quản lý song song Tab còn lại ở chế độ chạy ngầm (Auto-Hòa vốn, Trailing Stop, Thoát đảo chiều, Chờ giá hồi, Giới hạn Lãi/Lỗ ngày)
                bg_pk = "BTCUSD" if active_pk == "XAUUSD" else "XAUUSD"
                bg_sym = str(self.profile_settings.get(bg_pk, {}).get("symbol", "")).strip()
                if bg_sym:
                    with self._mt5_lock:
                        mt5.symbol_select(bg_sym, True)
                        bg_tick = mt5.symbol_info_tick(bg_sym)
                    _, bg_profit = self._update_open_positions_and_manage(bg_sym, profile_key=bg_pk)
                    self._check_daily_profit_loss_limits(bg_profit, bg_tick, profile_key=bg_pk)
                    if bg_tick is not None:
                        self._check_pending_pullback_order(bg_sym, bg_tick, profile_key=bg_pk)
                        self._pb_heartbeat(bg_pk, bg_sym, bg_tick)

                # Đang xem màn hình Backtest (biểu đồ live bị ẩn): không vẽ biểu đồ live để luồng giao diện rảnh cho biểu đồ Backtest.
                # Chỉ là vẽ; quản lý lệnh live ở trên vẫn chạy mỗi giây. Quay lại tab live -> _on_tab_changed vẽ lại ngay.
                if self.realtime_chart_var.get() and self.heartbeat_toggle and not self.is_analyzing and self._drag_start_px is None \
                        and not self._bt_workspace_shown():
                    self._refresh_realtime_chart(symbol, tf_str)
        except Exception as e:
            self.log_error("REALTIME_TICK_LOOP", e, show_in_ui=False)
            self._pb_note_exception(e)
        finally:
            if not getattr(self, "_app_closing", False):
                self.root.after(1000, self._realtime_tick_loop)

    def _get_today_deals_stats(self, symbol: str = None, tick=None, profile_key: str = None) -> tuple:
        """Tính tổng Lãi/Lỗ đã chốt (Realized PnL) và chuỗi lệnh thua liên tiếp trong ngày hôm nay theo Giờ Việt Nam (GMT+7) cho Bot."""
        if not MT5_AVAILABLE:
            return 0.0, 0, None
        pk = profile_key or self._resolve_profile_by_symbol(symbol)
        target_magic = self._get_profile_magic_number(pk)
        sym_key = f"{symbol or '__ALL__'}_{target_magic}"
        now_mono = time.monotonic()
        cached_entry = self._cached_deals_by_sym.get(sym_key)
        if cached_entry is not None and (now_mono - cached_entry[0]) < 10.0:
            return cached_entry[1]
        try:
            # Lấy ngày hôm nay theo Giờ Việt Nam (GMT+7, Asia/Ho_Chi_Minh)
            vn_now = datetime.now(tz=VN_TZ)
            vn_today_str = vn_now.strftime("%Y-%m-%d")
            # Điểm bắt đầu ngày hôm nay 00:00:00 (giờ VN) quy đổi sang timestamp UTC
            vn_today_start = vn_now.replace(hour=0, minute=0, second=0, microsecond=0)
            vn_today_start_ts = int(vn_today_start.timestamp())

            # Quét deals từ trước 00:00:00 VN (trừ 2 giờ dự phòng) đến hiện tại + 2 giờ
            query_from = datetime.fromtimestamp(vn_today_start_ts - 7200)
            query_to = datetime.fromtimestamp(int(time.time()) + 7200)
            with self._mt5_lock:
                deals = mt5.history_deals_get(query_from, query_to)
                if deals is None:
                    try:
                        _hd_err = mt5.last_error()
                    except Exception:
                        _hd_err = None
                    if _hd_err and _hd_err[0] == 1:  # None kèm mã 1 = không có deal nào (không phải lỗi đọc)
                        deals = ()
            if deals is None:
                if cached_entry is None:
                    # A10: chưa từng đọc được lịch sử -> số 0 là "không xác định": chặn mở lệnh mới cho đến khi đọc được
                    self.profile_ctx[pk]["deals_unknown"] = True
                return cached_entry[1] if cached_entry is not None else (0.0, 0, None)

            entry_by_pos = {}
            for d in deals:
                if d.entry == mt5.DEAL_ENTRY_IN:
                    entry_by_pos[d.position_id] = d

            realized = 0.0
            consec_losses = 0
            last_loss_deal = None
            for d in deals:
                if symbol and d.symbol and d.symbol != symbol:
                    continue
                # Quy đổi thời gian thực hiện deal d.time sang Giờ Việt Nam (GMT+7)
                d_vn_dt = datetime.fromtimestamp(int(d.time), tz=VN_TZ)
                if d_vn_dt.strftime("%Y-%m-%d") != vn_today_str:
                    continue
                if d.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL) and d.entry in (
                    mt5.DEAL_ENTRY_OUT,
                    mt5.DEAL_ENTRY_INOUT,
                    getattr(mt5, "DEAL_ENTRY_OUT_BY", 3),
                ):
                    pos_magic = self._resolve_position_magic(d.position_id, entry_by_pos)
                    if pos_magic is None:
                        continue
                    if pos_magic != target_magic:
                        continue
                    net_pnl = float(d.profit + d.swap + d.commission + getattr(d, "fee", 0.0))
                    realized += net_pnl
                    if net_pnl < -0.05:
                        consec_losses += 1
                        last_loss_deal = d
                    elif net_pnl > 0.05:
                        consec_losses = 0
                        last_loss_deal = None
            res_stats = (realized, consec_losses, last_loss_deal)
            self.profile_ctx[pk]["deals_unknown"] = False
            self._cached_deals_by_sym[sym_key] = (now_mono, res_stats)
            self._cached_deals_stats = res_stats
            self._last_deals_check_ts = now_mono
            return res_stats
        except Exception as e:
            self.log_error("GET_TODAY_DEALS_STATS", e, show_in_ui=False)
            if cached_entry is None:
                try:
                    self.profile_ctx[pk]["deals_unknown"] = True  # A10: fail-closed khi chưa có số liệu
                except Exception:
                    pass
            return cached_entry[1] if cached_entry is not None else (0.0, 0, None)

    def _get_closed_deals_by_range(self, symbol: str = None, start_date_str: str = None, end_date_str: str = None, use_cache: bool = True, profile_key: str = None, magic_scope: str = "PROFILE", store_cache: bool = True) -> list:
        """Lấy danh sách các lệnh đã đóng theo khoảng ngày (theo giờ VN GMT+7) kèm phân loại WIN / LOSS / BE+ cho Bot."""
        if not MT5_AVAILABLE:
            return []
        # D2: Lấy magic number cần lọc theo magic_scope
        magic_xau = self._get_profile_magic_number("XAUUSD")
        magic_btc = self._get_profile_magic_number("BTCUSD")
        if magic_scope == "PROFILE":
            pk_d = profile_key or self._resolve_profile_by_symbol(symbol)
            target_magic = self._get_profile_magic_number(pk_d)
        elif magic_scope == "BOT_ALL":
            target_magic = None  # dùng riêng bên dưới
        else:  # "ALL"
            target_magic = None
        sym_key = f"{symbol or '__ALL__'}_{target_magic}"
        now_mono = time.monotonic()
        vn_now = datetime.now(tz=VN_TZ)

        if not start_date_str:
            start_date_str = vn_now.strftime("%Y-%m-%d")
        if not end_date_str:
            end_date_str = start_date_str

        try:
            start_d = datetime.strptime(start_date_str.strip(), "%Y-%m-%d").date()
        except Exception:
            start_d = vn_now.date()
        try:
            end_d = datetime.strptime(end_date_str.strip(), "%Y-%m-%d").date()
        except Exception:
            end_d = start_d

        if start_d > end_d:
            start_d, end_d = end_d, start_d

        range_key = f"{sym_key}_{start_d}_{end_d}_{magic_scope}"
        if not hasattr(self, "_cached_closed_deals_map"):
            self._cached_closed_deals_map = {}
        if use_cache:
            cached = self._cached_closed_deals_map.get(range_key)
            if cached is not None and (now_mono - cached[0]) < 15.0:
                return cached[1]

        try:
            start_dt = datetime(start_d.year, start_d.month, start_d.day, 0, 0, 0, tzinfo=VN_TZ)
            end_dt = datetime(end_d.year, end_d.month, end_d.day, 23, 59, 59, tzinfo=VN_TZ)

            query_from = datetime.fromtimestamp(int(start_dt.timestamp()) - 7200)
            query_to = datetime.fromtimestamp(int(end_dt.timestamp()) + 7200)

            with self._mt5_lock:
                deals = mt5.history_deals_get(query_from, query_to)
                all_orders = mt5.history_orders_get(query_from, query_to)

            if not deals:
                if store_cache:
                    self._cached_closed_deals_map[range_key] = (now_mono, [])
                return []

            entry_by_pos = {}
            for d in deals:
                if d.entry == mt5.DEAL_ENTRY_IN:
                    entry_by_pos[d.position_id] = d

            # Gom nhóm toàn bộ orders theo position_id trong RAM (O(1) lookup, triệt tiêu hoàn toàn lỗi N+1 IPC gây đơ app)
            first_order_by_pos = {}
            if all_orders:
                for ord_item in all_orders:
                    pid = getattr(ord_item, "position_id", 0)
                    if pid and pid not in first_order_by_pos:
                        first_order_by_pos[pid] = ord_item

            closed_items = []
            closed_deals = [
                d for d in deals
                if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_INOUT, getattr(mt5, "DEAL_ENTRY_OUT_BY", 3))
            ]
            closed_deals.sort(key=lambda x: x.time, reverse=True)

            for d in closed_deals:
                if symbol and symbol != "Tất cả" and d.symbol and d.symbol != symbol:
                    continue
                if magic_scope == "PROFILE":
                    pos_magic = self._resolve_position_magic(d.position_id, entry_by_pos)
                    if pos_magic is None:
                        continue
                    if pos_magic != target_magic:
                        continue
                elif magic_scope == "BOT_ALL":
                    pos_magic = self._resolve_position_magic(d.position_id, entry_by_pos)
                    if pos_magic is None:
                        continue
                    if pos_magic not in (magic_xau, magic_btc):
                        continue
                # else "ALL": không lọc magic, bao gồm cả lệnh tay
                d_vn_dt = datetime.fromtimestamp(int(d.time), tz=VN_TZ)
                deal_date = d_vn_dt.date()
                if not (start_d <= deal_date <= end_d):
                    continue

                ed = entry_by_pos.get(d.position_id)
                orig_type = "BUY" if (ed and ed.type == mt5.DEAL_TYPE_BUY) else ("SELL" if d.type == mt5.DEAL_TYPE_BUY else "BUY")
                open_p = ed.price if ed else 0.0
                close_p = d.price
                open_t = datetime.fromtimestamp(ed.time, tz=VN_TZ).strftime("%H:%M:%S") if ed else "---"
                close_t = d_vn_dt.strftime("%H:%M:%S")
                date_close_str = d_vn_dt.strftime("%Y-%m-%d")
                dur_sec = max(0, int(d.time) - int(ed.time)) if ed else -1
                dur_str = self._format_duration(dur_sec) if ed else "---"
                net_pnl = float(d.profit + d.swap + d.commission + getattr(d, "fee", 0.0))
                cmt = str(d.comment or "").lower()
                is_sl_loss = ("[sl" in cmt or "sl" in cmt) and (net_pnl < -0.05)

                # Tính 1R và phân loại WIN (>= 1R) / BE+ (< 1R) / LOSS (< 0)
                contract_size = 100.0 if (d.symbol and "XAU" in d.symbol) else 1.0
                ord_first = first_order_by_pos.get(d.position_id)
                sl_price = ord_first.sl if ord_first is not None else 0.0
                tp_price = ord_first.tp if ord_first is not None else 0.0
                sl_dist = abs(open_p - sl_price) if (open_p > 0 and sl_price > 0) else 0.0
                one_r = (sl_dist * d.volume * contract_size) if sl_dist > 0 else (8.0 if (d.symbol and "XAU" in d.symbol) else 4.0)

                r_ratio = (net_pnl / one_r) if one_r > 0.001 else 0.0
                r_ratio_str = f"{r_ratio:+.2f}R" if abs(r_ratio) > 0.001 else "0.00R"

                # Lệnh thoát quanh hòa vốn (lỗ rất nhỏ >= -0.1R, ví dụ đã dời BE/TP về hòa rồi đóng lệch do spread)
                # được xếp BE+ (không tính là lệnh thua trong Win Rate)
                is_be_small_loss = (net_pnl < -0.05) and (one_r > 0.001) and (r_ratio >= -0.1)
                if net_pnl < -0.05 and not is_be_small_loss:
                    category = "LOSS"
                    if "[sl" in cmt:
                        status = f"ĐÃ CẮT LỖ SL ({close_t})"
                    else:
                        status = f"ĐÃ ĐÓNG LỖ ({close_t})"
                elif ("[tp" in cmt) or (r_ratio >= 1.0) or (net_pnl >= one_r):
                    category = "WIN"
                    if "[tp" in cmt:
                        status = f"ĐÃ CHỐT TP ({close_t})"
                    else:
                        status = f"ĐÃ CHỐT LÃI ({close_t})"
                else:
                    category = "BE+"
                    if "[sl" in cmt:
                        status = f"DỜI SL HÒA VỐN ({close_t})"
                    elif abs(net_pnl) <= 0.05:
                        status = f"HÒA VỐN ({close_t})"
                    else:
                        status = f"THOÁT BẢO TOÀN VỐN ({close_t})"
                _kr_reasons = {
                    "kr_timeall": "HẾT GIỜ GIỮ LỆNH - ĐÓNG TẤT", "kr_time": "HẾT GIỜ GIỮ LỆNH (CÓ LÃI)",
                    "kr_be": "GIÁ VỀ ENTRY - THOÁT (QUY TẮC 3)", "kr_gov": "GỠ VỐN",
                    "kr_revcut": "AI ĐẢO CHIỀU - CẮT LỖ CHỦ ĐỘNG", "kr_rev": "AI ĐẢO CHIỀU - CHỐT LÃI",
                    "kr_basket": "CHỐT LỜI CHÙM LỆNH", "kr_daily": "CHẠM GIỚI HẠN LÃI/LỖ NGÀY",
                    "kr_manual": "ĐÓNG TAY", "kr_all": "ĐÓNG TẤT CẢ (TAY)",
                }
                for _k in sorted(_kr_reasons, key=len, reverse=True):
                    if _k in cmt:
                        status = f"{_kr_reasons[_k]} ({close_t})"
                        break

                if not hasattr(self, "_closed_deals_by_ticket"):
                    self._closed_deals_by_ticket = {}

                cd_item = {
                    "ticket": d.position_id,
                    "date": date_close_str,
                    "open_ts": int(ed.time) if ed else int(d.time),
                    "open_time": open_t,
                    "close_time": close_t,
                    "close_ts": int(d.time),
                    "time_display": f"{open_t} → {close_t}",
                    "duration": dur_str,
                    "duration_sec": dur_sec,
                    "is_sl_loss": is_sl_loss,
                    "symbol": d.symbol,
                    "type": orig_type,
                    "volume": d.volume,
                    "open_price": open_p,
                    "close_price": close_p,
                    "sl": sl_price,
                    "tp": tp_price,
                    "net_pnl": net_pnl,
                    "one_r": one_r,
                    "r_ratio": r_ratio,
                    "r_ratio_str": r_ratio_str,
                    "category": category,
                    "status": status,
                }
                closed_items.append(cd_item)
                self._closed_deals_by_ticket[int(d.position_id)] = cd_item

            # store_cache=False (Telegram đọc nhanh): không ghi bộ đệm dùng chung -> luật SL nhanh/chuỗi thua giữ nguyên thời điểm
            if store_cache:
                self._cached_closed_deals_map[range_key] = (now_mono, closed_items)
            return closed_items
        except Exception as e:
            self.log_error("GET_CLOSED_DEALS", e, show_in_ui=False)
            return []

    def _get_closed_deal_info(self, ticket: int, symbol: str = None) -> dict:
        """Lấy thông tin chi tiết của một lệnh đã đóng theo mã ticket để hiển thị soi nến trên biểu đồ."""
        if not hasattr(self, "_closed_deals_by_ticket"):
            self._closed_deals_by_ticket = {}
        try:
            t_int = int(ticket)
        except (ValueError, TypeError):
            return None

        cd = self._closed_deals_by_ticket.get(t_int)
        if cd is not None:
            return cd

        # 1. Quét tìm trong danh sách hôm nay
        today_deals = self._get_today_closed_deals(symbol=symbol)
        for d in today_deals:
            if int(d.get("ticket", 0)) == t_int:
                self._closed_deals_by_ticket[t_int] = d
                return d

        # 2. Fallback: Truy vấn trực tiếp từ MT5 trong 7 ngày gần nhất nếu là lệnh cũ
        if MT5_AVAILABLE:
            try:
                now_ts = int(time.time())
                start_q = datetime.fromtimestamp(now_ts - 86400 * 7)
                end_q = datetime.fromtimestamp(now_ts + 86400)
                with self._mt5_lock:
                    d_list = mt5.history_deals_get(start_q, end_q, position=t_int)
                    o_list = mt5.history_orders_get(start_q, end_q, position=t_int)
                if d_list:
                    ed = next((x for x in d_list if x.entry == mt5.DEAL_ENTRY_IN), None)
                    xd = next((x for x in d_list if x.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_INOUT, getattr(mt5, "DEAL_ENTRY_OUT_BY", 3))), None)
                    if ed and xd:
                        orig_type = "BUY" if ed.type == mt5.DEAL_TYPE_BUY else "SELL"
                        first_ord = o_list[0] if o_list else None
                        sl_p = float(first_ord.sl) if first_ord else 0.0
                        tp_p = float(first_ord.tp) if first_ord else 0.0
                        net_p = float(xd.profit + xd.swap + xd.commission + getattr(xd, "fee", 0.0))
                        cmt = str(xd.comment or "").lower()
                        d_vn_dt = datetime.fromtimestamp(int(xd.time), tz=VN_TZ)
                        close_t = d_vn_dt.strftime("%H:%M:%S")
                        open_t = datetime.fromtimestamp(ed.time, tz=VN_TZ).strftime("%H:%M:%S")
                        dur_sec = max(0, int(xd.time) - int(ed.time))
                        status = "ĐÃ CẮT LỖ SL" if "[sl" in cmt else ("ĐÃ CHỐT TP" if "[tp" in cmt else ("LÃI" if net_p > 0 else "LỖ"))
                        cd = {
                            "ticket": t_int,
                            "date": d_vn_dt.strftime("%Y-%m-%d"),
                            "open_ts": int(ed.time),
                            "close_ts": int(xd.time),
                            "open_time": open_t,
                            "close_time": close_t,
                            "time_display": f"{open_t} → {close_t}",
                            "duration": self._format_duration(dur_sec),
                            "duration_sec": dur_sec,
                            "symbol": ed.symbol,
                            "type": orig_type,
                            "volume": float(ed.volume),
                            "open_price": float(ed.price),
                            "close_price": float(xd.price),
                            "sl": sl_p,
                            "tp": tp_p,
                            "net_pnl": net_p,
                            "status": f"{status} ({close_t})",
                        }
                        self._closed_deals_by_ticket[t_int] = cd
                        return cd
            except Exception:
                pass
        return None

    def _get_today_closed_deals(self, symbol: str = None, profile_key: str = None, magic_scope: str = "PROFILE") -> list:
        """Lấy danh sách các lệnh đã đóng hôm nay (theo giờ VN GMT+7) cho Bot."""
        today_s = datetime.now(tz=VN_TZ).strftime("%Y-%m-%d")
        return self._get_closed_deals_by_range(symbol=symbol, start_date_str=today_s, end_date_str=today_s, use_cache=True, profile_key=profile_key, magic_scope=magic_scope)

    def _check_daily_profit_loss_limits(self, floating_profit: float, tick=None, profile_key: str = None):
        """Kiểm tra mục tiêu Lãi ngày / Cắt lỗ ngày (USD) & Tạm ngừng sau chuỗi thua liên tiếp cho từng Tab (XAUUSD / BTCUSD) theo Giờ Việt Nam (GMT+7)."""
        pk = profile_key or self.active_profile_key
        cfg = self._get_profile_cfg(pk)
        ctx = self.profile_ctx[pk]
        today_str = datetime.now(tz=VN_TZ).strftime("%Y-%m-%d")
        symbol = str(cfg.get("symbol", "")).strip()
        realized_pnl, consec_losses, last_loss_deal = self._get_today_deals_stats(symbol=symbol, tick=tick, profile_key=pk)
        total_daily_pnl = realized_pnl + floating_profit
        ctx["cached_daily_pnl"] = total_daily_pnl
        ctx["consec_loss_count"] = consec_losses

        if not symbol:
            self._write_file_log(f"[{pk}] Symbol rỗng trong _check_daily_profit_loss_limits -> Bỏ qua kiểm tra giới hạn.", is_error=True)
            return

        try:
            daily_tp = max(float(cfg.get("daily_tp_usd", 0.0)), 0.0)
        except ValueError:
            daily_tp = 0.0
        try:
            daily_sl = max(abs(float(cfg.get("daily_sl_usd", 0.0))), 0.0)
        except ValueError:
            daily_sl = 0.0
        try:
            max_consec_loss = max(int(float(cfg.get("max_consec_loss", 0))), 0)
        except ValueError:
            max_consec_loss = 0
        try:
            cooldown_mins = max(float(cfg.get("cooldown_mins", 30.0)), 0.0)
        except ValueError:
            cooldown_mins = 0.0
        ana_tf_str = str(cfg.get("analysis_tf", "M15")).strip()
        ana_tf_mins = self._get_tf_minutes(ana_tf_str)
        fast_sl_threshold_sec = ana_tf_mins * 60
        try:
            fast_sl_cd_candles = max(float(cfg.get("fast_sl_cooldown_candles", cfg.get("fast_sl_cooldown_mins", 1.0))), 0.0)
        except ValueError:
            fast_sl_cd_candles = 0.0

        is_auto_active = self.auto_loop_running if pk == self.active_profile_key else bool(ctx.get("auto_loop_running", False))

        # 1. Xử lý Tạm ngừng sau chuỗi lệnh thua liên tiếp (= 0 là tắt)
        if max_consec_loss == 0 or cooldown_mins == 0.0:
            if ctx["cooldown_until"] is not None:
                ctx["cooldown_until"] = None
                if pk == self.active_profile_key:
                    self.cooldown_until = None
                self.log(f"[{pk}] Đã tắt bộ đếm tạm ngừng sau chuỗi thua (thông số = 0).")
        else:
            if ctx["cooldown_until"] is not None and datetime.now() >= ctx["cooldown_until"]:
                ctx["cooldown_until"] = None
                if pk == self.active_profile_key:
                    self.cooldown_until = None
                self.log(f"[{pk}] Hết thời gian tạm nghỉ chuỗi thua -> Tiếp tục giao dịch tự động.")
                self.show_toast(f"[{pk}] Hết tạm nghỉ chuỗi thua -> Tự động tiếp tục!", is_error=False, duration_ms=2500)
                if is_auto_active and not ctx.get("is_analyzing", False):
                    self.enqueue_ai_scan(profile_key=pk, reason="COOLDOWN_EXPIRED")

            if consec_losses >= max_consec_loss and last_loss_deal is not None:
                if ctx["last_cooldown_deal_ticket"] != last_loss_deal.ticket:
                    now_ts = int(time.time())
                    # A45: last_loss_deal.time là giờ SERVER; đổi về UTC trước khi so với now_ts
                    srv_off_cd = int(self._server_offset_sec(symbol))
                    elapsed_sec = max(0, now_ts - (int(last_loss_deal.time) - srv_off_cd))
                    total_cd_sec = cooldown_mins * 60.0
                    ctx["last_cooldown_deal_ticket"] = last_loss_deal.ticket
                    if elapsed_sec < total_cd_sec:
                        rem_sec = total_cd_sec - elapsed_sec
                        ctx["cooldown_until"] = datetime.now() + timedelta(seconds=rem_sec)
                        ctx["pending_pullback_order"] = None
                        self.log(
                            f"[{pk} - Tạm nghỉ chuỗi thua] Thua liên tiếp {consec_losses} lệnh -> "
                            f"Tạm nghỉ {cooldown_mins:.0f}p (đến {ctx['cooldown_until'].strftime('%H:%M:%S')})."
                        )

        # 1.5. Xử lý Tạm ngừng khi có lệnh dính SL nhanh (< 1 nến AI, = 0 là tắt)
        if fast_sl_cd_candles == 0.0:
            if ctx.get("fast_sl_cooldown_until") is not None:
                ctx["fast_sl_cooldown_until"] = None
                if pk == self.active_profile_key:
                    self.fast_sl_cooldown_until = None
                self.log(f"[{pk}] Đã tắt tạm nghỉ do SL nhanh (thông số = 0).")
        else:
            if ctx.get("fast_sl_cooldown_until") is not None and datetime.now() >= ctx["fast_sl_cooldown_until"]:
                ctx["fast_sl_cooldown_until"] = None
                if pk == self.active_profile_key:
                    self.fast_sl_cooldown_until = None
                self.log(f"[{pk}] Hết thời gian tạm nghỉ SL nhanh ({fast_sl_cd_candles:.0f} nến {ana_tf_str}) -> Tiếp tục giao dịch tự động.")
                self.show_toast(f"[{pk}] Hết tạm nghỉ SL nhanh -> Tự động tiếp tục!", is_error=False, duration_ms=2500)
                if is_auto_active and not ctx.get("is_analyzing", False):
                    self.enqueue_ai_scan(profile_key=pk, reason="COOLDOWN_EXPIRED")

            closed_deals = self._get_today_closed_deals(symbol=symbol)
            for cd in closed_deals:
                if cd.get("is_sl_loss") and (0 <= cd.get("duration_sec", -1) < fast_sl_threshold_sec):
                    last_ticket = cd.get("ticket")
                    if ctx.get("last_fast_sl_deal_ticket") != last_ticket:
                        now_ts = int(time.time())
                        # A45: close_ts là giờ SERVER; đổi về UTC (mặc định = bây giờ theo giờ server)
                        srv_off_fs = int(self._server_offset_sec(symbol))
                        close_ts = cd.get("close_ts", now_ts + srv_off_fs)
                        elapsed_sec = max(0, now_ts - (int(close_ts) - srv_off_fs))
                        total_cd_sec = fast_sl_cd_candles * ana_tf_mins * 60.0
                        ctx["last_fast_sl_deal_ticket"] = last_ticket
                        if elapsed_sec < total_cd_sec:
                            rem_sec = total_cd_sec - elapsed_sec
                            ctx["fast_sl_cooldown_until"] = datetime.now() + timedelta(seconds=rem_sec)
                            # fast_sl_same_dir (mặc định TẮT): chỉ nghỉ chiều của lệnh vừa SL nhanh, chiều ngược vẫn vào
                            fs_dir = cd.get("type") if (self._fast_sl_same_dir_on(cfg) and cd.get("type") in ("BUY", "SELL")) else None
                            ctx["fast_sl_cooldown_dir"] = fs_dir
                            _pp = ctx.get("pending_pullback_order")
                            if fs_dir is None or (_pp is not None and _pp.get("action") == fs_dir):
                                ctx["pending_pullback_order"] = None
                                if pk == self.active_profile_key:
                                    self.pending_pullback_order = None
                            dur_display = cd.get("duration", f"{cd.get('duration_sec', 0)}s")
                            cd_mins_total = fast_sl_cd_candles * ana_tf_mins
                            dir_note = f", chỉ chiều {fs_dir}" if fs_dir else ""
                            self.log(
                                f"[{pk} - Tạm nghỉ SL nhanh] Lệnh #{last_ticket} dính SL sau {dur_display} (< 1 nến {ana_tf_str}) -> "
                                f"Tạm nghỉ {fast_sl_cd_candles:.0f} nến {ana_tf_str} ({cd_mins_total:.0f}p, đến {ctx['fast_sl_cooldown_until'].strftime('%H:%M:%S')}{dir_note})."
                            )
                            self.show_toast(
                                f"[{pk}] Lệnh #{last_ticket} SL nhanh -> Tạm nghỉ {fast_sl_cd_candles:.0f} nến {ana_tf_str} ({cd_mins_total:.0f}p)",
                                is_error=True,
                                duration_ms=3000
                            )
                    break

        # 1.7. Xử lý Tạm nghỉ sau khi chốt lời chùm lệnh (Basket Cooldown)
        if ctx.get("basket_cooldown_until") is not None and datetime.now() >= ctx["basket_cooldown_until"]:
            ctx["basket_cooldown_until"] = None
            if pk == self.active_profile_key:
                self.basket_cooldown_until = None
            self.log(f"[{pk}] Hết thời gian nghỉ sau chốt chùm -> Tiếp tục giao dịch tự động.")
            self.show_toast(f"[{pk}] Hết tạm nghỉ chốt chùm -> Tự động tiếp tục!", is_error=False, duration_ms=2500)
            if is_auto_active and not ctx.get("is_analyzing", False):
                self.enqueue_ai_scan(profile_key=pk, reason="COOLDOWN_EXPIRED")

        # 2. Sang ngày mới theo giờ Việt Nam -> Tự động mở khóa giao dịch
        if ctx["daily_limit_locked_date"] is not None and ctx["daily_limit_locked_date"] != today_str:
            ctx["daily_limit_locked_date"] = None
            ctx["daily_limit_reason"] = ""
            ctx["daily_limit_locked_kind"] = None
            ctx["daily_limit_locked_threshold"] = 0.0
            if pk == self.active_profile_key:
                self.daily_limit_locked_date = None
                self.daily_limit_reason = ""
                self.daily_limit_locked_kind = None
                self.daily_limit_locked_threshold = 0.0
            self.log(f"[{pk}] Sang ngày giao dịch mới (00:00 GMT+7) -> Mở lại giao dịch tự động.")
            if is_auto_active and not ctx.get("is_analyzing", False):
                self.enqueue_ai_scan(profile_key=pk, reason="NEW_DAY_UNLOCKED")

        # Chỉ mở khóa trong ngày khi người dùng THAY ĐỔI ngưỡng tương ứng (về 0 hoặc khác giá trị đã lưu)
        # và PnL hiện tại không còn chạm ngưỡng mới. Tuyệt đối không mở khóa chỉ vì PnL dao động tạm thời.
        if ctx["daily_limit_locked_date"] == today_str:
            locked_kind = ctx.get("daily_limit_locked_kind")
            locked_thresh = float(ctx.get("daily_limit_locked_threshold") or 0.0)
            threshold_changed = False
            still_breached = False

            if locked_kind == "TP":
                if abs(daily_tp - locked_thresh) > 1e-4:
                    threshold_changed = True
                still_breached = (daily_tp > 0.0 and total_daily_pnl >= daily_tp)
            elif locked_kind == "SL":
                if abs(daily_sl - locked_thresh) > 1e-4:
                    threshold_changed = True
                still_breached = (daily_sl > 0.0 and total_daily_pnl <= -daily_sl)
            else:
                tp_still_hit = (daily_tp > 0.0 and total_daily_pnl >= daily_tp)
                sl_still_hit = (daily_sl > 0.0 and total_daily_pnl <= -daily_sl)
                still_breached = tp_still_hit or sl_still_hit
                threshold_changed = not still_breached

            if threshold_changed and not still_breached:
                ctx["daily_limit_locked_date"] = None
                ctx["daily_limit_reason"] = ""
                ctx["daily_limit_locked_kind"] = None
                ctx["daily_limit_locked_threshold"] = 0.0
                if pk == self.active_profile_key:
                    self.daily_limit_locked_date = None
                    self.daily_limit_reason = ""
                    self.daily_limit_locked_kind = None
                    self.daily_limit_locked_threshold = 0.0
                self.log(f"🔓 [{pk}] Đã mở khóa giao dịch trong ngày (người dùng thay đổi ngưỡng Lãi/Lỗ ngày: {locked_thresh:.1f}$ -> {(daily_tp if locked_kind == 'TP' else daily_sl):.1f}$).")
                if is_auto_active and not ctx.get("is_analyzing", False):
                    self.enqueue_ai_scan(profile_key=pk, reason="DAILY_LIMIT_UNLOCKED")

        # Kiểm tra chạm mức Chốt lời ngày (nếu > 0)
        if daily_tp > 0.0 and total_daily_pnl >= daily_tp and ctx["daily_limit_locked_date"] != today_str:
            ctx["daily_limit_locked_date"] = today_str
            ctx["daily_limit_reason"] = f"Đạt Lãi ngày ({total_daily_pnl:+.2f}$ >= +{daily_tp:.1f}$)"
            ctx["daily_limit_locked_kind"] = "TP"
            ctx["daily_limit_locked_threshold"] = daily_tp
            ctx["pending_pullback_order"] = None
            failed_close = self._daily_limit_close_bot_positions(pk, symbol, ctx)
            self._cached_deals_by_sym.clear()
            self._cached_closed_deals_map.clear()
            if failed_close == 0:
                self.log(f"🏆 [{pk} - QUẢN LÝ VỐN NGÀY] {ctx['daily_limit_reason']} -> Đã chốt toàn bộ lệnh {symbol} & DỪNG GIAO DỊCH hết hôm nay!")
            else:
                self.log(f"🏆 [{pk} - QUẢN LÝ VỐN NGÀY] {ctx['daily_limit_reason']} -> DỪNG GIAO DỊCH hết hôm nay nhưng còn {failed_close} lệnh bot chưa đóng được, sẽ thử lại (>= 5 giây/lần).")

        # Kiểm tra chạm mức Cắt lỗ ngày (nếu > 0)
        elif daily_sl > 0.0 and total_daily_pnl <= -daily_sl and ctx["daily_limit_locked_date"] != today_str:
            ctx["daily_limit_locked_date"] = today_str
            ctx["daily_limit_reason"] = f"Chạm Cắt lỗ ngày ({total_daily_pnl:+.2f}$ <= -{daily_sl:.1f}$)"
            ctx["daily_limit_locked_kind"] = "SL"
            ctx["daily_limit_locked_threshold"] = daily_sl
            ctx["pending_pullback_order"] = None
            failed_close = self._daily_limit_close_bot_positions(pk, symbol, ctx)
            self._cached_deals_by_sym.clear()
            self._cached_closed_deals_map.clear()
            if failed_close == 0:
                self.log(f"[{pk} - Quản lý vốn ngày] {ctx['daily_limit_reason']} -> Đã đóng toàn bộ lệnh {symbol} & dừng giao dịch hôm nay.")
            else:
                self.log(f"[{pk} - Quản lý vốn ngày] {ctx['daily_limit_reason']} -> Dừng giao dịch hôm nay nhưng còn {failed_close} lệnh bot chưa đóng được, sẽ thử lại (>= 5 giây/lần).")

        # A02: thử lại đóng lệnh BOT nếu lần đóng khi chạm ngưỡng chưa hết lệnh (<= 1 lần / 5 giây, log <= 1 lần / 15 giây)
        if ctx.get("daily_limit_pending_close"):
            if ctx["daily_limit_locked_date"] != today_str:
                ctx["daily_limit_pending_close"] = False  # đã mở khóa (sang ngày mới / đổi ngưỡng): không đóng lệnh nữa
            elif time.monotonic() - ctx.get("daily_close_last_try", -1e9) >= 5.0:
                failed_close = self._daily_limit_close_bot_positions(pk, symbol, ctx)
                now_m = time.monotonic()
                if failed_close == 0:
                    self.log(f"✅ [{pk} - Quản lý vốn ngày] Đã đóng hết lệnh bot {symbol} sau khi thử lại.")
                elif now_m - ctx.get("daily_close_last_log", -1e9) >= 15.0:
                    ctx["daily_close_last_log"] = now_m
                    self.log(f"⚠️ [{pk} - Quản lý vốn ngày] Còn {failed_close} lệnh bot {symbol} chưa đóng được -> thử lại sau 5 giây.")

        if pk == self.active_profile_key:
            self.cached_daily_pnl = ctx["cached_daily_pnl"]
            self.consec_loss_count = ctx["consec_loss_count"]
            self.cooldown_until = ctx["cooldown_until"]
            self.last_cooldown_deal_ticket = ctx["last_cooldown_deal_ticket"]
            self.fast_sl_cooldown_until = ctx.get("fast_sl_cooldown_until")
            self.last_fast_sl_deal_ticket = ctx.get("last_fast_sl_deal_ticket")
            self.daily_limit_locked_date = ctx["daily_limit_locked_date"]
            self.daily_limit_reason = ctx["daily_limit_reason"]
            self.daily_limit_locked_kind = ctx.get("daily_limit_locked_kind")
            self.daily_limit_locked_threshold = float(ctx.get("daily_limit_locked_threshold") or 0.0)
            self.pending_pullback_order = ctx["pending_pullback_order"]

    def _daily_limit_close_bot_positions(self, pk: str, symbol: str, ctx: dict) -> int:
        """A02: đóng các lệnh BOT của profile (lọc Magic qua _get_bot_positions, không đụng lệnh tay) và KIỂM TRA kết quả.
        Trả số lệnh bot chưa đóng được; đặt/xóa cờ ctx["daily_limit_pending_close"] để các vòng sau thử lại."""
        ctx["daily_close_last_try"] = time.monotonic()
        failed = 0
        for pos in (self._get_bot_positions(symbol, profile_key=pk) or []):
            try:
                ok_c, _msg = self._close_single_pos(pos, "DAILY")
            except Exception:
                ok_c = False
            if not ok_c:
                failed += 1
        ctx["daily_limit_pending_close"] = failed > 0
        return failed

    def _is_daily_limit_locked(self, profile_key: str = None) -> bool:
        pk = profile_key or self.active_profile_key
        locked_d = self.daily_limit_locked_date if pk == self.active_profile_key else self.profile_ctx[pk].get("daily_limit_locked_date")
        return locked_d == datetime.now(tz=VN_TZ).strftime("%Y-%m-%d")

    def _fast_sl_same_dir_on(self, cfg: dict) -> bool:
        return self._cfg_flag(cfg, "fast_sl_same_dir")

    @staticmethod
    def _cfg_flag(cfg: dict, key: str) -> bool:
        """Đọc ô tích cấu hình (bool hoặc chuỗi 'True'/'1' từ file/--set); thiếu khóa = TẮT."""
        return str(cfg.get(key, False)).strip().lower() in ("1", "true", "yes", "on")

    def _is_cooldown_active(self, profile_key: str = None, action: str = None) -> bool:
        """action ('BUY'/'SELL'): khi bật fast_sl_same_dir, tạm nghỉ SL nhanh chỉ chặn chiều vừa SL. action=None: mọi chiều (hiển thị)."""
        pk = profile_key or self.active_profile_key
        ctx = self.profile_ctx[pk]
        cd = self.cooldown_until if pk == self.active_profile_key else ctx.get("cooldown_until")
        bcd = ctx.get("basket_cooldown_until")
        fscd = self.fast_sl_cooldown_until if pk == self.active_profile_key else ctx.get("fast_sl_cooldown_until")
        now = datetime.now()
        is_loss_cd = (cd is not None and now < cd)
        is_basket_cd = (bcd is not None and now < bcd)
        is_fast_sl_cd = (fscd is not None and now < fscd)
        if is_fast_sl_cd and action in ("BUY", "SELL"):
            fs_dir = ctx.get("fast_sl_cooldown_dir")
            if fs_dir in ("BUY", "SELL") and fs_dir != action:
                is_fast_sl_cd = False
        return is_loss_cd or is_basket_cd or is_fast_sl_cd

    def _get_cooldown_remaining_str(self, profile_key: str = None) -> str:
        pk = profile_key or self.active_profile_key
        ctx = self.profile_ctx[pk]
        now = datetime.now()
        cd = self.cooldown_until if pk == self.active_profile_key else ctx.get("cooldown_until")
        bcd = ctx.get("basket_cooldown_until")
        fscd = self.fast_sl_cooldown_until if pk == self.active_profile_key else ctx.get("fast_sl_cooldown_until")
        parts = []
        if cd is not None and now < cd:
            losses = self.consec_loss_count if pk == self.active_profile_key else ctx.get("consec_loss_count", 0)
            rem = max(0, int((cd - now).total_seconds()))
            m, s = divmod(rem, 60)
            parts.append(f"Tạm ngưng giao dịch do chuỗi thua {losses} lệnh [{m:02d}p:{s:02d}s]")
        if bcd is not None and now < bcd:
            rem = max(0, int((bcd - now).total_seconds()))
            m, s = divmod(rem, 60)
            parts.append(f"Tạm ngưng giao dịch do sau chốt chùm [{m:02d}p:{s:02d}s]")
        if fscd is not None and now < fscd:
            rem = max(0, int((fscd - now).total_seconds()))
            m, s = divmod(rem, 60)
            cfg = self._get_profile_cfg(pk)
            ana_tf_str = str(cfg.get("analysis_tf", "M15")).strip()
            fs_dir = ctx.get("fast_sl_cooldown_dir")
            dir_note = f" chiều {fs_dir}" if fs_dir in ("BUY", "SELL") else ""
            parts.append(f"Tạm ngưng{dir_note} do SL nhanh (< 1 nến {ana_tf_str}) [{m:02d}p:{s:02d}s]")
        return " | ".join(parts)

    def _get_tf_minutes(self, tf_str: str) -> int:
        """Chuyển đổi chuỗi Timeframe (M1, M5, M15, M30, H1, H4, D1) sang số phút tương ứng."""
        tf_upper = str(tf_str).upper().strip()
        tf_min_map = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
        return tf_min_map.get(tf_upper, 5)

    def _resolve_profile_by_symbol(self, symbol: str) -> str:
        """Xác định profile key ('XAUUSD' hoặc 'BTCUSD') dựa theo symbol cấu hình của từng profile."""
        if not symbol:
            return self.active_profile_key
        sym_clean = str(symbol).strip().upper()
        for pk in ("XAUUSD", "BTCUSD"):
            cfg_s = str(self.profile_settings.get(pk, {}).get("symbol", "")).strip().upper()
            if cfg_s and (cfg_s == sym_clean or cfg_s in sym_clean or sym_clean in cfg_s):
                return pk
        if "BTC" in sym_clean:
            return "BTCUSD"
        return "XAUUSD"

    def _positions_get_checked(self, **kwargs):
        """
        Gọi mt5.positions_get(**kwargs) an toàn trong self._mt5_lock:
        - Nếu kết quả không None: trả về list(kết quả).
        - Nếu kết quả là None: gọi mt5.last_error().
          + Nếu err[0] == 1 (TRADE_RETCODE_DONE / Success): coi là danh sách rỗng và trả [].
          + Nếu err[0] != 1: trả về None (lỗi MT5 thực sự) và ghi log cảnh báo kèm mã lỗi.
        """
        if not MT5_AVAILABLE:
            return []
        try:
            with self._mt5_lock:
                res = mt5.positions_get(**kwargs)
                if res is not None:
                    return list(res)
                err = mt5.last_error()
                # err là tuple (code, description)
                err_code = err[0] if (isinstance(err, (tuple, list)) and len(err) > 0) else None
                if err_code == 1:
                    return []
                self._write_file_log(
                    f"[POSITIONS_GET_ERROR] mt5.positions_get({kwargs}) trả None với last_error={err}",
                    is_error=True
                )
                return None
        except Exception as e:
            self._write_file_log(f"[POSITIONS_GET_EX] mt5.positions_get({kwargs}) ngoại lệ: {e}", is_error=True)
            return None

    def _get_bot_positions(self, symbol: str, profile_key: str = None) -> list:
        """
        Lấy danh sách lệnh đang mở trên MT5 thuộc quyền quản lý của Bot cho một profile và symbol cụ thể.
        Luôn thực thi trong self._mt5_lock qua _positions_get_checked và lọc chính xác theo Magic Number.
        Nếu symbol rỗng: log cảnh báo và trả về list rỗng, tuyệt đối không truy vấn tất cả lệnh.
        """
        if not MT5_AVAILABLE:
            return []
        sym_str = str(symbol or "").strip()
        if not sym_str:
            self._write_file_log("[GET_BOT_POSITIONS] Cảnh báo: symbol rỗng -> Bỏ qua truy vấn MT5 positions.", is_error=True)
            return []
        pk = profile_key or self._resolve_profile_by_symbol(sym_str)
        target_magic = self._get_profile_magic_number(pk)
        try:
            raw_pos = self._positions_get_checked(symbol=sym_str)
            if not raw_pos:
                return []
            return [p for p in raw_pos if getattr(p, "magic", 0) == target_magic]
        except Exception as e:
            self.log_error("GET_BOT_POSITIONS", e, show_in_ui=False)
            return []

    def _get_profile_magic_number(self, profile_key: str = None) -> int:
        """Lấy Magic Number do người dùng thiết lập cho từng Tab (XAUUSD mặc định 20260501, BTCUSD mặc định 20260502)."""
        pk = profile_key or self.active_profile_key
        cfg = self._get_profile_cfg(pk)
        default_magic = 20260502 if pk == "BTCUSD" else 20260501
        raw_val = str(cfg.get("magic_number", default_magic)).strip()
        try:
            m_val = int(float(raw_val))
            return m_val if m_val > 0 else default_magic
        except Exception:
            return default_magic

    def _resolve_position_magic(self, pid: int, entry_by_pos: dict = None):
        """
        Xác định Magic Number ban đầu của một Position ID (ticket):
        1. Tra trong bộ nhớ đệm self._cached_pid_magic (bao gồm cả magic = 0 của lệnh tay).
        2. Nếu đang trong thời gian backoff (30s) do truy vấn trước thất bại -> trả None ngay.
        3. Tra trong entry_by_pos nếu có deal DEAL_ENTRY_IN trong mảng deals đang xét.
        4. Tra MT5 qua mt5.history_deals_get(position=pid). Nếu lỗi (None/exception) -> backoff 30s và trả None.
        """
        if not pid:
            return 0
        if not hasattr(self, "_cached_pid_magic"):
            self._cached_pid_magic = {}
        if not hasattr(self, "_pid_magic_backoff"):
            self._pid_magic_backoff = {}

        if pid in self._cached_pid_magic:
            return self._cached_pid_magic[pid]  # trả cả 0 (lệnh tay đã xác nhận)

        # Backoff: không gọi lại MT5 cho pid đã thất bại trong 30s
        backoff_until = self._pid_magic_backoff.get(pid, 0.0)
        if time.monotonic() < backoff_until:
            return None  # Chưa xác định được

        # Tra entry_by_pos trong RAM trước
        if entry_by_pos and pid in entry_by_pos:
            m = getattr(entry_by_pos[pid], "magic", None)
            if m is not None:
                m_int = int(m)
                self._cached_pid_magic[pid] = m_int
                return m_int

        # Gọi MT5
        if MT5_AVAILABLE:
            try:
                with self._mt5_lock:
                    p_deals = mt5.history_deals_get(position=pid)
                if p_deals is None:
                    self._pid_magic_backoff[pid] = time.monotonic() + 30.0
                    if not hasattr(self, "_logged_magic_fail_pids"):
                        self._logged_magic_fail_pids = set()
                    if pid not in self._logged_magic_fail_pids:
                        self._write_file_log(f"[RESOLVE_MAGIC] history_deals_get(position={pid}) trả None -> Backoff 30s.", is_error=True)
                        self._logged_magic_fail_pids.add(pid)
                    return None
                deal_in_code = getattr(mt5, "DEAL_ENTRY_IN", 0)
                for pd in p_deals:
                    if getattr(pd, "entry", None) == deal_in_code:
                        m_int = int(getattr(pd, "magic", 0) or 0)
                        self._cached_pid_magic[pid] = m_int  # cache cả 0 (lệnh tay)
                        return m_int
                # Không tìm thấy DEAL_ENTRY_IN trong lịch sử deals của position
                self._pid_magic_backoff[pid] = time.monotonic() + 30.0
                return None
            except Exception as ex:
                self._pid_magic_backoff[pid] = time.monotonic() + 30.0
                self._write_file_log(f"[RESOLVE_MAGIC] Exception cho pid={pid}: {ex}", is_error=True)
                return None
        return None

    def _get_supported_filling_modes(self, sym_info) -> list:
        """Trả về danh sách các kiểu khớp lệnh (Filling Mode) theo thứ tự ưu tiên mà mã giao dịch trên MT5 hỗ trợ."""
        modes = []
        if sym_info is not None:
            fm = getattr(sym_info, "filling_mode", 0) or 0
            # Bitmask chuẩn của MT5: 1 = FOK, 2 = IOC
            if fm & 2:
                modes.append(mt5.ORDER_FILLING_IOC)
            if fm & 1:
                modes.append(mt5.ORDER_FILLING_FOK)
        for fallback_m in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, getattr(mt5, "ORDER_FILLING_RETURN", 2)):
            if fallback_m not in modes:
                modes.append(fallback_m)
        return modes

    def _send_mt5_order_with_retry(self, request: dict, symbol: str, context_tag: str = "ORDER", max_retries: int = 3) -> tuple:
        """
        Gửi lệnh giao dịch MT5 (Mở lệnh / Đóng lệnh / Sửa SL-TP) an toàn tuyệt đối khi Scalping 2 mã cùng lúc:
        1. Khóa tuần tự qua self._mt5_lock + khoảng đệm 55ms giữa 2 lệnh để không bao giờ bị lỗi Trade Context Busy (10007).
        2. Tự động thử lại tối đa 3 lần (kèm cập nhật giá Bid/Ask mới nhất sau 85ms) nếu sàn báo Requote / Price Changed / Busy.
        3. Chống mở lệnh đúp khi gặp TIMEOUT / CONNECTION / None: kiểm tra positions_get xem lệnh đã mở chưa, không gửi lại bừa bãi.
        4. Ghi nhật ký chi tiết vào file log khi có bất kỳ từ chối nào từ MT5.
        """
        if not MT5_AVAILABLE:
            return False, None, "Chưa cài đặt thư viện MetaTrader5"

        is_deal = (request.get("action") == getattr(mt5, "TRADE_ACTION_DEAL", 1))
        is_new_order = is_deal and ("position" not in request)
        is_close_order = is_deal and ("position" in request)
        close_pos_ticket = request.get("position") if is_close_order else None

        # Mã lỗi có thể thử lại an toàn
        safe_retry_codes = {
            getattr(mt5, "TRADE_RETCODE_REQUOTE", 10004),
            getattr(mt5, "TRADE_RETCODE_BUSY", 10007),
            getattr(mt5, "TRADE_RETCODE_PRICE_CHANGED", 10020),
            getattr(mt5, "TRADE_RETCODE_PRICE_OFF", 10021),
        }
        network_uncertain_codes = {
            getattr(mt5, "TRADE_RETCODE_TIMEOUT", 10012),
            getattr(mt5, "TRADE_RETCODE_CONNECTION", 10031),
        }

        invalid_fill_code = getattr(mt5, "TRADE_RETCODE_INVALID_FILL", 10030)
        done_codes = {
            getattr(mt5, "TRADE_RETCODE_DONE", 10009),
            getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010),
            getattr(mt5, "TRADE_RETCODE_PLACED", 10008),
            getattr(mt5, "TRADE_RETCODE_NO_CHANGES", 10025),
        }

        with self._mt5_lock:
            # Khoảng đệm an toàn 55ms giữa 2 lệnh liên tiếp bắn vào MT5
            elapsed = time.monotonic() - self._last_mt5_order_mono
            if 0.0 <= elapsed < 0.055:
                time.sleep(0.055 - elapsed)

            sym_info = mt5.symbol_info(symbol) if symbol else None
            digits = sym_info.digits if sym_info is not None else 2
            filling_modes = self._get_supported_filling_modes(sym_info) if is_deal else [request.get("type_filling", 0)]

            # Lưu danh sách ticket đang tồn tại trước khi gửi lệnh mới (để kiểm tra dup sau timeout)
            snapshot_ok = True
            if is_new_order and symbol:
                _before_raw = self._positions_get_checked(symbol=symbol)
                if _before_raw is None:
                    snapshot_ok = False
                    before_tickets = set()
                else:
                    before_tickets = {p.ticket for p in _before_raw}
            else:
                before_tickets = set()

            last_res = None
            for attempt in range(1, max_retries + 1):
                # Với lệnh đóng vị thế: trước mỗi lần retry, kiểm tra position còn tồn tại hay đã đóng xong rồi
                if is_close_order and close_pos_ticket:
                    chk_p = self._positions_get_checked(ticket=int(close_pos_ticket))
                    if chk_p is not None and len(chk_p) == 0:
                        self._write_file_log(
                            f"[MT5_RETRY_OK - {context_tag}] Lệnh #{close_pos_ticket} không còn tồn tại trên MT5 (đã đóng thành công trước đó)."
                        )
                        return True, last_res, ""
                    # Nếu chk_p is None (lỗi MT5): KHÔNG trả thành công, tiếp tục retry bình thường

                if attempt > 1:
                    time.sleep(0.085)
                    if symbol and is_deal:
                        mt5.symbol_select(symbol, True)
                        fresh_tick = mt5.symbol_info_tick(symbol)
                        if fresh_tick is not None and fresh_tick.ask > 0 and fresh_tick.bid > 0:
                            is_buy_ord = (request.get("type") == getattr(mt5, "ORDER_TYPE_BUY", 0))
                            request["price"] = round(fresh_tick.ask if is_buy_ord else fresh_tick.bid, digits)

                for fill_m in filling_modes:
                    req_try = dict(request)
                    if is_deal:
                        req_try["type_filling"] = fill_m
                    res = mt5.order_send(req_try)
                    self._last_mt5_order_mono = time.monotonic()
                    last_res = res

                    if res is not None and res.retcode in done_codes:
                        if attempt > 1:
                            self._write_file_log(
                                f"[MT5_RETRY_OK - {context_tag}] Thành công ở lần thử {attempt}/{max_retries} ({symbol}, ticket={getattr(res, 'order', 0)})"
                            )
                        return True, res, ""

                    if res is not None and res.retcode == invalid_fill_code:
                        continue
                    break

                rc = getattr(last_res, "retcode", None)
                cmt = getattr(last_res, "comment", "")

                # Xử lý trường hợp mở lệnh mới gặp TIMEOUT, CONNECTION hoặc res is None
                if is_new_order and (last_res is None or rc in network_uncertain_codes):
                    self._write_file_log(
                        f"[MT5_ORDER_UNCERTAIN - {context_tag}] Gặp sự cố mạng/timeout ({symbol}, code={rc}, cmt={cmt}) -> Kiểm tra danh sách positions tránh mở lệnh đúp...",
                        is_error=True
                    )
                    time.sleep(0.15)
                    if not snapshot_ok:
                        self._write_file_log(
                            f"[MT5_ORDER_FAILED - {context_tag}] Không thể xác minh do chụp before_tickets thất bại (snapshot_ok=False) -> Trả thất bại an toàn, không gửi lại.",
                            is_error=True
                        )
                        break

                    _pos_after = self._positions_get_checked(symbol=symbol)
                    if _pos_after is None:
                        self._write_file_log(f"[MT5_ORDER_FAILED - {context_tag}] MT5 trả None khi kiểm tra positions sau timeout -> Trả thất bại an toàn.", is_error=True)
                        break
                    matched_pos = None
                    for p in _pos_after:
                        if (getattr(p, "magic", 0) == request.get("magic") and
                            getattr(p, "type", None) == request.get("type") and
                            abs(float(getattr(p, "volume", 0.0)) - float(request.get("volume", 0.0))) < 1e-4 and
                            p.ticket not in before_tickets):
                            matched_pos = p
                            break
                    if matched_pos is not None:
                        self._write_file_log(
                            f"[MT5_ORDER_DEDUP - {context_tag}] Phát hiện lệnh mở mới ticket={matched_pos.ticket} sau timeout -> Coi là thành công."
                        )
                        recovered_res = types.SimpleNamespace(
                            retcode=10009,
                            order=matched_pos.ticket,
                            deal=0,
                            price=getattr(matched_pos, "price_open", 0.0),
                            comment="recovered"
                        )
                        return True, recovered_res, ""
                    # Nếu không tìm thấy position mới -> Trả thất bại, KHÔNG gửi lại để tránh đúp lệnh
                    self._write_file_log(
                        f"[MT5_ORDER_FAILED - {context_tag}] Không tìm thấy lệnh mở mới sau timeout ({symbol}, code={rc}) -> Trả về thất bại an toàn, không gửi lại.",
                        is_error=True
                    )
                    break

                # Lệnh mở mới hoặc đóng/sửa chỉ retry khi gặp safe_retry_codes (REQUOTE, BUSY, PRICE_CHANGED, PRICE_OFF)
                # Với lệnh đóng hoặc sửa, nếu gặp TIMEOUT/CONNECTION thì cũng cho phép retry
                can_retry_code = (rc in safe_retry_codes) or ((not is_new_order) and (rc in network_uncertain_codes or last_res is None))
                if attempt < max_retries and can_retry_code:
                    self._write_file_log(
                        f"[MT5_RETRY {attempt}/{max_retries} - {context_tag}] Sàn bận/nhảy giá ({symbol}, code={rc}, comment={cmt}) -> Lấy giá mới thử lại...",
                        is_error=True
                    )
                    continue
                break

            code_str = str(getattr(last_res, "retcode", "None"))
            comment_str = str(getattr(last_res, "comment", ""))

            # Phân loại trạng thái thị trường đóng phiên / nghỉ giao phiên của sàn (10017: Market closed / Session closed)
            ret_val = getattr(last_res, "retcode", None)
            is_session_closed = (
                ret_val == getattr(mt5, "TRADE_RETCODE_MARKET_CLOSED", 10017)
                or "session closed" in comment_str.lower()
                or "market closed" in comment_str.lower()
            )

            if is_session_closed:
                self._write_file_log(
                    f"[THÔNG BÁO - {context_tag}] {symbol}: Thị trường đang nghỉ giao phiên ({comment_str}) -> Lệnh được MT5 bảo vệ an toàn bởi SL/TP và bot sẽ tự động xử lý tiếp khi mở phiên."
                )
                return False, last_res, f"Thị trường đang đóng phiên ({comment_str})"

            self.log_error(
                context_tag,
                extra_info=f"symbol={symbol} | retcode={code_str} | comment={comment_str} | request={request}",
                show_in_ui=False
            )
            return False, last_res, f"Mã lỗi: {code_str} - {comment_str}"

    def _check_price_action_entry_confirmation(
        self, symbol: str, action: str, scalp_tf: str = "M1", atr: float = 0.0, profile_key: str = None
    ) -> tuple:
        """
        Kiểm tra lực nến và phản ứng giá (Price Action) trên khung vào lệnh (M1/M5) trước khi khớp lệnh.
        Chặn 100% việc 'bắt dao rơi' khi nến đang cắm đầu rơi tự do (Marubozu) hoặc tăng dựng đứng.
        Yêu cầu có tín hiệu hấp thụ: Rút râu nến (Wick Rejection), hoặc Nến đảo chiều, hoặc Bật nảy từ đáy/đỉnh ngắn hạn.
        """
        pk = profile_key or ("BTCUSD" if "BTC" in str(symbol or "").upper() else "XAUUSD")
        cfg = self._get_profile_cfg(pk)
        pa_fail_open = bool(cfg.get("pa_fail_open", False))
        try:
            req_conf = max(1, min(3, int(cfg.get("pa_min_confirmations", 1))))
        except (ValueError, TypeError):
            req_conf = 1

        try:
            df_recent = self.fetch_mt5_candles(symbol, scalp_tf, 5, include_forming=True)
            if df_recent is None or len(df_recent) < 2:
                if pa_fail_open:
                    return True, "Dữ liệu nến không đủ -> Cho phép theo pa_fail_open"
                return False, "Thiếu dữ liệu nến PA trên khung vào lệnh"

            with self._mt5_lock:
                tick = mt5.symbol_info_tick(symbol) if MT5_AVAILABLE else None
            if not tick:
                if pa_fail_open:
                    return True, "Không lấy được tick -> Cho phép theo pa_fail_open"
                return False, "Không lấy được tick giá hiện tại từ MT5"

            return evaluate_price_action(action, df_recent, tick.ask, tick.bid, atr, scalp_tf, req_conf)

        except Exception as e:
            if pa_fail_open:
                return True, f"Lỗi PA check ({e}) -> Cho phép theo pa_fail_open"
            return False, f"Lỗi kiểm tra Price Action ({e})"

    def _scalp_closed_beyond_sl_ai(self, symbol: str, action: str, sl_ai: float, scalp_tf: str, ctx: dict) -> bool:
        """True nếu nến ĐÃ ĐÓNG gần nhất của khung scalp có giá đóng cửa vượt SL AI (BUY: close < SL AI; SELL: close > SL AI).
        Giới hạn 1 lần đọc nến / 3 giây (kết quả cache trong ctx). Lỗi đọc nến -> False (không hủy vì thiếu dữ liệu)."""
        try:
            now_m = time.monotonic()
            cache = ctx.get("pb_ai_break_cache")
            if cache is not None and (now_m - cache[0]) < 3.0 and cache[1] == (action, round(sl_ai, 2)):
                return cache[2]
            df_c = self.fetch_mt5_candles(symbol, scalp_tf, 3, include_forming=False)
            res = False
            if df_c is not None and len(df_c) >= 1:
                last_close_c = float(df_c["close"].iloc[-1])
                res = (last_close_c < sl_ai) if action == "BUY" else (last_close_c > sl_ai)
            ctx["pb_ai_break_cache"] = (now_m, (action, round(sl_ai, 2)), res)
            return res
        except Exception:
            return False

    def _pb_structure_blocks(self, pk: str, symbol: str, action: str, cfg: dict, ctx: dict):
        """Bộ lọc cấu trúc (trend_filter) xét LẠI ngay trước khi khớp lệnh canh vùng (cấu trúc có thể đổi trong lúc chờ giá hồi).
        Trả (True, lý do) nếu cấu trúc hiện tại ngược chiều lệnh. Cache 5 giây. Lỗi đọc nến -> (False, "") (giữ hành vi cũ), có log."""
        if not bool(cfg.get("trend_filter", True)):
            return False, ""
        try:
            now_m = time.monotonic()
            cache = ctx.get("pb_struct_cache")
            if cache is not None and (now_m - cache[0]) < 5.0:
                macro_ctx = cache[1]
            else:
                scalp_tf = str(cfg.get("tf", "M5")).strip() or "M5"
                analysis_tf = str(cfg.get("analysis_tf", scalp_tf)).strip() or scalp_tf
                chart_bars = max(int(cfg.get("lookback", 450)), 450)
                df_ana = self.fetch_mt5_candles(symbol, analysis_tf, chart_bars, include_forming=False)
                df_sc = self.fetch_mt5_candles(symbol, scalp_tf, 60, include_forming=False)
                atr_sc = self.calculate_atr(df_sc, 14)
                atr_an = self.calculate_atr(df_ana, 14) if analysis_tf != scalp_tf else atr_sc
                macro_ctx = self._analyze_scalping_micro_macro_context(df_ana, df_sc, max(atr_sc, atr_an * 0.65), profile_key=pk)
                ctx["pb_struct_cache"] = (now_m, macro_ctx)
            m_dir = macro_ctx.get("macro_dir", 0)
            if (action == "BUY" and m_dir == -1) or (action == "SELL" and m_dir == 1):
                return True, str(macro_ctx.get("structure_reason", ""))
            return False, ""
        except Exception as e:
            now_ts = time.time()
            if (now_ts - getattr(self, f"_last_pb_struct_err_ts_{pk}", 0)) >= 60.0:
                setattr(self, f"_last_pb_struct_err_ts_{pk}", now_ts)
                try:
                    self.log(f"⚠️ [{pk} - Canh vùng] Không xét lại được cấu trúc trước khi khớp lệnh ({e}) -> bỏ qua bước này.")
                except Exception:
                    pass
            return False, ""

    PB_DIAG_INTERVAL_SEC = 10.0   # log chẩn đoán canh vùng: tối đa 1 lần / 10 giây / (profile, cổng)
    PB_HEARTBEAT_SEC = 60.0       # nhịp "đang canh" khi có pending

    def _pb_gate_log(self, pk: str, gate: str, symbol: str, tick, n_open=None, note: str = "", p=None):
        """Log CHẨN ĐOÁN (không ảnh hưởng logic vào lệnh): khi _check_pending_pullback_order thoát ở một cổng
        mà giá đã gần vùng kích hoạt (cách SL <= Max SL hiệu dụng + dung sai + 1.0). Giới hạn 1 lần/10s cho mỗi (profile, cổng)."""
        try:
            ctx = self.profile_ctx[pk]
            if p is None:
                p = self.pending_pullback_order if pk == self.active_profile_key else ctx.get("pending_pullback_order")
            if p is None or tick is None:
                return
            now_m = time.monotonic()
            ts_map = self.__dict__.setdefault("_pb_diag_ts", {})
            if now_m - ts_map.get((pk, gate), -1e9) < self.PB_DIAG_INTERVAL_SEC:
                return
            cfg = self._get_profile_cfg(pk)
            action = p["action"]
            cur_price = tick.ask if action == "BUY" else tick.bid
            sl_price = float(p.get("sl_ai_price", p["sl_price"]))
            dist = (cur_price - sl_price) if action == "BUY" else (sl_price - cur_price)
            cap = float(p.get("effective_max_sl", cfg.get("max_sl_price", 0.0)) or 0.0)
            if cap > 0.0:
                if dist > cap + 2.0:  # còn xa (dung sai tối đa 1.0): khỏi tra ngưỡng
                    return
                tol = float(self._get_symbol_tolerances(symbol, tick).get("max_sl_tol", 0.05))
                if dist > cap + tol + 1.0:
                    return
            ts_map[(pk, gate)] = now_m
            if bool(cfg.get("debug_trace", False)):
                self._write_trace(pk, {"event": "pb_gate", "gate": gate, "symbol": symbol, "action": action,
                                       "bid": tick.bid, "ask": tick.ask, "cur_price": cur_price, "sl_ai": sl_price,
                                       "dist_sl": round(dist, 3), "cap": cap, "n_open": n_open, "note": note,
                                       "tick_time": int(getattr(tick, "time", 0) or 0)})
            try:
                max_spread = float(cfg.get("max_spread", 0.35))
            except (ValueError, TypeError):
                max_spread = float("nan")
            try:
                max_pos = int(cfg.get("max_pos", 3))
            except (ValueError, TypeError):
                max_pos = "?"
            self.log(
                f"🔎 [{pk} - Canh vùng] cổng={gate} | bid={tick.bid:.2f} | cách SL={dist:.2f} | Max SL hiệu dụng={cap:.2f} | "
                f"spread={tick.ask - tick.bid:.2f} | max_spread={max_spread:.2f} | lệnh mở={'?' if n_open is None else n_open} | max_pos={max_pos}"
                + (f" | {note}" if note else "")
            )
        except Exception:
            pass

    def _pb_note_check(self, pk: str, tick):
        """Ghi nhận lần kiểm tra canh vùng; trả (bid lần trước, ms từ lần trước) hoặc (None, None). Chỉ phục vụ log."""
        try:
            now_m = time.monotonic()
            last = self.__dict__.setdefault("_pb_last_chk", {})
            prev = last.get(pk)
            last[pk] = (now_m, float(tick.bid))
            if prev is None:
                return None, None
            return prev[1], (now_m - prev[0]) * 1000.0
        except Exception:
            return None, None

    def _pb_heartbeat(self, pk: str, symbol: str, tick):
        """Gọi sau mỗi lần _check_pending_pullback_order: đếm lần kiểm tra; mỗi 60s, nếu có pending thì ghi 1 dòng 'đang canh'."""
        try:
            now_m = time.monotonic()
            cnt = self.__dict__.setdefault("_pb_cnt", {"busy": 0, "exc": 0})
            hb = self.__dict__.setdefault("_pb_hb", {})
            h = hb.setdefault(pk, {"t0": now_m, "n": 0, "busy0": cnt["busy"], "exc0": cnt["exc"]})
            h["n"] += 1
            if now_m - h["t0"] < self.PB_HEARTBEAT_SEC:
                return
            n, m, k = h["n"], cnt["busy"] - h["busy0"], cnt["exc"] - h["exc0"]
            h.update(t0=now_m, n=0, busy0=cnt["busy"], exc0=cnt["exc"])
            ctx = self.profile_ctx[pk]
            p = self.pending_pullback_order if pk == self.active_profile_key else ctx.get("pending_pullback_order")
            if p is None or tick is None:
                return
            cfg = self._get_profile_cfg(pk)
            tols = self._get_symbol_tolerances(symbol, tick)
            action = p["action"]
            sl_price = float(p.get("sl_ai_price", p["sl_price"]))
            cur_price = tick.ask if action == "BUY" else tick.bid
            dist = (cur_price - sl_price) if action == "BUY" else (sl_price - cur_price)
            cap = float(p.get("effective_max_sl", cfg.get("max_sl_price", 0.0)) or 0.0)
            brk = tols.get("sl_break_tol", 0.25)
            if cap > 0.0:
                far = cap + tols.get("max_sl_tol", 0.05)
                lo, hi = ((sl_price - far, sl_price - brk) if action == "SELL" else (sl_price + brk, sl_price + far))
                zone = f"[{lo:.2f}, {hi:.2f}]"
            else:
                zone = "không giới hạn (theo AI)"
            self.log(
                f"🔎 [{pk} - Canh vùng] đang canh {action} | bid={tick.bid:.2f} | vùng kích hoạt={zone} | cách SL={dist:.2f} | "
                f"lần kiểm tra trong 60s={n} | lần bị bỏ qua do khóa MT5 bận={m} | ngoại lệ vòng tick={k}"
            )
        except Exception:
            pass

    def _pb_note_exception(self, ex):
        """Đếm ngoại lệ của vòng tick (K) và ghi tối đa 1 dòng/30s. Chỉ phục vụ log."""
        try:
            cnt = self.__dict__.setdefault("_pb_cnt", {"busy": 0, "exc": 0})
            cnt["exc"] += 1
            now_m = time.monotonic()
            if now_m - self.__dict__.get("_pb_exc_log_ts", -1e9) >= 30.0:
                self._pb_exc_log_ts = now_m
                self.log(f"🔎 [Canh vùng] vòng tick ném ngoại lệ (K={cnt['exc']}): {type(ex).__name__}: {str(ex)[:100]}")
        except Exception:
            pass

    def _check_pending_pullback_order(self, symbol: str, tick, profile_key: str = None):
        """Kiểm tra mỗi 1 giây: Nếu đang có tín hiệu chờ giá hồi về <= SL tối đa thì khớp lệnh ngay khi đạt (Có khóa chống đúp lệnh)."""
        pk = profile_key or self.active_profile_key
        if self._order_in_flight.get(pk, False):
            self._pb_gate_log(pk, "order_in_flight", symbol, tick)
            return
        ctx = self.profile_ctx[pk]
        p = self.pending_pullback_order if pk == self.active_profile_key else ctx.get("pending_pullback_order")
        if p is None:
            return
        if self._is_daily_limit_locked(pk) or self._is_cooldown_active(pk, action=p.get("action")):
            self._pb_gate_log(pk, "daily_limit_cooldown", symbol, tick)
            ctx["pending_pullback_order"] = None
            if pk == self.active_profile_key:
                self.pending_pullback_order = None
            return

        if p.get("symbol") != symbol:
            return

        cfg = self._get_profile_cfg(pk)
        is_win_blocked, win_block_reason = is_trading_window_blocked(cfg=cfg)
        if is_win_blocked:
            self._pb_gate_log(pk, "trading_window", symbol, tick)
            ctx["pending_pullback_order"] = None
            if pk == self.active_profile_key:
                self.pending_pullback_order = None
            now_ts = time.time()
            if (now_ts - getattr(self, f"_last_win_block_log_ts_{pk}", 0)) >= 30.0:
                setattr(self, f"_last_win_block_log_ts_{pk}", now_ts)
                self.log(f"⏸️ [{pk} - Hủy Vùng Canh Giá] {win_block_reason} -> Hủy canh lệnh, không vào lệnh mới.")
            return

        try:
            max_sl_price = max(float(cfg.get("max_sl_price", 5.0)), 0.0)
            max_spread = float(cfg.get("max_spread", 0.35))
            lot = float(cfg.get("lot", 0.01))
            max_pos = int(cfg.get("max_pos", 3))
            rr = max(float(cfg.get("rr", 2.0)), 0.0)
        except ValueError:
            max_sl_price, max_spread, lot, max_pos, rr = 5.0, 0.35, 0.01, 3, 2.0

        action = p["action"]
        sl_price = float(p["sl_price"])
        # SL AI gốc (chưa cộng Giá an toàn). Lệnh chờ cũ không có trường này -> dùng SL thật và luật hủy theo tick như trước
        _sl_ai_raw = p.get("sl_ai_price")
        use_sl_ai = _sl_ai_raw is not None
        sl_ref = float(_sl_ai_raw) if use_sl_ai else sl_price
        cur_price = tick.ask if action == "BUY" else tick.bid
        spread_usd = tick.ask - tick.bid
        tols = self._get_symbol_tolerances(symbol, tick)

        cur_sl_dist = (cur_price - sl_ref) if action == "BUY" else (sl_ref - cur_price)

        prev_bid_chk, prev_dt_ms = self._pb_note_check(pk, tick)
        # 1. Kịch bản gãy -> hủy lệnh chờ hồi. Luật mới: nến khung scalp ĐÓNG CỬA vượt SL AI (râu đâm xuyên mà nến đóng trong vùng an toàn thì vẫn giữ);
        #    luật cũ (lệnh chờ không có sl_ai_price): giá chạm/phá SL theo tick
        if use_sl_ai:
            if self._scalp_closed_beyond_sl_ai(symbol, action, sl_ref, str(cfg.get("tf", "M5")).strip() or "M5", ctx):
                self.log(f"❌ [{pk} - Hủy Lệnh Chờ Hồi {action}] Nến {str(cfg.get('tf', 'M5')).strip() or 'M5'} đã ĐÓNG CỬA vượt SL AI ({sl_ref:.2f}) -> kịch bản gãy, hủy canh vùng.")
                ctx["pending_pullback_order"] = None
                if pk == self.active_profile_key:
                    self.pending_pullback_order = None
                return
            if cur_sl_dist <= 0.0:
                # Râu đang đâm xuyên SL AI nhưng nến chưa đóng: chưa hủy, chỉ chưa vào lệnh
                self._pb_gate_log(pk, "gia_duoi_sl_ai", symbol, tick)
                return
        elif cur_sl_dist <= tols["sl_break_tol"]:
            _gap_tag = f" | bid lần kiểm tra trước={prev_bid_chk:.2f}, cách {prev_dt_ms:.0f}ms" if prev_bid_chk is not None else " | (chưa có lần kiểm tra trước)"
            self.log(f"❌ [{pk} - Hủy Lệnh Chờ Hồi {action}] Giá ({cur_price:.2f}) đã chạm/phá qua mức SL AI ({sl_price:.2f}).{_gap_tag}")
            ctx["pending_pullback_order"] = None
            if pk == self.active_profile_key:
                self.pending_pullback_order = None
            return

        # 2. Nếu giá đã hồi về vùng cho phép: Khoảng cách SL hiện tại <= SL tối đa (hoặc max_sl_cap == 0 là theo AI)
        max_sl_cap = p.get("effective_max_sl", max_sl_price)
        if max_sl_cap <= 0.0 or cur_sl_dist <= max_sl_cap + tols["max_sl_tol"]:
            is_auto_on = self.auto_loop_running if pk == self.active_profile_key else bool(ctx.get("auto_loop_running", False))
            should_execute = is_auto_on or p.get("manual_queued", False)
            if not should_execute:
                self._pb_gate_log(pk, "auto_off", symbol, tick)
                return

            # Kiểm tra xem có lệnh ngược chiều đang âm chờ thoát hòa vốn không
            open_positions = self._get_bot_positions(symbol, profile_key=pk)
            if open_positions:
                tols = self._get_symbol_tolerances(symbol)
                eps_eq = tols.get("eps_equal", 0.05)
                # Loại bỏ các lệnh ĐÃ DỜI BE (Risk-Free) khỏi bộ đếm số lệnh tối đa
                at_risk_positions = [pos for pos in open_positions if not self._is_pos_at_be(pos, eps_eq)]
                if len(at_risk_positions) >= max_pos:
                    self._pb_gate_log(pk, "max_pos_hoac_lenh_nguoc", symbol, tick, n_open=len(open_positions), note="lý do=đủ max_pos")
                    return
                for pos in ([] if self._cfg_flag(cfg, "allow_opposite_entry") else open_positions):
                    is_buy_pos = (pos.type == mt5.POSITION_TYPE_BUY)
                    if ((action == "BUY" and not is_buy_pos) or (action == "SELL" and is_buy_pos)) and pos.profit <= 0:
                        self._pb_gate_log(pk, "max_pos_hoac_lenh_nguoc", symbol, tick, n_open=len(open_positions), note="lý do=lệnh ngược chiều đang âm")
                        return

            if spread_usd > max_spread:
                self._pb_gate_log(pk, "spread", symbol, tick, n_open=len(open_positions) if open_positions else 0)
                return

            cached_tp_val = p.get("target_tp_price") or (self.cached_tp if pk == self.active_profile_key else ctx.get("cached_tp"))
            min_rr = 2.0 if p.get("sl_tightened_for_2r") else (rr if rr > 0.0 else 2.0)
            target_tp_dist = round(cur_sl_dist * max(rr, min_rr), 2)
            if cached_tp_val is not None:
                tp_price = float(cached_tp_val)
            else:
                tp_price = round(cur_price + target_tp_dist, 2) if action == "BUY" else round(cur_price - target_tp_dist, 2)

            # Nếu giá đã chạm hoặc vượt luôn mục tiêu TP của chu kỳ AI hiện tại -> Hủy canh vùng vì sóng đã chạy xong
            if (action == "BUY" and cur_price >= tp_price) or (action == "SELL" and cur_price <= tp_price):
                self.log(f"ℹ️ [{pk} - Đạt Mục Tiêu TP AI] Giá ({cur_price:.2f}) đã chạm vùng TP ({tp_price:.2f}) trước khi vào lệnh -> Kết thúc canh vùng chu kỳ này.")
                ctx["pending_pullback_order"] = None
                if pk == self.active_profile_key:
                    self.pending_pullback_order = None
                return

            # 3. KIỂM TRA XÁC NHẬN LỰC NẾN PRICE ACTION (CHỐNG BẮT DAO RƠI):
            confirm_pa_on = bool(cfg.get("confirm_entry_pa", True))
            scalp_tf = str(cfg.get("tf", "M5")).strip() or "M5"
            if confirm_pa_on:
                p_atr = float(p.get("atr", 1.5))
                is_confirmed, pa_msg = self._check_price_action_entry_confirmation(symbol, action, scalp_tf, atr=p_atr, profile_key=pk)
                # Chờ nến đóng sau khi PA chặn vì nến lực (rơi tự do / dựng cột): không vào lại trong cùng cây nến
                if bool(cfg.get("pa_wait_bar_close", True)):
                    try:
                        _tf_sec = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600}.get(scalp_tf.upper(), 60)
                        _bar_id = int(getattr(tick, "time", 0) or 0) // _tf_sec
                        _pa_txt = str(pa_msg or "")
                        if not is_confirmed and ("rơi tự do" in _pa_txt or "dựng cột tăng mạnh" in _pa_txt):
                            ctx["pa_momo_block"] = (action, _bar_id)
                        elif is_confirmed and _bar_id > 0 and ctx.get("pa_momo_block") == (action, _bar_id):
                            is_confirmed = False
                            pa_msg = f"Chờ nến {scalp_tf} đóng (vừa bị chặn vì nến lực, không vào lại trong cùng nến)"
                    except Exception:
                        pass
                if not is_confirmed:
                    now_ts_pa = time.time()
                    if (now_ts_pa - getattr(self, f"_last_pa_log_ts_{pk}", 0)) >= 10.0:
                        setattr(self, f"_last_pa_log_ts_{pk}", now_ts_pa)
                        self.log(f"⏳ [{pk} - Canh Vùng {action}] Giá ({cur_price:.2f}) đã vào vùng khớp nhưng đang chờ xác nhận Price Action: {pa_msg}")
                    self._pb_gate_log(pk, "price_action", symbol, tick, n_open=len(open_positions) if open_positions else 0, note=f"PA: {str(pa_msg)[:80]}")
                    return

            # Kiểm tra lại RR thực tế (TP AI cache có thể làm RR thấp hơn yêu cầu) -> giữ nguyên pending, chờ giá tốt hơn
            req_rr = max(rr, 2.0)
            real_rr = abs(tp_price - cur_price) / cur_sl_dist if cur_sl_dist > 0 else 0.0
            # Dung sai làm tròn: tp_price được round 2 chữ số thập phân nên RR có thể lệch tối đa 0.005/cur_sl_dist
            if real_rr < req_rr - (0.005 / cur_sl_dist if cur_sl_dist > 0 else 0.0):
                now_ts_rr = time.time()
                if (now_ts_rr - getattr(self, f"_last_rr_log_ts_{pk}", 0)) >= 10.0:
                    setattr(self, f"_last_rr_log_ts_{pk}", now_ts_rr)
                    self.log(f"⏳ [{pk} - Canh Vùng {action}] RR thực tế {real_rr:.2f}R < {req_rr:.2f}R yêu cầu (TP {tp_price:.2f}, SL cách {cur_sl_dist:.2f}$) -> giữ canh vùng, chưa vào lệnh.")
                self._pb_gate_log(pk, "rr_thuc", symbol, tick, n_open=len(open_positions) if open_positions else 0, note=f"RR thực={real_rr:.2f} < {req_rr:.2f}")
                return

            # 4. Bộ lọc cấu trúc xét LẠI trước khi khớp (tín hiệu tạo từ nến phân tích trước, cấu trúc có thể đã đổi): chặn, giữ canh vùng
            struct_block, struct_reason = self._pb_structure_blocks(pk, symbol, action, cfg, ctx)
            if struct_block:
                now_ts_st = time.time()
                if (now_ts_st - getattr(self, f"_last_struct_log_ts_{pk}", 0)) >= 10.0:
                    setattr(self, f"_last_struct_log_ts_{pk}", now_ts_st)
                    self.log(f"🛡️ [{pk} - Canh Vùng {action}] Giá ({cur_price:.2f}) đã vào vùng nhưng Cấu Trúc hiện tại ngược chiều ({struct_reason}) -> chưa vào lệnh.")
                self._pb_gate_log(pk, "cau_truc", symbol, tick, n_open=len(open_positions) if open_positions else 0, note=f"Cấu trúc: {struct_reason[:80]}")
                return

            # Thu hồi nguyên tử (Atomic Pop) pending_pullback_order TRƯỚC khi gửi lệnh để tuyệt đối không vào đúp 2 lệnh
            ctx["pending_pullback_order"] = None
            if pk == self.active_profile_key:
                self.pending_pullback_order = None

            ok_ord, ord_msg = self.execute_mt5_order(symbol, action, lot, sl_price, tp_price, max_pos, profile_key=pk, **({"sl_ai_price": sl_ref} if use_sl_ai else {}))
            if ok_ord:
                if ctx.get("last_candle_time") is not None:
                    self._last_order_candle_ts[pk] = ctx.get("last_candle_time")
                self.log(f"[{pk} - Khớp Lệnh Tại Vùng AI (SL <= {max_sl_cap:.2f}$, RR {real_rr:.2f}R >= {req_rr:.2f}R)] Giá về vùng {cur_price:.2f} (Cách SL {cur_sl_dist:.2f}$) -> {ord_msg}")
                # Nếu là chế độ canh vùng liên tục trong chu kỳ AI (zone_watch) và chưa đạt max_pos, duy trì canh vùng cho nhịp hồi tiếp theo (vẫn tuân thủ khóa 1 lệnh/nến M1 và khoảng cách nhồi lệnh)
                if p.get("zone_watch", False) and not p.get("manual_queued", False):
                    p_next = dict(p)
                    # A03: chỉ khôi phục nếu trong lúc gửi lệnh chưa có pending mới (luồng quét AI có thể đã đặt vùng mới)
                    if ctx.get("pending_pullback_order") is None:
                        ctx["pending_pullback_order"] = p_next
                        if pk == self.active_profile_key:
                            self.pending_pullback_order = p_next
            else:
                # Nếu chưa khớp được do đang vướng khóa 1 lệnh/nến M1, chưa đủ khoảng cách nhồi lệnh, chưa BE lệnh trước, hoặc giá giật nhẹ -> tiếp tục giữ trạng thái canh vùng
                self._pb_gate_log(pk, "execute", symbol, tick, n_open=len(open_positions) if open_positions else 0, note=f"kết quả: {str(ord_msg)[:100]}", p=p)
                if p.get("zone_watch", False) or "SL hiện tại cách" in ord_msg:
                    # A03: không ghi đè pending mới do luồng quét AI đặt trong lúc gửi lệnh
                    if ctx.get("pending_pullback_order") is None:
                        ctx["pending_pullback_order"] = p
                        if pk == self.active_profile_key:
                            self.pending_pullback_order = p
                return
        else:
            # Chưa vào vùng kích hoạt: chỉ log chẩn đoán khi giá còn cách vùng <= 1.0 (còn xa thì im lặng)
            self._pb_gate_log(pk, "chua_vao_vung", symbol, tick)

    def _update_left_quick_info(self, symbol, tf_str, tick, pos_count, total_profit):
        bid_str = f"{tick.bid:.2f}" if tick else "---"
        ask_str = f"{tick.ask:.2f}" if tick else "---"
        spr_str = f"{(tick.ask - tick.bid):.2f}$" if tick else "---"

        if self.cached_mean_df is not None:
            end_pred = float(self.cached_mean_df["close"].iloc[-1])
            hi_pred = float(self.cached_mean_df["high"].max())
            lo_pred = float(self.cached_mean_df["low"].min())
            if self.pending_pullback_order is not None:
                p = self.pending_pullback_order
                try:
                    max_sl_p = float(p.get("effective_max_sl") or self.max_sl_price_var.get())
                except (ValueError, TypeError):
                    max_sl_p = 5.0
                lim_entry = (p["sl_price"] + max_sl_p) if p["action"] == "BUY" else (p["sl_price"] - max_sl_p)
                rr_tag = " (RR>=2R)" if p.get("sl_tightened_for_2r") else ""
                op_sign = "<=" if p["action"] == "BUY" else ">="
                ai_state = f"Canh vùng {p['action']}{rr_tag} {op_sign} {lim_entry:.2f}"
            else:
                ai_state = "Đã có dự báo AI"
            trend_str = self.cached_action if self.cached_action else "ĐỨNG NGOÀI (WAIT)"
            target_str = f"{end_pred:.2f} (Đỉnh:{hi_pred:.2f} / Đáy:{lo_pred:.2f})"
            sltp_str = f"SL: {self.cached_sl:.2f} | TP: {self.cached_tp:.2f}" if self.cached_sl else "---"
        else:
            ai_state = "Đang phân tích AI..." if self.is_analyzing else "Chưa quét"
            trend_str = "---"
            target_str = "---"
            sltp_str = "---"

        ana_tf = self.analysis_tf_var.get().strip() or tf_str
        tf_display = f"{tf_str}" if ana_tf == tf_str else f"AI:{ana_tf} -> Scalp:{tf_str}"
        bot_run_state = f"● ĐANG CHẠY (Canh {tf_str})" if self.auto_loop_running else "● ĐANG DỪNG (Tắt Auto)"
        if self._is_daily_limit_locked():
            cap_state = f"{self.daily_limit_reason}"
        elif self._is_cooldown_active():
            cap_state = self._get_cooldown_remaining_str()
        else:
            cap_state = f"Bình thường (Thua liên tiếp: {self.consec_loss_count})"

        pred_n = self.pred_len_var.get().strip() or "6"
        magic_num = self._get_profile_magic_number(self.active_profile_key)
        be_cnt = getattr(self, "cached_be_count", 0)
        be_display_tag = f" - {be_cnt}BE+" if be_cnt > 0 else ""
        wr_display = f"{self.cached_win_rate:.1f}% ({self.cached_win_count}W - {self.cached_loss_count}L{be_display_tag})" if self.cached_closed_count > 0 else "---"
        lic_txt = "---"
        if hasattr(self, "lic_mgr") and self.lic_mgr is not None:
            st = self.lic_mgr.check_status()
            lic_txt = st.get("badge_text", "").replace("[", "").replace("]", "").strip()
            self._update_license_badge_ui()

        info_txt = (
            f"• Tab đang chọn: {self.active_profile_key} ({symbol} - {tf_display})\n"
            f"• Magic Number : {magic_num}\n"
            f"• Bid / Ask    : {bid_str} / {ask_str} ({spr_str})\n"
            f"• Lệnh đang mở : {pos_count} lệnh ({total_profit:+.2f}$)\n"
            f"• Lãi/Lỗ Ngày (VN): {self.cached_daily_pnl:+.2f} USD\n"
            f"• Win Rate Ngày: {wr_display}\n"
            f"• Bản quyền     : {lic_txt}\n"
            f"• Trạng thái Bot: {bot_run_state}\n"
            f"• Quản lý Vốn  : {cap_state}\n"
            f"────────────────────────────\n"
            f"• Trạng thái AI: {ai_state}\n"
            f"• Tín hiệu AI  : {trend_str}\n"
            f"• Dự báo {pred_n} nến : {target_str}\n"
            f"• Gợi ý AI     : {sltp_str}"
        )
        self.quick_info_lbl.config(text=info_txt)

    def _calc_target_pnl(self, pos, target_price: float) -> float:
        """Tính số tiền Lãi/Lỗ (USD) khi giá chạm SL hoặc TP."""
        if target_price <= 0:
            return 0.0
        try:
            with self._mt5_lock:
                pnl = mt5.order_calc_profit(pos.type, pos.symbol, pos.volume, pos.price_open, target_price)
            if pnl is not None:
                return float(pnl)
        except Exception:
            pass
        # Dự phòng tính theo contract size của mã giao dịch
        with self._mt5_lock:
            sym_info = mt5.symbol_info(pos.symbol) if MT5_AVAILABLE else None
        contract_sz = float(sym_info.trade_contract_size) if (sym_info and sym_info.trade_contract_size > 0) else (1.0 if "BTC" in str(pos.symbol).upper() else 100.0)
        diff = (target_price - pos.price_open) if pos.type == mt5.POSITION_TYPE_BUY else (pos.price_open - target_price)
        return float(diff * pos.volume * contract_sz)

    INITIAL_R_STALE_SEC = 30.0  # ticket vắng mặt liên tục từng này giây trong danh sách lệnh mở thì mới bị dọn khỏi cache

    def _initial_r_path(self) -> str:
        return os.path.join(SETTINGS_DIR, "initial_r_cache.json")

    def _load_initial_r_cache(self):
        """A11: nạp initial_r_cache từ file JSON. File thiếu/hỏng/sai dạng thì bỏ qua (dùng cách suy ra 1R hiện có)."""
        try:
            path = self._initial_r_path()
            if not os.path.exists(path):
                return
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw = data.get("r") if isinstance(data, dict) else None
            if not isinstance(raw, dict):
                return
            loaded = {}
            for k, v in raw.items():
                try:
                    ticket, r_val = int(k), float(v)
                except (ValueError, TypeError):
                    continue
                if math.isfinite(r_val) and 0.0 < r_val < 1e7:
                    loaded[ticket] = r_val
            self.initial_r_cache.update(loaded)
            self._initial_r_saved = dict(self.initial_r_cache)
        except Exception:
            pass

    def _save_initial_r_cache_if_changed(self):
        """A11: ghi initial_r_cache ra file khi (và chỉ khi) nó thay đổi so với lần ghi trước; ghi atomic (.tmp rồi os.replace)."""
        try:
            snap = dict(self.initial_r_cache)
            if snap == self.__dict__.get("_initial_r_saved"):
                return
            now_m = time.monotonic()
            if now_m < self.__dict__.get("_initial_r_fail_until", 0.0):
                return
            path = self._initial_r_path()
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"version": 1, "r": {str(k): v for k, v in snap.items()}}, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
                self._initial_r_saved = snap
            except Exception as ex:
                self._initial_r_fail_until = now_m + 30.0  # lỗi ghi: thử lại sau 30s, không spam log
                self._write_file_log(f"[INITIAL_R] Không ghi được initial_r_cache.json: {type(ex).__name__}", is_error=True)
        except Exception:
            pass

    def _sync_initial_r_cache(self, pk: str, positions: list):
        """A11: ghi nhận danh sách lệnh mở của profile pk; dọn ticket không còn lệnh mở thật (chỉ khi MỌI profile có symbol đã báo cáo,
        và ticket vắng mặt liên tục >= INITIAL_R_STALE_SEC để không xóa nhầm khi MT5 đọc lỗi tạm thời); rồi ghi file nếu có thay đổi."""
        try:
            now_m = time.monotonic()
            open_map = self.__dict__.setdefault("_r_open_by_pk", {})
            open_map[pk] = {p.ticket for p in (positions or [])}
            need = [k for k in self.profile_ctx if str(self.profile_settings.get(k, {}).get("symbol", "")).strip()]
            if need and all(k in open_map for k in need):
                open_all = set().union(*(open_map[k] for k in need))
                missing = self.__dict__.setdefault("_r_missing_since", {})
                for t in list(self.initial_r_cache):
                    if t in open_all:
                        missing.pop(t, None)
                    elif now_m - missing.setdefault(t, now_m) >= self.INITIAL_R_STALE_SEC:
                        self.initial_r_cache.pop(t, None)
                        missing.pop(t, None)
                for t in list(missing):
                    if t not in self.initial_r_cache:
                        missing.pop(t, None)
            self._save_initial_r_cache_if_changed()
        except Exception:
            pass

    def _get_pos_one_r_dist(self, pos, profile_key: str = None) -> float:
        """Tính khoảng cách 1R (|Entry - SL ban đầu|) của lệnh."""
        pk = profile_key or self.active_profile_key
        cfg = self._get_profile_cfg(pk)
        ctx = self.profile_ctx[pk]
        is_buy = (pos.type == mt5.POSITION_TYPE_BUY)
        # Nếu SL đang nằm ở vùng cắt lỗ ban đầu thì ghi nhận chính xác 1R = |Entry - SL|
        if is_buy and 0 < pos.sl < pos.price_open - 0.1:
            self.initial_r_cache[pos.ticket] = abs(pos.price_open - pos.sl)
        elif (not is_buy) and pos.sl > pos.price_open + 0.1:
            self.initial_r_cache[pos.ticket] = abs(pos.sl - pos.price_open)

        if pos.ticket in self.initial_r_cache:
            return self.initial_r_cache[pos.ticket]

        # Nếu cài R:R = 0 (TP Theo AI), ưu tiên lấy khoảng cách SL gần nhất do AI tính toán
        try:
            raw_rr = float(cfg.get("rr", 2.0))
        except ValueError:
            raw_rr = 2.0

        last_rec = self.last_recommendation if pk == self.active_profile_key else ctx.get("last_recommendation")
        if raw_rr <= 0.0 and last_rec and last_rec.get("sl_dist", 0) > 0.5:
            one_r = float(last_rec["sl_dist"])
            self.initial_r_cache[pos.ticket] = one_r
            return one_r

        # Nếu lệnh đã dời SL về Entry trước khi mở App, suy ra 1R từ khoảng cách TP / RR
        if pos.tp > 0:
            rr_val = raw_rr if raw_rr > 0.0 else 1.8
            one_r = abs(pos.tp - pos.price_open) / rr_val
            if one_r > 0.2:
                self.initial_r_cache[pos.ticket] = one_r
                return one_r
        return 150.0 if "BTC" in str(pos.symbol).upper() else 5.0

    def _is_pos_at_be(self, pos, eps_eq: float = None) -> bool:
        """
        Kiểm tra xem một vị thế đã được dời SL về Hòa Vốn (BE) hoặc Trailing có lãi hay chưa.
        Lệnh đã dời BE được tính là Risk-Free (hoàn toàn không còn rủi ro thua lỗ vốn ban đầu),
        do đó sẽ được LOẠI BỎ KHỎI BỘ ĐẾM SỐ LỆNH TỐI ĐA (max_pos) khi xét điều kiện nhồi lệnh.
        """
        if pos is None or getattr(pos, "sl", 0.0) <= 0.0:
            return False
        if eps_eq is None:
            eps_eq = self._get_symbol_tolerances(getattr(pos, "symbol", "")).get("eps_equal", 0.05)
        is_buy = (pos.type == getattr(mt5, "POSITION_TYPE_BUY", 0))
        if is_buy:
            return pos.sl >= (pos.price_open - eps_eq)
        else:
            return pos.sl <= (pos.price_open + eps_eq)

    def _recovery_close_step(self, pk: str, symbol: str, positions: list, cfg: dict):
        """Gỡ vốn (khóa recovery_close, mặc định TẮT): nếu lỗ ròng đã chốt trong ngày > 0 thì đóng 1 lệnh đang lãi
        có lãi >= phần lỗ cần gỡ và CHƯA dời BE. Không đổi các luật khác. Lỗi trong hàm không ảnh hưởng luồng chính."""
        try:
            ctx = self.profile_ctx[pk]
            now_m = time.monotonic()
            # Vừa đóng 1 lệnh gỡ vốn: chờ cache lãi/lỗ đã chốt (10s) cập nhật, tránh đóng đúp
            if now_m - ctx.get("_recovery_last_ts", -1e9) < 20.0:
                return
            with self._mt5_lock:
                realized, _, _ = self._get_today_deals_stats(symbol=symbol, profile_key=pk)
            debt = -float(realized)
            if debt <= 0.05:
                return
            eps_eq = self._get_symbol_tolerances(symbol).get("eps_equal", 0.05)
            cands = [pos for pos in positions if float(pos.profit) >= debt and not self._is_pos_at_be(pos, eps_eq)]
            if not cands:
                return
            pos = max(cands, key=lambda x: float(x.profit))
            ctx["_recovery_last_ts"] = now_m
            self.log(f"🧩 [{pk} - Gỡ vốn] Lỗ ròng đã chốt hôm nay {realized:+.2f}$ -> đóng lệnh #{pos.ticket} đang lãi {pos.profit:+.2f}$ (chưa BE) để gỡ vốn")
            if bool(cfg.get("debug_trace", False)):
                self._write_trace(pk, {"event": "recovery_close", "ticket": pos.ticket, "realized": round(realized, 2),
                                       "debt": round(debt, 2), "pos_profit": round(float(pos.profit), 2),
                                       "sl": pos.sl, "tp": pos.tp})
            if threading.current_thread() is threading.main_thread():
                self._dispatch_pos_action_async(("RECOVERY_CLOSE", pos.ticket), self._close_single_pos, pos, "GOV",
                                                log_prefix=f"[{pk} - Gỡ vốn] Lệnh #{pos.ticket} -> ")
            else:
                ok_r, msg_r = self._close_single_pos(pos, "GOV")
                self.log(f"[{pk} - Gỡ vốn] Lệnh #{pos.ticket} -> {msg_r}")
        except Exception as e:
            self._write_file_log(f"[{pk}] Lỗi gỡ vốn (recovery_close): {e}", is_error=True)

    def _stack_half_be_step(self, pk: str, symbol: str, positions: list, cfg: dict):
        """Nhồi lệnh: thoát nửa số lệnh giá xấu ở BE+ (khóa stack_half_be, mặc định TẮT, người dùng chọn 19/11/2026).
        Mỗi chiều: khi số lệnh cùng chiều CHƯA dời BE tăng lên n >= 2 -> chọn n//2 lệnh giá vào XẤU NHẤT (BUY: cao nhất,
        SELL: thấp nhất) đưa vào cơ chế 'chờ về Entry+ thoát' sẵn có (pending_be_exit_tickets: vòng quản lý dời TP về
        Entry+be_plus và đóng khi giá hồi về). Lệnh còn lại giữ nguyên. Chỉ chọn lại khi số lệnh TĂNG (lệnh xấu đóng xong
        không kéo theo lệnh khác). Lỗi trong hàm không ảnh hưởng luồng chính."""
        try:
            ctx = self.profile_ctx[pk]
            prev = ctx.setdefault("_stack_half_prev_n", {})
            eps_eq = self._get_symbol_tolerances(symbol).get("eps_equal", 0.05)
            try:
                be_plus = max(float(cfg.get("be_plus_price", 5.0 if pk == "BTCUSD" else 0.2)), 0.0)
            except (TypeError, ValueError):
                be_plus = 5.0 if pk == "BTCUSD" else 0.2
            pend = self.pending_be_exit_tickets
            for is_buy in (True, False):
                key = "BUY" if is_buy else "SELL"
                side = [p for p in (positions or []) if (p.type == mt5.POSITION_TYPE_BUY) == is_buy
                        and not self._is_pos_at_be(p, eps_eq)]
                n = len(side)
                prev_n = int(prev.get(key, 0) or 0)
                prev[key] = n
                if n < 2 or n <= prev_n:
                    continue

                def _marked(p):
                    be_t = (p.price_open + be_plus) if is_buy else (p.price_open - be_plus)
                    return p.ticket in pend or (p.tp > 0 and (abs(p.tp - be_t) < eps_eq or abs(p.tp - p.price_open) < eps_eq))

                need = n // 2 - sum(1 for p in side if _marked(p))
                if need <= 0:
                    continue
                cands = sorted((p for p in side if not _marked(p)), key=lambda p: p.price_open, reverse=is_buy)
                keep_txt = ", ".join(f"{p.price_open:.2f}" for p in cands[need:])
                for p in cands[:need]:
                    pend.add(p.ticket)
                    self.log(f"✂️ [{pk} - Nhồi lệnh {n} lệnh {key}] Lệnh #{p.ticket} giá xấu ({p.price_open:.2f}) -> chờ về "
                             f"Entry+{be_plus:g} thoát; giữ các lệnh giá đẹp hơn{(': ' + keep_txt) if keep_txt else ''}")
        except Exception as e:
            self._write_file_log(f"[{pk}] Lỗi nhồi lệnh thoát nửa ở BE+ (stack_half_be): {e}", is_error=True)

    def _update_open_positions_and_manage(self, symbol: str, profile_key: str = None):
        pk = profile_key or self.active_profile_key
        cfg = self._get_profile_cfg(pk)
        ctx = self.profile_ctx[pk]
        tree_widget = self.pos_tree_by_profile.get(pk, self.pos_tree)
        summary_widget = self.pos_summary_lbl_by_profile.get(pk, self.pos_summary_lbl)

        if not symbol:
            self._write_file_log(f"[{pk}] Symbol rỗng trong _update_open_positions_and_manage -> Bỏ qua quản lý vị thế.", is_error=True)
            return 0, 0.0

        positions = self._get_bot_positions(symbol, profile_key=pk)
        if positions and bool(cfg.get("recovery_close", False)):
            self._recovery_close_step(pk, symbol, positions, cfg)
        if self._cfg_flag(cfg, "stack_half_be"):
            self._stack_half_be_step(pk, symbol, positions, cfg)

        selected_tickets = [tree_widget.item(item, "values")[0] for item in tree_widget.selection()]
        target_rows = []

        total_profit = 0.0
        auto_be = bool(cfg.get("auto_be", True))
        auto_trail = bool(cfg.get("auto_trail", True))
        default_be_plus = 5.0 if pk == "BTCUSD" else 0.2
        try:
            be_trigger_r = float(cfg.get("be_trigger_r", 1.0))
            trail_dist_r = float(cfg.get("trail_dist_r", 0.35))
        except ValueError:
            be_trigger_r, trail_dist_r = 1.0, 0.35
        try:
            be_plus_price = max(float(cfg.get("be_plus_price", default_be_plus)), 0.0)
        except ValueError:
            be_plus_price = default_be_plus

        try:
            max_hold_mins = max(float(cfg.get("max_hold_mins", 0.0)), 0.0)
        except ValueError:
            max_hold_mins = 0.0
        max_hold_action = str(cfg.get("max_hold_action", "Dời SL về BE+ (nếu lãi) & Dời TP về hòa (nếu lỗ)")).strip()
        if max_hold_action == "Chốt lời (nếu lãi) & Dời TP về hòa (nếu lỗ)":
            max_hold_action = "Dời SL về BE+ (nếu lãi) & Dời TP về hòa (nếu lỗ)"  # lựa chọn cũ đã bỏ

        tols = self._get_symbol_tolerances(symbol)
        eps_eq = tols["eps_equal"]
        min_stop_d = tols["min_stop_dist"]
        min_trail_step = tols["min_trail_step"]

        for pos in positions:
            total_profit += pos.profit
            is_buy = (pos.type == mt5.POSITION_TYPE_BUY)
            type_str = "BUY" if is_buy else "SELL"
            open_time = datetime.fromtimestamp(pos.time).strftime("%H:%M:%S")
            # A38: pos.time là giờ SERVER; quy đổi về UTC bằng độ lệch server trước khi tính thời gian giữ
            srv_off = self._server_offset_sec(pos.symbol)
            elapsed_sec = max(0, int(time.time() + srv_off - pos.time))
            dur_str = self._format_duration(elapsed_sec)
            price_move = (pos.price_current - pos.price_open) if is_buy else (pos.price_open - pos.price_current)

            one_r = self._get_pos_one_r_dist(pos, profile_key=pk)
            current_r = price_move / max(one_r, 0.1)
            trail_dist_price = one_r * trail_dist_r

            status_str = f"Đang chạy ({current_r:+.2f}R)"
            with self._mt5_lock:
                sym_info = mt5.symbol_info(pos.symbol)
            digits = sym_info.digits if sym_info else 2
            entry_exact = round(pos.price_open, digits)
            be_target_price = round((pos.price_open + be_plus_price) if is_buy else (pos.price_open - be_plus_price), digits)
            be_label_tag = f"Entry+{be_plus_price:g}" if be_plus_price > 0 else "Entry"

            # Kiểm tra trạng thái an toàn: Lệnh đã được dời SL về Hòa vốn (BE) hoặc đang Trailing Stop hay chưa
            is_be_or_trailing = False
            if self._is_pos_at_be(pos, eps_eq):
                is_be_or_trailing = True
            elif auto_be and current_r >= be_trigger_r:
                is_be_or_trailing = True

            # Quy tắc Giữ lệnh tối đa (Time-Stop): Chỉ áp dụng cho các lệnh CHƯA dời BE/Trailing (ngâm lệnh ì ạch hoặc đang âm)
            # Lệnh đã dời BE / đang Trailing là lệnh Free-Risk hoàn toàn, được tự do chạy theo sóng để gồng lãi tối đa
            # Debug trace (tắt mặc định): trạng thái lệnh khi SL/TP/BE/trailing đổi, hoặc mỗi 60s
            if bool(cfg.get("debug_trace", False)):
                try:
                    _sig = (round(pos.sl, 2), round(pos.tp, 2), is_be_or_trailing)
                    _lt = self.__dict__.setdefault("_pos_trace_last", {})
                    _prev = _lt.get(pos.ticket)
                    _now_m = time.monotonic()
                    if _prev is None or _prev[0] != _sig or _now_m - _prev[1] >= 60.0:
                        _lt[pos.ticket] = (_sig, _now_m)
                        self._write_trace(pk, {"event": "pos_state", "ticket": pos.ticket, "type": type_str,
                                               "price_open": pos.price_open, "price_current": pos.price_current,
                                               "sl": pos.sl, "tp": pos.tp, "profit": round(pos.profit, 2),
                                               "current_r": round(current_r, 3), "elapsed_sec": elapsed_sec,
                                               "be_or_trailing": is_be_or_trailing, "max_hold_mins": max_hold_mins,
                                               "max_hold_action": max_hold_action})
                except Exception:
                    pass

            is_ui_thread = (threading.current_thread() is threading.main_thread())
            if max_hold_mins > 0.0 and (elapsed_sec / 60.0) >= max_hold_mins and not is_be_or_trailing:
                if max_hold_action.startswith("Dời SL về BE+ (nếu lãi)"):
                    if pos.profit > 0.0:
                        # Lệnh đang lãi: KHÔNG đóng, dời SL về Entry+ để khóa hòa vốn rồi cho chạy tiếp.
                        # Giá còn quá sát mức BE+ (dưới khoảng cách dừng tối thiểu) thì chờ lượt sau.
                        can_buy = is_buy and (pos.sl == 0 or pos.sl < be_target_price - eps_eq * 0.5) and be_target_price < pos.price_current - min_stop_d
                        can_sell = (not is_buy) and (pos.sl == 0 or pos.sl > be_target_price + eps_eq * 0.5) and be_target_price > pos.price_current + min_stop_d
                        if can_buy or can_sell:
                            log_m = (f"⌛ [{pk} - Giữ lệnh {max_hold_mins:g}p - Đang lãi] Lệnh {type_str} #{pos.ticket} giữ {dur_str} "
                                     f"({pos.profit:+.2f}$) -> Dời SL về {be_label_tag} ({be_target_price:.2f}), giữ lệnh chạy tiếp")
                            if is_ui_thread:
                                self._dispatch_pos_action_async(("MOD_SL_BE_HOLD", pos.ticket), self._modify_position_sltp, pos, be_target_price, pos.tp, log_msg=log_m)
                            elif self._modify_position_sltp(pos, be_target_price, pos.tp):
                                self.log(log_m)
                    else:
                        if pos.ticket not in self.pending_be_exit_tickets:
                            self.pending_be_exit_tickets.add(pos.ticket)
                            if abs(pos.tp - be_target_price) >= eps_eq:
                                log_m = f"⌛ [{pk} - Giữ lệnh {max_hold_mins:g}p - Chưa BE] Lệnh #{pos.ticket} giữ {dur_str} (đang âm {pos.profit:+.2f}$) -> Đã dời TP về {be_label_tag} ({be_target_price:.2f})"
                                if is_ui_thread:
                                    self._dispatch_pos_action_async(("MOD_TP_BE_HOLD", pos.ticket), self._modify_position_sltp, pos, pos.sl, be_target_price, log_msg=log_m)
                                elif self._modify_position_sltp(pos, pos.sl, be_target_price):
                                    self.log(log_m)
                elif "Chốt lời" in max_hold_action:
                    if pos.profit > 0.0:
                        if is_ui_thread:
                            self._dispatch_pos_action_async(
                                ("CLOSE_MAX_HOLD", pos.ticket), self._close_single_pos, pos, "TIME",
                                log_prefix=f"⌛ [{pk} - Giữ lệnh {max_hold_mins:g}p - Chưa BE] Lệnh #{pos.ticket} đã giữ {dur_str} >= {max_hold_mins:g}p & có lãi nhẹ ({pos.profit:+.2f}$) -> "
                            )
                        else:
                            ok_h, msg_h = self._close_single_pos(pos, "TIME")
                            if ok_h:
                                self.log(f"⌛ [{pk} - Giữ lệnh {max_hold_mins:g}p - Chưa BE] Lệnh #{pos.ticket} đã giữ {dur_str} >= {max_hold_mins:g}p & có lãi nhẹ ({pos.profit:+.2f}$) -> {msg_h}")
                                continue
                    else:
                        if pos.ticket not in self.pending_be_exit_tickets:
                            self.pending_be_exit_tickets.add(pos.ticket)
                            if abs(pos.tp - be_target_price) >= eps_eq:
                                log_m = f"⌛ [{pk} - Giữ lệnh {max_hold_mins:g}p - Chưa BE] Lệnh #{pos.ticket} giữ {dur_str} (đang âm {pos.profit:+.2f}$) -> Đã dời TP về {be_label_tag} ({be_target_price:.2f}) để tự thoát hòa vốn!"
                                if is_ui_thread:
                                    self._dispatch_pos_action_async(("MOD_TP_BE_HOLD", pos.ticket), self._modify_position_sltp, pos, pos.sl, be_target_price, log_msg=log_m)
                                elif self._modify_position_sltp(pos, pos.sl, be_target_price):
                                    self.log(log_m)
                elif "Dời SL" in max_hold_action:
                    if is_buy and (pos.sl == 0 or pos.sl < be_target_price - eps_eq * 0.5):
                        if be_target_price < pos.price_current - min_stop_d:
                            log_m = f"⌛ [{pk} - Giữ lệnh {max_hold_mins:g}p - Chưa BE] Lệnh BUY #{pos.ticket} giữ {dur_str} -> Đã dời SL về {be_label_tag} ({be_target_price:.2f})"
                            if is_ui_thread:
                                self._dispatch_pos_action_async(("MOD_SL_BE_HOLD", pos.ticket), self._modify_position_sltp, pos, be_target_price, pos.tp, log_msg=log_m)
                            elif self._modify_position_sltp(pos, be_target_price, pos.tp):
                                self.log(log_m)
                    elif (not is_buy) and (pos.sl == 0 or pos.sl > be_target_price + eps_eq * 0.5):
                        if be_target_price > pos.price_current + min_stop_d:
                            log_m = f"⌛ [{pk} - Giữ lệnh {max_hold_mins:g}p - Chưa BE] Lệnh SELL #{pos.ticket} giữ {dur_str} -> Đã kéo SL về {be_label_tag} ({be_target_price:.2f})"
                            if is_ui_thread:
                                self._dispatch_pos_action_async(("MOD_SL_BE_HOLD", pos.ticket), self._modify_position_sltp, pos, be_target_price, pos.tp, log_msg=log_m)
                            elif self._modify_position_sltp(pos, be_target_price, pos.tp):
                                self.log(log_m)
                elif "Đóng tất cả" in max_hold_action:
                    if is_ui_thread:
                        self._dispatch_pos_action_async(
                            ("CLOSE_ALL_HOLD", pos.ticket), self._close_single_pos, pos, "TIMEALL",
                            log_prefix=f"⌛ [{pk} - Giữ lệnh {max_hold_mins:g}p - Đóng khẩn cấp] Lệnh #{pos.ticket} giữ {dur_str} -> "
                        )
                    else:
                        ok_h, msg_h = self._close_single_pos(pos, "TIMEALL")
                        if ok_h:
                            self.log(f"⌛ [{pk} - Giữ lệnh {max_hold_mins:g}p - Đóng khẩn cấp] Lệnh #{pos.ticket} giữ {dur_str} -> {msg_h}")
                            continue

            # Kiểm tra nếu lệnh đang ở chế độ "Quy tắc 3: Chờ giá hồi về Entry (+ lãi nhỏ) để thoát khi AI báo đảo chiều"
            is_waiting_be_exit = (
                pos.ticket in self.pending_be_exit_tickets
                or (pos.tp > 0 and (abs(pos.tp - be_target_price) < eps_eq or abs(pos.tp - entry_exact) < eps_eq))
            )
            if is_waiting_be_exit:
                status_str = f"Chờ về {be_label_tag} thoát ({current_r:+.2f}R)"
                # Nếu giá đã hồi về chạm/vượt mức Entry + lãi nhỏ (be_target_price) mà sàn chưa khớp TP -> Đóng ngay lập tức
                if price_move >= be_plus_price and (pos.profit >= 0.0 or be_plus_price > 0.0):
                    if is_ui_thread:
                        self._dispatch_pos_action_async(
                            ("CLOSE_WAIT_BE", pos.ticket), self._close_single_pos, pos, "BE",
                            log_prefix=f"[{pk} - Thoát {be_label_tag}] Giá hồi về {be_target_price:.2f} -> ",
                            clear_be_ticket=pos.ticket
                        )
                    else:
                        ok_be, msg_be = self._close_single_pos(pos, "BE")
                        if ok_be:
                            self.pending_be_exit_tickets.discard(pos.ticket)
                            self.log(f"[{pk} - Thoát {be_label_tag}] Giá hồi về {be_target_price:.2f} -> {msg_be}")
                            continue
                # Nếu TP chưa được đặt về be_target_price (do lúc trước giá quá sát), tự động đặt TP = be_target_price khi đủ khoảng cách
                elif abs(pos.tp - be_target_price) >= eps_eq and abs(pos.price_current - be_target_price) > min_stop_d:
                    if (is_buy and pos.price_current < be_target_price - min_stop_d) or ((not is_buy) and pos.price_current > be_target_price + min_stop_d):
                        log_m = f"[{pk} - Quy tắc 3] Đặt TP lệnh #{pos.ticket} về {be_label_tag} ({be_target_price:.2f})"
                        if is_ui_thread:
                            self._dispatch_pos_action_async(("MOD_TP_RULE3", pos.ticket), self._modify_position_sltp, pos, pos.sl, be_target_price, log_msg=log_m)
                        elif self._modify_position_sltp(pos, pos.sl, be_target_price):
                            self.log(log_m)

            if is_buy:
                if abs(pos.sl - be_target_price) < eps_eq or abs(pos.sl - entry_exact) < eps_eq:
                    status_str = f"SL = {be_label_tag} ({current_r:+.2f}R)"
                elif pos.sl > be_target_price:
                    status_str = f"Trailing ({current_r:+.2f}R)"

                if auto_be and current_r >= be_trigger_r and (pos.sl == 0 or pos.sl < be_target_price - eps_eq * 0.5):
                    if be_target_price < pos.price_current - min_stop_d:
                        log_m = f"[{pk} - Auto-SL {be_label_tag} {current_r:.2f}R] Nâng SL BUY #{pos.ticket} lên {be_target_price:.2f} (+{be_plus_price:g} giá)"
                        if is_ui_thread:
                            self._dispatch_pos_action_async(("AUTO_BE", pos.ticket), self._modify_position_sltp, pos, be_target_price, pos.tp, log_msg=log_m)
                            status_str = f"SL = {be_label_tag} ({current_r:+.2f}R)"
                        elif self._modify_position_sltp(pos, be_target_price, pos.tp):
                            self.log(log_m)
                            status_str = f"SL = {be_label_tag} ({current_r:+.2f}R)"

                if auto_trail and current_r >= (be_trigger_r + 0.3):
                    desired_sl = round(pos.price_current - trail_dist_price, digits)
                    if desired_sl > be_target_price and desired_sl > pos.sl + min_trail_step and desired_sl < pos.price_current - min_stop_d:
                        log_m = f"[{pk} - Trailing {current_r:.2f}R] Nâng SL BUY #{pos.ticket} lên {desired_sl:.2f}"
                        if is_ui_thread:
                            self._dispatch_pos_action_async(("AUTO_TRAIL", pos.ticket), self._modify_position_sltp, pos, desired_sl, pos.tp, log_msg=log_m)
                            status_str = f"Trailing ({current_r:+.2f}R)"
                        elif self._modify_position_sltp(pos, desired_sl, pos.tp):
                            self.log(log_m)
                            status_str = f"Trailing ({current_r:+.2f}R)"
            else:
                if pos.sl > 0 and (abs(pos.sl - be_target_price) < eps_eq or abs(pos.sl - entry_exact) < eps_eq):
                    status_str = f"SL = {be_label_tag} ({current_r:+.2f}R)"
                elif 0 < pos.sl < be_target_price:
                    status_str = f"Trailing ({current_r:+.2f}R)"

                if auto_be and current_r >= be_trigger_r and (pos.sl == 0 or pos.sl > be_target_price + eps_eq * 0.5):
                    if be_target_price > pos.price_current + min_stop_d:
                        log_m = f"[{pk} - Auto-SL {be_label_tag} {current_r:.2f}R] Kéo SL SELL #{pos.ticket} xuống {be_target_price:.2f} (+{be_plus_price:g} giá)"
                        if is_ui_thread:
                            self._dispatch_pos_action_async(("AUTO_BE", pos.ticket), self._modify_position_sltp, pos, be_target_price, pos.tp, log_msg=log_m)
                            status_str = f"SL = {be_label_tag} ({current_r:+.2f}R)"
                        elif self._modify_position_sltp(pos, be_target_price, pos.tp):
                            self.log(log_m)
                            status_str = f"SL = {be_label_tag} ({current_r:+.2f}R)"

                if auto_trail and current_r >= (be_trigger_r + 0.3):
                    desired_sl = round(pos.price_current + trail_dist_price, digits)
                    if desired_sl < be_target_price and (pos.sl == 0 or desired_sl < pos.sl - min_trail_step) and desired_sl > pos.price_current + min_stop_d:
                        log_m = f"[{pk} - Trailing {current_r:.2f}R] Kéo SL SELL #{pos.ticket} xuống {desired_sl:.2f}"
                        if is_ui_thread:
                            self._dispatch_pos_action_async(("AUTO_TRAIL", pos.ticket), self._modify_position_sltp, pos, desired_sl, pos.tp, log_msg=log_m)
                            status_str = f"Trailing ({current_r:+.2f}R)"
                        elif self._modify_position_sltp(pos, desired_sl, pos.tp):
                            self.log(log_m)
                            status_str = f"Trailing ({current_r:+.2f}R)"

            tag = "profit_pos" if pos.profit >= 0 else "profit_neg"
            pos_vals = (
                str(pos.ticket), open_time, dur_str, pos.symbol, type_str, f"{pos.volume:.2f}",
                f"{pos.price_open:.2f}", f"{pos.price_current:.2f}",
                f"{pos.sl:.2f}" if pos.sl > 0 else "---",
                f"{pos.tp:.2f}" if pos.tp > 0 else "---",
                f"{pos.profit:+.2f} $", status_str
            )
            target_rows.append((str(pos.ticket), pos_vals, tag))

        # Thêm toàn bộ các lệnh đã chốt hôm nay (hiển thị chung trong cùng bảng, xếp mới nhất lên đầu)
        closed_deals = self._get_today_closed_deals(symbol=symbol, profile_key=pk)

        # Kiểm tra và gửi thông báo Telegram cho các lệnh vừa đóng
        if getattr(self, "telegram_enabled_var", None) and self.telegram_enabled_var.get():
            if getattr(self, "telegram_notify_close_var", None) and self.telegram_notify_close_var.get():
                app_start_ts = getattr(self, "_app_session_start_ts", int(time.time()))
                # Đọc nhanh lệnh vừa đóng cho Telegram (không ghi bộ đệm của luật giao dịch); bảng trên app vẫn dùng closed_deals
                tg_deals = self._telegram_fresh_closed_deals(pk, symbol, positions, closed_deals)
                tg_day_pnl = sum(float(c.get("net_pnl", 0.0) or 0.0) for c in tg_deals)
                for cd in tg_deals:
                    t_id = cd.get("ticket")
                    if not t_id:
                        continue
                    # Bỏ qua nếu lệnh này đã từng được quét/gửi trước đó
                    if t_id in self._telegram_sent_deal_tickets:
                        continue
                    self._telegram_sent_deal_tickets.add(t_id)

                    # CHỈ gửi thông báo nếu lệnh đóng TỪ THỜI ĐIỂM BẬT APP TRỞ ĐI
                    close_ts = cd.get("close_ts", 0)
                    # A45: close_ts là giờ SERVER; đổi về UTC trước khi so với giờ bật app
                    if close_ts - int(self._server_offset_sec(cd.get("symbol") or "")) >= app_start_ts:
                        try:
                            self._telegram_notify_order_closed(cd, day_pnl=tg_day_pnl)
                        except Exception:
                            pass

        closed_profit_sum = 0.0
        for cd in closed_deals:
            closed_profit_sum += cd["net_pnl"]
            tag = "closed_pos" if cd.get("category") == "WIN" else ("closed_neg" if cd.get("category") == "LOSS" else "closed_be")
            r_ratio_str = f" ({cd.get('r_ratio_str', '')})" if cd.get("r_ratio_str") else ""
            cat_tag = f" [{cd.get('category', '')}]" if cd.get("category") else ""
            cd_vals = (
                str(cd["ticket"]),
                cd["time_display"],
                cd.get("duration", "---"),
                cd["symbol"],
                cd["type"],
                f"{cd['volume']:.2f}",
                f"{cd['open_price']:.2f}" if cd["open_price"] > 0 else "---",
                f"{cd['close_price']:.2f} [Đóng]",
                f"{cd.get('sl', 0.0):.2f}" if cd.get('sl', 0.0) > 0 else "---",
                f"{cd.get('tp', 0.0):.2f}" if cd.get('tp', 0.0) > 0 else "---",
                f"{cd['net_pnl']:+.2f} $",
                f"{cd['status']}{r_ratio_str}{cat_tag}"
            )
            target_rows.append((str(cd["ticket"]), cd_vals, tag))

        # Cập nhật thông minh (Diff-update) tránh xóa-dựng lại bảng mỗi giây gây giật chuột
        existing_items = tree_widget.get_children()
        same_structure = (len(existing_items) == len(target_rows))
        if same_structure:
            for item_id, (t_str, vals, tag) in zip(existing_items, target_rows):
                cur_vals = tree_widget.item(item_id, "values")
                if cur_vals and str(cur_vals[0]) == t_str:
                    if cur_vals != vals:
                        tree_widget.item(item_id, values=vals, tags=(tag,))
                else:
                    same_structure = False
                    break

        if not same_structure:
            for row in existing_items:
                tree_widget.delete(row)
            for t_str, vals, tag in target_rows:
                new_id = tree_widget.insert("", tk.END, values=vals, tags=(tag,))
                if t_str in selected_tickets:
                    tree_widget.selection_add(new_id)

        # Chốt lời chùm lệnh (Basket Take Profit): Nếu tổng lãi các lệnh đang chạy đạt mức cài đặt (basket_tp_usd > 0)
        try:
            basket_tp_usd = max(float(cfg.get("basket_tp_usd", 0.0)), 0.0)
        except ValueError:
            basket_tp_usd = 0.0

        if basket_tp_usd > 0.0 and len(positions) > 0 and total_profit >= basket_tp_usd:
            if not ctx.get("is_basket_closing", False):
                ctx["is_basket_closing"] = True
                threading.Thread(
                    target=self._execute_basket_tp_close,
                    args=(pk, symbol, total_profit, basket_tp_usd, cfg),
                    daemon=True
                ).start()

        pnl_color = GREEN_UP if total_profit >= 0 else RED_DOWN
        daily_pnl_val = self.cached_daily_pnl if pk == self.active_profile_key else ctx.get("cached_daily_pnl", 0.0)
        daily_reason_val = self.daily_limit_reason if pk == self.active_profile_key else ctx.get("daily_limit_reason", "")
        is_locked = self._is_daily_limit_locked(pk)
        daily_lock_tag = f"  |  ĐÃ DỪNG HÔM NAY ({daily_reason_val})" if is_locked else ""
        closed_count = len(closed_deals)
        win_count = sum(1 for cd in closed_deals if cd.get("category") == "WIN")
        loss_count = sum(1 for cd in closed_deals if cd.get("category") == "LOSS")
        be_count = sum(1 for cd in closed_deals if cd.get("category") == "BE+")
        valid_trades = win_count + loss_count
        if valid_trades > 0:
            win_rate = (win_count / valid_trades) * 100.0
            win_detail = f"{win_count}W - {loss_count}L" if be_count == 0 else f"{win_count}W - {loss_count}L - {be_count}BE+"
            win_rate_tag = f"  |  Win Rate: {win_rate:.1f}% ({win_detail})"
        elif be_count > 0:
            win_rate_tag = f"  |  Win Rate: --- (0W - 0L - {be_count}BE+)"
        else:
            win_rate_tag = "  |  Win Rate: ---"

        ctx["cached_win_rate"] = (win_count / valid_trades * 100.0) if valid_trades > 0 else 0.0
        ctx["cached_win_count"] = win_count
        ctx["cached_loss_count"] = loss_count
        ctx["cached_be_count"] = be_count
        ctx["cached_closed_count"] = closed_count
        if pk == self.active_profile_key:
            self.cached_win_rate = ctx["cached_win_rate"]
            self.cached_win_count = win_count
            self.cached_loss_count = loss_count
            self.cached_be_count = be_count
            self.cached_closed_count = closed_count

        # Thống kê số lệnh chưa dời BE (chịu rủi ro) vs đã dời BE (Risk-Free)
        eps_eq_sum = tols["eps_equal"]
        at_risk_c = sum(1 for p in positions if not self._is_pos_at_be(p, eps_eq_sum))
        be_c = len(positions) - at_risk_c
        be_detail_str = f" ({at_risk_c} chưa BE, {be_c} đã BE)" if len(positions) > 0 and be_c > 0 else ""

        summary_widget.config(
            text=f"[{pk} - {symbol}] Đang mở: {len(positions)} lệnh{be_detail_str} ({total_profit:+.2f}$)  |  Đã chốt hôm nay: {closed_count} lệnh ({closed_profit_sum:+.2f}$){win_rate_tag}  |  Tổng Lãi/Lỗ Ngày (VN): {daily_pnl_val:+.2f}${daily_lock_tag}",
            fg=GOLD_ACCENT if is_locked else (pnl_color if len(positions) > 0 else CYAN_LIVE)
        )
        self._sync_initial_r_cache(pk, positions)
        return len(positions), total_profit

    def _dispatch_pos_action_async(self, action_key: tuple, fn, *args, log_msg: str = None, log_prefix: str = None, clear_be_ticket: int = None):
        """Thực thi đóng/sửa SL-TP bất đồng bộ trên luồng nền có cờ chống trùng lặp để không bao giờ gây đơ UI chính."""
        if action_key in self._pos_action_in_flight:
            return
        self._pos_action_in_flight.add(action_key)

        def _worker():
            try:
                res = fn(*args)
                if isinstance(res, tuple):
                    ok, msg = res[0], res[1]
                    if ok:
                        if clear_be_ticket is not None:
                            self.pending_be_exit_tickets.discard(clear_be_ticket)
                        if log_prefix:
                            self.log(f"{log_prefix}{msg}")
                        elif log_msg:
                            self.log(log_msg)
                elif res and log_msg:
                    self.log(log_msg)
            except Exception:
                pass
            finally:
                self._pos_action_in_flight.discard(action_key)

        threading.Thread(target=_worker, daemon=True).start()

    # Ngưỡng tuổi tối đa của tick (giây) khi dùng giá để tính SL mới (tighten_sl)
    TICK_MAX_AGE_SEC = 10.0

    def _get_fresh_tick(self, symbol: str):
        """Đọc tick mới nhất; trả None nếu không có tick hoặc tick quá cũ (> TICK_MAX_AGE_SEC).
        tick.time là giờ SERVER: ước lượng độ lệch múi giờ bằng cách làm tròn (tick.time - time.time()) về bội 900 giây;
        phần dư chính là tuổi tick (chỉ tin cậy khi tuổi < 450 giây, tick cũ hơn nữa thì coi như không xác định được)."""
        with self._mt5_lock:
            tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        t_srv = float(getattr(tick, "time", 0) or 0)
        if t_srv <= 0:
            return None
        diff = t_srv - time.time()
        age = -(diff - round(diff / 900.0) * 900.0)
        if age > self.TICK_MAX_AGE_SEC:
            return None
        return tick

    def _server_offset_sec(self, symbol: str) -> float:
        """A38: độ lệch giờ server MT5 so với UTC (giây), ước lượng từ tick.time - time.time() làm tròn về bội 900 giây.
        Không có tick / lỗi -> 0.0 (giữ hành vi cũ, không chặn luồng quản lý lệnh)."""
        try:
            with self._mt5_lock:
                tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                return 0.0
            t_srv = float(getattr(tick, "time", 0) or 0)
            if t_srv <= 0:
                return 0.0
            return round((t_srv - time.time()) / 900.0) * 900.0
        except Exception:
            return 0.0

    def _modify_position_sltp(self, pos, new_sl: float, new_tp: float, allow_widen_sl: bool = False) -> bool:
        with self._mt5_lock:
            sym_info = mt5.symbol_info(pos.symbol)
            digits = sym_info.digits if sym_info else 2
            r_sl = round(new_sl, digits) if new_sl > 0 else 0.0
            r_tp = round(new_tp, digits) if new_tp > 0 else 0.0
            cur_sl = round(float(getattr(pos, "sl", 0.0) or 0.0), digits)
            cur_tp = round(float(getattr(pos, "tp", 0.0) or 0.0), digits)
            if r_sl == cur_sl and r_tp == cur_tp:
                self._modify_fail_until.pop(pos.ticket, None)
                return True
            # Backoff: ticket vừa sửa SL/TP thất bại -> bỏ qua, không gửi MT5 trong 15 giây
            if time.monotonic() < self._modify_fail_until.get(pos.ticket, 0.0):
                return False
            # A37: đọc lại vị thế mới nhất theo ticket; snapshot `pos` có thể cũ (SL đã dời BE/trailing)
            fresh_list = self._positions_get_checked(ticket=pos.ticket)
            if not fresh_list:
                self.log(f"⚠️ [MODIFY_SLTP] #{pos.ticket}: không đọc lại được vị thế (đã đóng hoặc lỗi MT5) -> bỏ qua sửa SL/TP.")
                return False
            fresh = fresh_list[0]
            f_sl = round(float(getattr(fresh, "sl", 0.0) or 0.0), digits)
            f_tp = round(float(getattr(fresh, "tp", 0.0) or 0.0), digits)
            # Chỉ đổi trường mà lời gọi muốn đổi (khác snapshot); trường còn lại lấy từ vị thế vừa đọc
            r_sl = r_sl if r_sl != cur_sl else f_sl
            r_tp = r_tp if r_tp != cur_tp else f_tp
            # Chốt chặn: không nới SL ra xa hơn SL hiện tại (trừ khi allow_widen_sl)
            widen_blocked = False
            if (not allow_widen_sl) and f_sl > 0 and r_sl != f_sl:
                is_buy_f = (fresh.type == mt5.POSITION_TYPE_BUY)
                if r_sl <= 0 or (r_sl < f_sl if is_buy_f else r_sl > f_sl):
                    now_g = time.monotonic()
                    gkey = f"guard{pos.ticket}"
                    if now_g - self._modify_fail_log_ts.get(gkey, -1e9) >= 15.0:
                        self._modify_fail_log_ts[gkey] = now_g
                        self.log(f"🛡️ [MODIFY_SLTP] #{pos.ticket}: SL mới {r_sl} sẽ nới xa SL hiện tại {f_sl} -> bỏ qua (giữ SL hiện tại).")
                    r_sl = f_sl
                    widen_blocked = True
            if r_sl == f_sl and r_tp == f_tp:
                # Không còn gì để đổi so với vị thế hiện tại: không gửi lệnh
                return not widen_blocked
            req = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": pos.ticket,
                "symbol": pos.symbol,
                "sl": r_sl,
                "tp": r_tp
            }
            ok, _, _ = self._send_mt5_order_with_retry(req, pos.symbol, context_tag=f"MODIFY_SLTP_#{pos.ticket}", max_retries=2)
            if ok:
                self._modify_fail_until.pop(pos.ticket, None)
                self._modify_fail_log_ts.pop(pos.ticket, None)
            else:
                now_m = time.monotonic()
                self._modify_fail_until[pos.ticket] = now_m + 15.0
                if now_m - self._modify_fail_log_ts.get(pos.ticket, -1e9) >= 15.0:
                    self._modify_fail_log_ts[pos.ticket] = now_m
                    self.log(f"⚠️ [MODIFY_SLTP] Sửa SL/TP #{pos.ticket} thất bại -> tạm nghỉ 15 giây rồi mới thử lại.")
            return ok

    def _close_single_pos(self, pos, reason: str = None) -> tuple:
        """Đóng 1 lệnh. reason: mã lý do ngắn (TIME, BE, GOV, REV...) ghi vào comment MT5 dạng KR_<mã> để tra lại."""
        with self._mt5_lock:
            tick = mt5.symbol_info_tick(pos.symbol)
            sym_info = mt5.symbol_info(pos.symbol)
            if tick is None or sym_info is None:
                self.log_error(f"CLOSE_POS_#{pos.ticket}", extra_info=f"Không lấy được tick/symbol_info cho {pos.symbol}", show_in_ui=False)
                return False, f"Không lấy được giá đóng cho #{pos.ticket}"
            digits = sym_info.digits
            is_buy = (pos.type == mt5.POSITION_TYPE_BUY)
            close_type = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY
            price = tick.bid if is_buy else tick.ask

            spread = max(float(tick.ask - tick.bid), 0.0)
            point = getattr(sym_info, "point", 0.01) or 0.01
            dev_val = max(25, int(math.ceil(spread * 2.0 / point)))

            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "position": pos.ticket,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": close_type,
                "price": round(price, digits),
                "deviation": dev_val,
                "magic": pos.magic,
                "comment": (f"KR_{reason}"[:31] if reason else "AI_Close"),
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            ok, _, err_detail = self._send_mt5_order_with_retry(req, pos.symbol, context_tag=f"CLOSE_POS_#{pos.ticket}", max_retries=3)
            if not ok:
                return False, f"Không đóng được #{pos.ticket} ({err_detail})"
            return True, f"Đã đóng lệnh #{pos.ticket} ({pos.symbol}) | Lãi/Lỗ: {pos.profit:+.2f}$"

    def breakeven_all_positions(self):
        """Dời SL về giá Entry + khoản lãi nhỏ (be_plus_price) cho các lệnh đang có lãi của mã giao dịch."""
        pk = self.active_profile_key
        cfg = self._get_profile_cfg(pk)
        symbol = self.symbol_var.get().strip()
        if not symbol:
            self.show_toast("Symbol rỗng, không thể dời BE.", is_error=True)
            self._write_file_log("[BREAKEVEN_ALL] Symbol rỗng -> Bỏ qua dời BE.", is_error=True)
            return
        default_be_plus = 5.0 if pk == "BTCUSD" else 0.2
        try:
            be_plus_price = max(float(cfg.get("be_plus_price", default_be_plus)), 0.0)
        except ValueError:
            be_plus_price = default_be_plus
        positions = self._get_bot_positions(symbol, profile_key=pk)
        if not positions:
            messagebox.showinfo("Thông báo", f"Hiện không có lệnh {symbol} nào của Bot đang mở trên MT5.")
            return
        tols = self._get_symbol_tolerances(symbol)
        min_be_d = max(tols["min_manual_be_dist"], tols["min_stop_dist"])
        count = 0
        for pos in positions:
            is_buy = (pos.type == mt5.POSITION_TYPE_BUY)
            target_sl = (pos.price_open + be_plus_price) if is_buy else (pos.price_open - be_plus_price)
            if is_buy and pos.price_current > target_sl + min_be_d:
                if self._modify_position_sltp(pos, target_sl, pos.tp):
                    count += 1
            elif (not is_buy) and pos.price_current < target_sl - min_be_d:
                if self._modify_position_sltp(pos, target_sl, pos.tp):
                    count += 1
        plus_tag = f" (+{be_plus_price:g} giá)" if be_plus_price > 0 else ""
        self.log(f"🛡️ Đã dời SL về Entry{plus_tag} cho {count} lệnh {symbol} đang có lãi.")
        self._update_open_positions_and_manage(symbol, profile_key=pk)

    def close_selected_position(self):
        sel = self.pos_tree.selection()
        if not sel:
            messagebox.showwarning("Chưa chọn lệnh", "Hãy bấm chọn 1 dòng lệnh trong bảng bên dưới trước khi bấm Đóng!")
            return
        ticket_raw = str(self.pos_tree.item(sel[0], "values")[0]).replace("#", "").strip()
        try:
            ticket_int = int(ticket_raw)
        except ValueError:
            return
        with self._mt5_lock:
            positions = mt5.positions_get(ticket=ticket_int)
        if positions:
            ok, msg = self._close_single_pos(positions[0], "MANUAL")
            self.log(msg)
            symbol = self.symbol_var.get().strip()
            self._update_open_positions_and_manage(symbol, profile_key=self.active_profile_key)
        else:
            self.show_toast(f"Lệnh #{ticket_int} là lệnh đã đóng trong lịch sử, không thể đóng lại.", is_error=True, duration_ms=2500)

    def _close_all_positions_for_profile(self, profile_key: str, symbol: str, reason: str = None) -> int:
        """Đóng an toàn toàn bộ lệnh của một mã cụ thể trên MT5 (dùng cho cả nút bấm tay và chốt chùm tự động)."""
        sym_str = str(symbol or "").strip()
        if not sym_str:
            self._write_file_log(f"[{profile_key}] Symbol rỗng trong _close_all_positions_for_profile -> Bỏ qua đóng lệnh.", is_error=True)
            return 0
        positions = self._get_bot_positions(sym_str, profile_key=profile_key)
        if not positions:
            return 0
        closed = 0
        for pos in positions:
            ok, msg = self._close_single_pos(pos, reason)
            self.log(msg)
            if ok:
                closed += 1
        self.log(f"🔥 [{profile_key}] Đã đóng toàn bộ {closed}/{len(positions)} lệnh {sym_str} trên MT5.")
        self._ui_after(100, lambda: self._update_open_positions_and_manage(sym_str, profile_key=profile_key))
        return closed

    def _execute_basket_tp_close(self, pk: str, symbol: str, total_profit: float, basket_tp_usd: float, cfg: dict):
        """Đóng toàn bộ các lệnh đang mở khi đạt mục tiêu Chốt lời Chùm Lệnh và kích hoạt thời gian tạm nghỉ (Basket Cooldown)."""
        try:
            self.log(f"🎉 [{pk}] ĐẠT MỤC TIÊU CHỐT LỜI CHÙM LỆNH (+{total_profit:.2f}$ >= {basket_tp_usd:.2f}$) -> Đang đóng toàn bộ lệnh...")
            closed_cnt = self._close_all_positions_for_profile(pk, symbol, "BASKET")
            cooldown_m = 0.0
            try:
                cooldown_m = max(float(cfg.get("basket_cooldown_mins", 15.0)), 0.0)
            except ValueError:
                cooldown_m = 15.0

            ctx = self.profile_ctx[pk]
            if cooldown_m > 0.0:
                ctx["basket_cooldown_until"] = datetime.now() + timedelta(minutes=cooldown_m)
                ctx["pending_pullback_order"] = None
                if pk == self.active_profile_key:
                    self.pending_pullback_order = None
                cd_end_str = ctx["basket_cooldown_until"].strftime("%H:%M:%S")
                self.log(f"[{pk}] Tạm nghỉ vào lệnh mới {cooldown_m:g} phút sau khi chốt chùm (đến {cd_end_str}).")
                self.show_toast(f"[{pk}] Chốt chùm +{total_profit:.2f}$! Tạm nghỉ {cooldown_m:g}p.", is_error=False, duration_ms=3000)
            else:
                self.show_toast(f"[{pk}] Chốt chùm +{total_profit:.2f}$ ({closed_cnt} lệnh) thành công!", is_error=False, duration_ms=2500)
        except Exception as e:
            self.log_error("BASKET_TP_CLOSE", e)
        finally:
            self.profile_ctx[pk]["is_basket_closing"] = False

    def close_all_positions(self):
        pk = self.active_profile_key
        cfg = self._get_profile_cfg(pk)
        symbol = str(cfg.get("symbol", self.symbol_var.get())).strip()
        if not symbol:
            self.show_toast("Symbol rỗng, không thể đóng lệnh.", is_error=True)
            self._write_file_log("[CLOSE_ALL] Symbol rỗng -> Bỏ qua đóng toàn bộ lệnh.", is_error=True)
            return
        positions = self._get_bot_positions(symbol, profile_key=pk)
        if not positions:
            self.show_toast(f"Hiện không có lệnh {symbol} nào của Bot đang mở trên MT5.", is_error=False)
            return
        self._close_all_positions_for_profile(pk, symbol, "ALL")

    def _algo_trading_on(self, acc):
        """Algo thật = quyền EA của tài khoản (acc.trade_expert) VÀ nút Algo Trading của terminal MT5 (terminal_info().trade_allowed)."""
        try:
            with self._mt5_lock:
                ti = mt5.terminal_info()
            term_ok = bool(ti.trade_allowed) if ti is not None else True
            return bool(acc.trade_expert) and term_ok
        except Exception:
            return bool(getattr(acc, "trade_expert", False))

    def _mt5_initialize(self) -> bool:
        """mt5.initialize theo 'Đường dẫn MT5' trong cài đặt (mặc định DEFAULT_MT5_PATH); nếu file không tồn tại thì
        dùng MT5 đã gắn được lần trước (tự tìm), cuối cùng là initialize() mặc định. Gọi bên trong _mt5_lock."""
        try:
            cfg_path = str(self._get_profile_cfg(getattr(self, "active_profile_key", "XAUUSD")).get("mt5_path", DEFAULT_MT5_PATH) or "").strip().strip('"')
        except Exception:
            cfg_path = DEFAULT_MT5_PATH
        for pth in (cfg_path, getattr(self, "_mt5_found_path", "")):
            if pth and os.path.isfile(pth):
                if mt5.initialize(path=pth):
                    return True
        return bool(mt5.initialize())

    def _schedule_mt5_reconnect(self):
        """Tự thử kết nối MT5 lại khi lỗi (MT5 đang khởi động/đăng nhập): 5s/lần trong 2 phút đầu, sau đó 30s/lần."""
        try:
            if getattr(self, "_app_closing", False) or getattr(self, "_mt5_retry_pending", False):
                return
            self._mt5_retry_count = getattr(self, "_mt5_retry_count", 0) + 1
            delay_ms = 5000 if self._mt5_retry_count <= 24 else 30000
            self._mt5_retry_pending = True

            def _retry():
                self._mt5_retry_pending = False
                if not getattr(self, "_app_closing", False):
                    self.check_mt5_connection()

            self.root.after(delay_ms, _retry)
        except Exception:
            self._mt5_retry_pending = False

    def check_mt5_connection(self):
        if not MT5_AVAILABLE:
            self.mt5_status_lbl.config(text="Chưa cài MetaTrader5", fg=RED_DOWN)
            return False
        with self._mt5_lock:
            _init_ok = self._mt5_initialize()
            if not _init_ok:
                err_c = mt5.last_error()
                # Máy có thể có nhiều bộ cài MT5: thử gắn thẳng vào từng terminal64.exe ĐANG CHẠY,
                # dùng MT5 nào đang đăng nhập được tài khoản (tài khoản người dùng đang bật trên MT5, DEMO hay THẬT đều được)
                _diag = []
                for _pth in _running_mt5_terminal_paths():
                    try:
                        mt5.shutdown()
                        if mt5.initialize(path=_pth) and mt5.account_info() is not None:
                            _diag.append(f"{_pth}: OK")
                            _init_ok = True
                            self._mt5_found_path = _pth
                            break
                        _diag.append(f"{_pth}: {mt5.last_error()}")
                    except Exception as _e:
                        _diag.append(f"{_pth}: {_e}")
                if getattr(self, "_mt5_retry_count", 0) % 20 == 0:
                    try:
                        self.log(f"🔎 [MT5] Kết nối mặc định lỗi {err_c}. MT5 đang chạy trên máy: {'; '.join(_diag) if _diag else 'không thấy terminal64.exe nào'}")
                    except Exception:
                        pass
            if not _init_ok:
                try:
                    _auth_fail = int(err_c[0]) == -6
                except Exception:
                    _auth_fail = False
                _mt5_msg = (f"MT5 đã mở nhưng CHƯA ĐĂNG NHẬP được tài khoản ({err_c})" if _auth_fail
                            else f"Chưa mở MT5 Exness ({err_c})")
                self.mt5_status_lbl.config(text=_mt5_msg, fg=RED_DOWN)
                if getattr(self, "_mt5_retry_count", 0) % 20 == 0:  # giới hạn tần suất ghi log lỗi khi tự thử lại
                    self.log_error("MT5_INIT", extra_info=_mt5_msg, show_in_ui=False)
                self._schedule_mt5_reconnect()
                return False
            acc = mt5.account_info()
            try:
                _term_path = str(getattr(mt5.terminal_info(), "path", "") or "")
            except Exception:
                _term_path = ""
        if acc is None:
            self.mt5_status_lbl.config(text="Chưa đăng nhập TK Exness", fg=RED_DOWN)
            self._schedule_mt5_reconnect()
            return False
        if getattr(self, "_mt5_retry_count", 0) > 0:
            self.log(f"🔌 [MT5] Tự kết nối lại thành công sau {self._mt5_retry_count} lần thử.")
        self._mt5_retry_count = 0
        algo_on = self._algo_trading_on(acc)
        algo_str = "Algo:BẬT" if algo_on else "Algo:TẮT (bật nút Algo Trading trên MT5!)"
        self.mt5_status_lbl.config(
            text=f"TK: {acc.login} ({'THẬT' if getattr(acc, 'trade_mode', 0) == 2 else 'DEMO'}) | Dư: {acc.balance:.2f}$ | Eq: {acc.equity:.2f}$ | {algo_str}",
            fg=GREEN_UP if algo_on else RED_DOWN
        )
        self.log(f"Kết nối MT5 thành công: TK {acc.login} ({acc.server}) | Số dư {acc.balance:.2f} {acc.currency} | MT5: {_term_path}")
        return True

    def get_local_predictor(self, model_choice: str):
        with self._ai_inference_lock:
            norm_choice = model_choice.replace("Kronos-", "AI-", 1) if model_choice.startswith("Kronos-") else model_choice
            cfg = MODEL_CONFIGS.get(norm_choice, list(MODEL_CONFIGS.values())[0])
            key = cfg["model"]
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
            if key not in self.local_cache:
                base_dir = os.path.dirname(os.path.abspath(__file__))
                weights_root = os.path.join(base_dir, "model", "weights")
                os.makedirs(weights_root, exist_ok=True)

                tok_name = cfg["tokenizer"].split("/")[-1]
                mod_name = cfg["model"].split("/")[-1]
                local_tok_dir = os.path.join(weights_root, tok_name)
                local_mod_dir = os.path.join(weights_root, mod_name)

                fb_tok = cfg.get("fallback_tokenizer", cfg["tokenizer"])
                fb_mod = cfg.get("fallback_model", cfg["model"])

                self.log(f"Đang nạp mô hình {norm_choice} vào {device.upper()}...")
                if os.path.exists(os.path.join(local_tok_dir, "model.safetensors")):
                    tokenizer = KronosTokenizer.from_pretrained(local_tok_dir)
                else:
                    if cfg.get("is_custom", False):
                        self.log(f"ℹ️ Chưa có trọng số fine-tune '{tok_name}', tự động dùng '{fb_tok}'.")
                    tokenizer = KronosTokenizer.from_pretrained(fb_tok)
                    try:
                        tokenizer.save_pretrained(local_tok_dir)
                    except Exception:
                        pass

                if os.path.exists(os.path.join(local_mod_dir, "model.safetensors")):
                    model = Kronos.from_pretrained(local_mod_dir)
                else:
                    if cfg.get("is_custom", False):
                        self.log(f"ℹ️ Chưa có trọng số fine-tune '{mod_name}', tự động dùng '{fb_mod}'.")
                    model = Kronos.from_pretrained(fb_mod)
                    try:
                        model.save_pretrained(local_mod_dir)
                    except Exception:
                        pass

                tokenizer.eval()
                model.eval()
                # Nếu đổi sang model khác, dọn dẹp model cũ trong cache để không tích tụ 2-3 model trong RAM cùng lúc
                if len(self.local_cache) > 0 and key not in self.local_cache:
                    self.local_cache.clear()
                    self._cleanup_memory_and_trim_workingset()
                self.local_cache[key] = KronosPredictor(model, tokenizer, device=device, max_context=cfg["max_context"])
                self.log(f"Đã nạp xong mô hình AI trực tiếp từ thư mục máy ({device.upper()}).")
            return self.local_cache[key]

    def _cleanup_memory_and_trim_workingset(self):
        """
        Thu hồi bộ nhớ RAM chủ động và xả bộ đệm Windows WorkingSet:
        - Dọn dẹp rác Python (gc.collect).
        - Giải phóng bộ đệm VRAM PyTorch CUDA nếu có (torch.cuda.empty_cache).
        - Gọi Windows API EmptyWorkingSet để trả lại RAM vật lý cho hệ điều hành,
          chống tràn RAM (OOM) và triệt tiêu lỗi nén trang RAM (BSOD 0x12B FAULTY_HARDWARE_CORRUPTED_PAGE).
        """
        try:
            gc.collect(generation=2)
            if torch.cuda.is_available():
                try:
                    torch.cuda.empty_cache()
                    if hasattr(torch.cuda, "ipc_collect"):
                        torch.cuda.ipc_collect()
                except Exception:
                    pass
            if sys.platform == "win32":
                try:
                    from ctypes import wintypes
                    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                    psapi = ctypes.WinDLL("psapi", use_last_error=True)
                    psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
                    psapi.EmptyWorkingSet.restype = wintypes.BOOL
                    h_proc = kernel32.GetCurrentProcess()
                    psapi.EmptyWorkingSet(h_proc)
                except Exception:
                    pass
        except Exception:
            pass

    def _compute_adaptive_sampling_params(self, df_ai: pd.DataFrame, analysis_tf: str) -> tuple[float, float, str]:
        """Nhiệt độ/top-p thích ứng của Kronos. Logic ở trading_modules/kronos_sampling.py."""
        return compute_adaptive_sampling_params(df_ai, analysis_tf)

    def _analyze_scalping_micro_macro_context(self, df_ana: pd.DataFrame, df_scalp: pd.DataFrame, atr: float, profile_key: str = None) -> dict:
        """Cấu trúc thị trường (BOS/CHoCH, order-flow, râu nến). Logic nằm ở trading_modules/structure.py; hàm này chỉ đọc cấu hình rồi gọi module.
        profile_key: profile đang phân tích (đọc đúng structure_conflict_mode của profile đó, không lấy theo tab đang mở)."""
        conflict_mode = "neutral"
        try:
            pk = profile_key or getattr(self, "active_profile_key", "XAUUSD")
            cfg = self._get_profile_cfg(pk) if hasattr(self, "_get_profile_cfg") else {}
            conflict_mode = str(cfg.get("structure_conflict_mode", "neutral")).strip().lower()
        except Exception:
            conflict_mode = "neutral"
        res = analyze_structure_context(df_ana, df_scalp, atr, conflict_mode)
        if res.get("error"):
            try:
                now_ts = time.time()
                if (now_ts - getattr(self, "_last_struct_err_log_ts", 0)) >= 60.0:
                    self._last_struct_err_log_ts = now_ts
                    self.log(f"⚠️ [Cấu trúc thị trường] Lỗi khi phân tích ({res['error']}) -> tạm coi là Cân bằng (bộ lọc cấu trúc không chặn lần này).")
            except Exception:
                pass
        return res

    def _fast_predict_scenarios(self, predictor, df_in: pd.DataFrame, x_timestamp: pd.Series, y_timestamp: pd.Series, pred_len: int = 6, num_scenarios: int = 8, T: float = 1.0, top_k: int = 0, top_p: float = 0.9):
        """Sinh N kịch bản Kronos (lõi AI). Logic ở trading_modules/kronos_engine.py; hàm này chỉ truyền khóa suy luận và hàm dọn bộ nhớ."""
        return fast_predict_scenarios(predictor, df_in, x_timestamp, y_timestamp, pred_len=pred_len, num_scenarios=num_scenarios,
                                      T=T, top_k=top_k, top_p=top_p, inference_lock=self._ai_inference_lock,
                                      cleanup=self._cleanup_memory_and_trim_workingset)

    def fetch_mt5_candles(self, symbol: str, timeframe_str: str, count: int, include_forming: bool = False):
        tf_map = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "H1": mt5.TIMEFRAME_H1
        }
        tf = tf_map.get(timeframe_str, mt5.TIMEFRAME_M5)
        start_pos = 0 if include_forming else 1

        rates = None
        for attempt in range(3):
            with self._mt5_lock:
                if not self._mt5_initialize():
                    raise RuntimeError("Không kết nối được phần mềm MT5 Exness.")
                if not mt5.symbol_select(symbol, True):
                    raise RuntimeError(f"Không tìm thấy mã '{symbol}' trên MT5.")
                rates = mt5.copy_rates_from_pos(symbol, tf, start_pos, count)
            if rates is not None and len(rates) > 0:
                break
            if attempt < 2:
                time.sleep(0.04)

        if rates is None or len(rates) == 0:
            raise RuntimeError(f"Không lấy được nến {symbol} ({timeframe_str}) từ MT5.")

        df = pd.DataFrame(rates)
        df["timestamps"] = pd.to_datetime(df["time"], unit="s")
        df["volume"] = df["tick_volume"].astype(float)
        df = df[["timestamps", "open", "high", "low", "close", "volume"]].dropna()
        return df.sort_values("timestamps").reset_index(drop=True)

    def _refresh_realtime_chart(self, symbol: str = None, tf_str: str = None, force_full_redraw: bool = False):
        active_pk = self.active_profile_key
        cfg_cur = self._get_profile_cfg(active_pk)
        symbol = symbol or str(cfg_cur.get("symbol", active_pk)).strip()
        tf_str = tf_str or str(cfg_cur.get("tf", "M5")).strip() or "M5"
        ana_tf = self.analysis_tf_var.get().strip() or tf_str
        view_tf = getattr(self, "chart_view_tf_by_profile", {}).get(active_pk, tf_str)
        active_tf = view_tf

        # Fast-path: Nếu vẫn đang cùng 1 cây nến hiện tại và không có thay đổi lệnh/SL/TP/lệnh chọn -> chỉ cập nhật tại chỗ cây nến cuối (< 2ms, siêu mượt 60 FPS)
        if (
            not force_full_redraw
            and self._chart_hist_df is not None
            and len(self._chart_hist_df) > 0
            and self._forming_wick_line is not None
            and self._forming_body_rect is not None
        ):
            try:
                df_tail = self.fetch_mt5_candles(symbol, active_tf, 2, include_forming=True)
                last_row = df_tail.iloc[-1]
                last_ts = last_row["timestamps"]
                show_ai_ov = bool(getattr(self, "show_ai_chart_overlay", True))
                cur_key = (symbol, active_tf, show_ai_ov, last_ts)

                open_positions = self._get_bot_positions(symbol, profile_key=active_pk) if MT5_AVAILABLE else None
                sel_sig = tuple(sorted(self._get_selected_tickets_for_profile(active_pk)))
                pos_sig = (
                    (tuple((p.ticket, round(p.sl, 2), round(p.tp, 2)) for p in open_positions) if open_positions else ()),
                    sel_sig
                )
                pb_sig = (
                    (self.pending_pullback_order["action"], round(self.pending_pullback_order["sl_price"], 2))
                    if self.pending_pullback_order is not None else None
                )
                ai_sl_tp_sig = (
                    round(float(self.cached_sl), 2) if getattr(self, "cached_sl", None) is not None else None,
                    round(float(self.cached_tp), 2) if getattr(self, "cached_tp", None) is not None else None,
                )

                if cur_key == self._last_drawn_chart_key and (pos_sig, pb_sig, ai_sl_tp_sig) == self._last_drawn_pos_sig:
                    idx_last = len(self._chart_hist_df) - 1
                    o = float(last_row["open"])
                    h = float(last_row["high"])
                    l = float(last_row["low"])
                    c = float(last_row["close"])
                    v = float(last_row["volume"])
                    self._chart_hist_df.loc[idx_last, ["open", "high", "low", "close", "volume"]] = [o, h, l, c, v]

                    col = CYAN_LIVE if c >= o else "#ff4081"
                    self._forming_wick_line.set_ydata([l, h])
                    self._forming_wick_line.set_color(col)
                    bottom = min(o, c)
                    height = max(abs(c - o), 0.05)
                    self._forming_body_rect.set_y(bottom)
                    self._forming_body_rect.set_height(height)
                    self._forming_body_rect.set_facecolor(col)
                    self._forming_body_rect.set_edgecolor(col)

                    last_candle_ts = last_row["timestamps"]
                    cd_str = self._get_candle_countdown_str(last_candle_ts, active_tf, symbol=symbol)

                    if self._live_price_hline is not None:
                        self._live_price_hline.set_ydata([c, c])
                    if self._live_price_annot is not None:
                        self._live_price_annot.xy = (1.0, c)
                        self._live_price_annot.set_text(f" {c:.2f} ")
                    if self._candle_countdown_annot is not None:
                        self._candle_countdown_annot.xy = (1.0, c)
                        self._candle_countdown_annot.set_text(f" {cd_str} ")

                    # Cập nhật số Lãi/Lỗ ($) đang nhảy trên nhãn Entry của các lệnh đang mở (giữ nguyên nhãn lệnh chọn nếu có)
                    if open_positions and self._pos_text_artists:
                        eps_eq = self._get_symbol_tolerances(symbol)["eps_equal"]
                        cfg_live = self._get_profile_cfg(self.active_profile_key)
                        def_be_p = 5.0 if self.active_profile_key == "BTCUSD" else 0.2
                        try:
                            be_p_live = max(float(cfg_live.get("be_plus_price", def_be_p)), 0.0)
                        except ValueError:
                            be_p_live = def_be_p
                        pos_by_idx = {i: p for i, p in enumerate(open_positions)}
                        sel_tickets_live = self._get_selected_tickets_for_profile(self.active_profile_key)
                        for txt_artist, col_idx in self._pos_text_artists:
                            txt_str = txt_artist.get_text()
                            if "Entry:" in txt_str and col_idx in pos_by_idx:
                                p_obj = pos_by_idx[col_idx]
                                is_b = (p_obj.type == mt5.POSITION_TYPE_BUY)
                                be_t = (p_obj.price_open + be_p_live) if is_b else (p_obj.price_open - be_p_live)
                                is_sl_be = (p_obj.sl > 0 and (abs(p_obj.sl - be_t) < eps_eq or abs(p_obj.sl - p_obj.price_open) < eps_eq))
                                is_tp_be = (p_obj.tp > 0 and (abs(p_obj.tp - be_t) < eps_eq or abs(p_obj.tp - p_obj.price_open) < eps_eq))
                                be_tag = f" [SL=Entry+{be_p_live:g}]" if (is_sl_be and be_p_live > 0) else (" [SL=0]" if is_sl_be else (" [Chờ Entry+]" if is_tp_be else ""))
                                prefix = f"👉 [LỆNH #{p_obj.ticket}] " if p_obj.ticket in sel_tickets_live else ""
                                txt_artist.set_text(f" {prefix}Entry: {p_obj.price_open:.2f} ({p_obj.profit:+.1f}$){be_tag} ")

                    # ── TỰ ĐỘNG KÉO TRỤC GIÁ Y THEO NẾN VÀ GIÁ LIVE KHI ĐÂM XUYÊN BIÊN ──
                    cur_y0, cur_y1 = self.ax.get_ylim()
                    span_y = max(cur_y1 - cur_y0, 1.0)
                    margin_edge = span_y * 0.04  # Vùng an toàn 4% biên trên/dưới
                    x_lim = self.ax.get_xlim()
                    n_hist = len(self._chart_hist_df) if self._chart_hist_df is not None else getattr(self, "_chart_n_hist", 0)
                    is_viewing_live_tail = (x_lim[1] >= (n_hist - 5))

                    if not self.user_zoomed:
                        # Chế độ tự động: nếu giá/râu nến chạm sát hoặc vượt biên thì tự động ôm khít lại
                        if l < cur_y0 + margin_edge or h > cur_y1 - margin_edge or c < cur_y0 + margin_edge or c > cur_y1 - margin_edge:
                            new_ylim = self._compute_visible_ylim(x_lim[0], x_lim[1])
                            if new_ylim is not None:
                                self.saved_ylim = new_ylim
                                self.ax.set_ylim(new_ylim)
                    elif is_viewing_live_tail:
                        # Chế độ thu phóng thủ công ở vùng Live: nếu giá đâm xuyên qua thì tự động tịnh tiến kéo trục Y theo giá
                        if l < cur_y0 + margin_edge:
                            shift = (cur_y0 + margin_edge) - l + (span_y * 0.06)
                            self.saved_ylim = (cur_y0 - shift, cur_y1 - shift)
                            self.ax.set_ylim(self.saved_ylim)
                        elif h > cur_y1 - margin_edge:
                            shift = h - (cur_y1 - margin_edge) + (span_y * 0.06)
                            self.saved_ylim = (cur_y0 + shift, cur_y1 + shift)
                            self.ax.set_ylim(self.saved_ylim)

                    mode_label = "BIỂU ĐỒ REAL-TIME & DỰ BÁO AI" if show_ai_ov else "BIỂU ĐỒ LỆNH REAL-TIME"
                    zoom_hint = " [Nháy đúp để Reset]" if self.user_zoomed else ""
                    if show_ai_ov and active_tf != ana_tf:
                        zoom_hint = f" [💡 Bấm nút {ana_tf} để xem 6 nến dự báo AI]"
                    self.ax.set_title(
                        f"[{mode_label}] {symbol} ({active_tf}) — Giá Live: {c:.2f} ({cd_str}){zoom_hint}",
                        color=GOLD_ACCENT, fontsize=9, fontweight="bold"
                    )
                    self._request_canvas_redraw()
                    return
            except Exception:
                pass

        try:
            chart_bars = min(max(int(self.lookback_var.get() or 450), 400), 500)
        except ValueError:
            chart_bars = 450

        # Nếu đang soi nến của một lệnh đã đóng, đảm bảo số nến tải về đủ dài để bao quát nến khớp lệnh
        sel_f_ticket = getattr(self, "_selected_deal_ticket_focus", None)
        if sel_f_ticket:
            cd_focus = self._get_closed_deal_info(sel_f_ticket, symbol=symbol)
            if cd_focus and cd_focus.get("open_ts"):
                tf_sec_m = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600}
                t_sec = tf_sec_m.get(active_tf, 300)
                bars_needed = int((time.time() - cd_focus["open_ts"]) / t_sec) + 40
                if bars_needed > chart_bars:
                    chart_bars = min(5000, max(chart_bars, bars_needed))

        df_live = self.fetch_mt5_candles(symbol, active_tf, chart_bars, include_forming=True)
        if active_tf == ana_tf:
            self.cached_df_analysis = df_live
        if active_tf == tf_str:
            self.cached_df_hist = df_live
        show_fan = (bool(getattr(self, "show_ai_chart_overlay", True)) and active_tf == ana_tf)
        self._draw_candlestick_chart(
            df_hist=df_live,
            scenarios=self.cached_scenarios if show_fan else None,
            mean_df=self.cached_mean_df if show_fan else None,
            action=self.cached_action,
            sl_price=self.cached_sl,
            tp_price=self.cached_tp,
            symbol=symbol,
            tf=active_tf,
            has_forming_candle=True
        )

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        return calculate_atr(df, period)

    def execute_mt5_order(self, symbol: str, action: str, lot: float, sl_price: float, tp_price: float, max_positions: int, profile_key: str = None, sl_ai_price: float = None):
        pk = profile_key or self.active_profile_key
        cfg = self._get_profile_cfg(pk)
        ctx = self.profile_ctx[pk]

        if hasattr(self, "lic_mgr") and self.lic_mgr is not None:
            lic_st = self.lic_mgr.check_status()
            if not lic_st.get("is_valid", False):
                return False, str(lic_st.get('message', 'Bản quyền chưa kích hoạt hoặc đã hết hạn!'))

        # Khóa chống vào đúp 2 lệnh trên cùng 1 mã trong cùng thời điểm
        with self._order_guard_lock:
            if self._order_in_flight.get(pk, False):
                return False, f"⏳ [{pk}] Đang gửi lệnh trước đó, tự động chặn đúp lệnh."
            if (time.monotonic() - self._last_order_open_mono.get(pk, 0.0)) < 3.0:
                return False, f"⏳ [{pk}] Vừa khớp lệnh cách đây < 3.0s, tự động chặn đúp lệnh."
            self._order_in_flight[pk] = True

        try:
            with self._mt5_lock:
                if not self._mt5_initialize():
                    self.log_error(f"OPEN_{action}_{pk}", extra_info="Chưa kết nối MT5", show_in_ui=False)
                    return False, "Chưa kết nối MT5."
                if self._is_daily_limit_locked(pk):
                    reason = self.daily_limit_reason if pk == self.active_profile_key else ctx.get("daily_limit_reason", "")
                    return False, f"Đã tự động dừng giao dịch hết hôm nay ({reason})."
                if self._is_cooldown_active(pk, action=action):
                    return False, f"{self._get_cooldown_remaining_str(pk)} — Chưa được phép vào lệnh mới."
                if ctx.get("deals_unknown", False):
                    # A10: không đọc được lịch sử deals -> không kiểm được lãi/lỗ ngày và chuỗi thua -> không mở lệnh mới
                    return False, f"⛔ [{pk}] Không đọc được lịch sử giao dịch hôm nay từ MT5, tạm chặn mở lệnh mới (an toàn)."

                mt5.symbol_select(symbol, True)
                raw_positions = self._positions_get_checked(symbol=symbol)
                if raw_positions is None:
                    return False, "Không đọc được danh sách lệnh MT5, bỏ qua mở lệnh."
                target_magic = self._get_profile_magic_number(pk)
                positions = [p for p in raw_positions if getattr(p, "magic", 0) == target_magic]

                tols = self._get_symbol_tolerances(symbol)
                eps_eq = tols.get("eps_equal", 0.05)
                # Loại bỏ các lệnh ĐÃ DỜI BE (Risk-Free) khỏi bộ đếm số lệnh tối đa
                at_risk_positions = [p for p in positions if not self._is_pos_at_be(p, eps_eq)]
                if len(at_risk_positions) >= max_positions:
                    return False, f"Đã có {len(at_risk_positions)}/{max_positions} lệnh chưa dời BE đang mở (tổng {len(positions)} lệnh). Bỏ qua nhồi lệnh mới."

                # Kiểm tra các quy tắc nhồi lệnh (Multi-Entry Guard) khi đã có ít nhất 1 lệnh đang chạy
                if len(positions) > 0:
                    # 1. Giới hạn 1 lệnh mỗi cây nến
                    one_per_candle = bool(cfg.get("one_order_per_candle", True))
                    last_c_time = ctx.get("last_candle_time")
                    if one_per_candle and last_c_time is not None:
                        if self._last_order_candle_ts.get(pk) == last_c_time:
                            return False, f"🛡️ [{pk}] Đã có 1 lệnh mở trong nến {last_c_time}. Chờ nến tiếp theo mới xét nhồi lệnh."

                    # 2. Khoảng cách nhồi lệnh tối thiểu (min_entry_spacing)
                    default_spacing = 50.0 if pk == "BTCUSD" else 1.0
                    try:
                        min_spacing = max(float(cfg.get("min_entry_spacing", default_spacing)), 0.0)
                    except ValueError:
                        min_spacing = default_spacing

                    tick_check = mt5.symbol_info_tick(symbol)
                    cur_chk_price = (tick_check.ask if action == "BUY" else tick_check.bid) if tick_check else 0.0
                    if min_spacing > 0.0 and cur_chk_price > 0.0:
                        for p in positions:
                            dist = abs(cur_chk_price - p.price_open)
                            if dist < (min_spacing - 1e-4):
                                return False, f"⚠️ [{pk}] Giá hiện tại ({cur_chk_price:.2f}) quá sát lệnh #{p.ticket} (Entry {p.price_open:.2f}, cách {dist:.2f} < tối thiểu {min_spacing:.2f} giá). Bỏ qua nhồi lệnh."

                    # 3. Chế độ nhồi lệnh (entry_mode)
                    entry_mode = str(cfg.get("entry_mode", "Chỉ nhồi khi đã khóa lãi lệnh trước (Risk-Free)")).strip()
                    if "Risk-Free" in entry_mode:
                        # Quy tắc Risk-Free Pyramiding: Lệnh trước cùng chiều PHẢI đã được dời SL về hòa vốn (BE+)
                        for p in positions:
                            is_p_buy = (p.type == mt5.POSITION_TYPE_BUY)
                            if is_p_buy != (action == "BUY"):
                                continue
                            is_at_be = self._is_pos_at_be(p, eps_eq)
                            if not is_at_be:
                                dir_tag = "BUY" if is_p_buy else "SELL"
                                return False, f"🛡️ [{pk} - Risk-Free] Lệnh #{p.ticket} ({dir_tag} @ {p.price_open:.2f}) chưa dời SL về hòa vốn BE (SL: {p.sl:.2f}). Chưa đủ điều kiện nhồi lệnh an toàn."

                    elif "DCA" in entry_mode and cur_chk_price > 0.0:
                        # Quy tắc DCA: Lệnh BUY mới phải thấp hơn lệnh BUY trước; Lệnh SELL mới phải cao hơn lệnh SELL trước
                        # Lệnh đã dời BE chuyển sang khâu quản lý lệnh, không dùng làm mốc so giá DCA (giống bộ đếm max_pos)
                        same_dir_pos = [p for p in positions if (p.type == mt5.POSITION_TYPE_BUY) == (action == "BUY") and not self._is_pos_at_be(p, eps_eq)]
                        if same_dir_pos:
                            if action == "BUY":
                                best_price = min(p.price_open for p in same_dir_pos)
                                if cur_chk_price > (best_price - min_spacing + 1e-4):
                                    return False, f"⚠️ [{pk} - DCA] Giá BUY hiện tại ({cur_chk_price:.2f}) không thấp hơn lệnh BUY thấp nhất ({best_price:.2f}) ít nhất {min_spacing:.2f} giá. Bỏ qua nhồi lệnh."
                            else:
                                best_price = max(p.price_open for p in same_dir_pos)
                                if cur_chk_price < (best_price + min_spacing - 1e-4):
                                    return False, f"⚠️ [{pk} - DCA] Giá SELL hiện tại ({cur_chk_price:.2f}) không cao hơn lệnh SELL cao nhất ({best_price:.2f}) ít nhất {min_spacing:.2f} giá. Bỏ qua nhồi lệnh."

                tick = mt5.symbol_info_tick(symbol)
                sym_info = mt5.symbol_info(symbol)
                if tick is None or sym_info is None:
                    self.log_error(f"OPEN_{action}_{pk}", extra_info=f"Không đọc được giá Bid/Ask của {symbol}", show_in_ui=False)
                    return False, f"Không đọc được giá Bid/Ask của {symbol}."

                digits = sym_info.digits
                order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
                price = tick.ask if action == "BUY" else tick.bid

                # Chuẩn hóa khối lượng Lot theo volume_min / volume_max / volume_step của sàn
                v_min = getattr(sym_info, "volume_min", 0.01) or 0.01
                v_max = getattr(sym_info, "volume_max", 100.0) or 100.0
                v_step = getattr(sym_info, "volume_step", 0.01) or 0.01
                step_str = f"{v_step:.8f}".rstrip("0")
                step_decimals = len(step_str.split(".")[1]) if "." in step_str else 0
                if lot < v_min:
                    return False, f"Khối lượng lot ({lot:.4f}) nhỏ hơn volume_min của sàn ({v_min:.4f})."
                steps_cnt = math.floor((lot - v_min) / v_step + 1e-9)
                norm_lot = v_min + steps_cnt * v_step
                if norm_lot > v_max:
                    norm_lot = v_max
                norm_lot = round(norm_lot, step_decimals)
                if norm_lot <= 0.0:
                    return False, f"Khối lượng lot sau chuẩn hóa ({norm_lot}) không hợp lệ (<= 0)."

                # Kiểm tra cứng điều kiện khoảng cách SL tối đa & tối ưu tỷ lệ RR >= 2R
                try:
                    max_sl_price = max(float(cfg.get("max_sl_price", 5.0)), 0.0)
                except ValueError:
                    max_sl_price = 5.0

                tols = self._get_symbol_tolerances(symbol, tick)
                actual_sl_dist = (price - sl_price) if action == "BUY" else (sl_price - price)
                dist_tp = abs(tp_price - price)

                # Kiểm tra SL sai phía hoặc quá sát giá hiện tại
                min_stop_req = tols.get("min_stop_dist", 0.50)
                if actual_sl_dist <= 0.0:
                    return False, f"Mức SL ({sl_price:.2f}) sai phía so với giá vào lệnh {action} ({price:.2f}). Bỏ qua mở lệnh."
                if actual_sl_dist < min_stop_req:
                    return False, f"Khoảng cách SL ({actual_sl_dist:.2f} giá) quá sát mức tối thiểu sàn cho phép ({min_stop_req:.2f} giá). Bỏ qua mở lệnh."

                # Khoảng cách tính SL tối đa / RR: theo SL AI gốc nếu có (Giá an toàn chỉ kéo SL thật xa thêm), không thì theo SL thật như cũ
                if sl_ai_price is not None:
                    rr_dist = (price - sl_ai_price) if action == "BUY" else (sl_ai_price - price)
                    rr_anchor = sl_ai_price
                else:
                    rr_dist = actual_sl_dist
                    rr_anchor = sl_price
                if rr_dist <= 0.0:
                    return False, f"Giá vào ({price:.2f}) đã nằm sau mức SL AI ({rr_anchor:.2f}). Chờ giá quay lại phía trên SL AI."

                eff_max_sl = max_sl_price
                if rr_dist > 0.001 and dist_tp < 2.0 * rr_dist:
                    eff_max_sl = round(max_sl_price / 2.0, 2) if max_sl_price > 0.0 else round(rr_dist / 2.0, 2)

                if eff_max_sl > 0.0 and rr_dist > eff_max_sl + tols["max_sl_tol"]:
                    limit_entry = (rr_anchor + eff_max_sl) if action == "BUY" else (rr_anchor - eff_max_sl)
                    reason_tag = " (Tự động giảm 1/2 Max SL do RR < 2R)" if eff_max_sl < max_sl_price else ""
                    return (
                        False,
                        f"⏳ SL hiện tại cách {rr_dist:.2f} giá > SL tối đa ({eff_max_sl:.2f} giá{reason_tag}). "
                        f"Phải chờ giá hồi về {'<=' if action == 'BUY' else '>='} {limit_entry:.2f} mới vào lệnh!"
                    )

                if rr_dist > 0.001 and dist_tp < 2.0 * rr_dist:
                    min_2r_tp = round(rr_dist * 2.0, digits)
                    tp_price = round(price + min_2r_tp, digits) if action == "BUY" else round(price - min_2r_tp, digits)

                spread = max(float(tick.ask - tick.bid), 0.0)
                point = getattr(sym_info, "point", 0.01) or 0.01
                dev_val = max(25, int(math.ceil(spread * 2.0 / point)))

                magic_num = self._get_profile_magic_number(pk)
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": float(norm_lot),
                    "type": order_type,
                    "price": round(price, digits),
                    "sl": round(sl_price, digits),
                    "tp": round(tp_price, digits),
                    "deviation": dev_val,
                    "magic": magic_num,
                    "comment": f"AI_{pk}",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }

                # Gọi order_check trước khi gửi lệnh để phát hiện lỗi ký quỹ hoặc thông số không hợp lệ
                # Duyệt qua các filling mode được hỗ trợ để tránh bị từ chối oan (retcode 10030)
                invalid_fill_code = getattr(mt5, "TRADE_RETCODE_INVALID_FILL", 10030)
                chk_done_codes = {getattr(mt5, "TRADE_RETCODE_DONE", 10009), 0}
                filling_modes_chk = self._get_supported_filling_modes(sym_info)
                chk_pass = False
                chk_last_res = None
                for chk_mode in filling_modes_chk:
                    request["type_filling"] = chk_mode
                    with self._mt5_lock:
                        chk_res = mt5.order_check(request)
                    if chk_res is None:
                        self._write_file_log(f"[ORDER_CHECK_WARN - {pk}] MT5 order_check trả None cho mode {chk_mode}, bỏ qua và tiếp tục gửi.", is_error=True)
                        chk_pass = True
                        break
                    chk_last_res = chk_res
                    rc_chk = getattr(chk_res, "retcode", None)
                    if rc_chk in chk_done_codes:
                        chk_pass = True
                        break
                    if rc_chk == invalid_fill_code:
                        continue
                    break
                if not chk_pass and chk_last_res is not None:
                    return False, f"❌ [{pk}] MT5 order_check từ chối: {getattr(chk_last_res, 'comment', '')} (retcode={getattr(chk_last_res, 'retcode', None)})"

                ok_send, result, err_detail = self._send_mt5_order_with_retry(
                    request, symbol, context_tag=f"OPEN_{action}_{pk}", max_retries=3
                )
                if not ok_send:
                    return False, f"❌ [{pk}] MT5 từ chối lệnh ({err_detail})"

                self._last_order_open_mono[pk] = time.monotonic()
                if ctx.get("last_candle_time") is not None:
                    self._last_order_candle_ts[pk] = ctx.get("last_candle_time")
                exec_price = float(getattr(result, "price", 0.0) or price)
                ticket_id = getattr(result, "order", 0) or getattr(result, "deal", 0)
                try:
                    self._telegram_notify_order_opened(ticket_id, symbol, action, norm_lot, exec_price, sl_price, tp_price, pk)
                except Exception:
                    pass
                return (
                    True,
                    f"[{pk}] ĐÃ KHỚP LỆNH #{ticket_id} ({action} {norm_lot} lot {symbol} @ {exec_price:.2f}) | "
                    f"SL: {sl_price:.2f} (-{actual_sl_dist:.2f} giá) | TP: {tp_price:.2f} | Magic: {magic_num}"
                )
        finally:
            with self._order_guard_lock:
                self._order_in_flight[pk] = False

    def _cancel_pending_for_new_scan(self, pk: str):
        """Nến mới, AI bắt đầu phân tích lại: hủy lệnh chờ (canh vùng) của lần phân tích trước để lệnh cũ
        không khớp trong lúc chờ kết quả mới. Lệnh đang gửi lên sàn (order_in_flight) thì không đụng."""
        try:
            ctx = self.profile_ctx[pk]
            p = self.pending_pullback_order if pk == self.active_profile_key else ctx.get("pending_pullback_order")
            if p is None:
                p = ctx.get("pending_pullback_order")
            if p is None or self._order_in_flight.get(pk, False):
                return
            ctx["pending_pullback_order"] = None
            if pk == self.active_profile_key:
                self.pending_pullback_order = None
            self.log(f"🔁 [{pk}] Nến mới: hủy lệnh chờ {p.get('action', '?')} của lần phân tích trước, chờ AI phân tích lại.")
        except Exception:
            pass

    def enqueue_ai_scan(self, profile_key: str = None, reason: str = "MANUAL", closed_candle_ts=None) -> bool:
        """
        Đưa yêu cầu phân tích AI của một mã (XAUUSD / BTCUSD) vào Hàng Đợi Tuần Tự (FIFO Queue):
        - Nếu cả 2 mã đóng nến cùng lúc trên cùng khung thời gian (VD: cùng M5), mã thứ 2 sẽ xếp hàng chờ mã thứ 1 phân tích xong.
        - Tuyệt đối không chạy chồng chéo hay trộn lẫn dữ liệu nến giữa XAUUSD và BTCUSD.
        """
        pk = profile_key or self.active_profile_key
        if pk not in self.profile_ctx:
            return False

        with self._ai_queue_lock:
            if pk in self._ai_queued_profiles or pk == self._ai_current_running_pk:
                return False
            self._ai_queued_profiles.add(pk)
            self.profile_ctx[pk]["is_analyzing"] = True
            self._cancel_pending_for_new_scan(pk)
            running_other = self._ai_current_running_pk if (self._ai_current_running_pk and self._ai_current_running_pk != pk) else None

            if pk == self.active_profile_key:
                self.is_analyzing = True
                self._ui_after(0, self._start_scan_progress_anim)

            if running_other is not None:
                wait_title = f"⏳ [{pk}] ĐANG XẾP HÀNG CHỜ AI PHÂN TÍCH XONG [{running_other}]..."
                self.profile_ctx[pk]["_sig_title"] = wait_title
                self.profile_ctx[pk]["_sig_color"] = GOLD_ACCENT
                if pk == self.active_profile_key:
                    self._sig_title = wait_title
                    self._sig_color = GOLD_ACCENT
                    self._ui_after(
                        0,
                        lambda t=wait_title: self.signal_title_lbl.config(text=t, fg=GOLD_ACCENT)
                        if hasattr(self, "signal_title_lbl") and self.signal_title_lbl.winfo_exists()
                        else None
                    )
                self.log(f"⏳ [HÀNG ĐỢI AI - {pk}] Cùng nhịp quét -> Đã xếp hàng chờ AI phân tích xong [{running_other}] trước (Tuần tự FIFO, không trộn dữ liệu)...")

            self._ai_queue.put((pk, reason, closed_candle_ts, time.monotonic()))
        return True

    def _ai_queue_consumer_loop(self):
        """Luồng duy nhất chuyên xử lý Hàng Đợi Phân Tích AI theo đúng thứ tự FIFO (First-In-First-Out)."""
        while not getattr(self, "_app_closing", False):
            try:
                item = self._ai_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:
                break
            pk, reason, closed_candle_ts, enq_mono = item
            with self._ai_queue_lock:
                self._ai_queued_profiles.discard(pk)
                self._ai_current_running_pk = pk
            try:
                wait_sec = max(0.0, time.monotonic() - enq_mono)
                if wait_sec >= 0.25:
                    self.log(f"[HÀNG ĐỢI AI - {pk}] Tới lượt phân tích AI (sau {wait_sec:.1f}s xếp hàng)...")
                self._run_analysis_worker(profile_key=pk, reason=reason, closed_candle_ts=closed_candle_ts)
            except Exception as e:
                self.log_error(f"AI_QUEUE_WORKER_{pk}", e)
            finally:
                with self._ai_queue_lock:
                    if self._ai_current_running_pk == pk:
                        self._ai_current_running_pk = None
                try:
                    self._ai_queue.task_done()
                except Exception:
                    pass

    def trigger_single_scan(self, profile_key: str = None):
        pk = profile_key or self.active_profile_key
        if pk in self.profile_ctx:
            self.profile_ctx[pk]["_last_ai_scan_config_sig"] = self._get_ai_scan_config_sig(pk)
        self.enqueue_ai_scan(profile_key=pk, reason="AUTO_CANDLE")

    def _update_bot_status_bar(self, profile_key: str = None):
        """
        Cập nhật thanh trạng thái dừng/nghỉ của Bot:
        - Lỗi hoặc dính rủi ro cắt lỗ -> Chữ màu đỏ (RED_DOWN).
        - Trạng thái dừng bình thường (tắt tự động, chốt chùm, lãi ngày) -> Chữ màu trắng (#ffffff).
        - Khi Bot đang chạy bình thường (RUNNING) -> Không hiển thị (ẩn thanh trạng thái).
        """
        if not hasattr(self, "bot_status_frame") or not self.bot_status_frame.winfo_exists():
            return
        pk = profile_key or self.active_profile_key
        ctx = self.profile_ctx.get(pk, {})
        now = datetime.now()

        is_running = self.auto_loop_running if pk == self.active_profile_key else bool(ctx.get("auto_loop_running", False))
        cd = self.cooldown_until if pk == self.active_profile_key else ctx.get("cooldown_until")
        bcd = ctx.get("basket_cooldown_until")
        fscd = self.fast_sl_cooldown_until if pk == self.active_profile_key else ctx.get("fast_sl_cooldown_until")

        status_text = ""
        text_color = "#ffffff"

        # 1. Khóa do chạm giới hạn Lãi ngày / Cắt lỗ ngày
        if self._is_daily_limit_locked(pk):
            reason = self.daily_limit_reason if pk == self.active_profile_key else ctx.get("daily_limit_reason", "")
            status_text = f"[{pk}] Tạm ngưng giao dịch do {reason} [Mở lại 00:00]"
            text_color = "#ffffff" if "LÃI" in reason.upper() else RED_DOWN

        # 2. Tạm nghỉ do lệnh dính SL nhanh (Lỗi / Cắt lỗ -> Màu đỏ)
        elif fscd is not None and now < fscd:
            rem_sec = max(0, int((fscd - now).total_seconds()))
            m, s = divmod(rem_sec, 60)
            cfg = self._get_profile_cfg(pk)
            ana_tf_str = str(cfg.get("analysis_tf", "M15")).strip()
            status_text = f"[{pk}] Tạm ngưng do SL nhanh (< 1 nến {ana_tf_str}) [{m:02d}p:{s:02d}s]"
            text_color = RED_DOWN

        # 3. Tạm nghỉ sau chuỗi thua liên tiếp (Lỗi / Cắt lỗ -> Màu đỏ)
        elif cd is not None and now < cd:
            losses = self.consec_loss_count if pk == self.active_profile_key else ctx.get("consec_loss_count", 0)
            rem_sec = max(0, int((cd - now).total_seconds()))
            m, s = divmod(rem_sec, 60)
            status_text = f"[{pk}] Tạm ngưng giao dịch do chuỗi thua {losses} lệnh [{m:02d}p:{s:02d}s]"
            text_color = RED_DOWN

        # 4. Tạm nghỉ sau khi chốt lời chùm lệnh (Bình thường sau chốt lời -> Màu trắng)
        elif bcd is not None and now < bcd:
            rem_sec = max(0, int((bcd - now).total_seconds()))
            m, s = divmod(rem_sec, 60)
            status_text = f"[{pk}] Tạm ngưng giao dịch do sau chốt chùm [{m:02d}p:{s:02d}s]"
            text_color = "#ffffff"

        # 5. Bot AUTO đang TẮT (Thủ công / Dừng bình thường -> Màu trắng)
        elif not is_running:
            status_text = f"[{pk}] Tạm ngưng giao dịch (Tắt tự động)"
            text_color = "#ffffff"

        # 6. Bot đang chạy bình thường (is_running = True và không bị vi phạm/tạm ngưng) -> Ẩn thanh trạng thái
        else:
            status_text = ""

        if not status_text:
            self.bot_status_text_lbl.config(text="")
            self.bot_status_frame.configure(highlightbackground="#121824")
        else:
            self.bot_status_frame.configure(highlightbackground="#222f44")
            self.bot_status_text_lbl.config(text=status_text, fg=text_color)

    def _get_live_bot_status_text(self, profile_key: str = None) -> str:
        """Lấy chuỗi trạng thái thực tế của Bot theo đúng Tab để hiển thị đồng bộ trên thanh tín hiệu."""
        pk = profile_key or self.active_profile_key
        ctx = self.profile_ctx[pk]
        if self._is_daily_limit_locked(pk):
            reason = self.daily_limit_reason if pk == self.active_profile_key else ctx.get("daily_limit_reason", "")
            return f"Tạm ngưng giao dịch do {reason} [Mở lại 00:00]"
        if self._is_cooldown_active(pk):
            return self._get_cooldown_remaining_str(pk)
        is_running = self.auto_loop_running if pk == self.active_profile_key else bool(ctx.get("auto_loop_running", False))
        if is_running:
            return ""
        return f"Bot {pk} tạm ngưng giao dịch (Tắt tự động)"

    def _sync_signal_detail_status(self):
        """Cập nhật ngay lập tức dòng trạng thái Bot trên thanh tín hiệu khi người dùng gạt công tắc AUTO."""
        self._update_bot_status_bar(self.active_profile_key)

    def toggle_bot_start_stop(self):
        """Gộp chung START và STOP trên 1 công tắc gạt thay đổi trạng thái riêng cho Tab đang mở."""
        if self.auto_loop_running:
            self.stop_auto_scan()
        else:
            self.start_auto_scan()

    def start_auto_scan(self, profile_key: str = None):
        pk = profile_key or self.active_profile_key
        if self.profile_ctx[pk].get("auto_loop_running", False):
            return
        if hasattr(self, "lic_mgr") and self.lic_mgr is not None:
            lic_st = self.lic_mgr.check_status(force_refresh=False)
            if not lic_st.get("is_valid", False):
                self.show_toast("Bản quyền chưa kích hoạt hoặc đã hết hạn!", is_error=True, duration_ms=3000)
                self._open_license_dialog()
                return

        self.profile_ctx[pk]["auto_loop_running"] = True
        self._auto_worker_token[pk] = self._auto_worker_token.get(pk, 0) + 1
        worker_token = self._auto_worker_token[pk]

        if pk == self.active_profile_key:
            self.auto_loop_running = True
            self.auto_trade_var.set(True)
            # Bắt đầu animation trượt mượt mà ngay lập tức (0ms phản hồi tức thì)
            self._animate_toggle_switch(target_on=True)
            self._sync_signal_detail_status()

        cfg = self._get_profile_cfg(pk)
        sym = str(cfg.get("symbol", pk)).strip()
        tf_val = str(cfg.get("tf", "M5")).strip()
        ana_tf_val = str(cfg.get("analysis_tf", tf_val)).strip() or tf_val
        mode_desc = f"AI phân tích đóng nến {ana_tf_val} -> Python canh vùng giá {tf_val}/Tick" if ana_tf_val != tf_val else f"Canh đóng nến {tf_val} & bám sát vùng giá AI"
        self.log(f"[{pk} - {sym}] Đã BẬT công tắc AUTO ({mode_desc})...")
        if self.profile_ctx[pk].get("cached_mean_df") is None and not self.profile_ctx[pk].get("is_analyzing", False):
            self.enqueue_ai_scan(profile_key=pk, reason="AUTO_CANDLE")
        threading.Thread(target=self._auto_loop_worker, args=(pk, worker_token), daemon=True).start()

    def stop_auto_scan(self, profile_key: str = None):
        pk = profile_key or self.active_profile_key
        self.profile_ctx[pk]["auto_loop_running"] = False
        self._auto_worker_token[pk] = self._auto_worker_token.get(pk, 0) + 1
        self.pending_pullback_order = None
        self.profile_ctx[pk]["pending_pullback_order"] = None

        if pk == self.active_profile_key:
            self.auto_loop_running = False
            self.auto_trade_var.set(False)
            # Bắt đầu animation trượt mượt mà ngay lập tức (0ms phản hồi tức thì)
            self._animate_toggle_switch(target_on=False)
            self._sync_signal_detail_status()

        cfg = self._get_profile_cfg(pk)
        sym = str(cfg.get("symbol", pk)).strip()
        self.log(f"⏹️ [{pk} - {sym}] Đã TẮT công tắc AUTO (Ngừng canh nến & tắt tự động vào lệnh).")

    def _auto_loop_worker(self, profile_key: str = None, worker_token: int = None):
        pk = profile_key or self.active_profile_key
        if worker_token is None:
            worker_token = self._auto_worker_token.get(pk, 0)
        conn_err_logged = False
        while (
            not getattr(self, "_app_closing", False)
            and self.profile_ctx[pk].get("auto_loop_running", False)
            and self._auto_worker_token.get(pk, 0) == worker_token
        ):
            sleep_chunks = 4
            try:
                cfg = self._get_profile_cfg(pk)
                ctx = self.profile_ctx[pk]
                symbol = str(cfg.get("symbol", "")).strip()
                scalp_tf = str(cfg.get("tf", "M5")).strip() or "M5"
                ana_tf = str(cfg.get("analysis_tf", scalp_tf)).strip() or scalp_tf

                # 1. Cập nhật mốc thời gian nến Khung Vào Lệnh (scalp_tf, VD: M1) để quản lý giới hạn tối đa 1 lệnh/nến M1
                df_scalp_chk = self.fetch_mt5_candles(symbol, scalp_tf, 2, include_forming=False)
                if len(df_scalp_chk) > 0:
                    latest_scalp_ts = df_scalp_chk["timestamps"].iloc[-1]
                    ctx["last_candle_time"] = latest_scalp_ts
                    if pk == self.active_profile_key:
                        self.last_candle_time = latest_scalp_ts

                # 2. Chỉ kích hoạt phân tích AI khi đóng nến Khung Phân Tích (ana_tf, VD: M5 — 5 phút/lần)
                # Trong suốt chu kỳ 5 phút đó, Python giữ cố định vùng Entry/SL/TP của AI và canh giá Tick/M1 để khớp lệnh tức thì!
                df_check = df_scalp_chk if ana_tf == scalp_tf else self.fetch_mt5_candles(symbol, ana_tf, 3, include_forming=False)
                if (
                    not self.profile_ctx[pk].get("auto_loop_running", False)
                    or self._auto_worker_token.get(pk, 0) != worker_token
                ):
                    break

                if conn_err_logged:
                    conn_err_logged = False
                    self.log(f"🟢 [{pk} - {symbol}] Đã kết nối lại thành công với MT5 Exness! Tiếp tục tự động canh nến.")

                latest_ana_time = df_check["timestamps"].iloc[-1]
                last_ana_ct = ctx.get("last_ana_candle_time")
                is_ana = ctx.get("is_analyzing", False)
                if last_ana_ct != latest_ana_time and not is_ana:
                    ctx["last_ana_candle_time"] = latest_ana_time
                    tf_msg = f"{ana_tf} (Canh vùng {scalp_tf})" if ana_tf != scalp_tf else ana_tf
                    self.log(f"🔔 [{pk} - {symbol}] Nến phân tích {tf_msg} mới vừa đóng ({latest_ana_time}). Đưa vào hàng đợi phân tích AI...")
                    self.enqueue_ai_scan(profile_key=pk, reason="AUTO_CANDLE", closed_candle_ts=latest_ana_time)

                # Định kỳ xả bộ nhớ đệm RAM mỗi 30 nhịp lặp (~15s) để chống đầy RAM
                ctx["_auto_clean_cnt"] = ctx.get("_auto_clean_cnt", 0) + 1
                if ctx["_auto_clean_cnt"] >= 30:
                    ctx["_auto_clean_cnt"] = 0
                    self._cleanup_memory_and_trim_workingset()
            except Exception as e:
                if not conn_err_logged:
                    conn_err_logged = True
                    self.log(f"⚠️ [{pk}] Đang chờ kết nối MT5 Exness (Máy chủ sàn đang bảo trì hoặc mất kết nối: {e}). Bot sẽ tự kết nối lại ngay khi sàn mở lại...")
                    self.log_error(f"AUTO_CANDLE_WATCH_{pk}", e, show_in_ui=False)
                else:
                    self.log_error(f"AUTO_CANDLE_WATCH_{pk}", e, show_in_ui=False)
                sleep_chunks = 10  # Khi mất kết nối: giãn cách 5 giây (10 * 0.5s) thay vì 2s để tránh nghẽn CPU
            for _ in range(sleep_chunks):
                if (
                    getattr(self, "_app_closing", False)
                    or not self.profile_ctx[pk].get("auto_loop_running", False)
                    or self._auto_worker_token.get(pk, 0) != worker_token
                ):
                    return
                time.sleep(0.5)

    def _run_analysis_worker(self, profile_key: str = None, reason: str = "MANUAL", closed_candle_ts=None):
        pk = profile_key or self.active_profile_key
        self.profile_ctx[pk]["is_analyzing"] = True
        if pk == self.active_profile_key:
            self.is_analyzing = True
            self._ui_after(0, self._start_scan_progress_anim)
        try:
            with self._ai_scan_lock:
                # Chụp bản sao cấu hình độc lập của đúng hồ sơ pk (XAUUSD hoặc BTCUSD) tại thời điểm bắt đầu phân tích
                cfg = dict(self._get_profile_cfg(pk))
                ctx = self.profile_ctx[pk]
                ctx["_last_ai_scan_config_sig"] = self._get_ai_scan_config_sig(pk)

                # Kiểm tra Khung giờ cấm giao dịch (Trading Window Blocked - UTC+7): Không mở zone mới
                is_win_blocked, win_block_reason = is_trading_window_blocked(cfg=cfg)
                if is_win_blocked:
                    ctx["pending_pullback_order"] = None
                    if pk == self.active_profile_key:
                        self.pending_pullback_order = None
                    if bool(cfg.get("debug_trace", False)):
                        self._write_trace(pk, {"event": "window_blocked", "reason": win_block_reason})
                    self.log(f"⏸️ [{pk}] {win_block_reason} -> Tạm dừng phân tích, không mở zone mới.")
                    ctx["_sig_title"] = f"⚪ [{pk}] ĐỨNG NGOÀI (GIỜ CẤM GIAO DỊCH) — {win_block_reason}"
                    ctx["_sig_color"] = "#ffffff"
                    if pk == self.active_profile_key:
                        self._sig_title = ctx["_sig_title"]
                        self._sig_color = ctx["_sig_color"]
                        self._update_bot_status_bar(pk)
                    return
                default_sym = DEFAULT_PROFILE_SETTINGS.get(pk, {}).get("symbol", "XAUUSDm")
                symbol = str(cfg.get("symbol", default_sym)).strip() or default_sym
                scalp_tf = str(cfg.get("tf", "M5")).strip() or "M5"
                analysis_tf = str(cfg.get("analysis_tf", scalp_tf)).strip() or scalp_tf
                tf = scalp_tf
                tf_label = scalp_tf if analysis_tf == scalp_tf else f"AI:{analysis_tf} -> Scalp:{scalp_tf}"
                lookback = int(cfg.get("lookback", 450))
                pred_len = int(cfg.get("pred_len", 6))
                num_scenarios = int(cfg.get("scenarios", 8))
                min_consensus = float(cfg.get("consensus", 75.0))
                max_dispersion = float(cfg.get("max_dispersion", 2.5))
                min_move = float(cfg.get("min_move", 1.2))
                try:
                    rev_consensus = float(cfg.get("rev_consensus", min_consensus))
                except ValueError:
                    rev_consensus = min_consensus
                try:
                    rev_min_move = max(float(cfg.get("rev_min_move", 0.0)), 0.0)
                except ValueError:
                    rev_min_move = 0.0
                default_be_plus = 5.0 if pk == "BTCUSD" else 0.2
                try:
                    be_plus_price = max(float(cfg.get("be_plus_price", default_be_plus)), 0.0)
                except ValueError:
                    be_plus_price = default_be_plus
                max_spread = float(cfg.get("max_spread", 0.35))
                lot = float(cfg.get("lot", 0.01))
                sl_atr = float(cfg.get("sl_atr", 1.5))
                sl_buffer = max(float(cfg.get("sl_buffer", 0.5)), 0.0)
                max_sl_price = max(float(cfg.get("max_sl_price", 5.0)), 0.0)
                rr = max(float(cfg.get("rr", 2.0)), 0.0)
                max_pos = int(cfg.get("max_pos", 3))
                auto_rev_close = bool(cfg.get("auto_rev_close", True))
                model_choice = str(cfg.get("model", "AI-base (102.3M - Mạnh nhất)"))
                engine_mode = str(cfg.get("engine", "Chạy trực tiếp máy Local"))
                hf_space = str(cfg.get("hf_space", "")).strip()
                hf_token = str(cfg.get("hf_token", "")).strip()

                # 1. Lấy dữ liệu nến của Khung Phân Tích AI (analysis_tf) riêng cho mã symbol của pk
                if pk == self.active_profile_key:
                    self._set_loading_stage(28.0, f"Đang tải dữ liệu nến {symbol} ({analysis_tf}) từ MT5...")
                chart_bars = max(lookback, 450)
                df_with_forming = self.fetch_mt5_candles(symbol, analysis_tf, chart_bars + 1, include_forming=True)
                with self._mt5_lock:
                    tick = mt5.symbol_info_tick(symbol)
                tols = self._get_symbol_tolerances(symbol, tick)

                # Luôn phân tích trên các cây nến ĐÃ ĐÓNG của Khung Phân Tích AI (analysis_tf)
                # và lưu chính xác mốc thời gian nến đã đóng vào last_ana_candle_time để đồng bộ 100% với _auto_loop_worker
                if len(df_with_forming) > 35:
                    df = df_with_forming.iloc[:-1].reset_index(drop=True)
                else:
                    df = df_with_forming.iloc[-chart_bars:].reset_index(drop=True)

                ai_lookback = min(max(lookback, 50), 500)
                # Sao chép độc lập (Deep Copy) bảng dữ liệu nến đưa vào AI để không bao giờ bị tham chiếu chéo
                df_ai = df.iloc[-ai_lookback:][["timestamps", "open", "high", "low", "close", "volume"]].copy(deep=True).reset_index(drop=True)
                last_close = float(df_ai["close"].iloc[-1])

                # 2. Lấy nến Khung Scalping để canh thời điểm đóng nến & kết hợp ATR đa khung
                if analysis_tf == scalp_tf:
                    df_scalp = self.fetch_mt5_candles(symbol, scalp_tf, 60, include_forming=False)
                else:
                    if pk == self.active_profile_key:
                        self._set_loading_stage(40.0, f"Đang đồng bộ khung vào lệnh Scalping ({scalp_tf})...")
                    df_scalp = self.fetch_mt5_candles(symbol, scalp_tf, 60, include_forming=False)

                last_time = df_scalp["timestamps"].iloc[-1]
                ana_closed_time = df["timestamps"].iloc[-1]
                ctx["last_candle_time"] = last_time
                ctx["last_ana_candle_time"] = ana_closed_time
                if pk == self.active_profile_key:
                    self.last_candle_time = last_time
                    self.last_ana_candle_time = ana_closed_time
                atr_scalp = self.calculate_atr(df_scalp, 14)
                atr_ana = self.calculate_atr(df, 14) if analysis_tf != scalp_tf else atr_scalp
                atr = max(atr_scalp, atr_ana * 0.65)

                spread_usd = (tick.ask - tick.bid) if tick else 0.0

                x_timestamp = df_ai["timestamps"].copy(deep=True).reset_index(drop=True)
                delta = x_timestamp.iloc[-1] - x_timestamp.iloc[-2] if len(x_timestamp) >= 2 else pd.Timedelta(minutes=5)
                future_dates = [x_timestamp.iloc[-1] + delta * (i + 1) for i in range(pred_len)]
                y_timestamp = pd.Series(pd.to_datetime(future_dates))

                scenarios = None
                used_engine = "GTX 1650 Local" if torch.cuda.is_available() else "CPU Local (KV-Cache)"
                dyn_t, dyn_top_p, regime_tag = self._compute_adaptive_sampling_params(df_ai, analysis_tf)
                macro_ctx = self._analyze_scalping_micro_macro_context(df, df_scalp, atr, profile_key=pk)

                if engine_mode.startswith("Ưu tiên Hugging Face") and HF_CLIENT_AVAILABLE and hf_space:
                    try:
                        if pk == self.active_profile_key:
                            self._set_loading_stage(55.0, f"Đang gọi Hugging Face Space ({hf_space})...")
                        self.log(f"[{pk}] Đang gọi Hugging Face Space ({hf_space})...")

                        # TỰ ĐỘNG ĐÁNH THỨC SPACE NẾU ĐANG NGỦ (AUTO-WAKE SPACE)
                        if hf_token:
                            try:
                                from huggingface_hub import HfApi
                                hf_api = HfApi(token=hf_token)
                                runtime = hf_api.get_space_runtime(hf_space)
                                if runtime and getattr(runtime, "stage", None) in ("PAUSED", "SLEEPING", "STOPPED"):
                                    self.log(f"🔔 [{pk}] Phát hiện HF Space đang ngủ ({runtime.stage}) -> Đã gửi lệnh tự động đánh thức!")
                                    hf_api.restart_space(hf_space)
                                    self.log(f"⏳ [{pk}] Máy chủ HF đang khởi động (ước tính 30-45s)... Tạm chuyển sang Local CPU để không lỡ nhịp nến!")
                                    raise RuntimeError(f"HF Space đang được đánh thức ({runtime.stage}), tạm chuyển Local")
                            except Exception as wake_err:
                                if "đang được đánh thức" in str(wake_err):
                                    raise

                        client_kwargs = {}
                        if hf_token:
                            try:
                                import inspect
                                params = inspect.signature(Client.__init__).parameters
                                if "token" in params:
                                    client_kwargs["token"] = hf_token
                                elif "hf_token" in params:
                                    client_kwargs["hf_token"] = hf_token
                                else:
                                    client_kwargs["headers"] = {"Authorization": f"Bearer {hf_token}"}
                            except Exception:
                                client_kwargs["token"] = hf_token
                        client = Client(hf_space, **client_kwargs)
                        payload = df_ai.copy(deep=True)
                        payload["timestamps"] = payload["timestamps"].dt.strftime("%Y-%m-%d %H:%M:%S")
                        hf_model_arg = model_choice.replace("AI-", "Kronos-", 1) if model_choice.startswith("AI-") else model_choice
                        res_json = client.predict(
                            candles_json=payload.to_json(orient="records"),
                            model_choice=hf_model_arg,
                            pred_len=pred_len,
                            num_scenarios=num_scenarios,
                            temperature=dyn_t,
                            top_p=dyn_top_p,
                            api_name="/scalping_m5"
                        )
                        data = json.loads(res_json)
                        scenarios = []
                        for s_rec in data["scenarios"]:
                            s_df = pd.DataFrame(s_rec)
                            s_df["timestamps"] = pd.to_datetime(s_df["timestamps"])
                            scenarios.append(s_df)
                        used_engine = "Hugging Face A100"
                    except Exception as e:
                        err_str = str(e)
                        if "401" in err_str or "Repository Not Found" in err_str:
                            self.log(f"⚠️ [{pk}] Space '{hf_space}' là Private nhưng chưa nhập HF Token -> Chuyển sang chạy Local máy nhà")
                        elif "đang được đánh thức" in err_str:
                            pass
                        else:
                            self.log(f"⚠️ [{pk}] Chuyển sang chạy trực tiếp trên máy nhà ({err_str})")

                if scenarios is None:
                    if pk == self.active_profile_key:
                        self._set_loading_stage(62.0, f"Đang nạp mô hình {model_choice}...")
                    predictor = self.get_local_predictor(model_choice)
                    if pk == self.active_profile_key:
                        self._set_loading_stage(82.0, f"Đang phân tích {num_scenarios} kịch bản xu hướng AI ({symbol} | T={dyn_t:.2f})...")
                    # Ô 'Số luồng AI': 2 máy đặt cùng số luồng -> kết quả AI giống nhau (khác số luồng: sai số làm tròn khác)
                    try:
                        _req_thr = cfg.get("ai_threads", "Auto")
                        _n_thr, _clamped = resolve_ai_threads(_req_thr)
                        if torch.get_num_threads() != _n_thr:
                            torch.set_num_threads(_n_thr)
                        if _clamped and getattr(self, "_ai_threads_warned", None) != str(_req_thr):
                            self._ai_threads_warned = str(_req_thr)
                            self.log(f"⚠️ [{pk}] Số luồng AI chọn {_req_thr} vượt mức an toàn của máy này -> dùng {_n_thr} luồng (chống quá tải/sập nguồn).")
                    except Exception:
                        pass
                    scenarios = self._fast_predict_scenarios(
                        predictor=predictor,
                        df_in=df_ai,
                        x_timestamp=x_timestamp,
                        y_timestamp=y_timestamp,
                        pred_len=pred_len,
                        num_scenarios=num_scenarios,
                        T=dyn_t,
                        top_k=0,
                        top_p=dyn_top_p
                    )

                # Log chẩn đoán (mỗi lần phân tích 1 dòng): dấu vân tay dữ liệu đầu vào để so sánh 2 máy có cùng kết quả hay không
                try:
                    _lr = df_ai.iloc[-1]
                    _vol_sum = float(df_ai["volume"].sum()) if "volume" in df_ai.columns else -1.0
                    _thr = torch.get_num_threads()
                    self.log(
                        f"🧬 [{pk}] Dấu vân tay AI: engine={locals().get('used_engine', 'Local CPU')} | nến={len(df_ai)} | "
                        f"cuối O/H/L/C={float(_lr['open']):.2f}/{float(_lr['high']):.2f}/{float(_lr['low']):.2f}/{float(_lr['close']):.2f} | "
                        f"tổng Vol={_vol_sum:.0f} | kịch bản={num_scenarios} T={dyn_t:.2f} top_p={dyn_top_p:.2f} | luồng torch={_thr}"
                    )
                except Exception:
                    pass

                # Chấm điểm kịch bản (dùng chung với backtest): module trading_modules/scenario_scoring.py
                sc = score_scenarios(scenarios, last_close, atr, macro_ctx)
                bull_count, bear_count, total = sc["bull_count"], sc["bear_count"], sc["total"]
                bull_pct, bear_pct = sc["bull_pct"], sc["bear_pct"]
                avg_end_close, scenario_std, dispersion_r = sc["avg_end_close"], sc["scenario_std"], sc["dispersion_r"]
                best_idx = sc["best_idx"]
                mean_df = scenarios[best_idx][["timestamps", "open", "high", "low", "close"]].copy(deep=True)
                dir_pct, ai_dom_dir, confidence_pct = sc["dir_pct"], sc["ai_dom_dir"], sc["confidence_pct"]
                is_high_uncertainty = (max_dispersion > 0.0 and dispersion_r > max_dispersion)

                expected_move = avg_end_close - last_close
                ai_pred_high = float(mean_df["high"].max())
                ai_pred_low = float(mean_df["low"].min())
                ai_swing_low_dist = max(last_close - ai_pred_low, 0.0)
                ai_swing_high_dist = max(ai_pred_high - last_close, 0.0)
                base_atr_sl = max(atr * sl_atr, tols["min_atr_sl_floor"])

                ai_tp_dist_up = max(ai_pred_high - last_close, abs(expected_move), tols["min_tp_floor"])
                ai_tp_dist_down = max(last_close - ai_pred_low, abs(expected_move), tols["min_tp_floor"])

                trend_filter_on = bool(cfg.get("trend_filter", True))
                macro_dir = macro_ctx.get("macro_dir", 0)

                is_btc_sym = tols.get("is_btc", False)
                slippage_est = max(spread_usd * 0.5, 1.0 if is_btc_sym else 0.05)
                try:
                    expected_move_k = float(cfg.get("expected_move_k", 3.0))
                except (ValueError, TypeError):
                    expected_move_k = 3.0
                min_effective_move = max(min_move, expected_move_k * (spread_usd + slippage_est))

                use_confidence_gate = bool(cfg.get("use_confidence_gate", False))
                # Khi bật use_confidence_gate: yêu cầu confidence_pct >= min_consensus thay vì chỉ xét bull_pct / bear_pct
                is_bull_consensus = (confidence_pct >= min_consensus and bull_pct > bear_pct) if use_confidence_gate else (bull_pct >= min_consensus)
                is_bear_consensus = (confidence_pct >= min_consensus and bear_pct > bull_pct) if use_confidence_gate else (bear_pct >= min_consensus)
                # Nhận định cấu trúc đỉnh/đáy xoay (khóa structure_direction, mặc định TẮT). Khi bật: hướng vào lệnh
                # do cấu trúc quyết định (không có tín hiệu cấu trúc thì không vào lệnh); AI vẫn xác nhận biên độ dự báo.
                struct_sig, struct_reason = None, ""
                if bool(cfg.get("structure_direction", False)):
                    try:
                        _sw = analyze_swing_structure(df["open"].values, df["high"].values, df["low"].values,
                                                      df["close"].values, atr)
                        struct_sig, struct_reason = _sw["signal"], _sw["reason"]
                    except Exception:
                        struct_sig, struct_reason = None, "lỗi nhận định cấu trúc"
                    is_bull_consensus = (struct_sig == "BUY")
                    is_bear_consensus = (struct_sig == "SELL")

                action = None
                sig_color = "#ffffff"  # Chờ/Đứng ngoài: chữ trắng
                ref_tag = ""
                if is_bull_consensus and expected_move >= min_effective_move:
                    if is_high_uncertainty:
                        action = None
                        sig_color = "#ffffff"  # Chờ/Đứng ngoài: chữ trắng
                        sig_title = f"⚪ [{pk} - {symbol}] ĐỨNG NGOÀI (BẤT ĐỊNH CAO) — Đồng thuận: Tăng {bull_pct:.0f}% | Giảm {bear_pct:.0f}% | Phân tán: {dispersion_r:.2f}x ATR > {max_dispersion:.1f}x | Tin cậy: {confidence_pct:.0f}%"
                    elif trend_filter_on and macro_dir == -1:
                        action = None
                        sig_color = "#ff9800"  # Cảnh báo vàng cam: chặn ngược cấu trúc
                        struct_desc = macro_ctx.get("structure_reason", "Cấu trúc Giảm / Đổ đèo")
                        sig_title = f"🛡️ [{pk} - {symbol}] ĐỨNG NGOÀI (CHẶN BẮT ĐÁY) — AI gợi ý BUY ({bull_pct:.0f}%) nhưng Cấu Trúc Thị Trường đang GIẢM ({struct_desc}). Bảo toàn vốn!"
                        self.log(
                            f"🛡️ [{pk} - Chặn Bắt Đáy Ngược Cấu Trúc] Kịch bản AI có đồng thuận BUY ({bull_pct:.0f}%, {bull_count}/{total}), "
                            f"nhưng Cấu Trúc Thị Trường Không Độ Trễ đang GIẢM ({struct_desc} | {macro_ctx.get('summary_tag', 'CấuTrúc:Giảm')}). "
                            f"Bộ lọc Cấu Trúc Zero-Lag đã CHẶN mở lệnh BUY để bảo toàn vốn!"
                        )
                    else:
                        action = "BUY"
                        sig_color = GREEN_UP
                        sig_title = f"🟢 TÍN HIỆU [{pk}]: BUY {symbol} ({tf_label}) — Đồng thuận {bull_pct:.0f}% ({bull_count}/{total}, cần ≥ {min_consensus:.0f}%) | Tin cậy: {confidence_pct:.0f}% (Phân tán: {dispersion_r:.2f}x ATR)"
                    entry_ref = round(last_close, 2)
                    sl_ai_raw_dist = max(base_atr_sl, ai_swing_low_dist)
                    if str(cfg.get("sl_swing", "0")) in ("1", "true", "True"):  # THỬ NGHIỆM (mặc định tắt): SL dưới đáy swing gần nhất
                        _swd = nearest_swing_dist(df, "BUY", entry_ref)
                        if _swd is not None:
                            sl_ai_raw_dist = max(tols["min_atr_sl_floor"], _swd)
                    total_sl_dist = sl_ai_raw_dist + sl_buffer
                    sl_price = round(entry_ref - total_sl_dist, 2)
                    sl_ai_price = round(entry_ref - sl_ai_raw_dist, 2)  # SL AI gốc (chưa cộng Giá an toàn): neo vùng vào
                    eff_entry = min(entry_ref, round(sl_ai_price + max_sl_price, 2)) if max_sl_price > 0.0 else entry_ref
                    if rr > 0.0:
                        tp_price = round(eff_entry + (eff_entry - sl_price) * rr, 2)
                    else:
                        tp_price = round(max(ai_pred_high, eff_entry + ai_tp_dist_up), 2)
                elif is_bear_consensus and (-expected_move) >= min_effective_move:
                    if is_high_uncertainty:
                        action = None
                        sig_color = "#ffffff"  # Chờ/Đứng ngoài: chữ trắng
                        sig_title = f"⚪ [{pk} - {symbol}] ĐỨNG NGOÀI (BẤT ĐỊNH CAO) — Đồng thuận: Tăng {bull_pct:.0f}% | Giảm {bear_pct:.0f}% | Phân tán: {dispersion_r:.2f}x ATR > {max_dispersion:.1f}x | Tin cậy: {confidence_pct:.0f}%"
                    elif trend_filter_on and macro_dir == 1:
                        action = None
                        sig_color = "#ff9800"  # Cảnh báo vàng cam: chặn ngược cấu trúc
                        struct_desc = macro_ctx.get("structure_reason", "Cấu trúc Tăng / Dựng cột")
                        sig_title = f"🛡️ [{pk} - {symbol}] ĐỨNG NGOÀI (CHẶN BẮT ĐỈNH) — AI gợi ý SELL ({bear_pct:.0f}%) nhưng Cấu Trúc Thị Trường đang TĂNG ({struct_desc}). Bảo toàn vốn!"
                        self.log(
                            f"🛡️ [{pk} - Chặn Bắt Đỉnh Ngược Cấu Trúc] Kịch bản AI có đồng thuận SELL ({bear_pct:.0f}%, {bear_count}/{total}), "
                            f"nhưng Cấu Trúc Thị Trường Không Độ Trễ đang TĂNG ({struct_desc} | {macro_ctx.get('summary_tag', 'CấuTrúc:Tăng')}). "
                            f"Bộ lọc Cấu Trúc Zero-Lag đã CHẶN mở lệnh SELL để bảo toàn vốn!"
                        )
                    else:
                        action = "SELL"
                        sig_color = RED_DOWN
                        sig_title = f"🔴 TÍN HIỆU [{pk}]: SELL {symbol} ({tf_label}) — Đồng thuận {bear_pct:.0f}% ({bear_count}/{total}, cần ≥ {min_consensus:.0f}%) | Tin cậy: {confidence_pct:.0f}% (Phân tán: {dispersion_r:.2f}x ATR)"
                    entry_ref = round(last_close, 2)
                    sl_ai_raw_dist = max(base_atr_sl, ai_swing_high_dist)
                    if str(cfg.get("sl_swing", "0")) in ("1", "true", "True"):  # THỬ NGHIỆM (mặc định tắt): SL trên đỉnh swing gần nhất
                        _swd = nearest_swing_dist(df, "SELL", entry_ref)
                        if _swd is not None:
                            sl_ai_raw_dist = max(tols["min_atr_sl_floor"], _swd)
                    total_sl_dist = sl_ai_raw_dist + sl_buffer
                    sl_price = round(entry_ref + total_sl_dist, 2)
                    sl_ai_price = round(entry_ref + sl_ai_raw_dist, 2)  # SL AI gốc (chưa cộng Giá an toàn): neo vùng vào
                    eff_entry = max(entry_ref, round(sl_ai_price - max_sl_price, 2)) if max_sl_price > 0.0 else entry_ref
                    if rr > 0.0:
                        tp_price = round(eff_entry - (sl_price - eff_entry) * rr, 2)
                    else:
                        tp_price = round(min(ai_pred_low, eff_entry - ai_tp_dist_down), 2)
                else:
                    action = None
                    ctx["pending_pullback_order"] = None
                    entry_ref = last_close
                    is_bear_bias = (bear_pct > bull_pct) or (bear_pct == bull_pct and expected_move < 0)
                    if is_bear_bias:
                        ref_tag = " (Tham khảo SELL)"
                        sl_ai_raw_dist = max(base_atr_sl, ai_swing_high_dist)
                        total_sl_dist = sl_ai_raw_dist + sl_buffer
                        eff_sl_for_tp = min(total_sl_dist, max_sl_price) if max_sl_price > 0.0 else total_sl_dist
                        tp_dist = (eff_sl_for_tp * rr) if rr > 0.0 else ai_tp_dist_down
                        sl_price = round(last_close + total_sl_dist, 2)
                        sl_ai_price = round(last_close + sl_ai_raw_dist, 2)
                        tp_price = round(last_close - tp_dist, 2)
                    else:
                        ref_tag = " (Tham khảo BUY)"
                        sl_ai_raw_dist = max(base_atr_sl, ai_swing_low_dist)
                        total_sl_dist = sl_ai_raw_dist + sl_buffer
                        eff_sl_for_tp = min(total_sl_dist, max_sl_price) if max_sl_price > 0.0 else total_sl_dist
                        tp_dist = (eff_sl_for_tp * rr) if rr > 0.0 else ai_tp_dist_up
                        sl_price = round(last_close - total_sl_dist, 2)
                        sl_ai_price = round(last_close - sl_ai_raw_dist, 2)
                        tp_price = round(last_close + tp_dist, 2)

                    if use_confidence_gate and (bull_pct > bear_pct and confidence_pct < min_consensus):
                        sig_title = f"⚪ [{pk} - {symbol}] ĐỨNG NGOÀI (ĐỘ TIN CẬY AI THẤP) — Tin cậy {confidence_pct:.0f}% < {min_consensus:.0f}% (Đồng thuận: Tăng {bull_pct:.0f}%). Bảo toàn vốn!"
                        self.log(f"🛡️ [{pk}] Chặn mở lệnh BUY: Độ tin cậy AI tổng hợp ({confidence_pct:.0f}%) chưa đạt ngưỡng tối thiểu ({min_consensus:.0f}%).")
                    elif use_confidence_gate and (bear_pct > bull_pct and confidence_pct < min_consensus):
                        sig_title = f"⚪ [{pk} - {symbol}] ĐỨNG NGOÀI (ĐỘ TIN CẬY AI THẤP) — Tin cậy {confidence_pct:.0f}% < {min_consensus:.0f}% (Đồng thuận: Giảm {bear_pct:.0f}%). Bảo toàn vốn!"
                        self.log(f"🛡️ [{pk}] Chặn mở lệnh SELL: Độ tin cậy AI tổng hợp ({confidence_pct:.0f}%) chưa đạt ngưỡng tối thiểu ({min_consensus:.0f}%).")
                    elif bull_pct >= min_consensus and expected_move >= min_move and expected_move < min_effective_move:
                        cost_barrier = expected_move_k * (spread_usd + slippage_est)
                        sig_title = f"⚪ [{pk} - {symbol}] ĐỨNG NGOÀI (BIÊN ĐỘ BÙ PHÍ THẤP) — AI dự báo +{expected_move:.2f}$ < ngưỡng an toàn ({min_effective_move:.2f}$ = {expected_move_k:g}x chi phí). Tránh rủi ro phí sàn!"
                        self.log(f"🛡️ [{pk}] Biên độ dự báo AI (+{expected_move:.2f}$) không đủ bù chi phí giao dịch (Cần ≥ {min_effective_move:.2f}$ = max(min_move={min_move:.2f}$, k={expected_move_k:g}*(spread={spread_usd:.2f}$+slip={slippage_est:.2f}$))).")
                    elif bear_pct >= min_consensus and (-expected_move) >= min_move and (-expected_move) < min_effective_move:
                        cost_barrier = expected_move_k * (spread_usd + slippage_est)
                        sig_title = f"⚪ [{pk} - {symbol}] ĐỨNG NGOÀI (BIÊN ĐỘ BÙ PHÍ THẤP) — AI dự báo {expected_move:.2f}$ < ngưỡng an toàn ({min_effective_move:.2f}$ = {expected_move_k:g}x chi phí). Tránh rủi ro phí sàn!"
                        self.log(f"🛡️ [{pk}] Biên độ dự báo AI ({expected_move:.2f}$) không đủ bù chi phí giao dịch (Cần ≥ {min_effective_move:.2f}$ = max(min_move={min_move:.2f}$, k={expected_move_k:g}*(spread={spread_usd:.2f}$+slip={slippage_est:.2f}$))).")
                    # Đồng thuận ĐẠT nhưng AI dự báo giá đi chưa tới "Biên độ dự báo tối thiểu" (min_move): ghi rõ lý do
                    # (trước đây chỉ hiện dòng đồng thuận chung, người dùng 19/11 thấy "Giảm 100%" mà không hiểu vì sao đứng ngoài). Chỉ đổi chữ.
                    elif not bool(cfg.get("structure_direction", False)) and bull_pct >= min_consensus and expected_move < min_move:
                        sig_title = f"⚪ [{pk} - {symbol}] ĐỨNG NGOÀI (BIÊN ĐỘ NHỎ) — Đồng thuận Tăng {bull_pct:.0f}% (đạt ≥ {min_consensus:.0f}%) nhưng AI chỉ dự báo {expected_move:+.2f}$ < Biên độ tối thiểu {min_move:.2f}$ | Tin cậy: {confidence_pct:.0f}%"
                    elif not bool(cfg.get("structure_direction", False)) and bear_pct >= min_consensus and (-expected_move) < min_move:
                        sig_title = f"⚪ [{pk} - {symbol}] ĐỨNG NGOÀI (BIÊN ĐỘ NHỎ) — Đồng thuận Giảm {bear_pct:.0f}% (đạt ≥ {min_consensus:.0f}%) nhưng AI chỉ dự báo {expected_move:+.2f}$ < Biên độ tối thiểu {min_move:.2f}$ | Tin cậy: {confidence_pct:.0f}%"
                    elif is_bear_bias:
                        sig_title = f"⚪ [{pk} - {symbol}] ĐỨNG NGOÀI ({tf_label}) — Đồng thuận: Tăng {bull_pct:.0f}% | Giảm {bear_pct:.0f}% (cần ≥ {min_consensus:.0f}%) | Phân tán: {dispersion_r:.2f}x ATR | Tin cậy: {confidence_pct:.0f}%"
                    else:
                        sig_title = f"⚪ [{pk} - {symbol}] ĐỨNG NGOÀI ({tf_label}) — Đồng thuận: Tăng {bull_pct:.0f}% | Giảm {bear_pct:.0f}% (cần ≥ {min_consensus:.0f}%) | Phân tán: {dispersion_r:.2f}x ATR | Tin cậy: {confidence_pct:.0f}%"

                tp_dist = abs(tp_price - entry_ref)
                ctx["last_recommendation"] = {"sl_dist": sl_ai_raw_dist, "tp_dist": tp_dist}
                ctx["cached_scenarios"] = scenarios
                ctx["cached_mean_df"] = mean_df
                ctx["cached_action"] = action
                ctx["cached_sl"] = sl_price
                ctx["cached_tp"] = tp_price
                ctx["_sig_title"] = sig_title
                ctx["_sig_color"] = sig_color
                # Telegram (tin vào lệnh + nút '🧠 Phân tích AI'): đồng thuận thật và giờ của lần phân tích này. Chỉ hiển thị.
                try:
                    ctx["cached_consensus"] = f"{float(dir_pct):.0f}"
                    ctx["_tg_ai_info"] = {"ts": datetime.now(tz=VN_TZ).strftime("%H:%M:%S"), "tf": str(analysis_tf)}
                except Exception:
                    pass

                # XỬ LÝ 3 QUY TẮC BẢO VỆ KHI AI BÁO ĐẢO CHIỀU NGƯỢC LỆNH ĐANG GIỮ
                # Phân tầng thông minh: Lệnh CHƯA DỜI BE áp dụng rev_consensus (Mục 3), Lệnh ĐÃ DỜI BE áp dụng rev_be_consensus (mặc định 90%, 0=Tắt)
                has_losing_opposite_pos = False
                if auto_rev_close:
                    open_positions = self._get_bot_positions(symbol, profile_key=pk)
                    sym_info = None
                    if open_positions:
                        with self._mt5_lock:
                            sym_info = mt5.symbol_info(symbol)
                    if open_positions:
                        digits = sym_info.digits if sym_info else 2
                        eps_eq = tols["eps_equal"]
                        try:
                            rev_be_consensus = float(cfg.get("rev_be_consensus", 90.0))
                        except ValueError:
                            rev_be_consensus = 90.0

                        for pos in open_positions:
                            is_buy_pos = (pos.type == mt5.POSITION_TYPE_BUY)
                            opp_action = "SELL" if is_buy_pos else "BUY"
                            opp_pct = bear_pct if is_buy_pos else bull_pct
                            opp_move = (-expected_move) if is_buy_pos else expected_move

                            is_at_be = self._is_pos_at_be(pos, eps_eq)
                            if is_at_be:
                                # Lệnh ĐÃ DỜI BE: Phải đạt ngưỡng Đồng thuận đảo chiều 2 (rev_be_consensus, mặc định 90%, = 0 là TẮT)
                                if rev_be_consensus <= 0.0:
                                    continue
                                is_rev_triggered = (not is_high_uncertainty) and (opp_pct >= rev_be_consensus) and (rev_min_move <= 0.0 or opp_move >= rev_min_move)
                                rev_tag = f"Lệnh đã BE - Cần ≥ {rev_be_consensus:g}% [Đạt {opp_pct:.0f}%]"
                            else:
                                # Lệnh CHƯA DỜI BE: Áp dụng ngưỡng Đồng thuận đảo chiều 1 (rev_consensus, mặc định 75%) hoặc Tín hiệu chính AI
                                is_rev_triggered = (action == opp_action) or (
                                    (not is_high_uncertainty) and (opp_pct >= rev_consensus) and (rev_min_move <= 0.0 or opp_move >= rev_min_move)
                                )
                                rev_tag = f"Lệnh chưa BE - Cần ≥ {rev_consensus:g}% [Đạt {opp_pct:.0f}%]"

                            if not is_rev_triggered:
                                continue

                            # QUY TẮC 1: Lệnh ngược chiều đang CÓ LÃI (> 0$) -> Chốt lời ngay để đảo chiều
                            if pos.profit > 0.0:
                                ok_c, msg_c = self._close_single_pos(pos, "REV")
                                self.pending_be_exit_tickets.discard(pos.ticket)
                                self.log(f"🔄 [{pk} - Quy tắc 1 - AI Đảo Chiều sang {opp_action} ({rev_tag})] Lệnh #{pos.ticket} đang lãi -> {msg_c}")
                            else:
                                # QUY TẮC 2 & 3: Lệnh ngược chiều đang ÂM (<= 0$)
                                rev_loss_mode = str(cfg.get("rev_loss_mode", "wait_be")).strip().lower()
                                opp_dir = -1 if is_buy_pos else 1
                                is_opp_choch = (macro_ctx.get("break_type") == "CHOCH" and macro_ctx.get("macro_dir") == opp_dir)

                                if rev_loss_mode == "close_if_strong" and opp_pct >= rev_be_consensus and is_opp_choch:
                                    ok_c, msg_c = self._close_single_pos(pos, "REVCUT")
                                    self.pending_be_exit_tickets.discard(pos.ticket)
                                    self.log(
                                        f"✂️ [{pk} - Cắt Lỗ Chủ Động (close_if_strong)] Lệnh #{pos.ticket} ({pos.profit:+.2f}$) gặp đảo chiều mạnh "
                                        f"(Đồng thuận {opp_pct:.0f}% ≥ {rev_be_consensus:g}% + CHoCH {opp_action}) -> {msg_c}"
                                    )
                                elif rev_loss_mode == "tighten_sl":
                                    has_losing_opposite_pos = True
                                    # A39: lấy tick MỚI ngay trước khi tính SL (tick đầu lượt phân tích đã cũ sau khi chạy AI)
                                    fresh_tick = self._get_fresh_tick(pos.symbol)
                                    curr_sl = float(pos.sl) if pos.sl > 0 else (pos.price_open - (atr * sl_atr) if is_buy_pos else pos.price_open + (atr * sl_atr))
                                    min_step = tols.get("min_trail_step", 0.40)
                                    if fresh_tick is None:
                                        self.log(f"⚠️ [{pk} - tighten_sl] Tick {pos.symbol} thiếu/quá cũ (> {self.TICK_MAX_AGE_SEC:g}s) -> bỏ lượt siết SL #{pos.ticket}.")
                                        new_sl = curr_sl
                                        is_tighter = False
                                    else:
                                        cur_p = fresh_tick.bid if is_buy_pos else fresh_tick.ask
                                        cur_sl_dist = abs(cur_p - curr_sl)
                                        new_sl = round(cur_p - (cur_sl_dist * 0.5), digits) if is_buy_pos else round(cur_p + (cur_sl_dist * 0.5), digits)
                                        # Đảm bảo new_sl siết chặt hơn SL cũ
                                        is_tighter = (new_sl > curr_sl) if is_buy_pos else (new_sl < curr_sl)
                                    be_target_price = round((pos.price_open + be_plus_price) if is_buy_pos else (pos.price_open - be_plus_price), digits)
                                    be_label_tag = f"Entry+{be_plus_price:g}" if be_plus_price > 0 else "Entry"
                                    self.pending_be_exit_tickets.add(pos.ticket)
                                    if is_tighter and abs(new_sl - curr_sl) >= min_step:
                                        if self._modify_position_sltp(pos, new_sl, be_target_price):
                                            self.log(
                                                f"🛡️ [{pk} - Siết Cắt Lỗ 50% (tighten_sl)] Lệnh #{pos.ticket} ({pos.profit:+.2f}$) gặp đảo chiều {opp_action} ({rev_tag}) "
                                                f"-> Đã kéo SL từ {curr_sl:.2f} về {new_sl:.2f} (giảm 50% rủi ro) & TP về {be_label_tag} ({be_target_price:.2f})!"
                                            )
                                        else:
                                            self._modify_position_sltp(pos, pos.sl, be_target_price)
                                    else:
                                        self._modify_position_sltp(pos, pos.sl, be_target_price)
                                else:
                                    # "wait_be" (mặc định, giữ như cũ): Chờ hồi về Entry + khoản lãi nhỏ (be_plus_price) để thoát có lãi nhẹ
                                    has_losing_opposite_pos = True
                                    be_target_price = round((pos.price_open + be_plus_price) if is_buy_pos else (pos.price_open - be_plus_price), digits)
                                    be_label_tag = f"Entry+{be_plus_price:g}" if be_plus_price > 0 else "Entry"
                                    self.pending_be_exit_tickets.add(pos.ticket)
                                    if abs(pos.tp - be_target_price) >= eps_eq:
                                        if self._modify_position_sltp(pos, pos.sl, be_target_price):
                                            self.log(
                                                f"⏳ [{pk} - Quy tắc 2 & 3 - AI Đảo Chiều sang {opp_action} ({rev_tag})] Giữ lệnh #{pos.ticket} ({pos.profit:+.2f}$), "
                                                f"đã kéo điểm thoát về {be_label_tag} ({be_target_price:.2f}) chờ hồi thoát có lãi nhẹ!"
                                            )
                                        else:
                                            self.log(
                                                f"⏳ [{pk} - Quy tắc 2 & 3 - AI Đảo Chiều sang {opp_action} ({rev_tag})] Đã đánh dấu lệnh #{pos.ticket} ({pos.profit:+.2f}$) "
                                                f"tự động thoát ngay khi giá hồi về {be_label_tag} ({be_target_price:.2f})!"
                                            )

                # KIỂM TRA ĐIỀU KIỆN KHOẢNG CÁCH TỐI ĐA SL & TỶ LỆ REWARD/RISK >= 2R
                cur_sl_dist = abs(entry_ref - sl_price)
                dist_tp = abs(tp_price - entry_ref)

                effective_max_sl = max_sl_price
                sl_tightened_for_2r = False
                if action in ("BUY", "SELL") and cur_sl_dist > 0.001:
                    # Kiểm tra RR >= 2R tại điểm vào dự kiến (SL ± Max SL), không đo từ giá hiện tại (xem calc_effective_max_sl)
                    effective_max_sl, sl_tightened_for_2r = calc_effective_max_sl_ai(action, entry_ref, sl_ai_price, tp_price, max_sl_price)
                    if sl_tightened_for_2r:
                        base_sl_cap = max_sl_price if max_sl_price > 0.0 else cur_sl_dist
                        self.log(
                            f"⚖️ [{pk} - Tối Ưu RR ≥ 2R] Khoảng cách TP ({dist_tp:.2f}$) < 2R của SL ({cur_sl_dist:.2f}$) -> "
                            f"Tự động giảm SL tối đa: {base_sl_cap:.2f}$ ÷ 2 = {effective_max_sl:.2f}$ để thu gọn khoảng cách & tối ưu RR ≥ 2R!"
                        )

                limit_entry_price = round((sl_ai_price + effective_max_sl) if action == "BUY" else (sl_ai_price - effective_max_sl), 2) if effective_max_sl > 0.0 else entry_ref
                # Debug trace (tắt mặc định): số liệu AI và quyết định của nến này, để truy vết sau
                if bool(cfg.get("debug_trace", False)):
                    try:
                        self._write_trace(pk, {
                            "event": "analysis", "symbol": symbol, "candle": str(ana_closed_time), "scalp_candle": str(last_time),
                            "cfg_sig": ctx.get("_last_ai_scan_config_sig"),
                            "market": {"last_close": last_close, "bid": tick.bid if tick else None, "ask": tick.ask if tick else None,
                                       "spread": spread_usd, "atr": atr, "atr_scalp": atr_scalp, "atr_ana": atr_ana},
                            "ai": {"engine": used_engine, "model": model_choice, "scenarios": num_scenarios, "pred_len": pred_len,
                                   "dyn_t": dyn_t, "dyn_top_p": dyn_top_p, "regime": regime_tag,
                                   "bull_pct": bull_pct, "bear_pct": bear_pct, "dir_pct": dir_pct, "ai_dom_dir": ai_dom_dir,
                                   "confidence_pct": confidence_pct, "dispersion_r": dispersion_r, "scenario_std": scenario_std,
                                   "avg_end_close": avg_end_close, "bull_count": bull_count, "bear_count": bear_count, "total": total,
                                   "expected_move": expected_move, "pred_high": ai_pred_high, "pred_low": ai_pred_low,
                                   "structure": macro_ctx.get("summary_tag") if isinstance(macro_ctx, dict) else None},
                            "decision": {"action": action, "is_bull_consensus": is_bull_consensus, "is_bear_consensus": is_bear_consensus,
                                         "is_high_uncertainty": is_high_uncertainty, "min_consensus": min_consensus,
                                         "struct_sig": struct_sig, "struct_reason": struct_reason,
                                         "min_effective_move": min_effective_move, "use_confidence_gate": use_confidence_gate,
                                         "entry_ref": entry_ref, "sl_ai": sl_ai_price, "tp": tp_price,
                                         "effective_max_sl": effective_max_sl, "sl_tightened_for_2r": sl_tightened_for_2r,
                                         "limit_entry": limit_entry_price},
                            "text": sig_title,
                        })
                    except Exception:
                        pass
                is_auto_on = self.auto_loop_running if pk == self.active_profile_key else bool(ctx.get("auto_loop_running", False))

                trade_msg = self._get_live_bot_status_text(pk)
                if action is not None:
                    # Khởi tạo cấu trúc Vùng Canh Giá AI (Zone Watch) cố định cho suốt chu kỳ nến phân tích (VD: 5 phút của M5)
                    zone_watch_order = {
                        "symbol": symbol,
                        "action": action,
                        "sl_price": sl_price,
                        "sl_ai_price": sl_ai_price,
                        "manual_queued": False,
                        "zone_watch": True,
                        "effective_max_sl": effective_max_sl,
                        "sl_tightened_for_2r": sl_tightened_for_2r,
                        "target_tp_price": tp_price,
                        "atr": atr,
                    }
                    if self._is_daily_limit_locked(pk):
                        ctx["pending_pullback_order"] = None
                        reason_lk = self.daily_limit_reason if pk == self.active_profile_key else ctx.get("daily_limit_reason", "")
                        trade_msg = f"Đã dừng giao dịch hôm nay ({reason_lk})"
                        self.log(f"[{pk}] {trade_msg}")
                    elif self._is_cooldown_active(pk, action=action):
                        ctx["pending_pullback_order"] = None
                        trade_msg = self._get_cooldown_remaining_str(pk)
                        self.log(f"[{pk}] {trade_msg}")
                    elif has_losing_opposite_pos and not self._cfg_flag(cfg, "allow_opposite_entry"):
                        ctx["pending_pullback_order"] = zone_watch_order
                        trade_msg = f"⏳ Chờ lệnh ngược chiều hồi về Entry thoát (Canh vùng {action} <= {limit_entry_price:.2f})"
                        self.log(f"⏳ [{pk} - Quy tắc 2] Đang có lệnh ngược chiều chờ về Entry hòa vốn -> Tạm ngưng mở lệnh {action} mới.")
                    elif effective_max_sl > 0.0 and abs(entry_ref - sl_ai_price) > effective_max_sl + tols["max_sl_tol"]:
                        ctx["pending_pullback_order"] = zone_watch_order
                        cond_op = "<=" if action == "BUY" else ">="
                        tight_tag = " [Siết 1/2 Max SL do RR < 2R]" if sl_tightened_for_2r else ""
                        trade_msg = (
                            f"🎯 AI {analysis_tf} đã chốt vùng {action} -> Python đang canh giá về {cond_op} {limit_entry_price:.2f} "
                            f"(SL: {sl_price:.2f} [Max {effective_max_sl:.2f}${tight_tag}], TP: {tp_price:.2f})"
                        )
                        self.log(
                            f"🎯 [{pk} - Canh Vùng Giá AI {analysis_tf}{tight_tag}] AI báo {action} (SL: {sl_price:.2f}, TP: {tp_price:.2f}, cách SL {cur_sl_dist:.2f}$ > Max {effective_max_sl:.2f}$). "
                            f"Python đang canh giá {scalp_tf}/Tick về vùng {cond_op} {limit_entry_price:.2f} để tự động vào lệnh!"
                        )
                    elif spread_usd > max_spread:
                        ctx["pending_pullback_order"] = zone_watch_order
                        trade_msg = f"⚠️ Spread hiện tại cao ({spread_usd:.2f}$ > {max_spread}$) — Đang canh Spread hạ trong vùng {action} @ {limit_entry_price:.2f}"
                        self.log(f"[{pk}] {trade_msg}")
                    elif is_auto_on and self._last_order_candle_ts.get(pk) == last_time:
                        ctx["pending_pullback_order"] = zone_watch_order
                        trade_msg = f"🛡️ Đã vào lệnh trên nến {last_time} — Python tiếp tục canh vùng {action} cho nến {scalp_tf} kế tiếp"
                        self.log(f"🛡️ [{pk}] Đã mở lệnh trên cây nến {last_time}, giữ vùng canh giá {action} cho cây nến {scalp_tf} tiếp theo.")
                    elif is_auto_on:
                        confirm_pa_on = bool(cfg.get("confirm_entry_pa", True))
                        pa_ok = True
                        pa_msg = ""
                        if confirm_pa_on:
                            pa_ok, pa_msg = self._check_price_action_entry_confirmation(symbol, action, scalp_tf, atr=atr, profile_key=pk)
                        if not pa_ok:
                            ctx["pending_pullback_order"] = zone_watch_order
                            trade_msg = f"⏳ Giá trong vùng {action} ({last_close:.2f}) -> Đang chờ xác nhận Price Action ({pa_msg})"
                            self.log(f"⏳ [{pk} - Chờ Xác Nhận PA] Giá đã trong vùng {action} nhưng {pa_msg} -> Chuyển sang Vùng Canh Giá để chờ lực đỡ/lực xả an toàn!")
                        else:
                            # Giá hiện tại đã nằm ngay trong vùng Entry hợp lệ (SL <= Max SL & RR >= 2R) và có xác nhận PA -> Khớp lệnh ngay
                            if sl_tightened_for_2r and dist_tp < 2.0 * cur_sl_dist:
                                min_tp_dist = round(cur_sl_dist * 2.0, 2)
                                tp_price = round(entry_ref + min_tp_dist, 2) if action == "BUY" else round(entry_ref - min_tp_dist, 2)
                                dist_tp = min_tp_dist
                                zone_watch_order["target_tp_price"] = tp_price
                            ctx["pending_pullback_order"] = None
                            ok_ord, ord_msg = self.execute_mt5_order(symbol, action, lot, sl_price, tp_price, max_pos, profile_key=pk, sl_ai_price=sl_ai_price)
                            if ok_ord:
                                self._last_order_candle_ts[pk] = last_time
                            # Duy trì vùng canh giá trong suốt chu kỳ AI (VD: 5 phút M5) để nếu có nhịp hồi tiếp theo (và thỏa mãn Risk-Free/DCA + qua nến M1 mới) thì Python tự xử lý mà không cần gọi lại AI
                            ctx["pending_pullback_order"] = zone_watch_order
                            trade_msg = ord_msg
                            self.log(ord_msg)
                    else:
                        ctx["pending_pullback_order"] = zone_watch_order
                        self.log(f"[{pk}] Có vùng tín hiệu {action} {symbol} @ {limit_entry_price:.2f} (SL: {sl_price:.2f} [-{cur_sl_dist:.2f} giá], TP: {tp_price:.2f}) — Gạt công tắc AUTO sang RUNNING để Python tự canh giá vào lệnh.")
                else:
                    ctx["pending_pullback_order"] = None
                    if is_high_uncertainty:
                        trade_msg = f"⚠️ Chặn mở lệnh: Độ phân tán kịch bản AI cao ({dispersion_r:.2f}x ATR > {max_dispersion:.1f}x ATR) — Bất định cao, tránh bẫy giá"
                        self.log(f"⚠️ [{pk}] Độ phân tán kịch bản AI cao ({dispersion_r:.2f}x ATR > {max_dispersion:.1f}x ATR, độ lệch {scenario_std:.2f}$). Tự động chặn vào lệnh mới để tránh bẫy giá bất định.")
                    else:
                        self.log(f"[{pk} - {symbol}] Phân tích xong nến {analysis_tf} ({ana_closed_time}): Chưa đủ điều kiện vào lệnh.")

                max_sl_str = f"Max: {effective_max_sl:.2f} giá" if effective_max_sl > 0.0 else "Theo AI"
                if sl_tightened_for_2r:
                    max_sl_str += " (RR<2R)"
                actual_rr = (dist_tp / cur_sl_dist) if cur_sl_dist > 0.001 else 0.0
                rr_str = f"1:{actual_rr:.2f}R" if actual_rr > 0 else "---"
                ctx_summary_str = macro_ctx.get("summary_tag", "Cân bằng")
                detail_prefix = (
                    f"Giá đóng nến: {last_close:.2f}  |  Dự báo {pred_len} nến: {avg_end_close:.2f} ({expected_move:+.2f}$)  |  Phân tán: {dispersion_r:.2f}x ATR ({scenario_std:.2f}$)  |  Tin cậy AI: {confidence_pct:.0f}% [{regime_tag}: T={dyn_t:.2f} | {ctx_summary_str}]\n"
                    f"Gợi ý AI{ref_tag} ->  Entry: {entry_ref:.2f}   |   SL: {sl_price:.2f} (SL AI: {cur_sl_dist:.2f} giá [{max_sl_str}])   |   TP: {tp_price:.2f} (Cách {dist_tp:.2f} giá)   |   Tỷ lệ: {rr_str}"
                )
                ctx["_last_detail_prefix"] = detail_prefix
                detail_text = detail_prefix

                if pk == self.active_profile_key:
                    self._set_loading_stage(96.0, f"Đang dựng biểu đồ nến Real-Time ({symbol})...")
                chart_bars = min(max(lookback, 450), 500)
                df_live_scalp = self.fetch_mt5_candles(symbol, scalp_tf, chart_bars, include_forming=True)
                df_live_ana = df_live_scalp if analysis_tf == scalp_tf else self.fetch_mt5_candles(symbol, analysis_tf, chart_bars, include_forming=True)
                ctx["cached_df_hist"] = df_live_scalp
                ctx["cached_df_analysis"] = df_live_ana

                if pk == self.active_profile_key:
                    for attr in (
                        "last_recommendation", "pending_pullback_order", "cached_scenarios",
                        "cached_mean_df", "cached_action", "cached_sl", "cached_tp",
                        "cached_df_hist", "cached_df_analysis", "_last_detail_prefix",
                        "_sig_title", "_sig_color"
                    ):
                        setattr(self, attr, ctx[attr])

                self._ui_after(
                    0, self._update_ui_results, pk, df_live_scalp, df_live_ana,
                    scenarios, mean_df, sig_title, sig_color, detail_text,
                    action, sl_price, tp_price, symbol, scalp_tf, analysis_tf
                )
        except Exception as e:
            self.log_error(f"ANALYSIS_WORKER_{pk}", e)
            if self._is_initial_loading:
                self._ui_after(0, self._finish_startup_loading)
        finally:
            self.profile_ctx[pk]["is_analyzing"] = False
            if pk == self.active_profile_key:
                self.is_analyzing = False
                self._ui_after(0, self._finish_scan_progress_anim)
            self._cleanup_memory_and_trim_workingset()

    def _update_ui_results(self, profile_key, df_live_scalp, df_live_ana, scenarios, mean_df, sig_title, sig_color, detail_text, action, sl_price, tp_price, symbol, scalp_tf, analysis_tf):
        # Luôn cập nhật bảng 6 nến dự báo AI riêng của đúng Tab đó (ngay cả khi đang chạy ngầm)
        pred_tree = getattr(self, "pred_tree_by_profile", {}).get(profile_key)
        if pred_tree is not None and mean_df is not None:
            try:
                for row in pred_tree.get_children():
                    pred_tree.delete(row)
                for _, r in mean_df.iterrows():
                    ts_str = r["timestamps"].strftime("%Y-%m-%d %H:%M")
                    pred_tree.insert("", tk.END, values=(ts_str, f"{r['open']:.2f}", f"{r['high']:.2f}", f"{r['low']:.2f}", f"{r['close']:.2f}"))
            except Exception:
                pass

        # Chỉ cập nhật lên giao diện chính nếu kết quả thuộc về đúng Tab mà người dùng đang mở!
        if profile_key == self.active_profile_key:
            for attr in (
                "last_recommendation", "pending_pullback_order", "cached_scenarios",
                "cached_mean_df", "cached_action", "cached_sl", "cached_tp",
                "cached_df_hist", "cached_df_analysis", "_last_detail_prefix",
                "_sig_title", "_sig_color"
            ):
                setattr(self, attr, self.profile_ctx[profile_key].get(attr))
            self._update_forecast_tab_header()
            self.signal_title_lbl.config(text=sig_title, fg=sig_color)
            self.signal_detail_lbl.config(text=detail_text)
            self._update_bot_status_bar(profile_key)

            view_tf = getattr(self, "chart_view_tf_by_profile", {}).get(profile_key, scalp_tf)
            if view_tf == scalp_tf:
                chart_df = df_live_scalp
                chart_tf = scalp_tf
            elif view_tf == analysis_tf:
                chart_df = df_live_ana
                chart_tf = analysis_tf
            else:
                try:
                    chart_bars = min(max(int(self.lookback_var.get() or 450), 400), 500)
                except ValueError:
                    chart_bars = 450
                chart_df = self.fetch_mt5_candles(symbol, view_tf, chart_bars, include_forming=True)
                chart_tf = view_tf

            show_fan = (bool(getattr(self, "show_ai_chart_overlay", True)) and chart_tf == analysis_tf)
            self._highlight_active_tf_btn(chart_tf)
            self._draw_candlestick_chart(
                chart_df,
                scenarios if show_fan else None,
                mean_df if show_fan else None,
                action,
                sl_price,
                tp_price,
                symbol,
                chart_tf,
                has_forming_candle=True
            )

        if self._is_initial_loading:
            self._finish_startup_loading()

    # =========================================================================
    # VẼ BIỂU ĐỒ HỢP NHẤT REAL-TIME & DỰ BÁO AI (400 NẾN LỊCH SỬ + CROSSHAIR TRADINGVIEW)
    # =========================================================================
    def _draw_candlestick_chart(self, df_hist, scenarios, mean_df, action, sl_price, tp_price, symbol, tf, has_forming_candle=True):
        self.ax.clear()
        self._style_axes()
        self._highlight_active_tf_btn(tf)
        self._pos_text_artists = []

        # Nạp tối đa 450 nến lịch sử phía sau để khi thu nhỏ (Zoom Out) hoặc kéo lùi không bị khoảng trống đen
        max_hist_bars = 450
        hist = df_hist.iloc[-max_hist_bars:].reset_index(drop=True)
        n_hist = len(hist)
        show_ai_ov = bool(getattr(self, "show_ai_chart_overlay", True))
        # Hiển thị phần nến dự báo AI ngay trên biểu đồ hợp nhất khi bật lớp phủ Dự Báo AI và đang ở khung phân tích
        # Tách cụm 6 nến dự báo AI cách xa cây nến đang chạy một khoảng đệm 3 nến (pred_gap = 3) để dễ quan sát và phân biệt rõ ràng
        n_pred = (len(mean_df) if mean_df is not None else 0) if show_ai_ov else 0
        pred_gap = 3 if n_pred > 0 else 0
        total_x = n_hist + pred_gap + n_pred

        self._chart_hist_df = hist
        self._chart_mean_df = mean_df if show_ai_ov else None
        self._chart_n_hist = n_hist
        self._chart_pred_gap = pred_gap
        self._chart_total_x = total_x

        default_vis = 170
        if self.user_zoomed and self.saved_xlim is not None:
            view_xlim = self.saved_xlim
        else:
            view_xlim = (max(-1.5, n_hist - default_vis - 0.5), total_x + 4.0)

        # 1. Vẽ toàn bộ nến lịch sử bằng Vectorized Collection (gộp 399 nến đã đóng thành 2 Artist duy nhất giúp tăng tốc 30x)
        opens = hist["open"].to_numpy(dtype=float)
        highs = hist["high"].to_numpy(dtype=float)
        lows = hist["low"].to_numpy(dtype=float)
        closes = hist["close"].to_numpy(dtype=float)

        n_closed = (n_hist - 1) if (has_forming_candle and n_hist >= 1) else n_hist
        if n_closed > 0:
            xs_c = np.arange(n_closed, dtype=float)
            o_c = opens[:n_closed]
            h_c = highs[:n_closed]
            l_c = lows[:n_closed]
            c_c = closes[:n_closed]
            colors_c = [GREEN_UP if c_c[k] >= o_c[k] else RED_DOWN for k in range(n_closed)]

            wick_segs = [((xs_c[k], l_c[k]), (xs_c[k], h_c[k])) for k in range(n_closed)]
            wick_coll = LineCollection(wick_segs, colors=colors_c, linewidths=1.2, zorder=2)
            self.ax.add_collection(wick_coll)

            bottoms_c = np.minimum(o_c, c_c)
            heights_c = np.maximum(np.abs(c_c - o_c), 0.05)
            tops_c = bottoms_c + heights_c
            # Thân nến rộng 0.66 (±0.33) giúp khoảng cách giữa các cây nến gần lại 50% (từ 0.68 xuống 0.34)
            body_verts = [
                [
                    (xs_c[k] - 0.33, bottoms_c[k]),
                    (xs_c[k] + 0.33, bottoms_c[k]),
                    (xs_c[k] + 0.33, tops_c[k]),
                    (xs_c[k] - 0.33, tops_c[k]),
                ]
                for k in range(n_closed)
            ]
            body_coll = PolyCollection(body_verts, facecolors=colors_c, edgecolors=colors_c, linewidths=0.5, zorder=3)
            self.ax.add_collection(body_coll)

        # Vẽ riêng cây nến đang nhảy (Forming Candle) để có thể cập nhật tại chỗ mỗi 2 giây mà không cần vẽ lại biểu đồ
        self._forming_wick_line = None
        self._forming_body_rect = None
        if has_forming_candle and n_hist >= 1:
            i_f = n_hist - 1
            o_f, h_f, l_f, c_f = opens[i_f], highs[i_f], lows[i_f], closes[i_f]
            col_f = CYAN_LIVE if c_f >= o_f else "#ff4081"
            (self._forming_wick_line,) = self.ax.plot([i_f, i_f], [l_f, h_f], color=col_f, linewidth=1.4, zorder=4)
            bot_f = min(o_f, c_f)
            hgt_f = max(abs(c_f - o_f), 0.05)
            self._forming_body_rect = Rectangle((i_f - 0.33, bot_f), 0.66, hgt_f, facecolor=col_f, edgecolor=col_f, zorder=4)
            self.ax.add_patch(self._forming_body_rect)

        live_price = float(closes[-1])
        self._live_price_hline = self.ax.axhline(
            y=live_price, color=CYAN_LIVE, linestyle="-.", linewidth=0.9, alpha=0.85
        )

        last_candle_ts = hist["timestamps"].iloc[-1]
        cd_str = self._get_candle_countdown_str(last_candle_ts, tf, symbol=symbol)

        # Hiển thị Nhãn Mức Giá Hiện Tại trực tiếp trên Thanh Giá bên phải (như TradingView)
        self._live_price_annot = self.ax.annotate(
            f" {live_price:.2f} ",
            xy=(1.0, live_price),
            xycoords=("axes fraction", "data"),
            xytext=(2, 0),
            textcoords="offset points",
            color="#000000",
            fontsize=8.5,
            fontweight="bold",
            va="center",
            ha="left",
            bbox=dict(boxstyle="square,pad=0.22", facecolor=CYAN_LIVE, edgecolor="#00b8d4", lw=1),
            annotation_clip=False,
            zorder=20
        )

        # Đồng hồ đếm ngược thời gian đóng cây nến hiện tại trực tiếp dưới nhãn giá (chuẩn TradingView)
        self._candle_countdown_annot = self.ax.annotate(
            f" {cd_str} ",
            xy=(1.0, live_price),
            xycoords=("axes fraction", "data"),
            xytext=(2, -13),
            textcoords="offset points",
            color="#00e5ff",
            fontsize=8.0,
            fontweight="bold",
            va="center",
            ha="left",
            bbox=dict(boxstyle="square,pad=0.20", facecolor="#131722", edgecolor="#2a2e39", lw=1),
            annotation_clip=False,
            zorder=19
        )

        span_x = max(view_xlim[1] - view_xlim[0], 10.0)
        base_label_x = view_xlim[0] + span_x * 0.018
        col_step_x = span_x * 0.18

        # 2. Luôn hiển thị nhãn Entry, SL, TP kèm cả Mức Giá & Số Lãi/Lỗ ($) của các lệnh đang mở
        open_positions = None
        sel_tickets = self._get_selected_tickets_for_profile(self.active_profile_key)
        has_sel = len(sel_tickets) > 0
        if MT5_AVAILABLE:
            open_positions = self._get_bot_positions(symbol, profile_key=self.active_profile_key)
            if open_positions:
                eps_eq = self._get_symbol_tolerances(symbol)["eps_equal"]
                cfg_chart = self._get_profile_cfg(self.active_profile_key)
                def_be_p = 5.0 if self.active_profile_key == "BTCUSD" else 0.2
                try:
                    be_p_chart = max(float(cfg_chart.get("be_plus_price", def_be_p)), 0.0)
                except ValueError:
                    be_p_chart = def_be_p
                for idx_p, pos in enumerate(open_positions):
                    is_buy = (pos.type == mt5.POSITION_TYPE_BUY)
                    is_sel = (pos.ticket in sel_tickets)
                    be_target_c = (pos.price_open + be_p_chart) if is_buy else (pos.price_open - be_p_chart)
                    is_sl_at_entry = (pos.sl > 0 and abs(pos.sl - pos.price_open) < eps_eq)
                    is_tp_at_entry = (pos.tp > 0 and abs(pos.tp - pos.price_open) < eps_eq)
                    is_sl_at_be_plus = (pos.sl > 0 and abs(pos.sl - be_target_c) < eps_eq)
                    is_tp_at_be_plus = (pos.tp > 0 and abs(pos.tp - be_target_c) < eps_eq)
                    be_tag = (
                        f" [SL=Entry+{be_p_chart:g}]" if (is_sl_at_be_plus and be_p_chart > 0)
                        else (" [SL=0]" if is_sl_at_entry else (" [Chờ Entry+]" if (is_tp_at_be_plus or is_tp_at_entry) else ""))
                    )
                    offset_x = base_label_x + (idx_p * col_step_x)

                    # Tính toán trước số Lãi/Lỗ ($) dự kiến khi chạm SL hoặc TP
                    sl_pnl = self._calc_target_pnl(pos, pos.sl) if pos.sl > 0 else 0.0
                    tp_pnl = self._calc_target_pnl(pos, pos.tp) if pos.tp > 0 else 0.0
                    is_profit_sl = (sl_pnl >= 0)

                    # Tùy biến màu sắc, độ dày nét và nhãn khi người dùng chọn lệnh trong danh sách
                    if is_sel:
                        # 1. LỆNH ĐƯỢC CHỌN TRONG BẢNG: Nổi bật vừa phải, đường kẻ thanh mảnh
                        entry_col = "#00e5ff"  # Neon Cyan
                        entry_w = 1.15
                        entry_ls = "-"
                        entry_alpha = 0.95
                        entry_bg = "#00e5ff"
                        entry_fg = "#000000"
                        entry_txt = f" 👉 [#{pos.ticket}] Entry: {pos.price_open:.2f} ({pos.profit:+.1f}$){be_tag} "
                        entry_fs = 7.5
                        z_line, z_badge = 28, 32

                        sl_col = "#ffea00" if is_profit_sl else "#ff1744"  # Neon Amber nếu khóa lãi, Neon Red nếu cắt lỗ
                        sl_w = 1.0
                        sl_ls = "--"
                        sl_alpha = 0.95
                        sl_bg = "#ffea00" if is_profit_sl else "#ff1744"
                        sl_fg = "#000000" if is_profit_sl else "#ffffff"
                        sl_txt = f" 👉 [#{pos.ticket}] SL: {pos.sl:.2f} ({sl_pnl:+.1f}$) "
                        sl_fs = 7.2

                        tp_col = "#00e676"  # Neon Green
                        tp_w = 1.0
                        tp_ls = "--"
                        tp_alpha = 0.95
                        tp_bg = "#00e676"
                        tp_fg = "#000000"
                        tp_txt = f" 👉 [#{pos.ticket}] TP: {pos.tp:.2f} ({tp_pnl:+.1f}$) "
                        tp_fs = 7.2
                    elif has_sel:
                        # 2. CÁC LỆNH KHÁC KHI ĐANG CÓ LỆNH ĐƯỢC CHỌN: Làm mờ nhẹ để mắt người dùng tập trung vào lệnh chọn
                        entry_col = "#00e676" if is_buy else "#ff5252"
                        entry_w = 0.8
                        entry_ls = ":"
                        entry_alpha = 0.40
                        entry_bg = "#0f3923" if is_buy else "#451a1a"
                        entry_fg = "#7ec89e" if is_buy else "#c87e7e"
                        entry_txt = f" Entry: {pos.price_open:.2f} ({pos.profit:+.1f}$){be_tag} "
                        entry_fs = 6.8
                        z_line, z_badge = 10, 12

                        sl_col = "#cca000" if is_profit_sl else "#8a2a35"
                        sl_w = 0.75
                        sl_ls = ":"
                        sl_alpha = 0.35
                        sl_bg = "#3a2c10" if is_profit_sl else "#301317"
                        sl_fg = "#b39860" if is_profit_sl else "#9c6068"
                        sl_txt = f" SL: {pos.sl:.2f} ({sl_pnl:+.1f}$) "
                        sl_fs = 6.8

                        tp_col = "#2a6644"
                        tp_w = 0.75
                        tp_ls = ":"
                        tp_alpha = 0.35
                        tp_bg = "#112e1e"
                        tp_fg = "#699e7d"
                        tp_txt = f" TP: {pos.tp:.2f} ({tp_pnl:+.1f}$) "
                        tp_fs = 6.8
                    else:
                        # 3. KHÔNG CÓ LỆNH NÀO ĐƯỢC CHỌN: Hiển thị giao diện tiêu chuẩn cân bằng
                        entry_col = "#00e676" if is_buy else "#ff5252"
                        entry_w = 0.9
                        entry_ls = "--"
                        entry_alpha = 0.85
                        entry_bg = "#00e676" if is_buy else "#ff5252"
                        entry_fg = "#000000" if is_buy else "#ffffff"
                        entry_txt = f" Entry: {pos.price_open:.2f} ({pos.profit:+.1f}$){be_tag} "
                        entry_fs = 7.2
                        z_line, z_badge = 15, 18

                        sl_col = GOLD_ACCENT if is_profit_sl else "#ff5252"
                        sl_w = 0.85
                        sl_ls = ":"
                        sl_alpha = 0.80
                        sl_bg = GOLD_ACCENT if is_profit_sl else "#6e1b26"
                        sl_fg = "#000000" if is_profit_sl else "#ffffff"
                        sl_txt = f" SL: {pos.sl:.2f} ({sl_pnl:+.1f}$) "
                        sl_fs = 7.0

                        tp_col = "#69f0ae"
                        tp_w = 0.85
                        tp_ls = ":"
                        tp_alpha = 0.80
                        tp_bg = "#69f0ae"
                        tp_fg = "#000000"
                        tp_txt = f" TP: {pos.tp:.2f} ({tp_pnl:+.1f}$) "
                        tp_fs = 7.0

                    # Đường Entry kèm mức giá & số lãi/lỗ hiện tại
                    self.ax.axhline(y=pos.price_open, color=entry_col, linestyle=entry_ls, linewidth=entry_w, alpha=entry_alpha, zorder=z_line)
                    t_ent = self.ax.text(
                        offset_x, pos.price_open, entry_txt,
                        color=entry_fg, backgroundcolor=entry_bg,
                        fontsize=entry_fs, fontweight="bold", va="center", clip_on=True, zorder=z_badge
                    )
                    self._pos_text_artists.append((t_ent, idx_p))

                    # Đường SL kèm mức giá SL & số lỗ/lãi dự kiến
                    if pos.sl > 0 and not is_sl_at_entry:
                        self.ax.axhline(y=pos.sl, color=sl_col, linestyle=sl_ls, linewidth=sl_w, alpha=sl_alpha, zorder=z_line)
                        t_sl = self.ax.text(
                            offset_x, pos.sl, sl_txt,
                            color=sl_fg, backgroundcolor=sl_bg,
                            fontsize=sl_fs, fontweight="bold", va="center", clip_on=True, zorder=z_badge
                        )
                        self._pos_text_artists.append((t_sl, idx_p))

                    # Đường TP kèm mức giá TP & số lãi dự kiến
                    if pos.tp > 0 and not is_tp_at_entry:
                        self.ax.axhline(y=pos.tp, color=tp_col, linestyle=tp_ls, linewidth=tp_w, alpha=tp_alpha, zorder=z_line)
                        t_tp = self.ax.text(
                            offset_x, pos.tp, tp_txt,
                            color=tp_fg, backgroundcolor=tp_bg,
                            fontsize=tp_fs, fontweight="bold", va="center", clip_on=True, zorder=z_badge
                        )
                        self._pos_text_artists.append((t_tp, idx_p))

        # 2b. Hiển thị Lệnh Đã Đóng & Tô Màu Vàng Cây Nến Khớp Lệnh khi người dùng nhấp chọn trong bảng Lịch Sử
        sel_closed_deal = None
        if sel_tickets:
            for t_sel in sel_tickets:
                cd_cand = self._get_closed_deal_info(t_sel, symbol=symbol)
                if cd_cand is not None:
                    sel_closed_deal = cd_cand
                    break

        self._active_viewing_closed_deal = sel_closed_deal

        if sel_closed_deal is not None:
            cd = sel_closed_deal
            open_ts = int(cd.get("open_ts") or 0)
            open_p = float(cd.get("open_price") or 0.0)
            sl_p = float(cd.get("sl") or 0.0)
            tp_p = float(cd.get("tp") or 0.0)
            close_p = float(cd.get("close_price") or 0.0)
            net_pnl = float(cd.get("net_pnl") or 0.0)
            cmd_type = str(cd.get("type", "BUY")).upper()
            is_buy = (cmd_type == "BUY")
            status_str = str(cd.get("status", "")).strip()

            # 1. Tìm cây nến khớp lệnh chính xác trên biểu đồ
            tf_sec_map = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600}
            tf_sec = tf_sec_map.get(tf, 300)
            k_match = -1
            ts_vals = [int(pd.to_datetime(t).timestamp()) for t in hist["timestamps"]] if n_hist > 0 else []
            if open_ts > 0 and n_hist > 0:
                for idx_k, c_ts in enumerate(ts_vals):
                    if c_ts <= open_ts < (c_ts + tf_sec):
                        k_match = idx_k
                        break
                if k_match < 0:
                    diffs = [abs(c_ts - open_ts) for c_ts in ts_vals]
                    min_diff_idx = int(np.argmin(diffs))
                    if diffs[min_diff_idx] <= tf_sec * 2:
                        k_match = min_diff_idx

            # Tự động trượt tầm nhìn về cây nến khớp lệnh (Auto-focus) nếu vừa nhấp chọn
            if getattr(self, "_selected_deal_ticket_focus", None) == cd["ticket"] and k_match >= 0:
                # Giữ nguyên độ rộng khung nhìn hiện tại (không phóng to), chỉ dời khung sao cho nến vào lệnh nằm giữa
                _span = max(float(view_xlim[1] - view_xlim[0]), 70.0)
                _left = k_match - _span * 0.5
                _right = _left + _span
                if _right > total_x + 5.0:
                    _right = total_x + 5.0
                    _left = _right - _span
                if _left < -1.5:
                    _left = -1.5
                    _right = _left + _span
                new_xlim = (_left, _right)
                self.saved_xlim = new_xlim
                self.user_zoomed = True
                self.ax.set_xlim(new_xlim)
                view_xlim = new_xlim
                span_x = max(view_xlim[1] - view_xlim[0], 10.0)
                base_label_x = view_xlim[0] + span_x * 0.018
                col_step_x = span_x * 0.18
                self._selected_deal_ticket_focus = None

                # Tự động tính toán lại trục Y ôm trọn các nến quanh lệnh đã đóng và các mức Entry/SL/TP/Exit
                auto_y = self._compute_visible_ylim(new_xlim[0], new_xlim[1])
                if auto_y is not None:
                    self.saved_ylim = auto_y
                    self.ax.set_ylim(auto_y)

            # 2. Tô màu VÀNG cây nến khớp lệnh & Đánh dấu X cây nến đóng lệnh (Không vẽ khung chú thích che nến)
            if 0 <= k_match < n_hist:
                o_m = float(opens[k_match])
                h_m = float(highs[k_match])
                l_m = float(lows[k_match])
                c_m = float(closes[k_match])

                # Râu nến vàng thanh mảnh
                self.ax.plot([k_match, k_match], [l_m, h_m], color="#ffd700", linewidth=1.8, zorder=25)
                # Thân nến vàng gọn gàng với viền mảnh
                bot_m = min(o_m, c_m)
                hgt_m = max(abs(c_m - o_m), 0.06)
                gold_rect = Rectangle(
                    (k_match - 0.36, bot_m), 0.72, hgt_m,
                    facecolor="#ffd700", edgecolor="#ffffff", linewidth=0.8, alpha=0.92, zorder=26
                )
                self.ax.add_patch(gold_rect)

                # Đánh dấu cây nến đóng lệnh bằng dấu x (chỉ vẽ dấu x, không vẽ khung chú thích)
                close_ts = int(cd.get("close_ts") or 0)
                k_close = -1
                if close_ts > 0 and n_hist > 0:
                    for idx_k, c_ts in enumerate(ts_vals):
                        if c_ts <= close_ts < (c_ts + tf_sec):
                            k_close = idx_k
                            break
                if 0 <= k_close < n_hist and close_p > 0:
                    exit_col = "#ff1744" if net_pnl < 0 else "#00e676"
                    self.ax.plot(k_close, close_p, marker="x", markersize=8, color=exit_col, markeredgewidth=1.6, zorder=40)

            # 3. Vẽ 3 đường mức giá Entry, SL, TP của lệnh đã đóng (Nét mảnh thanh thoát, nhãn tối viền mảnh không che chữ)
            # Đường Entry
            self.ax.axhline(y=open_p, color="#ffd700", linestyle="--", linewidth=0.9, alpha=0.80, zorder=28)
            t_closed_ent = self.ax.text(
                base_label_x, open_p,
                f" [#{cd['ticket']}] Entry: {open_p:.2f} | PnL: {net_pnl:+.2f}$ ",
                color="#ffd700",
                bbox=dict(boxstyle="square,pad=0.18", facecolor="#131722", edgecolor="#ffd700", lw=0.8, alpha=0.88),
                fontsize=7.2, fontweight="bold", va="center", clip_on=True, zorder=35
            )
            self._pos_text_artists.append((t_closed_ent, 0))

            # Đường SL
            if sl_p > 0:
                sl_dist = abs(open_p - sl_p)
                self.ax.axhline(y=sl_p, color="#ff5252", linestyle=":", linewidth=0.85, alpha=0.75, zorder=28)
                t_closed_sl = self.ax.text(
                    base_label_x, sl_p,
                    f" [#{cd['ticket']}] SL: {sl_p:.2f} (-{sl_dist:.2f}) ",
                    color="#ff8a80",
                    bbox=dict(boxstyle="square,pad=0.18", facecolor="#131722", edgecolor="#ff5252", lw=0.8, alpha=0.88),
                    fontsize=7.0, va="center", clip_on=True, zorder=35
                )
                self._pos_text_artists.append((t_closed_sl, 0))

            # Đường TP
            if tp_p > 0:
                tp_dist = abs(tp_p - open_p)
                self.ax.axhline(y=tp_p, color="#69f0ae", linestyle=":", linewidth=0.85, alpha=0.75, zorder=28)
                t_closed_tp = self.ax.text(
                    base_label_x, tp_p,
                    f" [#{cd['ticket']}] TP: {tp_p:.2f} (+{tp_dist:.2f}) ",
                    color="#69f0ae",
                    bbox=dict(boxstyle="square,pad=0.18", facecolor="#131722", edgecolor="#69f0ae", lw=0.8, alpha=0.88),
                    fontsize=7.0, va="center", clip_on=True, zorder=35
                )
                self._pos_text_artists.append((t_closed_tp, 0))

            # Đường Exit nếu khác SL và TP
            if close_p > 0 and (sl_p == 0 or abs(close_p - sl_p) > 0.05) and (tp_p == 0 or abs(close_p - tp_p) > 0.05):
                self.ax.axhline(y=close_p, color="#ffb74d", linestyle=":", linewidth=0.8, alpha=0.70, zorder=27)
                t_closed_exit = self.ax.text(
                    base_label_x + col_step_x, close_p,
                    f" [#{cd['ticket']}] Đóng: {close_p:.2f} ",
                    color="#ffb74d",
                    bbox=dict(boxstyle="square,pad=0.18", facecolor="#131722", edgecolor="#ffb74d", lw=0.8, alpha=0.88),
                    fontsize=7.0, va="center", clip_on=True, zorder=34
                )
                self._pos_text_artists.append((t_closed_exit, 1))

        # Vẽ đường Canh Vùng Giá AI <= SL Tối Đa (nếu đang có vùng canh giá hoạt động)
        if self.pending_pullback_order is not None:
            p = self.pending_pullback_order
            try:
                max_sl_p = float(p.get("effective_max_sl") or self.max_sl_price_var.get())
            except (ValueError, TypeError):
                max_sl_p = 5.0
            lim_entry = (p["sl_price"] + max_sl_p) if p["action"] == "BUY" else (p["sl_price"] - max_sl_p)
            op_sign = "<=" if p["action"] == "BUY" else ">="
            self.ax.axhline(y=lim_entry, color=GOLD_ACCENT, linestyle="--", linewidth=0.85, alpha=0.80)
            tight_tag = " (RR≥2R)" if p.get("sl_tightened_for_2r") else ""
            t_wait = self.ax.text(
                base_label_x, lim_entry,
                f" 🎯 Vùng canh {p['action']}{tight_tag} {op_sign} {lim_entry:.2f} (SL≤{max_sl_p:.2f}$) ",
                color=GOLD_ACCENT,
                bbox=dict(boxstyle="square,pad=0.18", facecolor="#131722", edgecolor=GOLD_ACCENT, lw=0.8, alpha=0.88),
                fontsize=7.2, fontweight="bold", va="center", clip_on=True
            )
            self._pos_text_artists.append((t_wait, 0))

        # 3. Cụm Dự báo Kronos AI (Tách cách xa cây nến đang chạy pred_gap = 3 nhịp, chỉ vẽ 6 Nến Dự Báo AI gọn gàng, ẩn các đường kịch bản tơ nhện)
        all_times = list(hist["timestamps"].dt.strftime("%H:%M"))
        if show_ai_ov and mean_df is not None:
            pred_start_x = n_hist + pred_gap
            sep_x = n_hist + (pred_gap * 0.5) - 0.5

            for j in range(n_pred):
                idx_x = pred_start_x + j
                o, h, l, c = mean_df.loc[j, ["open", "high", "low", "close"]]
                color = "#ffd54f" if c >= o else "#ff8a65"
                self.ax.plot([idx_x, idx_x], [l, h], color=color, linewidth=1.25)
                bottom = min(o, c)
                height = max(abs(c - o), 0.05)
                self.ax.add_patch(Rectangle((idx_x - 0.30, bottom), 0.60, height, facecolor=color, edgecolor=color, label="Nến Dự báo AI" if j == 0 else ""))

            self.ax.axvline(x=sep_x, color=GOLD_ACCENT, linestyle="--", linewidth=0.95, alpha=0.75)
            all_times += [""] * pred_gap + list(mean_df["timestamps"].dt.strftime("%H:%M"))

        # Luôn hiển thị đường & nhãn SL/TP Đề Xuất của AI với nét mảnh thanh thoát trên mọi khung giờ (M1/M5/M15/H1) và kể cả khi đang có lệnh mở
        if show_ai_ov and sl_price is not None and tp_price is not None:
            try:
                lot_val = float(self.lot_var.get())
            except ValueError:
                lot_val = 0.01
            with self._mt5_lock:
                sym_info = mt5.symbol_info(symbol) if MT5_AVAILABLE else None
            contract_sz = float(sym_info.trade_contract_size) if (sym_info and sym_info.trade_contract_size > 0) else (1.0 if "BTC" in str(symbol).upper() else 100.0)
            # Cố định số tiền USD ước tính theo khoảng cách từ Entry đóng nến đến SL/TP (không tính theo live_price để nhãn text đứng yên tuyệt đối)
            entry_anchor = float(hist["close"].iloc[-2]) if (has_forming_candle and len(hist) >= 2) else float(hist["close"].iloc[-1])
            est_sl_usd = -abs(entry_anchor - sl_price) * lot_val * contract_sz
            est_tp_usd = abs(tp_price - entry_anchor) * lot_val * contract_sz
            ai_col_idx = max(len(open_positions) if open_positions else 0, 1 if self.pending_pullback_order is not None else 0)
            ai_label_x = base_label_x + (ai_col_idx * col_step_x)

            self.ax.axhline(
                y=sl_price, color="#ff6b81", linestyle=(0, (4, 3)), linewidth=0.75, alpha=0.80, zorder=14,
                label=f"SL đề xuất AI: {sl_price:.2f} ({est_sl_usd:+.1f}$)"
            )
            t_ai_sl = self.ax.text(
                ai_label_x, sl_price,
                f" SL AI: {sl_price:.2f} ({est_sl_usd:+.1f}$) ",
                color="#ffcdd2", backgroundcolor="#3b1219",
                fontsize=7.0, va="center", clip_on=True, zorder=17
            )
            self._pos_text_artists.append((t_ai_sl, ai_col_idx))

            self.ax.axhline(
                y=tp_price, color="#69f0ae", linestyle=(0, (4, 3)), linewidth=0.75, alpha=0.80, zorder=14,
                label=f"TP đề xuất AI: {tp_price:.2f} ({est_tp_usd:+.1f}$)"
            )
            t_ai_tp = self.ax.text(
                ai_label_x, tp_price,
                f" TP AI: {tp_price:.2f} ({est_tp_usd:+.1f}$) ",
                color="#b9f6ca", backgroundcolor="#0d2e1e",
                fontsize=7.0, va="center", clip_on=True, zorder=17
            )
            self._pos_text_artists.append((t_ai_tp, ai_col_idx))

        self._chart_all_times = all_times

        # 4. Khởi tạo bộ Crosshair nét đứt + Nhãn giá tối bên phải kiểu TradingView (Ảnh 1)
        self.cross_vline = self.ax.axvline(x=n_hist - 1, color="#8b98a9", linestyle="--", linewidth=0.85, alpha=0.85, visible=False, zorder=20)
        self.cross_hline = self.ax.axhline(y=live_price, color="#8b98a9", linestyle="--", linewidth=0.85, alpha=0.85, visible=False, zorder=20)
        self.cross_price_annot = self.ax.annotate(
            "",
            xy=(1.0, live_price),
            xycoords=("axes fraction", "data"),
            xytext=(2, 0),
            textcoords="offset points",
            color="#ffffff",
            fontsize=8.5,
            fontweight="bold",
            va="center",
            ha="left",
            bbox=dict(boxstyle="square,pad=0.24", facecolor="#131722", edgecolor="#4c525e", lw=1),
            annotation_clip=False,
            visible=False,
            zorder=25
        )
        self.cross_time_annot = self.ax.annotate(
            "",
            xy=(n_hist - 1, 0.0),
            xycoords=("data", "axes fraction"),
            xytext=(0, -14),
            textcoords="offset points",
            color="#ffffff",
            fontsize=8,
            fontweight="bold",
            va="center",
            ha="center",
            bbox=dict(boxstyle="square,pad=0.22", facecolor="#131722", edgecolor="#4c525e", lw=1),
            annotation_clip=False,
            visible=False,
            zorder=25
        )

        mode_label = "BIỂU ĐỒ REAL-TIME & DỰ BÁO AI" if show_ai_ov else "BIỂU ĐỒ LỆNH REAL-TIME"
        zoom_hint = " [Nháy đúp để Reset]" if self.user_zoomed else ""
        ana_tf_hint = self.analysis_tf_var.get().strip()
        if show_ai_ov and tf != ana_tf_hint:
            zoom_hint = f" [💡 Bấm nút {ana_tf_hint} để xem 6 nến dự báo AI]"
        if sel_closed_deal is not None:
            cd = sel_closed_deal
            open_p_str = f"{float(cd.get('open_price') or 0.0):.2f}"
            close_p_str = f"{float(cd.get('close_price') or 0.0):.2f}"
            pnl_str = f"{float(cd.get('net_pnl') or 0.0):+.2f}$"
            self.ax.set_title(
                f"🔍 [SOI NẾN LỆNH #{cd['ticket']}] {cd.get('type')} @ {open_p_str} ➔ Đóng @ {close_p_str} | PnL: {pnl_str} ({status_str}) — {symbol} ({tf}){zoom_hint}",
                color=GOLD_ACCENT, fontsize=8.8, fontweight="bold"
            )
        else:
            self.ax.set_title(
                f"[{mode_label}] {symbol} ({tf}) — Giá Live: {live_price:.2f} ({cd_str}){zoom_hint}",
                color=GOLD_ACCENT, fontsize=9, fontweight="bold"
            )
        handles, labels = self.ax.get_legend_handles_labels()
        if handles and any(bool(lbl and not lbl.startswith("_")) for lbl in labels):
            self.ax.legend(loc="upper left", facecolor=PANEL_BG, edgecolor="#2b3548", labelcolor=TEXT_MAIN, fontsize=8, framealpha=0.85)

        if self.user_zoomed and self.saved_xlim is not None:
            if self.saved_ylim is None:
                self.saved_ylim = self._compute_visible_ylim(self.saved_xlim[0], self.saved_xlim[1])

            # Nếu người dùng đang zoom nhưng giá hoặc râu nến mới đâm xuyên ra ngoài thì tự động kéo trục Y theo (CHỈ khi đang ở vùng đuôi Live)
            is_viewing_live_tail = (self.saved_xlim[1] >= (n_hist - 5))
            if is_viewing_live_tail and self.saved_ylim is not None:
                cur_y0, cur_y1 = self.saved_ylim
                span_y = max(cur_y1 - cur_y0, 1.0)
                margin_y = span_y * 0.04
                last_l = float(hist["low"].iloc[-1])
                last_h = float(hist["high"].iloc[-1])
                if last_l < cur_y0 + margin_y:
                    shift = (cur_y0 + margin_y) - last_l + (span_y * 0.06)
                    self.saved_ylim = (cur_y0 - shift, cur_y1 - shift)
                elif last_h > cur_y1 - margin_y:
                    shift = last_h - (cur_y1 - margin_y) + (span_y * 0.06)
                    self.saved_ylim = (cur_y0 + shift, cur_y1 + shift)

            self.ax.set_xlim(self.saved_xlim)
            if self.saved_ylim is not None:
                self.ax.set_ylim(self.saved_ylim)
            self._sync_xlim_dependents(self.saved_xlim)
        else:
            self.ax.set_xlim(view_xlim)
            auto_ylim = self._compute_visible_ylim(view_xlim[0], view_xlim[1])
            if auto_ylim is not None:
                self.saved_ylim = auto_ylim
                self.ax.set_ylim(auto_ylim)
            self._sync_xlim_dependents(view_xlim)

        if self._last_hover_xy is not None:
            self._apply_crosshair_xy(self._last_hover_xy[0], self._last_hover_xy[1])

        sel_sig = tuple(sorted(self._get_selected_tickets_for_profile(self.active_profile_key)))
        pos_sig = (
            (tuple((p.ticket, round(p.sl, 2), round(p.tp, 2)) for p in open_positions) if open_positions else ()),
            sel_sig
        )
        pb_sig = (
            (self.pending_pullback_order["action"], round(self.pending_pullback_order["sl_price"], 2))
            if self.pending_pullback_order is not None else None
        )
        ai_sl_tp_sig = (
            round(float(sl_price), 2) if sl_price is not None else None,
            round(float(tp_price), 2) if tp_price is not None else None,
        )
        self._last_drawn_chart_key = (symbol, tf, show_ai_ov, hist["timestamps"].iloc[-1])
        self._last_drawn_pos_sig = (pos_sig, pb_sig, ai_sl_tp_sig)

        self.fig.subplots_adjust(left=0.045, right=0.945, top=0.93, bottom=0.07)
        self._request_canvas_redraw()


def run_first_time_setup():
    """Thiết lập thông số mặc định và tự động tạo Shortcut chạy phần mềm trong thư mục & ngoài Desktop."""
    import subprocess
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    default_settings = dict(DEFAULT_PROFILE_SETTINGS["XAUUSD"])
    default_settings["profiles"] = {
        "XAUUSD": dict(DEFAULT_PROFILE_SETTINGS["XAUUSD"]),
        "BTCUSD": dict(DEFAULT_PROFILE_SETTINGS["BTCUSD"]),
    }
    if not os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(default_settings, f, ensure_ascii=False, indent=2)

    # Nếu chưa có Icon trong AppData, tự động tạo Icon tròn vàng/đen sắc nét
    if not os.path.exists(ICON_ICO_FILE) or not os.path.exists(ICON_PNG_FILE):
        try:
            import base64, io
            from PIL import Image
            img = Image.open(io.BytesIO(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAGAAAABgCAYAAADimHc4AAABCGlDQ1BJQ0MgUHJvZmlsZQAAeJxjYGA8wQAELAYMDLl5JUVB7k4KEZFRCuwPGBiBEAwSk4sLGHADoKpv1yBqL+viUYcLcKakFicD6Q9ArFIEtBxopAiQLZIOYWuA2EkQtg2IXV5SUAJkB4DYRSFBzkB2CpCtkY7ETkJiJxcUgdT3ANk2uTmlyQh3M/Ck5oUGA2kOIJZhKGYIYnBncAL5H6IkfxEDg8VXBgbmCQixpJkMDNtbGRgkbiHEVBYwMPC3MDBsO48QQ4RJQWJRIliIBYiZ0tIYGD4tZ2DgjWRgEL7AwMAVDQsIHG5TALvNnSEfCNMZchhSgSKeDHkMyQx6QJYRgwGDIYMZAKbWPz9HbOBQAABaaUlEQVR42l39Z6xuWXrfB/5W2PGNJ5+bc+WqrqrOgWqSzdSkSYpqUhQlUSJBQZIVLGAwg5n5MEZ7BhA8gD0a2f4gjwXCkMbWGLItmaLEoG6yc6yunOvWzeece/Kbdl5hPqy3ipq5QAG3Lg7Ou/d+137Ws/7pEX/0D/+v3lqLtQ4pJQDOhb9ba3DOIZQAL9BKAyClRAgQSqKUQgiBEII4jhBC4oVHqQhrLF4AQjAajZjMZ2gEURzjpEBLhbeWtm1JshSkxHQCISRZlmGMRUqJlBLnHNZapBRIGT4TwAsQgEDgvec//PPBdf2H9/Qf/oyxluO9XeJY0Rutcnp8hDcNSZyztn2Wgwd3qIoFF68/xrwsmBw8pJjNGa6MuXjlBg/u3WLY7zE5nVJOZ8RJQm9zk35/wHw+w7QtTdVw4cpljPfhOoVEOIuzDiEE+oMbc84jpaTr2uVNa7rO0+v3EVLSNA2mM/R7PYwxaK2w1mKsRUcRnTFY5+nlOSqSKKlw1oKUZHnGvJjjmhbV69GaDqElOIczljhNaI3DmI4szomTBO89Sim893jvkVIA8sOH6r0LN4NALB9o+Dn5H3wFPiwgKdFa4Vz4XUIIhJS4pkFrCTiq+Yw8jjmanBKNY04OdqmrBVJHTI4PKcsifI4Abx3vvfUGGxurlFXBbDZBK4UXDtqWo537WNuBiFnfXKcoZrSNQUiJNQZvHSsrKwDoruuQUpIkEV1n0FqjtUZK9eHq7rqOfr/PYrGgqiriNKFsGvJexurKKsZahBRgHXVRIr2ECHSkiZKE+XROuSjQaYyrSqSOSAHrLUIqWuPxTrEyWsHj8eHZgRBI8cGj9GilUVphrMFZT6QV+LCSASIdYZ1dfgkehFj+Lk/XdR/em3MOAXhnSXspk6MjEm3weIwxKAVVOaduW9bXh5TFnMFgxOHBPr1ej7Is2D57BpznZP8I7y0i0nTOMD86wgkPSqKEZDo5xZgG4cBYjxeK7a0ttNa0xqCFEDjncM6hdXjoSikcEMcxXdvhvcMag7OW8foa/UEf7xxIRVWHL8R2HUma4ryn7Vq6zhBHmul0znCwysbGOaq6QmqFUBrbNFjrEFIQJQk6irHWggARakpYwT68tkqGt+Hg9JD1lQ2yJGNelCA9w16fpuk4mh6wMlhFCIED8C4UJyHxWMrFgt5g+GFZss4jdMJwtMLsZEJdV5w9d46mrqmKmu3ts9RlgTGGKEupqwrvPecvXMCYlv2dPWxn8VpSNy1KKlrboZVEWPDeYLoaEWlM2zJaXWO8voEzlqZrwn3HcbxcNWq5OixN29J2HbGOiCNNlGSUdUOW5wxWRnRtS1NWRFFMkiR0xlDPC9KVGIFga2uLqqgpi5rNrS2QCucdeX+AdQ7nPbrX+7B8ODzWGrRUKCEJy97jjQckSEiznPd3bnLr4DYf7Q/YPz7mtH6IjiTD+SbDQZ+bhzd5RF/nwtolqrZZ1guPBNI8YW4stmuRcYwUAi8gyxPmxYy6KVldX6MzHfP5nNW1NZpFyWKxoDceEsea8eoKa+vr1HXN/u4eXV2TpikWiOKItuuQWpPGMa5tIYqo2wZvO6Iopi4Ldu8ucM4jpEApGfaAzhiUBmfAOkve6xEva7IVYNoGYwzD0QjrHfP5nEGahzIURcRxQqcq5vMFWZYyP51gDAxGq1jAtTVYR6c0SZognA8P2Ptl+RCkUcK0mnFUHJDHGYN0QN0VVM2USTFn5+CIWzt79EYZ33rpFRblnC9+4WnOjtb53X/1h8RRxNmz69w/2GE1H3Nhc4vxcEQvHxKRMilmlLZmgzEreZ+6azBNQWkLpscn5HlGHEfM53OyPMc0LbZpAc/m9iYeWN/aYHp8zOnRCV1jiKII5wxSaIRxSOtRaUycp0SjAVVV04s1xrRY6yjLBVrHy/InGA1X0Wme01OKpm3pOsvKeIRSksW8JOn1GIwGNFXN7PiEYjEnTWMSpRFRRKIknTUM8gy1sYpwHiUkrXOYpqPuKkTb0tYNvgMrPVJrxisbICRKSiId3sC7xzv8YOd13trf4ezqGDUvOT494Xg6pywrlIjZOzzhJz71JPOmQseSew92cbZiNI54sF+xaTu+8YM3GA5TwJNlCWfPbNDr9WiTjEXX8ZHtizzhDMMoJ4o0arnJxDoOb7VUCMJCXHQNa1vrJHnGyckJp/uHNLOCpihBStrWhn0lBRWFB53nKUoqjLEMRwPatsXZmGJRYEyHsS1SSLSOiJRG/Y1f+OKXu7YjUposzxFA0zakw5zeaIgxHVGakuUZXd0yn82IdIQXAt8YpBB01pBmKV1rKBcFTRM6qa6tscaQJH36w3Gov0qhpSaJEqxznCwO+c5b3+O7u6/xg3feI7OWpix5cLygUYrFtGFediyKmpVBH9cUPPvMZV567Q5tW3P/wTHv3zviZz77FPd2dmk6wem0pLEWYzykCfcOT5DWQOd45fZ71ElHW05ZWR0jvITWomNFnOXoKEJFmnlZsH3+HGmecvjwgHpWUE7nLGYz4jhGSEWW5+SDATLSNFWNAKq2xlrHeDwiTVI605H3ekRRTJ73EB6cMQjnaYoa3XWGKI6I45i6quisYXV7C9M21NMZzjq6xJClKRvnzuCAk719ullHHGeURYFQAtt0eCcwxpOkCb5zZPGAtN8LbSMgpWY86rOoZ9w9eIsX33uT77/1Lp0zyNGArhXsHB/RzxJap7j/YBepMwZZSpKmzBYlu3sLnn32Ohc3hvzMTz7P1vYG/+J/+TpJInn1jfsMxzlpnlF2ljv7M27vnnL+4ja2PqapKqK1ITfv3+L2vOMH777Bx556gsvr22RRn5iE1hqK6YztC+eJs5SuKGgWFdWioClq4iwjzVK8BS8EddfSNC22blgZjyiaGikF08mESGn6KyOkkMtGwJPECXSWummQ2iP+5B/9Yy+9oSxLsn5KnMYY6/DGMchzZKIpqppYaYSUyEhjLbjakeY5cRxTVSVtVaGjmF5/AJH6sB8PnY0gUpqqrfjB2y/y7v23ubWzQ9M6pIy5vfuQtJfhESSxwBqHrVsunVll4iNu33vIdj9mmGfMqo4nLw358c/eoD8ecPniOWYnU/7X3/8Btx9WZLFkWlbsF5aVUZ/cGnYPT0iHPYTwtFUduiTruXh+A6lgbWWVq9ubbOTrPHX1Cag6dKxZzBbYpqapGvb3DxjkPbJeTpbnTA6PMd5hnKVrWzY3NzCmYzoviKRikOd4DzJSqDgm74WyaFtDWdZ462jKCu1FOImqSBFlaejzI43HUzcNmBbvAekx1pAmEWXRsjJcQWuNcZakn5MPBwgPXdchfTi5dqajl/fobMeb997gh2+/xDdeepVZ1fLo5bMUkwUHi2OEVKRI0jTh5s4BMorYHPcYJikPTwuyOKFrHVNX0jSWouxTd5Ko7Ni5v0tnoSqhmM1opKSVEZGSmLrmqceuIKXg1Tv7RFJycX2E0JKDWcnNu8dsreQM+w1/8O0XSGLFzC24NtpCLySJTnEytOiXrlymMx3CeeaTGVVVIWTopLa3z5HEEQ/u3yPLMoSHqqqJtA6dDoBxdG2LEBIZKYxzoCTamgapQEjNYjqj1+8DoKSgrmuSJCGOYsqiJMtzFpOSXm+IjiJM1yGUBCTWOqxzJEnMbDFHScl4OObm7vt88+Xv8sO332BRO4ajNXRWILHUCowSXBmP2eqlyCiijSQ+SimnM+4fTdFSUjUNnW05uzbANjWL+Yzf+/2X6PckzhpQGWvDjCyJiCLN7u4pXaRxWcqLb9+hEpLLj1zC1TVXtzdxpsPKY2ato/ESbwx5L0cLwR9+7QdcvLDG8zeusy23Gak+/Y0ei9NTXFnjhQQH47VV4jQhz3K6zrO3+4B0WV6cEOFg6qGtalzX4k1GnKSoNEULQyUqjHXoWGm6riPSCuNaitmcJMmwWJI0oalb6rol7/dIkgFZptBJgvEOtEIC3jskAqUUxjjuH+2xc3iX8XDEv/vm15i0hqPScXh0it874fyZNe6UUwa9nIvXr3J3Z4dBkuDbhghPUddkqaLtWrJIsNFLaBuYTObkccza6gpSwFPP32B1fZ2Xv/MqST/F7c04nc0ZDXOSNCaNBdZLIikxpkNYy+7BMQqoGsvlc9tMJ6e8++AELTUPdveIlaZsOhatYZTc4TOPPcl8WiCmlqsb5xj2h6A1Do9EMJvNODk+JklT8iyj7VraqsG1LZ1oiKMY14J1NdZ6lHc4b7DG4b1HI0BpRVWXDAYDpIxpjUVH4Qg/GA6IkoTOWTyeKEkwzi5XPkRRzPfe/BFRqvjU45/k5Xff5MHsAd98/VXmxZSLZ7d5/tI66XDA4XRB1yn+5b/9Nq2zrCHpZTU6jnnjwUNG/Zi18ZCNfkrX1hzXNdWixbuWyXzGqNdnNN7khbcf8uTVdY53dvBlhReCb/zgPbbOnKGcF8yLCRujIVEW4bxlc3VE1s+YTj2n85LToqHXzymmE45mBSezglQKfuPXf4ksFWyujlgcn/D+nT3+9bf/BDrNJz/yBEdlwfrKOq8e3qJeNFwdbTIvS1ZWxkgpKWYLWmOI4wglJR8gIqbtiLTGWUuqNFJonPZ4D+q3f/5nv9y2HWura8va1ZH2egjlUVpj24Z8NKA3HLCYLIiyLGA0BFBMSkkUxXzj5W8zN0cs2ik/ePl7PP7oeSSC61fO4OsC0bU89sglxsMB84NTPvb0VX7rL3yel196jVlVc2l7zFq/j29appMC7zzWWITwSOHoxTFJlPLqW2/TtDV5nvHCi3d4450HIOHmvV32Hjzk/JkNeqmiN0iIIoGSMcbC/KQglorN1TGb630m84LYNPzHv/kLbKwOWEngk89c5dqFNYrTQ5St2dpYQwDXrm1w594eq5sjjkXBu3fvcDFdJdMxcZrgrKEzhrWNDXSkads2AIqCD4HEru2QSuGswztPZwxSScQ3/6v/0ld1xWhlhWJeBOQwjkIZsh1pmpD3+ywWFaZ2jMYjEAInw5dQNQ1lW7LoKv7Rf/9PmNclkfBMJnO2Ns6GtnN2iHWeLB9greXjT56jrCriSPHO/Rm7xwsiJXAOVAxV3RI7iKOELI3p5RF4wfmtKzx14wm+8/rLfPW73w0neCVJ44if/tSnee6xJ3njndfZOXqA0aCFpCgqyrbGo+mnEXXZkSc9ii5ABE9d3WJra0gcCf79N94gTwOa62lJ81UOj+cc7D/gzJl1jBNkcY8vffqn6EeaVMZIL/DCM9xYJV2eoMv5guPjY5QKi1QrTV3URFKRJAm6lwYkVGt03dQATE8n9PI+jWmxzlFVJQJJ19XUpQWp6A/6mCW8K7zAekev1+MPvvdV9k7vo2JJYhPObAwwFk5mJXEcs7La5/FHL3HwcIF3HV/5/jt01qPjnIeHx5zZGmI9aK0YxDGPX7zMtbNXuHb2OlILbu3cYjhY47PPfZzt1XXObG3xypuvsLU2ACGYTCt+5Qtf5LFLV3jiyhXeu3eP4/mUy+cuYuqWW7s3ubd/m9P5KTqS1GVF27XcfHBEawW8s0NnDc8+ehYZJTz1zGVe/dE77B01LJqO8cqQM9trHByX9PqKB+Ye9Z7n05eepGtrsl6P4mTOwf2H6FizurnB9pkzTCenSCmp64o4jjBti7EK17YkSUzXtWiEINIRi0UAibTWKCkZjFaoq5Y07ZFlKSiJsRaHQHgP1i4JGsfeyRH/+qvfYXt9g1EvAyMZpSn3i5LhSp/YnfDKi+/w/l5DlMS88d4OnXCsjgdsDlOktVw8c4aP3HiSp288w+VzlxlkGd57rHM8++jTCAVNVbN/eMDWyjpf+MSP8eatV0nTnGevP8vqaMz+4QGxVPzEJz+L7xxIgdSaTz3zPLfff5e92RG3d29y88Ed7h4f0+9FvH9vl4PTKVIpvIxpypbDvR3OrPXY3NziwcEpq+MI1wJWsPNwwo9e/wq//OOfQSmHTBK6tsF5xbnLVxmsjrAChLHUdUNTlyRJgvIEgstbvLV0XYeSAvG/ffk/9TrSgMDZjjiKiOKEfGVMkuS4xpINe1jrPyQ9wuYbgYR//fXf53f/7b9lZTSiJwXFogQRsTkesD+dkCQZm6OYqiwwVuBFxHDQBxlQTt8YfvYzP8bHP/Ipzq5u4iW0TR3qv5T4JRwthF+eqAOINxqOefmdt5ACPvLI48wXBVUxoywXjFbWiZYkUbUoqOsCHSmGoyHlvGDvcJfX33+Hr7/8Ep03ZFlGohwn0wXSOaQ2xEmfg6nBm4b18ZCj0xlJLOj3YhaVwkU1v/Hjn+PRwQ1UmrBx/iJJmlHXDc57lJQ0iwWHe7toLWjbhq7tcMaRJilJEuGsRf29X//1Lw9Hq4zHq8xmE0bjIc5DkgZQCSeIs/RDik9KGTD8WPMv/uhf8bXXXiCOYrztuHhmSNUaHh4v0HHEaJCwKFtOFw1VJ9g9POW93UNiLXjsyhZNbfh7v/l3+cmPf5ZUapq2CYcdETD8QH2KQH8umTAhJN45qqpie22D9fGYoiwRMtTaqlhQFguc6SiLBXE/YbQ2xDvHYrqgrWoG+YCnr1zn0rnz/OidV7l8dsib7+/xwtt3qDoPMmK6MDhnWBv3OK1a9g5OWV9LuX5pnZPpnF6vz7ReULeOGxcuMp8VtM2SWvUBzEvShGI2o2tbnLNEcUxnLAqwxtIfDlF/50u/8uWmaZjNZsSRAgF53ieNU4piwXA4AiFh+QDSJKZoGv7Zv/v/cPfwDnmasD5IEDImixRbGyPqruM7r97ieFrT7/U4ni6YFDWtb+lnCaurW0ynM77wyS/wmWefpyqrQBcKUFItsZMPuqw/43TFElMJX8gHPHHYiAGElPSyHh5HMT3Fuw6Bo14U2DY0FMWixtoOKyy9KGYxr3nxrTewJsbZmqLtmNeGrmtJk5TXbz3gvXu7PHltkxuXt4jiFCEU1y9uEuV9Cjth7+SQs6vnGQ6GiCVxhActJaZtkFKiI4WKI2zX4o0njlM6Z9DH+3sIKYmTBCkV1nk6a2hmC0YrY7wOtIl3DqUk02LGd956gbfv3+Yzn3gS6Q2vvXmHeVmz1h/xqY89zfp4wMFpRZZGzOYT4jRiHGk2Bmv8pS9+iWeffJr5ZMZHHnuceVUEzlEKhOc/4Hw/IM/DGxDEAKEECZY9NuCEBC+RS3beS8F4bQspFbv3bmHaFik008kErTVZ3kNpRV1UdG3LTz//ca5unEGlEQ8e7vD111/g3vEJVec5mU84s7HK2kqfpx67zMefe5TX3rrLdHbE2krF8x+/jCXmhR++ysVmj/V0K3DhzoIXWEBGEd1ijorV8kvRGDqiSOO9Rf3OL3zxy2KpPLBOMhiNSdIe47UNoij5MyIcyPOcN269xz//g/+Fz33scTIp+OOv/oA7uzO0gHPbGa4pmc1qHp4sMLZmbTiinwueu3qF/+Sv/C1+7KOfpBenbG9sUNbVsrRJnHdIIfn/kzUE8sKHh+6dA+cAG7he6wmc5Z/9bCCTPc4abNsiROCTB8NhIJqSiLqssN4zXlunqiqGWcogSTi7ucnWyhqmqbB0qCjFmIokSuilMakoibXn7r0Zt3dPuPfe+1y7fp4LNy7w1T/5Ouc2znN2Y5uuM0gEUsmw4TYNQoRrlQ68c3g8UkvUb/7kT3w5ihI8sLK6yXh1Dak1CL9cheJDtUFrWr7+0reYF1NuXL3CH/3Rd7l3ULCy3qcXwTBRfOdHN3n5vUNmdc2ZzTV6seITjzzF3/mrf5sL2+eYFwUsQTtYYibLGv/BPvNBuZOAsxbnHTpOkP2MaJAh8gzRy1B5iogU3gbOGmsCySkgyTNsZ6jLEmsNTVPRNE14GAhWt7ZY3dhmf+8h8/kU2xqKouDSuQs8fvUxZrMJs3KKThIeHs+5/eCYe/ePOLfWo25rvI7ZOZhxtHOfZ597jOlkTpoozm1cCi26cygt6ZoW5zqSOEbrAHI2VUWaZ8S9HB2nGUQxvV4KWlHUC5IkQqgYXIAjrHPkWc43X/4Bf/rD73P10jYP7u9yXJbsnU7oDVIeubFKvWg4c+48xf4xz5xbY9QfYQ385q/+dVZGK5RFgVZBmsESO5IInPv/1fN4Z8OKjzT9jTXi1RU6BUXXkPf79PIe4DE+cNnWQj2f0ZxOaI5OcFUNDlbPbONMkM1EaUxb18xnM0ZrawxX1ljMZ3gT1B3OeOIsxXvHKE344qd/nP1v1KRZwqULHbfu73BmPGRvf872ap/dd0+5fzRjtRfz4L07rPUzvv3D73F+5QpPXXmSRblASoVfLiypJBaHjmNUGtN5h6s7tNQxOkroqhojW5wQrKyMg4yi7fBAEqWUVckP330doROq+YJ4pY/BQ6SZNy3fenWXVDhUrPnM05fYWu3x2vsT8rQPzuC6Dh1pnLHgQao/21y1Vtjlv4NHJxlykLOytUljHL//9a+xu7+Lt5Zf/YVfJF4x3N29R20aNjfOsH3xEnpjleHWJl3TUDw8YnF/h7auWTmzjbMOHUf0RqCyXhC+KEExnRJLBUAtLL1BH9u1S9Jc0RpBMz3luUfPMYotL7z+Pm3tsLcFejRCRjFF04L1HO0fM5sXvH77NR65cJ0kifGAsy6cr5IEYQx+yYF7D8I71O/8/C9+uZjPibSk38/J+z3apg0YzFKgMG1KvvL69/nWqy+TCsl8esrzzzzCZF5xMGvoBPRizbRq2D8tqBYNN3cneGn5+FOP8vHHPx5KDYBj2cu7D1tNKZf6CCnob6wz2Njk7tFDdg4P+O/+x3/Oy++8hZaKtqq4v/OAxy6dYzGZoJD04wgpFM56rAl6nHw0JtvcoGtbukWJUgrrA5gopSRSipOdPabHR3hrqOqarN9HRwotFLNFwWAw4uH8kJu793nz5h4PHhxz96QgSmLyfo+jqkJJySNnxzxy9Qwvv3EH4h4PpqekY8XGYJ1EJ7RVRaRFEGQ5B85jrSdLw9um264kTkKb2bYGYQqUknSVwTpLL8144+57fOf9V1mYhtN5TS48d+7u8hOffYb7R/NwUQriRHDt7IitlTPcPtgjT+DGhcfI04yqqZblJSjxlFLoSC57ZofSmt7GJjd37pMfHZNIwVe+8U02V8d87tOfpGs6Xnn1VQZJSi4kG3FKojWpcbQnx8jBGKs1KsuxokMqyeoTjzAbPaS8fR+NQKqgzzk5OGBxekLbVgz6A6xSRFGEsJ6qrYiziKyX8Mz1x3j55tsYabhy6SLjtVPKuiKLFUk2om1bfu4Ln+DWzdsURct02tE7M+Dlh++wmq/x7Nkn6LqG3ijFAc5YqvmCNEtoupYkTdAOwmshoHMQRwqtZRANaUnTdtzcvcPB4Qm28RyeztgaDnj1nVs8/tR1/uLPfIL//l/9KdnWKo9cPcNID/gHv/n3ubf3gEgJHr3+GHVXB6EaQRKoIo0S8sPNXamIeLxKsah55/U3uXrp0pILiNhcX2d+cMgbb73NymjATz37HC98/4fc37/Pue2zPPfoYyjZw3QlyiZ0nYEo7B+yqegPesgzG5S7D8HaZbsNUaKJoj7D0YjMOqqiDChlB71ej7Wz5/nC1etsrZ+lq2uuXrrGP/pn/w2lmXJ0WnK4d8pf+bUfZzDMePvduzSknM6mpCuah3uH3B/d5dHVSzgBOs3o2gaPAyTGNPQHA+bFAr2+cYamrcPpNkoRErSWqCgKTFhb8fq777M4qTCNIY1z9o8nMEh47dV3+dynn2HYz5guLI9dG/KxG59ga3WN9eEQj6eqqg+7nUDahP7XGYvwEh3FxP0Vjg9OoW159OI1/s23/oTZ7JR+LwdrsMZwbn2Djz76GGtS8+L+IbaosGWDLRqUF5i2RsQJ1A4nJUqDVRJnPYPeEL+2wuJ0QiwUIMjyLDBW1mDMsgVWkquPP0Hay3He0ZUVzz/+JGHLkHz2o5/k5fd+yK37p8RZxLiX8NqPXmE2b9mZzEl6GfPjBucmvGTe4dNXniOKFKZqQHqkVuB9OAMYS+QFGq3pJeOwU7ugZhZKgwOtIt6+/SplU5ELzeWrm5i25eAoRiuYzBa89ca7ZDpisajZ2d3lqYsVnekoFwXeW7RWyOVNe+8xIqjIOufAW1Q85PS44HjvIVkc83vf+Aq7s2P+7l/8Dd699Q4n0ylFWXFSHfF7u/e5d/Ean3vqKabTCSv9HqptscKjW4mUDV4FqSUO0DpwGk1LmsV0aYrvDGmWUc0A0SKFwFkHOExnmJ8e4hmhkwytIpqyxglPkmYU5YL9vYdYY0mjiJtvvU9dd2xvDllbh82tbdI04q3bh8yKirtH93ju2vMs5hNsszwbeEfnHNZ16ChBCucwXYuxHdYHlgbr0EtR7jdf/BGZVvy5Tz7GxXNraN3yyz/3cT71sSc5Opzy2pv30Aoeu9DD1XDlzCW8dQgBSutQ+1hK8ZZQhzceJRVpPqRYVMz2H3JycEC5WLB3uIeWgt//2lf44auv8HD3Iev9EZ967GkioXjr3m0m8xnloiBSKsAR3uK9xdIguwrVVFhvcbYFU+O6CruoGKY5Mg6qj/n0NMgw6wrhLba15L0B85MZJw+PaasG+QEe5QXSC7ZGW0xPF1w600dguXt3n729GZ/53Ef587/wKXqq48a1bX7pix8hF463b9/CKk/e75H3e4Cg7QxKanqDASLRqL/3q7/2ZQJ/jFQKoeRS/ZDw8s13+P7rL3Dp8hajQcr+wwNGgx7V7JSqarm7c8qd/RlNW/P4xWv81pd+m2ceeZyyCIoBpdXSLxBwGpTAtEGeHRBCxfH+Ib6q8N5yeHLC9sqYo+Mj7uztkicZP/nRT9FXmqoseePOTdaGY569fAXrHf1hH5XGeGvwpkVaR2RB2KX0MWw8qDTHZzkYhxIK4yyRAmM8TdmSpClta8nyPhevX2O0tUmcxEHDv2xTTddx8eIFzp+5zL0H97l1/wH7Jy1aCZRzmLphME54+HDCmfNnWVvJ2Xmww8rqNmdWN2jrhqosEV6Q9fp475ieHqO9lLilpFsIGcSsSpKkKa+8/Tr7x6c898w1/uRr32Fl3OfubMH9+yc4IakQxNrzyz/2c/wnf/lvgHC0bdDU+A8k4XEC3mK6DmmD7E8KD16zKC1N26Cs4ebdO4Dl4vlzvC4Fn3vqWU6mE/74W19HCM/R/JTt9XU+/fjTYCWjNKecl+iiRjgD2mMjjcGhk5hYapzzQfgbgcokztaoDvr9EXmag3Ps3nvA5pltwDObz6isIXIB9hACUEuIY1kRPvHMs3zyIx/lv/h//WP+hz/8A6Z1zc7eKc5bnnriDGkU8/Kbd/id3/5l3njjfV5541WeuXCVrm3AGuI0Jh302L9/nzzN0EJJpPd463AYpFQkccze0SHv3r/D2kbOvXt3ub0zYedgQX/YY7ewnN8esxpLnr20zpe+8HMIb6iqlijSOCVR3iNFFLAbHzT/AoEUCo+jQ1GUU7qmwrSGs2sbpGnMnb37HB0dcml9k6IsuH72AmdWV5lUc26cvchKkjGfT0kiQeahrOZ4rYnzHJUoIqVACFQUQawgkggkwji0VJi2QHtNLSVKS85dvoT14WGv5VvY5YP3IijfxAc+A7/UvbYtaZLwi5//Cd598BZvPDhFItg/PObeQUVRT2jrjnffucnGuM+9nbsczk8RncE7qIoCjyDLUk5PTtB4j450IMGdxTmLUgkPD/d5f/c+Vy6sAp5OKsqq42NPrXL1whbTGubTQ5658Thn1jYxXUesY9Dhol3ngsFDCnAykBRCBWm60jzcP2VyfIT2Fm8sqY546e3XeeraFT7+6JMcT2aYuqWcTulvbKL7I2gNtSsYZJqT431e+9ELJFieePIphpubxGTorI8TBAtUEkGcIuMUUo2IJcJqotbTGHAmGDXEkq0CcN59aMMKNq1wZkGEPj6Alo6zm+f52OMfYef4m2ysbfH8k1dIRcPvf/8dMIqDgwmJhAf7Mw6PJ1zqj3Ha4p2jXsyxzjEYDdEfoI3WWTQCoRRKKHYnB0gBJ0dTLmyukiWKfi/jvbt7XLpylel8xsZowOPXn6CxJXXVoaQiI0cpjZMqrCwnEM7jhcCK4IuSSmHbBbaaoeI+Ho8zFU9euMidnR3e2nnAlQvn2BoMuLZ9nr2jI86dPUckBEkk+OGLL/Pf/fs/ZK9oeOzKIzxs3uRnPtNDpgNqAb0sQ+YZqtdH5Sk+zvBa4OoG0xpEUROlfYxY6kY0YEEo0OgP0VeJQEaSrm6YT07x1gRfXBwTy5inrz7BN17+EafTimEecfPBDturGyzmBVmcc/f+DlGkOD7e4dJwNehsW8tiMQulWkUBWDc2fDOttVhvqduWH775KvPGMa86ZvOK5x+9zN3dIw5nlh+9foumLbiwuc75MxdQWpH3E/J+QlFOmS+mWNsQRxHSiWU3IT5kuADWRwNSKfC2DZCyhX6U8Nq9O7y1t4PvDDfWtsE6LmxuoY1FeceLL7/EP/mDP+LA9knyIXptzDd2Dnjj/i5xnhH3e8TDMXo0RPR7+KyH7yXIfo7Mg2ArFh7ZOZwUuLBLhA5NSvyS9TNdUILPjifs7+zQtRUCSVPWzI9PWMxP2ByOuby5BaLmjXfucjD33L6/z2PXtmi6hlnRcLxoeXv3DvOqpG07vLNIJ0ijFGstWmqJFnqJUVi0UhzMTtmbnHA8L+mtDXnn/V2++DOf4fBkzmS+IM5i8ljzxLVH6MU55XwBQpAkKWkayPS2q4miZKkKdh+SLMESZcl7/UBmC4NAgnU44bl27iLv7T9kPpsiti4ghUDajlgKbt+/z15Z4SPB4vQheMP3v7VLiuTwIzdIRxkqBq1NWNJ0OK8AjV+u3LprkaZCRDFCZktsyqFIl0xWuMZIR9jI0piGrTPngr9NgK0b6qLEas/l8xd45uYT3D3co5cOKWvLxtYKn/7oY3z7my9QNpI701OuHB9Ty44kToLxw1j8EqCU+GW9W3rDkiiiKBdMZjN6g5RZ1XE8rfnW91/hF3/us6yPxyzmFSvDjKtnzy97fo0SmqZuKcsSHYfavyhn1KZGRmFjlF4gXKizIotgNCJe2WKQSRQdnbF85NwFfvLJp7lx9hyurZDVnL5qePflH5JurnPu2mVOHt4jSSXjfs7m6irDNOaxS2eJ2xKqGa6YYYsJvirBttA20Jmw2l0XzHhFhbIeHAgXcHrvHFiP9UHn2st6rKytBirROhACnWcMttbpD8ckScaj156il2QcTWb0kphf+MnneeXlt9g9LDgua4bDPpODo2BpKmvKqsEpH94458OW6VyASJ33SKE4npyytr7C9uXzvPXuXcy84Z2b+0T6+/zyF5/nf/y97xGriEHcZzY5BSFIe/lSyJXhnSeKEoz11G0VCJI4AR8gCbE8fV59/Cov37rP3luv85HzlwgH0pbrqyuMx0NU02InE05nFQLLmeuPca6r+NzVq7y+aDBS0lUlf+mnP89Hr1+inhwTZRrjOyLXx4XuEeEBFeGMo5gskN4gCZ4soQRCqA8pUKlDw+BFcEz6JVnknaWYzMIeqTSL6YSFjhj2eiRaMxoO+YWf+ijf/vaPeO29Q6J+j+HKgCcevYS0LUZYojQiI6EuPXVZEOcJWuCXVjlBHMU8nEz45t03OHaGZDLj8oV1dnaPOFyUvPjqbdJY8XOff55b79wP/bSOKBYFXVkG10xsWcwbvFf0e0PiLAYR3rIPxJICD1rTFiXjPOe//uY3WP3iz3P9zEWKsmBt1Ed6i44j8rPbZImlTgTtbMbFp57gV371Swy/8lVmwM987OP82BPXqE8OiSOJtxHWtAH6kDqo0ySoNKKZFLjWBj9cU6GGa1jhYAlTO78UnbllB0eANYwxVFWJVJ6mKoP9aNhjcTIlMo7xeIsnn+nx3e+8zMvvHDA3jjO9iPNnV6jLBQtX885in5HPyWSE6QxCqkDQoEJ9xksirWm6mp39HR7sHPD4mW0SZ8nSmEoaxnHO91+8TZIlPHb1EYSDzlqiLKNzHQBN0zAcjlgUJYtysvRgSZI4J44DBh7HGuUd1f1DaCq+8PnP8+7DPZ648Sh1U4KK0BJiKYhjTZRY1vsJBzffRuUJ29ce5S+vrtJX0HeW4ugQpSymlTiboPAQtYikQKQamgRZ1CwePMCVc9T6WsDnl2WR5YqXS6VFIIk0xbygKOdIIcnzHl1b4pwh0lBMZ5i2Y/3CWa5ePc8rr73AS28/hDRFmIYsT0i8x7Qd9w6mXFg7posu0ktjAJIkRUcaLUWQWispMa5he2OdzbjPjUvbvH/3GN9UWDwXz2/TzSt2FxVv37zPxbUtyqIiSQJxn/Z6tF1D13XMT+aoOCLJ0mB9xVMVFWVR0uv1wtF/URLXp8iy5NlL13nv7i1a74mzDKQnwpAmkiSO0DrhzONPMjARnTBEXUsURdjFlFlVEXmLMwarJF5KSCKwHViPazvixFA8PKC4t09vdUhZlUSijxQgZNCkfui2dx4pFWVR0jQNg0GKc0EsVtVVOOPUlrqs2bp8kdJbTg/u8/7th0yMZTOK2T6/zenkmNemC2wc8dTjl2DSkl9IKIoFMo5I8z7bZ7bR1aKmP+jTmQ6kR2mJaTvuv7fD9RsXWFQtvmu5fmYEVvDVuqUoGpqmJe0NybKMNM3RicZ5gzEtQiREcRQYKARSS3p5P5DVxiyN1I62LRinCVqkfOJjnyNyJV5bhDDEWLqyIe9tIMer0O8xkDHdvKJta0zTIJcbIy50L04HCYsTwdHjnEcbRzedM71zSCIVxloSpYKHWwVzhpQK713YhAUoFRTOaRrTdRVNHVploQSx1BSzmpWtDVrX0HaeclFQtw1Zv8+Pf/o6zhreT0CnMcNc89ZL7/LJG5eIYoEgwUrJyuo6Ud5Ht3VNHWlUHBEJTdtaklQxXxTs3tpjMOqhlOLO+zusbYzQSrOxkrK1tsX2+fOYtgkaeB+8YDrNEUjs8kQplkyQcy4c7b3D2w6hl8EetmU07JFsrNLVAtsdkyQalEa4FDEaY/Ic6RS2qnHGgJIQRzivEfKD4IsIYoFNNF6Hz0+NwUxnHD04ImogWl0NEpimJRrEGOuX12cQTuBl2IRnkwkIi0403dyRJjFt26E6x/HBPtsXzhHlGW3T0k9jNtbOsL1yl8WRYX58ytHphNppKEpO7lYURUOWRHS2Q8YRsUoYrq7QOYeOdETXtqR58DYZU1PVBTeunGX/uOJ05wjhBWmkMK7k/GqfWFg+8vjHkctOQUkZnB5C4mxoM5Vebrg+/L8XLhiaVUAj4zhmsLLG7HgPQUPXTEgGA0x0FtHVxCqilpJpa0lpEKoLABkhGoA8Bm3AgPc6CLS0xiuP0opEJix2DykPJ0gVw2iM855EOFxXQZou8yQk3pulkFfR1jVgGAx61HW5jDQIG/VsOmG4skIy6NMYg9AajeLKucf5wUs/4snzGfuHc+6flBjjGA9zCuu4enmbzrZEcYzpHOPNVYTWiLZFO2cZjsZBeyOD2VgKj/SSUU/z7KUrHMxK7h0cMi9Kqqrk6pl1NtfW6LolTgLLzUqDk4DBS8lgOAwC22U8QTjy+JAs4jyDC1dozYKOBkVD14oQenF/yvTeCclHbuBwFIsTskGKVyCcXR6WQqnwyuGExEuFVjGx1pii4t5LL+EnM9YvnEcoTYdACIdqauJ0FS8jEDa4VJbZGF6GEhlFwdJkOoMxHd55TNcRZQnpsE9ZVEFI4CSnswlba2tYNHf3pvR6KUJHPHZxk6ub63zzjZvEiUYkinw0oilbBqtjnA24kh6ujcBLbGuQWuItWONABPVWVXc0pWHUTxn1Uuom5sal9WXMC5RlhbOWKFPoSCC0QgpNWxtmszmRJnRYeQ/vwYsl4OckIo5Zu/Iok+OHGNcAEilTulrgtUJmgkgmYBOMq8BZvANpDMpanAqaH0WC6QzFwYTj2/c5vXOXcSS49tg1nDW0TYsag0wstnGY8RAtQXiBX8YmCO8RTpCkKfPF6YeaVIRGesvidM7GhbNEcUZZlEgcpgzXhPA8cf0Mc2/I0gTtFfOy5eHxFKUVGANCons5aW8FFafYLmBn2nuH96CXyKWKFHXbMStKnIETSjphcR56wzFuekJjQr5EVZR4b0mzGKmDpqergxwwTTOscnhvMa2hKUqyLAt6yQ8Cl7xFJjnrZ69SVlOKqqRrPd2oT7Tap2wrfOfQXhFpCd7ibYcxHa7uqMs5xWzO7OCU6cND3Kxi2Fm2V3qcv3oRqzVVWROv9FGxwNctMhoTD4YYZ7HOhD1hydp52yGXllKVKjpDIPmNIR/0SbIU6z35cIgpF5Rdy/r6GtOuoqlrUp0wGq5ysn+Mcy3HwmFdS7HwNHVHnGb08xHOhJwJ0xl02VTgBd5CHCcoFaSBi6rh6qVzvHPzPnkv44kbF1gZ92lWU6rJjPlixnhzBUeHw4AX1IsK4QRRoqlsgVSaKFLkg5z5vA6ZPzoC6xEyHMmsD3LCvD8mTUe0tqUbrtDULV1VI1OYn5zw/jvvslicUBcFVVnRnC6gWBBZT+JhGCvWs5zxIGLj3Dr0c6wDrTRSSBIVYWpJtL2NFYBSpGmC0jEiTRBRBFkE3hNP12lnp9R7e2jXYQT0VsfhINl2dG3N4nSKt466aljMF+xPKy5dPMfqSs7Kep8XX7vNg+MJT1y7yP2d3ZBvpCKcDQKA2WyGdQ4tnEZKh9AWpKFtO4bjdZ4YDlFa8fiTN6jLAlzLu+/d4ezmClLHTGYlZ1cqfGuwrSQaxnjvyfI0PDzjQHbEqSLLJUJ5ikVBfzBACrl00IcYMYkI3Y0UJCoi9YJhmhJvbnF0dMKPvvsCd+/eCclerSF1jp6Myfpj+lKRIMiEo5coRptDxEpG6w2+ChE4rp7TzXuIwTlUf4xAYqYLynIBPgBvcZaih33UyoDRxir+8gWy7XPs/eglutmEbDRkNj1FEw5reE+Up+gkYrIo6YSiqqe8e/eEjY0xZ89ukPYy+v2Y8eaA9TioMKQUTE+nqChYvrRp6xC5oiGRkkU5p+hakJZMK3Zmp2yMV3jw4JAoT3nrrXt84XPPMG/nIe8ASdvUeBGsmIvFAikk49VVOtPSdC1dFzayNIup6gIhFAi7jAVyRCr9ULjqfNCGxjri3t4er7zyOjaSXLrxOKasEKcLVFcRG4tqOyLpiK0lSxWDrSGDjQGuM7jGgtBhL/PQdoLVc9sBx3nvLse338YL0CiU0OhhD51l9EcD0s1Voq11hpcvkP3059l/6XVMMQ8pKF2HM3yYn4fyTMoFW+s9XnzjHuPtLd64+ZAz5zbYOznikl5DKU9BwrQqWFVx8DH0exwdHYSoAhVrHA4pFNILqvmc/cWC1UHO6fE89PVOcHjvgOPTBdPphNPZDsY9Qiw6ohhM2+JaH9pME15TpSKkF5i6QcqIKJZY39E5S6oUTRPMasYYvBckaYIxHTLpcVC33DqYk4/PkvYrzPEE5UAlIcci1Z5IRhBZMg2DzSHp9ghTl1BYfAsCh5COVmaMLl0lHgw5fOUdjl78IdFajzZSuEVNFKeoaY2eKtyiYDaZMJwVZEVJcuksZz/+ER688CJqbojznHJWBSVh0+LmLSfzXZy1vH//hJW54dy5DQ4eHnP3ZELWCrxynFkH3xnaqiTvZcxnE5QAbZ3D2gDEVW3J1toaV89c5J0fvcB80bJ9Zh1rOrJRymJeEOeKLI548ZVX2EjP8Klnn2dRFgHlXHpikzimmC9QUiG0Joo1WskQ5GSD92tuLL3eiNlJSaoFLWDriHRtHTNa4+jWPfLVVUTT0c2ndLMS6QuUhTTPwRtyESGloXduiM8EeIOKIqxukMagjaKpNcNnH2V0/gKz9+/z4LvfI+l7KgVd1eDbmrYtgyBYKhrvST10dx+wUlTQNMSPXmTriRvce+FVhDdILRBWMspGfOe1V3np7be5emWdtZUeURSRpBInJE9fPc+99/eQieUjl64yzHIcFu86sAZTN+h+NqBzlrpq6A9y6qajqEriJMI7zWQyp5xVeNOBhc9/9DFwcG//iHk7x3uBMTY4wz0IB60zeKHQSYYxbUjesgbhJb08Q0gRlNfOEkearmtII01dt/jhBtPK4IVGRxG2rBGzksQHtirRil6siIQkX+kRryTEGxlN12DqjnZW4lqLrixF7Rh98jP0rz+K6TwHb72LNyW+Pw4AXFHjhUePBtjOU89nxGWL9cEBKq2DRCN6mvTqRVbObTF5/y4iBC0ilGZSHrJ3cMCNq2f40s98lD/42mvcvfsQlWjObG9gE83a1phIBxS4axqaeYHQijjPkNPTGUJJ8jynaxy9fMDHnnyalUSxtprTdB6EYqWX8gt/7nGubeR884V3KNuOxrcQxQzGI5I0C4o6qYjSlEF/gKkN3n6QdhghtaaqWuazEtsa6qLA1BWmMyzKmtWPfgK1thZUA3mPvNcjV5oBAt05+nlGb5SS5op8oEnWekSbKzTOBtWl1ggVI9HMGk36iU8xfv5prHN0TcvpyREijUEpOmMQWcr6M8/Rv3iD8eNPsf7cczRY6ukRnakpygXznQOqW3uY/UNWtzdQaYq3Di8lnbU412Hx/MFXXqKfaX771z7Pkxc3USj2Dhdc2t6k30mun7lOnmcIBzqJUUmMUBKZ9/vYzmHbDqklddfwsSeeJnYRd97fxS1qtvsxf+5jj1MUBf/2G68xqwXnt9bI4h5pnqK0JhnkDNZWyMYhlbDtGnQsSHUCnaRZtCzmNd4JBBpnBErExFGOVprhjUcodMbh/Yc09QJLA1WDb1qs6ehpGGSS/jhBDzPcQCGHEWiJSnKEjGkXNfZ0yqJ0rHzmk2w8+wxd0SCExdsG2Xp858nijCROGG2eo2k67rzyGndfuUU5tVz8yPMYL/GLBb5e0BYLyqMp7f4xwtbkK0MkikSFUpzFPba311hUln/5r77H3u4ev/QzH+WpC+uIpuHdt9+nKzqeuPIIp9MpMtaIWKMSTRzFSKQI6i4tiKIYvGcYZzz7yCPkWcrKKOPRq9uUiwXffeku+5OOtXHG9HTB6emMSGmc9yy7SupFsXS4CLwPiVJVVaJRZFmG0hIhHVEWIXToKuLhKma4xcn+Ec4Hdk65gHLmWUqcpqS9HCUliZZkmSYXEj0v0cqDNfjaIOYGs5BsffpzrD31OF1TIYSkbTqaOuQ3SC8Q1gUGD4fygnPXrpP1IqZ3HyDjlGxzi7b1OCFovacqF9T7B7hiRn+YolSEEwIdaRZFSzNvWFvNeDir+MOvvca9W3f55NMXefLiNlmW8bFnHmdzdS0kp/jAwWsVUTc1MpgWQMcaaw1lUWE6x5m1bTKtiKRn0It5784u945K1tdGdE3Dg/1TDo93OD48BiKUihFOBQmK98Ft2Tl0EniBEGscqD2tJY0Jcu0ojogvXEDFKb1eRpRoVBSR5AOUkvimIU4iRJ6RrITDUOo1MRneSGIvibMEZcEwYOsnf4rVp5/COU+UpMhI05UtcZwRr4woZiX1pCLR6dIGqxEqweGJtAZn6a+t0lYdprE432LamqZtsVWN1oTfqxRV13FaHvLuvSNq49jcXOP+tOWrP3yPzkMSWc6MB5xd2UaqFOMEaZzhrcB0dpkjIRVdF8StzjZLckJx4+I1lDXoJKNtLdOyQ6cZlWnp9VI2N1bY2X/I8eSIdbWN8J6urcE5rAleXh2poMXXwd1i0EujtQx9tLHo0RiTDSgXE0xnQxsql5mhJhzUkjylO51ifMfa9ga+avCnE0SkQyxYnOB9TH52iyjpMXnvAdGSZI8GPeLOQ1Ozvr1Nu3eAbRWLowJyRdM0NLUnFmCFwSUSO3dIHWOcQzYVWTTAe0HTNCgVITQkxOwd7XNvb4fzF88xm1d0jcFHmmnjOTpdoBONaisev/YoURyj25DDIbXGWkuiM7RKZFAqWI92UQhQ1ZrtrS1GwzFtV6KURirNmfUej185i20bhFQsGsMb77/Bz5y9RDmboZMYnWTQOaLlwUqrENinsxysxTuPWBoVhPT4fp9iMaepKoIoYcnRlh1RFhOZHPPglCjWiC4EyspEIZOAJ/k4RSYZxpxQHd/l9L23iaMI35ig9u7H9HojdJ4SZzE3PvMUvhN0xjMt5pwcHaKLDlcbhk9dQfV72Pv7KOdRnUMmS5IeD9aH6xeSOIp55e03OCkarp5ZpRi1tN4T3T8kFlDVlsnpgiTusba2TtM0y8xqsfQ7hzxrDQ4hHDqK8DYk1joF48GAzdURr9zcw1rP1tqYo1s7rCQdhXMUrWWxaPnR2y/zYx//c2T9PrYzWA9JGn3I8dkuoKpegIrEUg4osM6gk5hWSOaTGbYzgbyPoyDoioHC0BY1Xnp829FzDpxG9xJsGoe4MimQIma6s8N8MSWXCfWiQegEaxViomiyCfHqmHhtlSTV5CImz3PGZzdZP3eGh2/fgY11+ufW6E4XFPvHwR3atmgjUCLAQN42dCYiTVNmxYIX3nmZ2axmJdNksWAlVrwxm3L5xiXqxnB0fMSTjz1FbzBCGY+1f+b8F4BtOzReB+eKkHjlaV2AaHUSs9Jfg07w/r0drpzdZtxPOD6Z8PvffJetrTF5pJCi5s3bb/Gp5z7HfDohTuIPk8uRAi8dWkpcZ7GI4MWSEu0lMkqZS4WPPDrWAY0lmCq0iJg93EdXJUmaoeeOalaikuDgEVGExELdIUcJlZP4RlJg6ZxAVB1LERJCaoZ1TGJ7KJ2CslSuwztDsrHGlc0NGqGoZwsevP0+7WJGnkTEKtCXAo/UywdnHGlvhe+9+kPevHWLncOSWzt7PDye8hs//SyffeYavWHO/v4+3gjyaLgkpQJRJIWkM12I9ZcSraIoXOTSJB1HEcYahIX/6PNf4Aev/4h7eyeY1vDMUzcY9DKefLRE6YheIsiyjK9/7zs89chHGKyuUFUV2otltoNAx0nYeJPgTsR5kAphwMcprbFLXRIIKxAKhAs3m6UZPm/oJqfYRYuOUqppRVfW5OMU5YGqQZuGjSef5u2v/hCNR+sMhMcqMAqSOGT2q9YTxxlJX2GrEik1SiU4rUgc1LOC5mhCLAVSh2gGBYhU4hKBwTLMepzOa/7421+j1+txI804mbWsrww4t71GmmS8+OrbHB1PydOcX/7ZL9KLUypTIqQM0f9KYZbLVEdK40UgpEMSigwJ5x4unjnHMzee4fuv/YAHR1P86+9xfmOFn/7kdeJen1hHJFHC/Z19fu+r/5rPf/Zn2drYwDUdUoYIHCkDSQMevAq+VwGxVrQ6xjUO5QMI50QIsZPCUs0WSBEhoh7Sn1CbFmWCzyDPoaOFRCJ0hJ9MuP7IWeLhT3Pr1Zt0+ye0ZRPKmoVWwCI1bI1HrFzYxkWEaJ68B3GCw2HrmvWrm1y8t83x3XtEMUgZIWOF6kVoDcoL3tt5yFe++TW87PiFn/gkrqqYVC2mrShOj3n33bscnxQINB956lnObp/FmA588ESr5RyDDxTX4t6//2PvPUuf1If5GMGYpjW1t/yn/+X/jZfeeQ8lBIMsYmOUk6UJKxtjrl45y6XL53jp5fcYZRv8/d/5e8zni2XIhggQhQp2VEkQP6EEou6YVZajOA7TJZzDG4tY+nmFiPCVpTs+Rp9MyaxA1nPsYkoiOuLYEKUSAWSDnHR7lWzzHHXcZzGracuOajIPISNZTL69wfr2VlBCxIFGVYBXemnLKrEnJzS37nH/hRdxTY1MY+Jxj+T8OtEgZu3Cdf7x//Q/s7PzLp//c59if++IN197i2JWMCk7FosptREsGsvzTz3Of/5//L+AE3RNizDBtoYEhCWKE6wBLVRwrYglT4sQS3t9MFWuDsb82n/0a7zz4B9Rt47jec28CjhOdu+It9+6y2c+/Tif/dwn+Ooff5tX3niZpx57lqapP9ScfjjVwvuglo41J7cOME7Aeh+zWCypwbAJR3GYJ+C9xUQJNslpixqMwZuljNJaOmfIM003m9HN5rSHU+T6KoONTfTWJqp3DZX3sNKFQ59zeB8md+A8TkiEEvj5HHN8THd6gs4iBmc2OLl/F5krZD/BSk+SZXz/zbe5f/cWP/aZ53jthdf4/vdf5qQ0VG1LEiXULrztWZLwF3/xS/TyjMW8RkcpRIHHRoZN3TtPlCi0TGKc98ssf/nhmA4ply6RSPLE44+xtrnO23ceMF4dMx6knJ5MuXswYVAn+O+9webWKvkw41/+u3/Lxz7+Gax3KBFmzISorqDdkVrhqobJ7V3Uyhi9tYqQUaj9Sy+Zcy6MUnEQ5xlWQm0XKB3qvqsLnInxrcU4guIg0lhzgjw5pX3wEL26SbSyAmtrxKvrS63QMn3FtVC3NPMCZTra6SnUHd62eAXp6ipycUq6OiBeWcXGsH7uOv/w//1f4aVjOp3xwsvv8P5py2HRcPbsOoONIbK2nC4WXL10hsduXMOgkEkcdEeAsIGbEHKZACYVWiUpwno6J3A+nM4iqVDLTlIi2Fjf4M997NOU9puUrWd9Y41IKxa1Yzabc7iQvP/uLusbI9648z4/fOkHPHb9CY6P9xFSsrGyHgxqBCCqPSlYnM7RBtKrZ8LK8B68C7JtIZDK4DtouxJha2QaYRuLiT2ijbE48BHCWnzd4hDotiNOMpqmwVUP6I72cQdDeiujgNgOU2Qch8NQ1eDaDl82iCTCmg7VWqzQ6NUVhhfPsj+bIKqKc2ev8ert+9zevc+F7T7v3brH7smMg6omGyZcOrvKWn/A7cmUi4N1Pv7Ec6yub3+4ygUhhbipm7DIFURKITWo//1//He+XLSWedkQgE8VNuE4Jo6TME8mzvjEc5/gBy98j7sPd5FdS1E3jFbGzE6nZLHm7OqQvYdHvLtzyK1bb3D2zDnu3nufyeKYR64+Sd0EEkNpTb2oWBxN6BYF0fYqnfA4Cx4J3iG8AdvSLBMdXWuxLkQke+Ehi/GxplsKwnxnwspSUUihkiFohH5Kgqc5PSYf9ejKGTJR6CilOz3BVSXKOtrZgnBsDPctVoaIVPDye+8yF4oKxT/53f+Wm3f3UAJ6WrF7MKHqLDeuXmQ2nYP3zLqaZ85d4v/0N/7BcvKTDeMA6prFdEZT11jT4bzD0QEdelbXTBYVdd0SRwFk8kujm9YKHcd478gizeef/yi3HtyiQ6CjiHoxZ7XXY5gH79fu8ZTjouH93SPeef8NIp3TMwVREpHleWjrpKLqNdhBQjOZoveO8NsDnPEfJnNZ12Jsi1JJ+C9TGGHxSRzI/K7GR8sQwVZiRRiIg/TQlljhiPMezf4M00uI+j2KnROyfkzlp+h2glgauE3ZEvf7GKEQ/QE6S9GDnKYFswxcff/WLW7e3WM+X/Bwz3FulHN+a0ThPOVkzur6mNJatrKUn3j+42R5wmJRghRYa7GmI000eR4hhMciEMqilUT9zd/67S93dY0zLeqDPDYV6pSUAuk80nWUxZxHr1wnI+Jb3/ouB8dz5rOC1WGPM+sDpFS88f4+45URR6cTVnqSO3d2qOqKu/fv00tTIjxNuaATkmnR4oWnrixEnrpc0DQ1bVPhrEdJHZoDZ0OH5B1C+mXso8MJh9eKKO2hsihoPb1HxMtDmbAoK6ibBjXsA4JyVgQFRNlRVy3RIKdD0gpJvjFEZDH5xgYnbc23XnqFd269z0uvv82invLimzcZDPohmm0lZXOUM68sD4+Ca6jvHX/r1/86v/jFn6PtWoDgIZhO6KoC4zpM1wShl7V465FCoKW1CNPimwq8RWiHrw11U9EJaKX8MM7edoZf/OkvcvfeHf75H/w7RmsraCWIE82sLJi3Bjcv+PhTj3Dn7j7PPHmDg9Njbt/7I7Ik4ed/7AuU1QIhPD7LaTctZloFFbMIp0WpJF6E+TVKiLAfLPWlSmk8BuIIKVJsU9B5i5cCr1IECTKRJIsGU5fIzTW09dTzCWo8RiUp9awgWxkjI03ZVuQXNvECbCxJsx7xSo/Xvv0av/8Hv0fSH3Htxlm+/d3XeOrGNV6//YC6g1nRstrvkUnBmbObHE+P+dLP/iy/9ku/xGxeLEegNJi6oKkKvPSoWtK2Nc5ZtI5Jswzfy9GiXKBNQ6Yhz2OUFpiuxpgWR3i9u0ijlKLrDE3b8pf//K9y895N3rjzgI6AbEZJSL5tjOC9W7tsrwx57e2bPP3EFQY9ycP5bb756gt87plP0BhDFOe4psWlwQiYZDKwS51laaoHL5afWyOI0TJCRRHOiHBTQmPqFqU0NpY4IbDak4x12KgzjU4iVF/TRopk2EM1PWwsSMY9dNPHCBuwK62QeY9//53vc+/hW1y8uk2/N+C733+NNJY82D+kQ6C0pJelNE1H21lQDZ974gn+yq/+FcoqyNetC83MytY6Q7caWhnv6doWaw1RnJDEMUpLpM5jVBYRJWG0E8Zhmy4kmSQxRJLOeZrOUlQV0+kErSP+6l/4ywzyDA8syppzG2MubAzIE8n6ypgsjyhqx95eQaQ8m1s9fP6QP/3BHzGdzImlo6hqWu+oGxMOT21L19W0bYOpW0zTUMym1E1LKzpaU2GNwxH2CCMELpJ0sUbmKT6SGCexWuMHKVY5jDS4YU6bKErbQj/BxFC2JbYncIlEZzEqzvjqCz+kloeMxzneWO4/mFN3mtJAbzAki2Iun1vj4vktTqYNTjhGieZv/tW/Rq+fUjUljpCzJJQMFKlUwTFZljgp6Q2HYe6agMZa1F/79b/yZYsGHdMYT2M8TihaL7BorJfUnaEzjrYzGO9ZNC0ba1tIKXnxtVdoW8u4n/HcU49wcHLCtCjYXFsj1Ypbtx/Q6/V49937RDrl7t5dTB1x9doFjo+POZkVGGcpi4pyUWJMS1vVdHUdAk8JmIxdEvsOA20XOgwZUrasCd2G0AIUCCWQcXiNrNMBl4+DbznwsRq77MeV0lTG8o0fvMYP3vg+bVPzzW+8SNl43r55n/VBj0Gvx93DQ9ZXe3zuuRucHJ2yt3fMdF7yt/76b/HFn/opirIkIAoWpVWYzSkkOgpTCbMsI4qiEA2tZLAoOYv6lV/60pdN5xFE1KZjUbc0RjIpGo5OpkxnBVXrqI2lE57ZoqSoDEVtuXzpOlVT8vqbr1HWDbGSDPOM09Mi5MM5w1o/4tL5MUjFG2/dpqtqdFJz98G7DKIBxbRiVlWUXUdVFnR1F0x13mGNx3qL7UKaibcO0zZY0y4PbDbYW7tuOZ4wtKnGhkAQCO4X5wx+GXVprEEJhTUdmI6j4xn/7jvf4/DkHvfuPODe7T0G/ZiN9RyJwhroLNRVwSNn15mdTrl1e5eqrvj1X/kSX/qFX2S+mC2V1C1CCExnaOoa03bBCuV9iGl27gNAgLIq8MKi7z/YwXVN8Dz0xsRxirGhr63rmrppGeQDEB4hAtESxTHOgnSKT33yJ7n34B67u3d5/d279PIMY2oe7D7k6sVznEwW/Oil29ReIYTj5LTAoVnMpzxyvaafbfHe3XukPUFkcwQKgWM4zFgf9UhURCNDzgMmZAwl/YzIWpRQaOlw3hLFaUgjVx7hLS7SdJ1ACUtb+wCmqXAav3t/wSCRjHs533vlHb723e+xcXbE+/sz6Dp2DhqG+YTaCob9Me/euUukDLfv7FJXDVka84lPfopf+vkv4rFUTRNkLtZhmtlyKGqHVIKmqVAqxmIZjtc+HO6QxCleeMQ//X/+U181DSKK6A1GWOuWOZcJ1tjgp0qSgL0sB3nqOCbWCUoF9XCaxXz9T/8N3/vuV5mcViT9lOPJnEhrRnmOFIJBKqmN587+hPW1AUrC+c0BZ7cvYkzHpbM9bt9qMSQs5tMwpCGJOLuyytqgR5rE5HEc5N9pjFYqyF2cATxxlhMr0MJjTUeSKZSU4DqcNUjhUVhu7x7x+uEePSTeCd68v8u7d/chjtk/WfDouU1iBUezBR7FtCppO8O51RXqqmRlkPLrf+FX+et/+S/R1g06inDGcHx0RNM0tGVNsoy6aduCcj6jWpTkwxHbl6+A96RZSr/fDx7r/8d//t94LyRSReRpCirM+12OMw0x61oFVfMy0Kltarqu+zMcXyriKOL22y/x9a/9HkeTGYvOsyhKVsc9FmWNcoJf+vGnmcwavvHKu1w4s0bT1NS15Zd/7hNcubDOiy8dsH3+CWbTE8rFnOl8zmw6xTYN3vkgyFKeOIpJkxQlJNo54iSBSKK9w9QLhHeB4DEOb1sELmjxI81eUfPk5x7lcH/CH3/1R8hYotKEvb0pn376Mfo9ye9/63WU7BjkGfvTgl4vo6cVl9ZX+d/93b/Lx557jtOjQ+IoCY56a5FKEiUxTdNSlyWd6bCdCWCkUggkSoDD0XUdo/EqD49qxP/9H/7XPs2zZZidXIbohdGGkdYYa7HGhA8REms9cRyFVaXCgy/LkqapECjKyS5f+9P/jZ2jI7a3Ntg5POX6lS1eeukWvViiJGye3eLKpbPcv7fHm3f3+PGffR7ahgdvPeSTz32Uta2LKN0jWk56tc5gmpaqnIfppEJgXfAKxFIGGYx0YBxaSYbDPr08QytJmsbEkaRqa/aOTvjXX/k2K1c22Tq7xZ/+wXe5fvYsV6+d5fV3b3G0d4J1HVZInn7iAu/eOeTaxU3u7zzkkYsX+T/87b/HI1evMZlPEEi0VEghaNpmOdw0iM+MtWGwkQhQvJehsKZxtJy/rHjn9h5/8s030OsbG8uhmiHTrKkqcJ7OdphI4x2UxYK6qtlaX0c4S6pjRJJimg6cY9gfoAc9rHP0z57hwrnz/Jvf/2cYM6d3eZPWem48fonZdIZXkvWLG5wUFQvriLKY9e11jvYP2Ds64cVXvs/jV+9x4/IVLl66xsponTwfEkcRiRLBwC1lyGdWoAWYZeir9xYZBYNf4xoqUzE5PuL+zg43b9/j9bfvsjebkbLNeHNAMsoohONoesr22XXKpiOJIkYjhYwirl8Y06Pkkzce56/95u+wsrrBw4PTACEoSefBeQNSEuXZcuaBwNrlDLUko20bnLcsZnPiOEEuQcw//JOXKDqPTkUgyFMt0FGMsg3zk2Oq+ZTx2phr164jxUYAt5biWy9CGlY86oecHR/k2k1d4f2Mc5sr/NZv/A7f//43+MrX/oRSSC5cPY93HU5JbNOw0U/40SsHDNeGIQjw4QylBVYmPNgveP/O99HJD7l8dp2z2+usr45Z6Q8Zjob0spxIqxDKtKRSO2MoFiVFVTIt5hwcn3B3Z587946oS0M/zyiqlkRJZqcFXQd5L+f2/Yc8dfkpJlXDYJSHQRUqZufOPrmEz//0z/LzP/NzZGlG05Q4B7NFzf7BAaurq2xsrDM5PmJRFqyur1EXJV3X0ev3SZJQ65USDPtroY3WcOfNW2S9lMiBdvUp3lmSPCeSnnyccGb9EjgT5CftnKpqGY/H2M7gvSWKYoxQRFohPmCXIgm9GG893sE4T/iLv/xrfOIjH+N/+J//J45nc4zULI7mrGYZb+0+wEtBmidUleP4aEqsNIMspnCCGZpURLxw9xR155REeVItGA5zRv2M4ahPnEYY47DeUzcd5aJmvqgoKkPZWKrO0FmHFIK669hYWeF40TKfziirjjTPwDneubXD2tqY2awmX8loFyWXNrb5m3/tt3j6icfoXIdtDaN+L8ymd4ZL5zdp6oaTk4d0dU2sI0ztEB7iJEcnGdN5ycnJCUmasLq2jhRBkv/sk9d49omrTCZTNM6wPh6TJHpZWw2xlniX4LygqhZ468GFjQ0TIsCiKEISpBYhhPvPUrGILF0zxzdw5dJl/s9//x/wT/7FP+OVezeJEsn793aJ4xQhlqLgzrGYLRhHMXvTOTLtESUJj19aQ8qI42mFUp7T0nJnViIXM/yDCYMkYmttRGccD46mOKnIs4Qoy9Cq5fIwI89i3r37kEndUhSnRJFiVtdMJjOSJEZFmmllObm1Q5opkg6eu/40f/u3/xaDPKKtq4BeSk1nBEoo+oMhnWmoy5okitH9GCE1Og7j0c2sRU6mJFmMFGBaw8nRMQJPr5dROkNTNzStQa+vrRApTRJFRHFEXZd406KjBHRMmq0ghAz97NLUrF0UgiwUOBETxxAhgkF6mbmmdYwwjllZAp5f//O/ztnvfo37+/u88dbbHJ1OaRuDjiKOTyeYxiEySRxFdCIcqnpCEGvJD3d2eebqNhtnVtg91ahIsDEacvPuQ0b9iMPTirNrQ/pZwmRRc25zyN7+Mbm0mMWUlVxzOi0Z9zKaRYGwnulkitAKawztbML6cMRHn36WS1vbfOaTn0V4x9HxKWmcYE2LaVuqssB2Lf1+jlCa/soKAoV3HXXT4TrDYLiCbSua1hA5x3g0RMiI1jScHB3i2opkMKIumuXYFdtQNwXOxrR1CE7K4ojFYs6sqBAYtAhQu0DQNhVZ1gv4vgOJpTN18OpqiVcK6TyRTggTAjxRnJLEgl/56V9CKsXDg33+8Gt/zP/6ja/T04rJ4emHcwJyITnpOqJEc/vhMbWBrdUR83nFwdGclZUhR5OCjUQjBRwczykaw7ifcHo6wQPzE4foWiZdS9VZtBRoPLGD0oI1nvJ4wfZqj41+ys99+jP8wk//HOfPXwibZlvRVAXCS5q6C/ShVkgtKGtDub9PnETE3YBEp2ilaTuDa2pM15GkEuE6qjpMIxFK4X1H3ktpmw5ZVGgJSnq07zoW0wmI4JJJohjTKg6PT5guCpIkohct8QshydIUKQXWdtR1R5SkATogZMHpNMYjloZvGYIbnAO1HHiP49L5C/ylP/+rfPeHP+DRlYwH5Skz7+lpQyRqLg1WOCgL7kwNUZLS0556OWblZDJjPBiwKMowt1EFTnsxbyitZaMf07WGk7KlcZa28QjrODPs4coJw8hSeMcqLY+srFLkMb/1G3+NfpYxn8+ou5ZIhSCnOE7DiV9DkvVI0x5JXgfa1LQ0VcV0dopSEXXbEssIISxlYZBCEiUZOEtVFkRpQmPCoGuVRCHIu6nR/V4fvxwhIoSgqWu0VmxsrTMeDYPBQiu0FkgZVM9KCpT0oFM8Aq0lcZyilKKsq4CHmGoZFZ8QqfD746UmclG3pL0+z3/kef7wD3+f61du8F/8Z/8Zk9NjXnvrRR7u7hBZyVx6jGjoiGitoylrOudZ1FOEgNIJFnWFCuHvIAT3jg3KdWg8PQkrsSWLFINcc+7aszzy6FMM17b4b3/3n/IHf/ICP/65L6C04vjkGL3kw5sm8NdIgbcyOPuRSwGbBBWjgU63xEmMMSGCRy9HlSPDBA2hgxtUxjHnH32a6cE+5fSUpmkDTmQt4qv//He9FJJyUaK0oqyqkCwbRchlVGOapQgRLJZCBdjaeYeSeik9kUQ6QmpFXQXnvIyioIrzBo9EyQillqJcoegnKftHR3zl63/E53/sJ7hx8TLCB8js4PCIBw/usrN3h/3DHWaLBaarabuWzhosDveB+NQ5FIJISoQOk1NTpYnjnH42ZGN9i7Uz59hYP8fqeBW9jNC6u7/D9773bT778c+xub4eomgEpGmCUIGGdc6ipEbpwHloFZABYwxCaryzdF2DN0vVhxRIGQfwz4X5kqqXs33heojh8Z7p3h7zo0NYpsiLf//Pftdb65CEzAQPlFWJNS7Iy1VIOvwgQSrNUpQM0Y7WLrMUfMhZE0qRRSk4T9U1GC9obPgSIy3J0og0iYPaOrzlCBW+NO09aRKFIZhShPiczmAdIQy8WLAopnSmpa4rurYAZ5FSI2WE0uEANuyNGA7GYfR4GpR7zgm6rg3KZrmMzJeSKEqx1mCdI1IxXoDDBgZumXemZIBatNZIaXGmpa47jA3/LoCua+lMGwQFMgrRN97hTEdrHf3xGqtnzjE7PaI8PiRWcQjr0Ardtg111QSxqBMgIU1jkjhHyoCtW2M+gIYoFsWHMmvhQ8RLlKZICDN7BSAVxaJk/2RK1SicF0jlSWLo5xnDQY84kgGwMgYlJInSGNuQOoNz/kM4V2tNv9cjXRkyHKZkvZUQKtI1ONehVYzSYdxuZwzSg9KSpqlDlFriSJOMOI4DoqoU4Z0E6zwyisLb40JcgRJyubqDXjaKP9ApmRDFrGxQi1iBUHLpbTYIkX24WFnOxVGEzKK2LKlPT+lmEyIZxG91UQS+oLUdWgYNf+M6jPG0rSGNDVGa4I3AWxOSRRzIpYA3BJtKiGKkTEiiiFa2NMbgfUeUxPTzHB1ZpAhglPchcqauWkARaUE/S9DLrE4pJYagIh72hwHNFAFxtT7g6u1iRu3DqhVS4myF9WFel4wiFALbLoe65cHtH1ZpR0dHFKW4ZUxCUKi5MKD0gxG9jlCbhSdKovC2yqDYkEthl5QSvcyUdt5BlFK7Zqn9lKAilHPgQ+hJPhzi244s7eO8oS6qoE1KNDpNMlAWnCdOEgSBUPBLXtYvN2eBxAmDVNGHXqfOeYQzSLvMaxYSogQwpGnO6som1nfh4QoViJIgQMR7i8CEAD8PsdJBNbd8hTvTgQtEfCAxBJFOAm1qQxKvUprOOUzbBlEvgjCfXaBiHRJ8nQ351C4kobsPZlJ2Fu+Db0HrKAxcFkFGYjqz9JaF2TN2OXYlJCYGaaNfCtmwFukFdhnR770G3+CsCXZZpRBKgVbYzmKaQFmmeUaUJuhYxzhhsF0IXhJCIEWYFRnpkP/gnMM7izHNco6doDaWxjoiPErp5aDOEA+pZBoG2sswoZQlE6Qi/WGAawjHUxjfIYzDfTiLPTBG1ppleHBIMJRimUPtPRqJlA6EJ4oDVC4/mMC33JxNa+h8i+k6uq4MBL9OMF334RhElpFnNg5IqvWOum1oq/rDMtIaR2XD5i8IX4CUILwGDBGOWAZPg9SKKI6XDUwwrisZwk+cd+HjfEScCLSOwQn+v4P9/e2F5hZkAAAAAElFTkSuQmCC"))).resize((256, 256), Image.Resampling.LANCZOS)
            img.save(ICON_PNG_FILE, format="PNG")
            img.save(ICON_ICO_FILE, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
        except Exception:
            pass

    work_dir = os.path.dirname(os.path.abspath(__file__))
    weights_root = os.path.join(work_dir, "model", "weights")
    os.makedirs(weights_root, exist_ok=True)
    for repo_id, cls in [
        ("NeoQuasar/Kronos-Tokenizer-base", KronosTokenizer),
        ("NeoQuasar/Kronos-Tokenizer-2k", KronosTokenizer),
        ("NeoQuasar/Kronos-base", Kronos),
        ("NeoQuasar/Kronos-small", Kronos),
        ("NeoQuasar/Kronos-mini", Kronos),
    ]:
        w_name = repo_id.split("/")[-1]
        w_dir = os.path.join(weights_root, w_name)
        if not os.path.exists(os.path.join(w_dir, "model.safetensors")):
            try:
                obj = cls.from_pretrained(repo_id)
                obj.save_pretrained(w_dir)
            except Exception:
                pass

    bot_py = os.path.abspath(__file__)
    py_dir = os.path.dirname(sys.executable)
    pyw_exe = os.path.join(py_dir, "pythonw.exe")
    if not os.path.exists(pyw_exe):
        pyw_exe = sys.executable

    ps_cmd = (
        "$WshShell = New-Object -ComObject WScript.Shell; "
        f"$workDir = '{work_dir}'; "
        f"$botPy = '{bot_py}'; "
        f"$pywExe = '{pyw_exe}'; "
        f"$icoPath = '{ICON_ICO_FILE}'; "
        "$deskDir = [Environment]::GetFolderPath('Desktop'); "
        "foreach ($lnk in @((Join-Path $workDir 'AI Scalping M5.lnk'), (Join-Path $deskDir 'AI Scalping M5.lnk'))) { "
        "$s = $WshShell.CreateShortcut($lnk); "
        "$s.TargetPath = $pywExe; "
        "$s.Arguments = '-B \"' + $botPy + '\"'; "
        "$s.WorkingDirectory = $workDir; "
        "if (Test-Path $icoPath) { $s.IconLocation = $icoPath + ',0' }; "
        "$s.Description = 'AI (KhaTran) - Phần Mềm Scalping Vàng & Quản Lý Lệnh Real-Time'; "
        "$s.Save() }"
    )
    try:
        startupinfo = None
        creationflags = 0
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
            stdin=subprocess.DEVNULL,
            startupinfo=startupinfo,
            check=False,
            creationflags=creationflags
        )
    except Exception:
        pass


def _running_mt5_terminal_paths() -> list:
    """Đường dẫn đầy đủ các terminal64.exe (MT5) đang chạy, chỉ dùng Win32 API (không bật console). Lỗi -> []."""
    paths = []
    try:
        import ctypes.wintypes as wt

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                        ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                        ("szExeFile", ctypes.c_wchar * 260)]

        k32 = ctypes.windll.kernel32
        k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
        snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
        if not snap or snap == wt.HANDLE(-1).value:
            return []
        try:
            pe = PROCESSENTRY32W()
            pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = k32.Process32FirstW(snap, ctypes.byref(pe))
            while ok:
                if pe.szExeFile.lower() == "terminal64.exe":
                    h = k32.OpenProcess(0x1000, False, pe.th32ProcessID)  # PROCESS_QUERY_LIMITED_INFORMATION
                    if h:
                        try:
                            buf = ctypes.create_unicode_buffer(1024)
                            size = wt.DWORD(1024)
                            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) and buf.value not in paths:
                                paths.append(buf.value)
                        finally:
                            k32.CloseHandle(h)
                ok = k32.Process32NextW(snap, ctypes.byref(pe))
        finally:
            k32.CloseHandle(snap)
    except Exception:
        return paths
    return paths


def _find_other_running_bot_pids() -> list:
    """Tìm toàn bộ các tiến trình python/pythonw khác đang chạy mt5_scalping_bot.py ngầm trên máy hoàn toàn trong bộ nhớ (không bật console)."""
    import subprocess
    my_pid = os.getpid()
    found_pids = set()

    # 1. Kiểm tra PID lưu trong INSTANCE_LOCK_FILE trước (nhanh nhất, < 1ms qua Win32 API thuần)
    try:
        if os.path.exists(INSTANCE_LOCK_FILE):
            with open(INSTANCE_LOCK_FILE, "r", encoding="utf-8") as f:
                lock_data = json.load(f)
            locked_pid = int(lock_data.get("pid", 0))
            if locked_pid > 0 and locked_pid != my_pid:
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                h_proc = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, locked_pid)
                if h_proc:
                    exit_code = ctypes.c_ulong(0)
                    if ctypes.windll.kernel32.GetExitCodeProcess(h_proc, ctypes.byref(exit_code)) and exit_code.value == 259:
                        found_pids.add(locked_pid)
                    ctypes.windll.kernel32.CloseHandle(h_proc)
    except Exception:
        pass

    # Nếu đã tìm thấy PID từ lock file, trả về ngay lập tức (không cần gọi bất kỳ tiến trình con nào)
    if found_pids:
        return sorted(found_pids)

    # 2. Quét dự phòng bằng tasklist có cờ CREATE_NO_WINDOW và SW_HIDE (ẩn 100%, không spawn powershell làm chớp màn hình)
    try:
        startupinfo = None
        creationflags = 0
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        res = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq pythonw.exe", "/FO", "CSV", "/NH"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=3,
            startupinfo=startupinfo,
            creationflags=creationflags
        )
        for line in (res.stdout or "").splitlines():
            parts = [p.strip('"') for p in line.split(",")]
            if len(parts) >= 2 and parts[1].isdigit():
                pid_val = int(parts[1])
                if pid_val > 0 and pid_val != my_pid:
                    found_pids.add(pid_val)
    except Exception:
        pass

    return sorted(found_pids)


def _terminate_pids(pids: list):
    """Đóng dứt khoát danh sách các tiến trình App cũ đang chạy ngầm."""
    import subprocess
    PROCESS_TERMINATE = 0x0001
    for pid in pids:
        killed = False
        try:
            h_proc = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
            if h_proc:
                ctypes.windll.kernel32.TerminateProcess(h_proc, 0)
                ctypes.windll.kernel32.CloseHandle(h_proc)
                killed = True
        except Exception:
            pass
        if not killed:
            try:
                startupinfo = None
                creationflags = 0
                if sys.platform == "win32":
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = 0  # SW_HIDE
                    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=3,
                    startupinfo=startupinfo,
                    creationflags=creationflags
                )
            except Exception:
                pass


def ensure_single_app_instance():
    """
    Kiểm tra khi khởi động xem có phiên bản App nào đang mở sẵn hoặc bị treo chạy ngầm không:
    - Nếu khởi động tự động sau khi cập nhật OTA (--after-update): Tự động đóng phiên cũ đang thoát và mở thẳng phiên mới (không hiện bảng hỏi).
    - Nếu khởi động lần đầu bình thường: Trả về ngay lập tức (< 0.05ms), tuyệt đối không mở Terminal hay quét tiến trình.
    - Nếu phát hiện tiến trình cũ: Hiển thị hộp thoại cho phép người dùng ĐÓNG tiến trình ngầm cũ để mở phiên mới sạch sẽ,
      hoặc chuyển sang cửa sổ đang mở sẵn (tuyệt đối không để 2 App chạy song song gây xung đột MT5 / vào đúp lệnh).
    """
    global _SINGLETON_MUTEX_HANDLE
    is_after_update = ("--after-update" in sys.argv)
    if is_after_update:
        old_pids = _find_other_running_bot_pids()
        if old_pids:
            _terminate_pids(old_pids)
            time.sleep(0.35)

    mutex_name = "Local\\KhaTran_Kronos_MT5_Scalping_Bot_Singleton_v2"
    already_exists = False
    try:
        _SINGLETON_MUTEX_HANDLE = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            already_exists = True
    except Exception:
        pass

    # Nếu không bị trùng Mutex (hoặc vừa khởi chạy tự động sau cập nhật OTA): Ghi nhận lock file và vào app tức thì
    if not already_exists or is_after_update:
        try:
            os.makedirs(os.path.dirname(INSTANCE_LOCK_FILE), exist_ok=True)
            with open(INSTANCE_LOCK_FILE, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "time": time.time()}, f)
        except Exception:
            pass
        return

    other_pids = _find_other_running_bot_pids()
    if not already_exists and not other_pids:
        return

    pids_label = ", ".join(str(p) for p in other_pids) if other_pids else "Đang hoạt động"
    tmp_root = tk.Tk()
    tmp_root.withdraw()
    try:
        tmp_root.attributes("-topmost", True)
    except Exception:
        pass

    choice_kill_old = messagebox.askyesno(
        "⚠️ Phát Hiện App Đang Chạy Ngầm / Mở Trùng",
        f"Phát hiện phần mềm AI Scalping Bot (PID: {pids_label}) đang chạy sẵn hoặc đang chạy ngầm trên máy!\n"
        f"Nếu để 2 App chạy cùng lúc sẽ gây xung đột kết nối MT5 và vào đúp lệnh.\n\n"
        f"• Bấm [YES / Có]: Tự động ĐÓNG toàn bộ tiến trình cũ đang chạy ngầm và khởi động phiên mới này.\n"
        f"• Bấm [NO / Không]: Giữ nguyên App đang chạy (không mở thêm cửa sổ mới).",
        parent=tmp_root
    )
    tmp_root.destroy()

    if choice_kill_old:
        if other_pids:
            _terminate_pids(other_pids)
            time.sleep(0.45)
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            ts_ms = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            daily_path = os.path.join(LOG_DIR, f"bot_activity_{datetime.now().strftime('%Y-%m-%d')}.log")
            with open(daily_path, "a", encoding="utf-8") as f:
                f.write(f"[{ts_ms}] 🛡️ [CHỐNG XUNG ĐỘT NGẦM] Đã tự động đóng tiến trình App cũ (PID: {pids_label}) để khởi động phiên mới sạch sẽ.\n")
        except Exception:
            pass
    else:
        # Đưa cửa sổ App đang chạy sẵn lên tiền cảnh nếu có lưu HWND
        try:
            if os.path.exists(INSTANCE_LOCK_FILE):
                with open(INSTANCE_LOCK_FILE, "r", encoding="utf-8") as f:
                    lock_data = json.load(f)
                hwnd = int(lock_data.get("hwnd", 0))
                if hwnd > 0:
                    ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        sys.exit(0)


def _run_packed_replay_job_from_env():
    """Tiến trình con do tab Backtest khởi chạy (biến môi trường KRONOS_REPLAY_JOB, bản .exe / launcher + core_engine.bin):
    chạy 1 lượt backtest bằng CODE LIVE rồi THOÁT ngay (không mở giao diện, không đụng bot live đang chạy ở tiến trình khác)."""
    job = os.environ.pop("KRONOS_REPLAY_JOB", "")
    if not job:
        return
    for _s in (sys.stdout, sys.stderr):  # stdout/stderr con là cp1252 -> chữ Việt lỗi; ép UTF-8
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    code = 1
    try:
        raw = getattr(sys.modules.get("__main__"), "_CONTAINER_RAW_DICT", None)
        if raw:
            import marshal
            try:
                import argparse  # noqa: F401 (chỉ dùng khi chạy dòng lệnh)
            except ImportError:
                sys.modules["argparse"] = types.ModuleType("argparse")
            for name in ("bt_ai_cache", "run_backtest", "live_replay"):
                if name not in raw:
                    raise RuntimeError(f"core_engine.bin thiếu module {name} (cần đóng gói lại bằng tools/binary_packer.py)")
                m = sys.modules.get(name)
                if m is None:
                    m = types.ModuleType(name)
                    sys.modules[name] = m
                if not getattr(m, "_packed_loaded", False):
                    m.__dict__.setdefault("__file__", os.path.join(os.getcwd(), "tools", name + ".py"))
                    exec(marshal.loads(raw[name]), m.__dict__)
                    m._packed_loaded = True
        else:
            tools_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
            if tools_dir not in sys.path:
                sys.path.insert(0, tools_dir)
        import live_replay
        code = int(live_replay.run_replay_job(job))
    except BaseException as e:  # noqa: BLE001 - báo lỗi về app qua file tiến độ
        try:
            with open(os.path.join(os.path.dirname(job), "progress.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "error", "msg": f"{type(e).__name__}: {e}", "tb": traceback.format_exc()[-3000:]},
                                   ensure_ascii=False) + "\n")
        except Exception:
            pass
    finally:
        os._exit(code)


_run_packed_replay_job_from_env()


if __name__ == "__main__":
    if "--setup" in sys.argv:
        run_first_time_setup()
        sys.exit(0)
    ensure_single_app_instance()

    from kronos_license import LicenseManager, show_activation_dialog
    lic_mgr = LicenseManager()
    lic_status = lic_mgr.check_status()

    root = tk.Tk()
    if not lic_status.get("is_valid", False):
        root.withdraw()
        # Chặn mở phần mềm khi chưa kích hoạt hoặc hết hạn! Hiển thị ngay cửa sổ nhập key
        activated = show_activation_dialog(root, lic_mgr, is_startup_block=True)
        if not activated:
            sys.exit(0)
        lic_status = lic_mgr.check_status(force_refresh=True)
        if not lic_status.get("is_valid", False):
            sys.exit(0)
        root.deiconify()

    app = KronosDesktopApp(root, lic_mgr=lic_mgr)
    root.mainloop()
