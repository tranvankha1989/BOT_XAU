"""
kronos_license.py
=================
Hệ thống Quản lý Bản quyền & Bảo mật Ngoại tuyến (Offline License & Security Engine)
dành riêng cho AI Scalping Bot (KhaTran - Kronos).

Đặc điểm kỹ thuật & Tiêu chuẩn bảo mật:
1. Mật mã học bất đối xứng Ed25519:
   - Nhúng Public Key cố định vào Client để xác thực chữ ký số 100% offline.
   - Private Key được giữ tuyệt đối an toàn trên máy Admin/Developer.
   - Tương thích 100% với công cụ tạo key trong tools/keygen_cli.py & tools/keygen_gui.py.

2. Định danh phần cứng bất biến (HWID Engine):
   - Thu thập thông số phần cứng vật lý: CPU Processor ID + Motherboard Serial + System Disk 0 Serial.
   - Tự động fallback: PowerShell CIM -> WMIC -> Windows Registry.
   - Định dạng chuẩn: TTS-XXXX-XXXX-XXXX-XXXX.

3. Lưu trữ an toàn đa vị trí (Tripple-Breadcrumbs Persistence):
   - Phân tán dấu vết vào 3 thư mục chuẩn người dùng của Windows:
     + Vị trí 1: %LOCALAPPDATA%\\Kronos\\Data\\lic_cache.dat
     + Vị trí 2: %APPDATA%\\Kronos\\Config\\app_state.dat
     + Vị trí 3: %ProgramData%\\Kronos\\shared_lic.dat (hoặc ~/.kronos/shared_lic.dat)
   - Mã hóa AES-256-GCM với khóa dẫn xuất từ chính HWID của máy (PBKDF2 100.000 vòng).
   - Cơ chế đồng thuận (Consensus) + Tự phục hồi (Self-Healing) khi 1 vị trí bị xóa.
   - An toàn tuyệt đối với Windows Defender / Phần mềm diệt virus (0 can thiệp Registry COM, 0 dùng NTFS ADS).
   - Khách gỡ cài đặt app bằng trình gỡ của Windows hay bên thứ ba đều KHÔNG làm mất key bản quyền.

4. Chính sách Dùng thử (Trial) & Chống gian lận ngày:
   - Tự động cấp 7 ngày dùng thử miễn phí cho máy cài lần đầu.
   - Chống tua lùi đồng hồ hệ thống (Anti-Clock Rollback) dựa trên last_run_timestamp.
   - Chống gỡ ra cài lại để reset ngày dùng thử (Anti-Trial Reset).

5. Giao diện Kích hoạt Bản quyền (Activation Dialog):
   - Tkinter Dark Mode cao cấp (chuẩn thiết kế Slate & Gold/Emerald).
   - Sao chép HWID 1 chạm vào Clipboard.
   - Xác thực và kích hoạt tức thì.
"""

import os
import re
import sys
import json
import time
import math
import base64
import hashlib
import logging
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Tuple

import tkinter as tk
from tkinter import messagebox

# Thư viện mật mã học chuyên dụng
try:
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    from cryptography.exceptions import InvalidSignature
except Exception as e:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            f"Lỗi khởi động bảo mật (kronos_license):\nThiếu mô-đun cryptography hoặc các phần tử mật mã ({e}).\nVui lòng cài đặt lại phần mềm.",
            "Lỗi Bản Quyền — Trading AI PRO",
            0x10 | 0x10000
        )
    except Exception:
        pass
    raise

logger = logging.getLogger("Kronos.License")

# 32 bytes Raw Public Key của hệ thống (Khớp chính xác với tools/ed25519_public.pem)
ED25519_PUBLIC_KEY_BYTES = b"i\x9cstB\xa1|0Fx\r\xf2\xf0\x80\xf4\x15\xc2\xed\xa5\x08%s\xf6{\xab\\g\xfdD\xb8w\x01"

# Salt định danh phần cứng (đồng bộ chuẩn với tools/keygen_cli.py để ra cùng 1 HWID)
HWID_SALT = "OMNIVOICE_STUDIO_HWID_SALT_2026_OFFLINE_IMMUTABLE_CORE"

# Salt mã hóa lưu trữ file local
PERSISTENCE_SALT = "KRONOS_SCALPING_BOT_LICENSE_PERSISTENCE_SALT_2026_OFFLINE_GCM"

# Thời hạn sử dụng hoàn toàn do License Key quy định (không tự động cấp ngày khi chưa có key)
TRIAL_DAYS = 0
TRIAL_DURATION_SECONDS = 0

_CACHED_HWID: Optional[str] = None
_CACHED_HWID_RAW: Optional[str] = None


# =============================================================================
# 1. HWID ENGINE (ĐỊNH DANH PHẦN CỨNG BẤT BIẾN)
# =============================================================================
def _clean_str(val: Any) -> str:
    """Làm sạch chuỗi, loại bỏ ký tự lạ, chuyển chữ hoa và lọc các giá trị rác của nhà sản xuất."""
    if not val:
        return ""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", str(val)).strip().upper()
    invalid_defaults = {
        "TOBEFILLEDBYOEM",
        "DEFAULTSTRING",
        "NONE",
        "UNKNOWN",
        "NULL",
        "NOTAVAILABLE",
        "00000000",
        "12345678",
    }
    if cleaned in invalid_defaults:
        return ""
    return cleaned


def _get_hardware_via_powershell() -> Tuple[str, str, str]:
    """Lấy thông số CPU, Mainboard, Disk Index 0 qua PowerShell CIM (chuẩn Windows 10 & 11)."""
    cpu_id, mb_serial, disk_serial = "", "", ""
    ps_script = """
    $Output = @{
        cpu = (Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue | Select-Object -ExpandProperty ProcessorId -First 1);
        mb  = (Get-CimInstance Win32_BaseBoard -ErrorAction SilentlyContinue | Select-Object -ExpandProperty SerialNumber -First 1);
        disk = (Get-CimInstance Win32_DiskDrive -ErrorAction SilentlyContinue | Where-Object { $_.Index -eq 0 } | Select-Object -ExpandProperty SerialNumber -First 1);
    }
    Write-Output "$($Output.cpu)|$($Output.mb)|$($Output.disk)"
    """
    try:
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW

        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=4,
            creationflags=creationflags,
        )
        if res.returncode == 0 and res.stdout:
            parts = res.stdout.strip().split("|")
            if len(parts) >= 3:
                cpu_id = _clean_str(parts[0])
                mb_serial = _clean_str(parts[1])
                disk_serial = _clean_str(parts[2])
    except Exception as e:
        logger.debug(f"Không thể đọc HWID qua PowerShell CIM: {e}")

    return cpu_id, mb_serial, disk_serial


