"""
launcher.py
───────────
Native In-Memory Secure Bootloader & Crash-Proof Launcher
Dành cho ứng dụng: Trading AI PRO by Kha Tran Design

Quy trình khởi chạy:
1. Thiết lập thư mục làm việc chuẩn xác tại thư mục cài đặt ứng dụng.
2. Xác định vị trí tệp nhị phân 'core_engine.bin' (tại thư mục cài đặt hoặc PyInstaller bundle).
3. Giải mã trực tiếp trong RAM (In-Memory) bằng chuẩn AES-256-GCM.
4. Nạp và đăng ký các module 'model', 'kronos_license', 'kronos_updater', 'mt5_scalping_bot'.
5. Kích hoạt lớp kiểm định bản quyền máy tính (HWID Node-Locked Security Gate).
6. Khởi chạy giao diện chính với cơ chế bẫy lỗi toàn diện (Crash Shield & Win32 Native Fallback).
"""

import os
import sys
import types
import marshal
import hashlib
import traceback

# 1. Đảm bảo thư mục làm việc luôn cố định tại thư mục chứa file thực thi
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    os.chdir(APP_DIR)
except Exception:
    pass


def show_native_error(title: str, message: str):
    """
    Hiển thị thông báo lỗi trực tiếp qua Win32 API cấp thấp.
    Đảm bảo 100% người dùng luôn nhìn thấy lý do lỗi kể cả khi Tkinter bị hỏng hay thiếu DLL.
    """
    try:
        import ctypes
        # 0x10 = MB_ICONERROR, 0x10000 = MB_SETFOREGROUND
        ctypes.windll.user32.MessageBoxW(0, str(message), str(title), 0x10 | 0x10000)
    except Exception:
        try:
            import tkinter as tk
            from tkinter import messagebox as mb
            _t = tk.Tk()
            _t.withdraw()
            _t.attributes("-topmost", True)
            mb.showerror(title, message)
            _t.destroy()
        except Exception:
            pass


# 2. Kiểm tra thư viện mã hóa bảo mật
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    show_native_error("Lỗi Khởi Động", "Thiếu thư viện mã hóa bảo mật 'cryptography'. Vui lòng cài đặt lại phần mềm.")
    sys.exit(1)

# Chỉ dẫn phân tích tĩnh để PyInstaller gom trọn vẹn toàn bộ thư viện nhị phân vào gói chạy
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
    import einops
    import safetensors
    import tqdm

# Khóa bí mật dẫn xuất nội bộ của ứng dụng (32 bytes AES-256)
PROPRIETARY_SALT = b"TRADING_AI_PRO_KHA_TRAN_DESIGN_IMMUTABLE_PROPRIETARY_SALT_2026"
ENGINE_KEY = hashlib.sha256(b"KHA_TRAN_TRADING_ALGORITHM_AES_KEY_V1" + PROPRIETARY_SALT).digest()
AAD_HEADER = b"TRADING_AI_PRO_ENCRYPTED_BINARY_CONTAINER_V1"


def get_app_dir() -> str:
    """Lấy thư mục gốc chứa file thực thi của ứng dụng."""
    return APP_DIR


