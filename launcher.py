"""
launcher.py
───────────
Native In-Memory Secure Bootloader
Dành cho ứng dụng: Trading AI PRO by Kha Tran Design

Quy trình khởi chạy:
1. Xác định vị trí tệp nhị phân 'core_engine.bin' (tại thư mục cài đặt hoặc PyInstaller bundle).
2. Giải mã trực tiếp trong RAM (In-Memory) bằng chuẩn AES-256-GCM.
3. Nạp và đăng ký các module 'kronos_license', 'kronos_updater', 'mt5_scalping_bot'.
4. Kích hoạt lớp kiểm định bản quyền máy tính (HWID Node-Locked Security Gate).
5. Khởi chạy giao diện chính. Tuyệt đối không để lại file mã nguồn trên ổ cứng.
"""

import os
import sys
import types
import marshal
import hashlib
import tkinter as tk

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    import tkinter.messagebox as mb
    mb.showerror("Lỗi Khởi Động", "Thiếu thư viện mã hóa bảo mật 'cryptography'. Vui lòng cài đặt lại phần mềm.")
    sys.exit(1)

# Chỉ dẫn phân tích tĩnh để PyInstaller gom trọn vẹn thư viện nhị phân vào gói chạy
if False:
    import MetaTrader5
    import torch
    import torch.nn.functional
    import numpy
    import pandas
    import PIL
    import matplotlib
    import gradio_client
    import huggingface_hub

# Khóa bí mật dẫn xuất nội bộ của ứng dụng (32 bytes AES-256)
PROPRIETARY_SALT = b"TRADING_AI_PRO_KHA_TRAN_DESIGN_IMMUTABLE_PROPRIETARY_SALT_2026"
ENGINE_KEY = hashlib.sha256(b"KHA_TRAN_TRADING_ALGORITHM_AES_KEY_V1" + PROPRIETARY_SALT).digest()
AAD_HEADER = b"TRADING_AI_PRO_ENCRYPTED_BINARY_CONTAINER_V1"


def get_app_dir() -> str:
    """Lấy thư mục gốc chứa file thực thi của ứng dụng."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def load_binary_engine():
    """Giải mã và nạp mã nguồn từ core_engine.bin trực tiếp vào RAM."""
    app_dir = get_app_dir()
    bin_path = os.path.join(app_dir, "core_engine.bin")

    # Kiểm tra thêm trong _MEIPASS nếu đóng gói một tệp PyInstaller
    if not os.path.exists(bin_path) and hasattr(sys, "_MEIPASS"):
        bin_path = os.path.join(sys._MEIPASS, "core_engine.bin")

    if not os.path.exists(bin_path):
        import tkinter.messagebox as mb
        mb.showerror("Lỗi Bản Cài Đặt", f"Không tìm thấy tệp nhị phân cốt lõi:\n{bin_path}\nVui lòng cài đặt lại ứng dụng.")
        sys.exit(1)

    try:
        with open(bin_path, "rb") as f_in:
            raw_data = f_in.read()

        nonce = raw_data[:12]
        ciphertext = raw_data[12:]
        aesgcm = AESGCM(ENGINE_KEY)
        container_bytes = aesgcm.decrypt(nonce, ciphertext, AAD_HEADER)
        raw_dict = marshal.loads(container_bytes)

        # Nạp tất cả các module vào sys.modules
        for mod_name, code_bytes in raw_dict.items():
            code_obj = marshal.loads(code_bytes)
            mod = types.ModuleType(mod_name)
            mod.__file__ = os.path.join(app_dir, f"{mod_name}.pyd")
            sys.modules[mod_name] = mod
            exec(code_obj, mod.__dict__)

        # Giải phóng bộ nhớ đệm giải mã
        del container_bytes
        del raw_data
    except Exception as e:
        import tkinter.messagebox as mb
        mb.showerror("Lỗi Giải Mã Nhị Phân", f"Không thể giải mã tệp nhị phân lõi:\n{e}\nTệp có thể đã bị can thiệp trái phép.")
        sys.exit(1)


def main():
    # 1. Nạp toàn bộ động cơ nhị phân vào RAM
    load_binary_engine()

    # 2. Truy xuất các thành phần đã nạp trong RAM
    bot_mod = sys.modules.get("mt5_scalping_bot")
    lic_mod = sys.modules.get("kronos_license")

    if not bot_mod or not lic_mod:
        sys.exit(1)

    # 3. Đảm bảo chạy đơn tiến trình
    if hasattr(bot_mod, "ensure_single_app_instance"):
        bot_mod.ensure_single_app_instance()

    # 4. Kiểm định bản quyền bảo vệ phần cứng (HWID Gate)
    LicenseManager = getattr(lic_mod, "LicenseManager")
    show_activation_dialog = getattr(lic_mod, "show_activation_dialog")
    KronosDesktopApp = getattr(bot_mod, "KronosDesktopApp")

    lic_mgr = LicenseManager()
    lic_status = lic_mgr.check_status()

    root = tk.Tk()
    if not lic_status.get("is_valid", False):
        root.withdraw()
        # Chặn mở bot khi chưa có key: Bật cửa sổ nhập key
        activated = show_activation_dialog(root, lic_mgr, is_startup_block=True)
        if not activated:
            sys.exit(0)
        lic_status = lic_mgr.check_status(force_refresh=True)
        if not lic_status.get("is_valid", False):
            sys.exit(0)
        root.deiconify()

    # 5. Khởi chạy giao diện chính
    app = KronosDesktopApp(root, lic_mgr=lic_mgr)
    root.mainloop()


if __name__ == "__main__":
    main()