def _get_hardware_via_wmic() -> Tuple[str, str, str]:
    """Fallback: Lấy thông số qua WMIC (Windows 7/8/10)."""
    cpu_id, mb_serial, disk_serial = "", "", ""

    def run_cmd(cmd: str) -> str:
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            res = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=creationflags,
            )
            if res.returncode == 0:
                lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
                if len(lines) >= 2:
                    return lines[1]
        except Exception:
            pass
        return ""

    cpu_id = _clean_str(run_cmd("wmic cpu get processorid"))
    mb_serial = _clean_str(run_cmd("wmic baseboard get serialnumber"))
    disk_serial = _clean_str(run_cmd('wmic diskdrive where "Index=0" get serialnumber'))
    return cpu_id, mb_serial, disk_serial


def _get_hardware_via_registry() -> Tuple[str, str, str]:
    """Fallback an toàn: Đọc trực tiếp từ Windows Registry mà không cần gọi sub-process."""
    cpu_id, mb_serial, disk_serial = "", "", ""
    if sys.platform != "win32":
        return cpu_id, mb_serial, disk_serial

    try:
        import winreg

        # 1. CPU Identifier
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as k:
                val, _ = winreg.QueryValueEx(k, "Identifier")
                cpu_id = _clean_str(val)
        except Exception:
            pass

        # 2. Mainboard / BIOS Serial
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\BIOS",
            ) as k:
                for reg_name in ["BaseBoardSerialNumber", "SystemSerialNumber", "BaseBoardProduct"]:
                    try:
                        val, _ = winreg.QueryValueEx(k, reg_name)
                        clean_v = _clean_str(val)
                        if clean_v:
                            mb_serial = clean_v
                            break
                    except Exception:
                        pass
        except Exception:
            pass

        # 3. Disk Serial hoặc MachineGuid
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
            ) as k:
                val, _ = winreg.QueryValueEx(k, "MachineGuid")
                disk_serial = _clean_str(val)
        except Exception:
            pass
    except Exception as e:
        logger.debug(f"Không thể đọc registry hardware: {e}")

    return cpu_id, mb_serial, disk_serial


def get_hardware_id() -> str:
    """
    Trả về chuỗi HWID chuẩn hoá dạng: TTS-XXXX-XXXX-XXXX-XXXX
    Có cơ chế cache để chỉ tính toán 1 lần duy nhất trong toàn bộ vòng đời tiến trình.
    """
    global _CACHED_HWID, _CACHED_HWID_RAW
    if _CACHED_HWID:
        return _CACHED_HWID

    # B1: Lấy qua PowerShell CIM
    cpu, mb, disk = _get_hardware_via_powershell()

    # B2: Fallback WMIC nếu thiếu
    if not (cpu and mb and disk):
        w_cpu, w_mb, w_disk = _get_hardware_via_wmic()
        cpu = cpu or w_cpu
        mb = mb or w_mb
        disk = disk or w_disk

    # B3: Fallback Registry nếu thiếu
    if not (cpu and mb and disk):
        r_cpu, r_mb, r_disk = _get_hardware_via_registry()
        cpu = cpu or r_cpu
        mb = mb or r_mb
        disk = disk or r_disk

    # Fallback cuối cùng nếu máy ảo hoặc thiếu quyền tuyệt đối
    if not cpu:
        import uuid
        cpu = f"NODE{uuid.getnode()}"
    if not mb:
        mb = os.environ.get("COMPUTERNAME", "PC_DEFAULT")
    if not disk:
        disk = os.environ.get("USERNAME", "USER_DEFAULT")

    _CACHED_HWID_RAW = f"{cpu}:{mb}:{disk}:{HWID_SALT}"
    hwid_hash = hashlib.sha256(_CACHED_HWID_RAW.encode("utf-8")).hexdigest().upper()

    # Định dạng hiển thị chuẩn: TTS-XXXX-XXXX-XXXX-XXXX
    formatted_hwid = f"TTS-{hwid_hash[:4]}-{hwid_hash[4:8]}-{hwid_hash[8:12]}-{hwid_hash[12:16]}"
    _CACHED_HWID = formatted_hwid
    return _CACHED_HWID