def load_binary_engine():
    """Giải mã và nạp mã nguồn từ core_engine.bin trực tiếp vào RAM."""
    app_dir = get_app_dir()
    bin_path = os.path.join(app_dir, "core_engine.bin")

    # Kiểm tra thêm trong _MEIPASS nếu đóng gói một tệp PyInstaller
    if not os.path.exists(bin_path) and hasattr(sys, "_MEIPASS"):
        bin_path = os.path.join(sys._MEIPASS, "core_engine.bin")

    if not os.path.exists(bin_path):
        show_native_error(
            "Lỗi Bản Cài Đặt",
            f"Không tìm thấy tệp nhị phân cốt lõi:\n{bin_path}\nVui lòng cài đặt lại ứng dụng."
        )
        sys.exit(1)

    try:
        with open(bin_path, "rb") as f_in:
            raw_data = f_in.read()

        nonce = raw_data[:12]
        ciphertext = raw_data[12:]
        aesgcm = AESGCM(ENGINE_KEY)
        container_bytes = aesgcm.decrypt(nonce, ciphertext, AAD_HEADER)
        raw_dict = marshal.loads(container_bytes)

        # B1: Khởi tạo tất cả module và thiết lập __package__, __path__ trong sys.modules
        for mod_name in raw_dict.keys():
            mod = types.ModuleType(mod_name)
            if "." in mod_name:
                pkg_name = mod_name.rsplit(".", 1)[0]
                mod.__package__ = pkg_name
            else:
                mod.__package__ = mod_name
                mod.__path__ = []
            mod.__file__ = os.path.join(app_dir, f"{mod_name}.pyd")
            sys.modules[mod_name] = mod

        # B2: Gán submodules vào parent packages
        for mod_name in raw_dict.keys():
            if "." in mod_name:
                pkg_name, sub_name = mod_name.rsplit(".", 1)
                if pkg_name in sys.modules:
                    setattr(sys.modules[pkg_name], sub_name, sys.modules[mod_name])

        # B3: Thực thi code objects theo thứ tự phụ thuộc
        order = ["model.module", "model.kronos", "model", "kronos_license", "kronos_updater", "mt5_scalping_bot"]
        executed = set()
        for mod_name in order:
            if mod_name in raw_dict:
                code_obj = marshal.loads(raw_dict[mod_name])
                exec(code_obj, sys.modules[mod_name].__dict__)
                executed.add(mod_name)
        for mod_name, code_bytes in raw_dict.items():
            if mod_name not in executed:
                code_obj = marshal.loads(code_bytes)
                exec(code_obj, sys.modules[mod_name].__dict__)

        # Giải phóng bộ nhớ đệm giải mã
        del container_bytes
        del raw_data
    except Exception as e:
        tb = traceback.format_exc()
        show_native_error(
            "Lỗi Giải Mã Nhị Phân",
            f"Không thể giải mã hoặc nạp tệp nhị phân lõi:\n{e}\n\nChi tiết:\n{tb}"
        )
        sys.exit(1)


def main():
    import tkinter as tk

    # 1. Nạp toàn bộ động cơ nhị phân vào RAM
    load_binary_engine()

    # 2. Truy xuất các thành phần đã nạp trong RAM
    bot_mod = sys.modules.get("mt5_scalping_bot")
    lic_mod = sys.modules.get("kronos_license")

    if not bot_mod or not lic_mod:
        show_native_error("Lỗi Khởi Động", "Không tìm thấy module thực thi mt5_scalping_bot hoặc kronos_license.")
        sys.exit(1)

    # 3. Đảm bảo chạy đơn tiến trình
    if hasattr(bot_mod, "ensure_single_app_instance"):
        try:
            bot_mod.ensure_single_app_instance()
        except Exception as e:
            # Không để lỗi kiểm tra singleton chặn việc mở app trên VM
            pass

    # 4. Kiểm định bản quyền bảo vệ phần cứng (HWID Gate)
    LicenseManager = getattr(lic_mod, "LicenseManager")
    show_activation_dialog = getattr(lic_mod, "show_activation_dialog")
    KronosDesktopApp = getattr(bot_mod, "KronosDesktopApp")

    lic_mgr = LicenseManager()
    lic_status = lic_mgr.check_status()

    if not lic_status.get("is_valid", False):
        # Chặn mở bot khi chưa có key: Bật cửa sổ nhập key độc lập (parent=None để hiển thị rõ ràng trên mọi màn hình/VM)
        activated = show_activation_dialog(parent=None, license_mgr=lic_mgr, is_startup_block=True)
        if not activated:
            sys.exit(0)
        lic_status = lic_mgr.check_status(force_refresh=True)
        if not lic_status.get("is_valid", False):
            sys.exit(0)

    # 5. Khởi chạy giao diện chính
    root = tk.Tk()
    app = KronosDesktopApp(root, lic_mgr=lic_mgr)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err_msg = traceback.format_exc()
        try:
            log_dir = os.path.join(get_app_dir(), "logs")
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, "crash_startup.log"), "w", encoding="utf-8") as f:
                f.write(err_msg)
        except Exception:
            pass

        show_native_error(
            "Lỗi Khởi Động Ứng Dụng — Trading AI PRO",
            f"Ứng dụng gặp sự cố khi khởi động:\n\n{err_msg}\n\nNhật ký chi tiết được ghi tại logs/crash_startup.log."
        )
        sys.exit(1)