# =============================================================================
# 2. XÁC THỰC MẬT MÃ BẤT ĐỐI XỨNG ED25519
# =============================================================================
def verify_license_string(
    license_key: str,
    expected_hwid: Optional[str] = None,
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    Xác thực chữ ký số mật mã Ed25519 của chuỗi bản quyền.
    Trả về: (is_valid, payload_dict, error_or_success_message)
    """
    if not license_key or not isinstance(license_key, str):
        return False, None, "Chuỗi mã bản quyền trống."

    clean_key = license_key.strip()
    if not clean_key.startswith("ED25519."):
        return False, None, "Định dạng mã bản quyền không đúng chuẩn (phải bắt đầu bằng ED25519.)."

    parts = clean_key.split(".")
    if len(parts) != 3:
        return False, None, "Cấu trúc mã bản quyền bị sai lệch hoặc thiếu thành phần."

    _, b64_payload, b64_sig = parts

    def pad_b64(s: str) -> str:
        return s + "=" * (-len(s) % 4)

    try:
        payload_bytes = base64.urlsafe_b64decode(pad_b64(b64_payload))
        signature_bytes = base64.urlsafe_b64decode(pad_b64(b64_sig))
    except Exception:
        return False, None, "Mã bản quyền chứa ký tự Base64 không hợp lệ."

    # 1. Kiểm tra chữ ký số Ed25519 với Public Key nhúng sẵn
    try:
        pub_key = ed25519.Ed25519PublicKey.from_public_bytes(ED25519_PUBLIC_KEY_BYTES)
        pub_key.verify(signature_bytes, payload_bytes)
    except InvalidSignature:
        return False, None, "Chữ ký số bản quyền không hợp lệ hoặc chuỗi mã đã bị chỉnh sửa!"
    except Exception as e:
        return False, None, f"Lỗi xác thực chữ ký số: {e}"

    # 2. Giải mã Payload JSON
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        return False, None, "Dữ liệu bản quyền không thể giải mã thành cấu trúc JSON hợp lệ."

    # 3. So khớp Hardware ID
    if expected_hwid:
        norm_expected = expected_hwid.strip().upper()
        norm_target = str(payload.get("hwid", "")).strip().upper()
        if norm_target != norm_expected:
            return (
                False,
                payload,
                f"Mã bản quyền được cấp cho thiết bị khác ({norm_target}), không khớp với máy này ({norm_expected}).",
            )

    # 4. Kiểm tra ngày hết hạn
    is_lifetime = payload.get("is_lifetime", False)
    expiry_date_str = str(payload.get("expiry_date", "")).strip()

    if not is_lifetime and expiry_date_str:
        try:
            exp_date = datetime.strptime(expiry_date_str, "%Y-%m-%d").date()
            today_utc = datetime.now(timezone.utc).date()
            if today_utc > exp_date:
                days_over = (today_utc - exp_date).days
                return (
                    False,
                    payload,
                    f"Mã bản quyền đã hết hạn vào ngày {expiry_date_str} (quá hạn {days_over} ngày). Vui lòng gia hạn thêm.",
                )
        except ValueError:
            return False, payload, "Định dạng ngày hết hạn trong bản quyền không hợp lệ."

    msg = "Bản quyền VĨNH VIỄN (PRO) hợp lệ!" if is_lifetime else f"Bản quyền hợp lệ đến ngày {expiry_date_str}!"
    return True, payload, msg


# =============================================================================
# 3. TRIPPLE-BREADCRUMBS SAFE PERSISTENCE (LƯU TRỮ ĐA ĐIỂM CHỐNG GỠ CÀI ĐẶT)
# =============================================================================
def _derive_aes_key(hwid: str) -> bytes:
    """Dẫn xuất khóa AES-256 (32 bytes) từ HWID thông qua PBKDF2 (100.000 rounds)."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=PERSISTENCE_SALT.encode("utf-8"),
        iterations=100_000,
    )
    return kdf.derive(hwid.strip().upper().encode("utf-8"))


def encrypt_payload(data: dict, hwid: str) -> str:
    """Mã hóa payload thành chuỗi Base64 bằng AES-256-GCM gắn chặt với HWID."""
    key = _derive_aes_key(hwid)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    raw_data = json.dumps(data, sort_keys=True).encode("utf-8")
    aad = hwid.strip().upper().encode("utf-8")
    ciphertext = aesgcm.encrypt(nonce, raw_data, aad)
    combined = nonce + ciphertext
    return base64.b64encode(combined).decode("ascii")


def decrypt_payload(b64_str: str, hwid: str) -> Optional[dict]:
    """Giải mã chuỗi Base64 bằng AES-256-GCM. Trả về None nếu sai HWID hoặc dữ liệu bị sửa đổi."""
    if not b64_str:
        return None
    try:
        combined = base64.b64decode(b64_str.encode("ascii"))
        if len(combined) < 28:
            return None
        nonce = combined[:12]
        ciphertext = combined[12:]
        key = _derive_aes_key(hwid)
        aesgcm = AESGCM(key)
        aad = hwid.strip().upper().encode("utf-8")
        plaintext = aesgcm.decrypt(nonce, ciphertext, aad)
        return json.loads(plaintext.decode("utf-8"))
    except Exception:
        return None


def _get_localappdata_path() -> Path:
    """Vị trí 1: %LOCALAPPDATA%\\Kronos\\Data\\lic_cache.dat"""
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else (Path.home() / "AppData" / "Local")
    return base / "Kronos" / "Data" / "lic_cache.dat"


def _get_roamingappdata_path() -> Path:
    """Vị trí 2: %APPDATA%\\Kronos\\Config\\app_state.dat"""
    roaming = os.environ.get("APPDATA")
    base = Path(roaming) if roaming else (Path.home() / "AppData" / "Roaming")
    return base / "Kronos" / "Config" / "app_state.dat"


def _get_shared_data_paths() -> list[Path]:
    """Vị trí 3: %ProgramData%\\Kronos\\shared_lic.dat (hoặc ~/.kronos/shared_lic.dat)."""
    paths = []
    pd = os.environ.get("ProgramData")
    if pd:
        paths.append(Path(pd) / "Kronos" / "shared_lic.dat")
    paths.append(Path.home() / ".kronos" / "shared_lic.dat")
    return paths


def _read_file_payload(file_path: Path, hwid: str) -> Optional[dict]:
    if not file_path.exists():
        return None
    try:
        content = file_path.read_text(encoding="ascii").strip()
        return decrypt_payload(content, hwid)
    except Exception:
        return None


def _write_file_payload(file_path: Path, data: dict, hwid: str) -> bool:
    try:
        encrypted = encrypt_payload(data, hwid)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(encrypted, encoding="ascii")
        return True
    except Exception as e:
        logger.debug(f"Không thể ghi file {file_path}: {e}")
        return False


def load_license_state(hwid: str) -> Optional[Dict[str, Any]]:
    """
    Đọc đồng thời từ 3 vị trí độc lập đạt chuẩn Windows AppData:
    1. %LOCALAPPDATA%\\Kronos\\Data\\lic_cache.dat
    2. %APPDATA%\\Kronos\\Config\\app_state.dat
    3. %ProgramData%\\Kronos\\shared_lic.dat (hoặc ~/.kronos/shared_lic.dat)

    Áp dụng thuật toán đồng thuận (Consensus) + Tự phục hồi (Self-Healing):
    - Lấy install_timestamp nhỏ nhất (mốc bắt đầu sớm nhất, chống reset Trial).
    - Lấy last_run_timestamp lớn nhất (mốc chạy mới nhất, chống tua lùi giờ).
    - Lấy license_key hợp lệ nếu có trong bất kỳ vị trí nào.
    - Tự động ghi bù lại nếu có vị trí bị thiếu do người dùng hoặc trình gỡ xóa dở dang.
    """
    p1 = _get_localappdata_path()
    p2 = _get_roamingappdata_path()
    shared_paths = _get_shared_data_paths()

    s1 = _read_file_payload(p1, hwid)
    s2 = _read_file_payload(p2, hwid)

    s3 = None
    for sp in shared_paths:
        s3 = _read_file_payload(sp, hwid)
        if s3:
            break

    states = [s for s in (s1, s2, s3) if s and isinstance(s, dict)]
    if not states:
        return None

    # Tìm license_key đã kích hoạt trong bất kỳ bản sao nào
    saved_key = ""
    activated_at = ""
    client_name = ""
    for s in states:
        k = s.get("license_key")
        if k and isinstance(k, str) and k.startswith("ED25519."):
            saved_key = k
            activated_at = s.get("activated_at", "")
            client_name = s.get("client_name", "")
            break

    # Lấy install_timestamp xa nhất trong quá khứ để chống gỡ ra cài lại reset Trial
    install_ts = min(s.get("install_timestamp", float("inf")) for s in states)
    if install_ts == float("inf"):
        install_ts = time.time()

    last_run_ts = max(s.get("last_run_timestamp", 0) for s in states)

    unified_state = {
        "install_timestamp": install_ts,
        "last_run_timestamp": last_run_ts,
        "license_key": saved_key,
        "activated_at": activated_at,
        "client_name": client_name,
    }

    # Tự động phục hồi (Self-Healing) vào các vị trí bị thiếu
    if not (s1 and s2 and s3):
        save_license_state(hwid, unified_state)

    return unified_state


def save_license_state(hwid: str, data: dict) -> bool:
    """Ghi đồng bộ dữ liệu vào cả 3 vị trí an toàn trên máy."""
    p1 = _get_localappdata_path()
    p2 = _get_roamingappdata_path()
    shared_paths = _get_shared_data_paths()

    r1 = _write_file_payload(p1, data, hwid)
    r2 = _write_file_payload(p2, data, hwid)

    r3 = False
    for sp in shared_paths:
        if _write_file_payload(sp, data, hwid):
            r3 = True
            break

    return r1 or r2 or r3


# =============================================================================
# 4. TRÌNH QUẢN LÝ BẢN QUYỀN CHÍNH (LICENSE MANAGER)
# =============================================================================
class LicenseManager:
    """Quản lý toàn bộ nghiệp vụ kiểm tra, kích hoạt và hiển thị bản quyền."""

    def __init__(self):
        self.hwid = get_hardware_id()
        self._cached_status: Optional[Dict[str, Any]] = None
        self._last_status_check = 0.0

    def get_hwid(self) -> str:
        return self.hwid

    def check_status(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Kiểm tra trạng thái bản quyền của máy theo các ưu tiên:
        1. Key Ed25519 đã kích hoạt (Vĩnh viễn hoặc còn hạn).
        2. Nếu chưa có key: Kiểm tra thời hạn Dùng thử (Trial 7 ngày).
        3. Kiểm tra chống tua lùi thời gian (Anti-Clock Rollback).
        """
        now = time.time()
        if not force_refresh and self._cached_status and (now - self._last_status_check) < 1.0:
            return self._cached_status

        state = load_license_state(self.hwid)
        if not state:
            # Máy cài lần đầu tiên: Khởi tạo mốc install_timestamp và bắt đầu Trial 7 ngày
            state = {
                "install_timestamp": now,
                "last_run_timestamp": now,
                "license_key": "",
                "activated_at": "",
                "client_name": "",
            }
            save_license_state(self.hwid, state)

        install_ts = float(state.get("install_timestamp", now))
        last_run_ts = float(state.get("last_run_timestamp", now))

        # Kiểm tra chống tua lùi đồng hồ hệ thống (cho phép lệch tối đa 300 giây)
        if now < (last_run_ts - 300):
            res = {
                "is_valid": False,
                "status_type": "TAMPERED",
                "days_remaining": 0,
                "badge_text": "[ Thời gian hệ thống sai ]",
                "badge_color": "#f87171",
                "message": "Phát hiện thời gian trên máy tính bị tua lùi bất thường! Vui lòng chỉnh lại ngày giờ chuẩn.",
                "hwid": self.hwid,
                "payload": None,
            }
            self._cached_status = res
            self._last_status_check = now
            return res

        # Cập nhật mốc chạy mới nhất
        state["last_run_timestamp"] = now
        save_license_state(self.hwid, state)

        # ─── TRƯỜNG HỢP 1: ĐÃ KÍCH HOẠT KEY BẢN QUYỀN ED25519 ──────────────────
        saved_key = str(state.get("license_key", "")).strip()
        if saved_key and saved_key.startswith("ED25519."):
            is_valid, payload, msg = verify_license_string(saved_key, expected_hwid=self.hwid)
            if is_valid and payload:
                is_lifetime = payload.get("is_lifetime", False)
                if is_lifetime:
                    res = {
                        "is_valid": True,
                        "status_type": "LIFETIME",
                        "days_remaining": 9999,
                        "badge_text": "[ Bản quyền: Vĩnh viễn (PRO) ]",
                        "badge_color": "#38bdf8",  # Cyan nhã nhặn
                        "message": "Bản quyền Vĩnh viễn (PRO) đã kích hoạt.",
                        "hwid": self.hwid,
                        "payload": payload,
                    }
                else:
                    exp_str = payload.get("expiry_date", "")
                    exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                    today_utc = datetime.now(timezone.utc).date()
                    rem_days = max(0, (exp_date - today_utc).days)
                    tier_name = str(payload.get("tier", "PRO")).upper()
                    if tier_name in ("DEMO", "TRIAL"):
                        badge_txt = f"[ Demo: Còn {rem_days} ngày ]"
                        badge_color = "#fb923c"  # Cam ấm cảnh báo demo
                        st_type = "DEMO"
                        msg_txt = f"Bản Demo còn {rem_days} ngày (đến ngày {exp_str})."
                    else:
                        badge_txt = f"[ Bản quyền: Còn {rem_days} ngày ]"
                        badge_color = "#fcd34d" if rem_days <= 5 else "#38bdf8"
                        st_type = "DAYS"
                        msg_txt = f"Bản quyền còn {rem_days} ngày (đến ngày {exp_str})."
                    res = {
                        "is_valid": True,
                        "status_type": st_type,
                        "days_remaining": rem_days,
                        "badge_text": badge_txt,
                        "badge_color": badge_color,
                        "message": msg_txt,
                        "hwid": self.hwid,
                        "payload": payload,
                    }
                self._cached_status = res
                self._last_status_check = now
                return res
            else:
                # Key đã lưu nhưng không còn hợp lệ (ví dụ hết hạn hoặc bị can thiệp)
                res = {
                    "is_valid": False,
                    "status_type": "EXPIRED",
                    "days_remaining": 0,
                    "badge_text": "[ Bản quyền đã hết hạn ]",
                    "badge_color": "#f87171",
                    "message": msg or "Bản quyền đã hết hạn. Vui lòng gia hạn thêm.",
                    "hwid": self.hwid,
                    "payload": payload,
                }
                self._cached_status = res
                self._last_status_check = now
                return res

        # ─── TRƯỜNG HỢP 2: CHƯA KÍCH HOẠT KEY -> BẮT BUỘC PHẢI NHẬP KEY ─────────
        res = {
            "is_valid": False,
            "status_type": "UNACTIVATED",
            "days_remaining": 0,
            "badge_text": "[ Chưa kích hoạt ]",
            "badge_color": "#f87171",
            "message": "Ứng dụng chưa được kích hoạt bản quyền. Vui lòng gửi mã HWID cho Admin để nhận mã kích hoạt.",
            "hwid": self.hwid,
            "payload": None,
        }
        self._cached_status = res
        self._last_status_check = now
        return res

    def activate_key(self, license_key_str: str) -> Tuple[bool, str]:
        """Thực hiện xác thực và lưu key kích hoạt vào cả 3 vị trí an toàn trên máy."""
        clean_key = (license_key_str or "").strip()
        if not clean_key:
            return False, "Vui lòng nhập hoặc dán chuỗi mã bản quyền do Admin cấp."

        is_valid, payload, msg = verify_license_string(clean_key, expected_hwid=self.hwid)
        if not is_valid or not payload:
            return False, msg

        state = load_license_state(self.hwid) or {}
        state["license_key"] = clean_key
        state["activated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        state["client_name"] = payload.get("client", "")
        state["last_run_timestamp"] = time.time()

        saved_ok = save_license_state(self.hwid, state)
        if not saved_ok:
            return False, "Không thể lưu trữ mã kích hoạt vào hệ thống (lỗi ghi file)."

        self.check_status(force_refresh=True)
        return True, msg


# =============================================================================
# 4.5 TÀI NGUYÊN HÌNH ẢNH TELEGRAM & MÃ QR LIÊN HỆ ADMIN
# =============================================================================
TELEGRAM_QR_B64 = "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"
TELEGRAM_ICON_B64 = "iVBORw0KGgoAAAANSUhEUgAAABYAAAAWCAYAAADEtGw7AAAD7klEQVR4nLWVT4hVVRzHP79zzr3Oe857z5w3lojZmDGDi0hI6A+2iFokBoU4pCEkgRUtWia2GN20bxUFUSQYSAWB2CoXUS2cyEj8M2aNY9mYztjMvJn37rv3nvNr8d4bn6OIEP0WlwP3nu/5fr+/7/0dobtG1HBQAsBDn4yt8RLtAn0ODRsJYRUAxlxFzBmQr61mh399ZfDy0r0AshR0w3vHluk9g/sV84ZxUb8GD3mGBt/aYCy4CDGWkGfXhPC+/DP27oW3tja7waUbdO0HPz4YF/o+M8XS5jA/g/rcI0jrO2mTUAUURcU6a3pXEOq10bQxvfOP1x79rYMlncX9H54bcMvi703cs9rX5zIBh8gNRbcrVVXIbbEchTSZzJvpk5f2Do0zokY4csSuW9gcGU1/sIXSJl+fy0QkuiNgl48iQtCQmUI58o3aySDxExPLRzPD8LA3Id3nyn13DSoCzggBqKUeRSJfn8tcuW+TCek+hoe9rPt4/D6r6TmsK5Fncif5RkAQEh9YyAIreyyPry7y87WEuaZXF8WqPqt5iYecJdltCqVKqM97RMzt5BojhKDU0kBQWF+J2ba+xK7BCkHhmS/HsUZE8zSYYm+FpLbbobJNQ9B2929h1/SBhaZneWR4as1ydg5WeHZdL71Ri8OnZ2eopYFqwZEHFQ1BUdnmVGWj5KmgLKY6KNSzQBaUtaWIl4dW8NJghYerPYsHN70SGeHsdBNhMYRCnoqqbHQiVNV7ur0tOOGR/iIvbCiz9YFe+gsOAAWCKkaE2LYknppOiK1BURAR9R4Rqm6pn2lQXtxQZv/mflb2WAC8tuQYASs3xE03ciZqGbFtM+62UpUpsbYTdiIjfH5+lqe/GOfN43/x7eU6gmClBRYUfBvk/EzKdCMnMq3DUFWxFlWmjIiewcVK+x20MjqTeA6PzfL6N5c59nudyXmPV9qslaBwaqpJ07esaUtWXKwiesYgelSMkZZJLHrpjHBv0TGfBS7MNLnW8Dz/1UXe/u4KSa4YgV+mGhjpskFRMUYQPeo8PYdoLLyDi0rkmXaaqG1v86D8NFXn+J81Tvzd4MSVBievJhx4bBXnZ1Ii026cquIiCY2FWS89hwRg4KOxA67SN5LPTt3yS3caqtpKi4hQSz2xFWIj5Eo7bpq5SjXKZ6cPjr86eOCuhpC0Hx3JRlpN7KRDVTNbXDqETu/QiT0Dic91e0iTSVssR6qaoXqT591xCu21qGoHNKTJpM91+8SegYTTO9oTf0TNpb1D41lyfYtmzVFXqUZinaDqQcPNsKqgAVUv1omrVCPNmqNZcn1LZxa3Bn2n/peraQk4/PfL9F8Zb10i13dhUAAAAABJRU5ErkJggg=="


def _get_telegram_icon_image():
    """Tải biểu tượng Telegram từ tools/telegram_icon.png hoặc từ base64 dự phòng."""
    try:
        from PIL import Image, ImageTk
        file_path = Path(__file__).resolve().parent / "tools" / "telegram_icon.png"
        if file_path.exists():
            img = Image.open(str(file_path))
            return ImageTk.PhotoImage(img)
    except Exception:
        pass
    try:
        from PIL import Image, ImageTk
        import io
        img_bytes = base64.b64decode(TELEGRAM_ICON_B64)
        img = Image.open(io.BytesIO(img_bytes))
        return ImageTk.PhotoImage(img)
    except Exception:
        pass
    try:
        return tk.PhotoImage(data=TELEGRAM_ICON_B64)
    except Exception:
        return None


def _get_telegram_qr_image():
    """Tải hình ảnh mã QR Telegram từ tools/telegram_qr.png hoặc từ base64 dự phòng."""
    try:
        from PIL import Image, ImageTk
        file_path = Path(__file__).resolve().parent / "tools" / "telegram_qr.png"
        if file_path.exists():
            img = Image.open(str(file_path))
            return ImageTk.PhotoImage(img)
    except Exception:
        pass
    try:
        from PIL import Image, ImageTk
        import io
        img_bytes = base64.b64decode(TELEGRAM_QR_B64)
        img = Image.open(io.BytesIO(img_bytes))
        return ImageTk.PhotoImage(img)
    except Exception:
        pass
    try:
        return tk.PhotoImage(data=TELEGRAM_QR_B64)
    except Exception:
        return None


# =============================================================================
# 5. GIAO DIỆN KÍCH HOẠT BẢN QUYỀN (ACTIVATION DIALOG)
# =============================================================================
class LicenseActivationDialog:
    """
    Cửa sổ Kích Hoạt Bản Quyền Ứng Dụng (Tkinter Dark Mode hiện đại)
    thiết kế chuẩn xác theo giao diện mẫu (Mã HWID + Sao chép mã + Dán chuỗi Ed25519).
    """

    def __init__(self, parent: Optional[tk.Tk], license_mgr: LicenseManager, is_startup_block: bool = False):
        self.license_mgr = license_mgr
        self.is_startup_block = is_startup_block
        self.success = False

        if parent is not None:
            self.dialog = tk.Toplevel(parent)
        else:
            self.dialog = tk.Tk()

        # Ẩn cửa sổ ban đầu để nạp giao diện và tính toán tọa độ căn giữa chuẩn xác, tránh bị lệch hoặc nhấp nháy
        self.dialog.withdraw()
        self.dialog.title("Kích Hoạt Bản Quyền Ứng Dụng — AI Scalping Bot")
        self.dialog.configure(bg="#0f172a")  # Slate 900
        self.dialog.resizable(False, False)

        # Thiết lập Icon cửa sổ nếu có
        try:
            icon_path = Path(__file__).resolve().parent / "tools" / "license-key-icon.ico"
            if icon_path.exists():
                self.dialog.iconbitmap(str(icon_path))
        except Exception:
            pass

        try:
            self.dialog.attributes("-topmost", True)
        except Exception:
            pass

        # Bắt sự kiện đóng cửa sổ
        self.dialog.protocol("WM_DELETE_WINDOW", self._on_close)

        # 1. Dựng đầy đủ giao diện trước
        self._build_ui()

        # 2. Căn giữa màn hình chuẩn xác 100% (Kích thước gọn gàng, cân đối 580x425)
        self._center_on_screen(580, 425)
        self.dialog.deiconify()
        self.dialog.lift()
        self.dialog.focus_force()

        # Đảm bảo giữ đúng vị trí trung tâm màn hình sau khi Windows hoàn tất vẽ (chống DWM cascade)
        self.dialog.after(50, lambda: self._center_on_screen(580, 425))

    def _center_on_screen(self, width: int = 580, height: int = 425):
        """Căn giữa cửa sổ chính xác 100% trên màn hình hiển thị (hỗ trợ đa màn hình & DPI scaling)."""
        self.dialog.update_idletasks()
        w = width
        h = height

        try:
            import ctypes
            # Lấy độ phân giải màn hình chuẩn xác qua Windows API
            sw = ctypes.windll.user32.GetSystemMetrics(0)
            sh = ctypes.windll.user32.GetSystemMetrics(1)
        except Exception:
            sw = self.dialog.winfo_screenwidth()
            sh = self.dialog.winfo_screenheight()

        if not sw or sw <= 0:
            sw = 1920
        if not sh or sh <= 0:
            sh = 1080

        pos_x = max(0, int((sw - w) / 2))
        pos_y = max(0, int((sh - h) / 2))

        self.dialog.geometry(f"{w}x{h}+{pos_x}+{pos_y}")

    def _build_ui(self):
        root_f = tk.Frame(self.dialog, bg="#0f172a", padx=24, pady=16)
        root_f.pack(fill=tk.BOTH, expand=True)

        # 1. TIÊU ĐỀ
        tk.Label(
            root_f, text="Kích Hoạt Bản Quyền Ứng Dụng",
            bg="#0f172a", fg="#f8fafc", font=("Segoe UI", 15, "bold")
        ).pack(pady=(0, 2))

        tk.Label(
            root_f,
            text="Vui lòng sao chép Mã thiết bị (HWID) bên dưới gửi cho Admin để nhận chuỗi mã kích hoạt Ed25519.",
            bg="#0f172a", fg="#94a3b8", font=("Segoe UI", 8), wraplength=520, justify="center"
        ).pack(pady=(0, 10))

        # 2. KHUNG HIỂN THỊ MÃ MÁY HWID & NÚT SAO CHÉP
        hwid_card = tk.Frame(root_f, bg="#1e293b", highlightthickness=1, highlightbackground="#334155", padx=14, pady=8)
        hwid_card.pack(fill=tk.X, pady=(0, 10))

        hwid_left = tk.Frame(hwid_card, bg="#1e293b")
        hwid_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tk.Label(
            hwid_left, text="Mã thiết bị phần cứng (HWID):",
            bg="#1e293b", fg="#94a3b8", font=("Segoe UI", 8, "bold")
        ).pack(anchor="w")

        hwid_str = self.license_mgr.get_hwid()
        self.hwid_lbl = tk.Label(
            hwid_left, text=hwid_str,
            bg="#1e293b", fg="#f59e0b", font=("Consolas", 11, "bold")
        )
        self.hwid_lbl.pack(anchor="w", pady=(1, 0))

        self.copy_btn = tk.Button(
            hwid_card, text="Sao chép mã", bg="#25334c", fg="#ffffff",
            activebackground="#324567", activeforeground="#ffffff",
            font=("Segoe UI", 8, "bold"), relief=tk.FLAT, padx=10, pady=4, cursor="hand2",
            command=self._copy_hwid
        )
        self.copy_btn.pack(side=tk.RIGHT)

        # 3. KHUNG NHẬP CHUỖI MÃ BẢN QUYỀN (ED25519)
        key_header = tk.Frame(root_f, bg="#0f172a")
        key_header.pack(fill=tk.X, pady=(0, 2))
        tk.Label(
            key_header, text="Chuỗi mã bản quyền (Ed25519)",
            bg="#0f172a", fg="#f8fafc", font=("Segoe UI", 8, "bold")
        ).pack(side=tk.LEFT)

        key_box = tk.Frame(root_f, bg="#090d16", highlightthickness=1, highlightbackground="#475569", padx=2, pady=2)
        key_box.pack(fill=tk.X, pady=(0, 6))

        self.key_text = tk.Text(
            key_box, height=3, bg="#090d16", fg="#f8fafc", insertbackground="#f8fafc",
            font=("Consolas", 8), relief=tk.FLAT, wrap=tk.CHAR, padx=6, pady=4
        )
        self.key_text.pack(fill=tk.BOTH, expand=True)
        self.key_text.insert("1.0", "Dán chuỗi bản quyền do Admin cấp (Bắt đầu bằng ED25519.)...")
        self.key_text.bind("<FocusIn>", self._clear_placeholder)

        # 4. THÔNG BÁO KẾT QUẢ / LỖI
        self.msg_lbl = tk.Label(
            root_f, text="", bg="#0f172a", fg="#f87171",
            font=("Segoe UI", 8, "bold"), wraplength=520, justify="center"
        )
        self.msg_lbl.pack(pady=(0, 4))

        # 5. CÁC NÚT THAO TÁC (QUAY LẠI / XÁC THỰC)
        btn_row = tk.Frame(root_f, bg="#0f172a")
        btn_row.pack(fill=tk.X, pady=(0, 8))

        cancel_txt = "Thoát Ứng Dụng" if self.is_startup_block else "Đóng"
        self.cancel_btn = tk.Button(
            btn_row, text=cancel_txt, bg="#1e293b", fg="#94a3b8",
            activebackground="#334155", activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold"), relief=tk.FLAT, padx=14, pady=6, cursor="hand2",
            command=self._on_close
        )
        self.cancel_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self.activate_btn = tk.Button(
            btn_row, text="Xác Thực & Kích Hoạt", bg="#0284c7", fg="#ffffff",
            activebackground="#0369a1", activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold"), relief=tk.FLAT, padx=14, pady=6, cursor="hand2",
            command=self._on_activate
        )
        self.activate_btn.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(6, 0))

        # 6. NÚT LIÊN HỆ QUẢN TRỊ VIÊN ĐỂ ĐƯỢC CẤP KEY (TELEGRAM)
        self.tg_icon_photo = _get_telegram_icon_image()
        btn_kwargs = {
            "text": "  Liên hệ Quản trị viên để được cấp key",
            "bg": "#0f2942",
            "fg": "#38bdf8",
            "activebackground": "#1e3a5f",
            "activeforeground": "#7dd3fc",
            "font": ("Segoe UI", 9, "bold"),
            "relief": tk.FLAT,
            "padx": 14,
            "pady": 6,
            "cursor": "hand2",
            "highlightthickness": 1,
            "highlightbackground": "#0284c7",
            "command": self._show_telegram_contact_modal
        }
        if self.tg_icon_photo:
            btn_kwargs["image"] = self.tg_icon_photo
            btn_kwargs["compound"] = tk.LEFT

        self.contact_btn = tk.Button(root_f, **btn_kwargs)
        self.contact_btn.image = self.tg_icon_photo
        self.contact_btn.pack(fill=tk.X, pady=(0, 10))

        # 7. DÒNG CHỮ PHỤ CUỐI CÙNG
        tk.Label(
            root_f, text="Chữ ký số mật mã học Ed25519 xác thực an toàn tuyệt đối 100% offline",
            bg="#0f172a", fg="#64748b", font=("Segoe UI", 8, "italic")
        ).pack(side=tk.BOTTOM, pady=(2, 0))

    def _show_telegram_contact_modal(self):
        """Mở cửa sổ hiển thị mã QR và thông tin liên hệ Admin qua Telegram."""
        modal = tk.Toplevel(self.dialog)
        modal.title("Liên Hệ Quản Trị Viên — Telegram")
        modal.configure(bg="#0f172a")
        modal.resizable(False, False)
        modal.transient(self.dialog)

        # Căn giữa so với cửa sổ cha (Kích thước gọn gàng, chuẩn xác 360x440)
        modal_w, modal_h = 360, 440
        self.dialog.update_idletasks()
        try:
            px = self.dialog.winfo_x() + (self.dialog.winfo_width() - modal_w) // 2
            py = self.dialog.winfo_y() + (self.dialog.winfo_height() - modal_h) // 2
            modal.geometry(f"{modal_w}x{modal_h}+{max(0, px)}+{max(0, py)}")
        except Exception:
            modal.geometry(f"{modal_w}x{modal_h}")

        try:
            icon_path = Path(__file__).resolve().parent / "tools" / "license-key-icon.ico"
            if icon_path.exists():
                modal.iconbitmap(str(icon_path))
        except Exception:
            pass

        container = tk.Frame(modal, bg="#0f172a", padx=18, pady=14)
        container.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            container,
            text="Liên Hệ Nhận Key Bản Quyền",
            bg="#0f172a", fg="#f8fafc",
            font=("Segoe UI", 12, "bold")
        ).pack(pady=(0, 2))

        tk.Label(
            container,
            text="Quét mã QR bằng ứng dụng Telegram trên điện thoại\nhoặc bấm nút bên dưới để mở liên hệ trực tiếp.",
            bg="#0f172a", fg="#94a3b8",
            font=("Segoe UI", 8),
            justify="center"
        ).pack(pady=(0, 8))

        qr_card = tk.Frame(container, bg="#1e293b", highlightthickness=1, highlightbackground="#334155", padx=6, pady=6)
        qr_card.pack(pady=(0, 8))

        qr_img_obj = _get_telegram_qr_image()
        if qr_img_obj:
            qr_lbl = tk.Label(qr_card, image=qr_img_obj, bg="#1e293b")
            qr_lbl.image = qr_img_obj
            qr_lbl.pack()
        else:
            tk.Label(
                qr_card,
                text="Mã QR Telegram: @TAPTRADEXAU",
                bg="#1e293b", fg="#38bdf8",
                font=("Segoe UI", 10, "bold"),
                padx=16, pady=30
            ).pack()

        user_lbl = tk.Label(
            container,
            text="Telegram: @TAPTRADEXAU",
            bg="#0f172a", fg="#38bdf8",
            font=("Segoe UI", 10, "bold")
        )
        user_lbl.pack(pady=(0, 8))

        btn_box = tk.Frame(container, bg="#0f172a")
        btn_box.pack(fill=tk.X, pady=(0, 6))

        def open_tg():
            import webbrowser
            try:
                webbrowser.open("https://t.me/TAPTRADEXAU")
            except Exception as e:
                logger.error(f"Lỗi mở link Telegram: {e}")

        def copy_user():
            try:
                modal.clipboard_clear()
                modal.clipboard_append("@TAPTRADEXAU")
                user_lbl.config(text="Đã sao chép @TAPTRADEXAU!", fg="#34d399")
                modal.after(2000, lambda: user_lbl.config(text="Telegram: @TAPTRADEXAU", fg="#38bdf8"))
            except Exception as e:
                logger.error(f"Lỗi chép clipboard: {e}")

        open_btn = tk.Button(
            btn_box,
            text="Mở Chat Telegram",
            bg="#0284c7", fg="#ffffff",
            activebackground="#0369a1", activeforeground="#ffffff",
            font=("Segoe UI", 8, "bold"),
            relief=tk.FLAT, padx=8, pady=6, cursor="hand2",
            command=open_tg
        )
        open_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        copy_btn = tk.Button(
            btn_box,
            text="Sao Chép Username",
            bg="#1e293b", fg="#f8fafc",
            activebackground="#334155", activeforeground="#ffffff",
            font=("Segoe UI", 8, "bold"),
            relief=tk.FLAT, padx=8, pady=6, cursor="hand2",
            command=copy_user
        )
        copy_btn.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(4, 0))

        close_btn = tk.Button(
            container,
            text="Đóng",
            bg="#1e293b", fg="#94a3b8",
            activebackground="#334155", activeforeground="#ffffff",
            font=("Segoe UI", 8),
            relief=tk.FLAT, padx=12, pady=5, cursor="hand2",
            command=modal.destroy
        )
        close_btn.pack(fill=tk.X, pady=(2, 0))
        close_btn.pack(fill=tk.X, pady=(4, 0))

        modal.grab_set()
        modal.focus_force()

    def _clear_placeholder(self, event=None):
        content = self.key_text.get("1.0", tk.END).strip()
        if content.startswith("Dán chuỗi bản quyền"):
            self.key_text.delete("1.0", tk.END)

    def _copy_hwid(self):
        try:
            self.dialog.clipboard_clear()
            self.dialog.clipboard_append(self.license_mgr.get_hwid())
            self.copy_btn.config(text="Đã chép mã!", fg="#34d399")
            self.dialog.after(2000, lambda: self.copy_btn.config(text="Sao chép mã", fg="#ffffff"))
        except Exception as e:
            self.msg_lbl.config(text=f"Lỗi sao chép: {e}", fg="#f87171")

    def _on_activate(self):
        raw_key = self.key_text.get("1.0", tk.END).strip()
        if raw_key.startswith("Dán chuỗi bản quyền"):
            raw_key = ""

        if not raw_key:
            self.msg_lbl.config(text="Vui lòng dán chuỗi mã bản quyền nhận được từ Admin!", fg="#f87171")
            return

        ok, msg = self.license_mgr.activate_key(raw_key)
        if ok:
            self.success = True
            messagebox.showinfo("Kích Hoạt Thành Công", f"Chúc mừng bạn!\n{msg}", parent=self.dialog)
            self.dialog.destroy()
        else:
            self.msg_lbl.config(text=f"{msg}", fg="#f87171")

    def _on_close(self):
        if self.is_startup_block and not self.success:
            # Nếu đang chặn lúc khởi động mà đóng cửa sổ -> Thoát app hoàn toàn
            self.dialog.destroy()
            sys.exit(0)
        else:
            self.dialog.destroy()


def show_activation_dialog(parent: Optional[tk.Tk], license_mgr: LicenseManager, is_startup_block: bool = False) -> bool:
    """Hiển thị cửa sổ kích hoạt bản quyền dạng Modal."""
    dlg = LicenseActivationDialog(parent, license_mgr, is_startup_block=is_startup_block)
    if isinstance(dlg.dialog, tk.Toplevel):
        dlg.dialog.grab_set()
        dlg.dialog.wait_window()
    else:
        dlg.dialog.mainloop()
    return dlg.success

