import ctypes
import io
import json
import logging
import os
import queue
import struct
import sys
import tempfile
import threading
import types
import webbrowser
import winreg
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

# 打包成 exe 后，数据和日志放在 exe 所在目录；
# 直接运行脚本时，放在 CursorHub.py 同目录。
APP_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
THEMES_FILE = APP_DIR / "themes.json"
SETTINGS_FILE = APP_DIR / "settings.json"
LOG_FILE = APP_DIR / "CursorHub.log"

# 窗口 / exe 图标文件名
ICON_FILE_NAME = "icon.ico"

# 先配置日志，再导入 customtkinter：打包后如果资源缺失，也能在日志里留下线索
try:
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        encoding="utf-8",
    )
except Exception:
    logging.basicConfig(level=logging.INFO)

try:
    import customtkinter as ctk
except Exception:
    logging.exception("导入 customtkinter 失败")
    raise

try:
    from PIL import Image, ImageDraw
except Exception:
    Image = None
    ImageDraw = None
    logging.exception("导入 Pillow 失败，图片预览不可用")

try:
    # ani_file 0.1.3 仍在使用 Pillow 10 已移除的 Image.ANTIALIAS，
    # 这里补上等价别名后再导入，兼容 Pillow 10+。
    if Image is not None and not hasattr(Image, "ANTIALIAS"):
        Image.ANTIALIAS = Image.Resampling.LANCZOS

    from ani_file import ani_file as ani_file_lib
except ModuleNotFoundError:
    ani_file_lib = None
    logging.info("未安装 ani_file，.ani 预览将使用内置 RIFF 解析器")
except Exception:
    ani_file_lib = None
    logging.exception("导入 ani_file 失败，.ani 预览将使用内置 RIFF 解析器")

CURSORS_KEY = r"Control Panel\Cursors"

__version__ = "1.6.0"
GITHUB_URL = "https://github.com/hk-sh123584/CursorHub"

# 应用主题前自动备份当前光标配置，最多保留 20 份
BACKUP_DIR = APP_DIR / "backups"
MAX_BACKUP_FILES = 20

# 外观模式：内部值 -> 界面显示文字
APPEARANCE_LABELS = {
    "dark": "深色",
    "light": "浅色",
    "system": "跟随系统",
}
LABEL_TO_APPEARANCE = {
    label: mode for mode, label in APPEARANCE_LABELS.items()
}

# 应用完成提示：显示文字 -> 停留秒数（0 表示常驻）
APPLY_NOTICE_OPTIONS = {
    "2 秒": 2,
    "4 秒": 4,
    "6 秒": 6,
    "10 秒": 10,
    "常驻": 0,
}
DEFAULT_APPLY_NOTICE_SECONDS = 4
NOTICE_SECONDS_TO_LABEL = {
    seconds: label for label, seconds in APPLY_NOTICE_OPTIONS.items()
}

# LoadImageW 参数
IMAGE_CURSOR = 2
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040

# 图片预览：CTkImage 显示尺寸（像素）
PREVIEW_IMAGE_SIZE = 64

# PNG 文件魔数，用于识别 PNG-in-CUR 光标帧
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# 预览窗口文件列表的配色（浅色模式, 深色模式）
ROW_BASE_COLOR = ("#FFFFFF", "#27272A")
ROW_HOVER_COLOR = ("#EFF6FF", "#3A3A44")
ROW_BORDER_COLOR = ("#E4E4E7", "#3F3F46")
ROW_SELECTED_BORDER_COLOR = ("#2563EB", "#60A5FA")

# 显式声明参数/返回值类型，避免 64 位系统上句柄被截断
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.LoadImageW.argtypes = [
    wintypes.HINSTANCE,
    wintypes.LPCWSTR,
    wintypes.UINT,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.LoadImageW.restype = wintypes.HANDLE

user32.SetSystemCursor.argtypes = [wintypes.HANDLE, wintypes.DWORD]
user32.SetSystemCursor.restype = wintypes.BOOL

user32.DestroyCursor.argtypes = [wintypes.HANDLE]
user32.DestroyCursor.restype = wintypes.BOOL

# 判断主窗口是否在前台（用于切回程序时自动检查主题路径）
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND

# 17 个标准光标角色（注册表值名）
CURSOR_ROLES = [
    "Arrow",
    "Help",
    "AppStarting",
    "Wait",
    "Crosshair",
    "IBeam",
    "NWPen",
    "No",
    "SizeNS",
    "SizeWE",
    "SizeNWSE",
    "SizeNESW",
    "SizeAll",
    "UpArrow",
    "Hand",
    "Pin",
    "Person",
]

# Windows 内置默认光标路径（Windows Aero 方案）
# Windows 默认方案里 Crosshair/IBeam 是空值，这里用系统自带的
# cross_r.cur / beam_r.cur 作为等效的显式文件。
SYSTEM_DEFAULT_CURSOR_PATHS = {
    "Arrow": r"C:\Windows\Cursors\aero_arrow.cur",
    "Help": r"C:\Windows\Cursors\aero_helpsel.cur",
    "AppStarting": r"C:\Windows\Cursors\aero_working.ani",
    "Wait": r"C:\Windows\Cursors\aero_busy.ani",
    "Crosshair": r"C:\Windows\Cursors\cross_r.cur",
    "IBeam": r"C:\Windows\Cursors\beam_r.cur",
    "NWPen": r"C:\Windows\Cursors\aero_pen.cur",
    "No": r"C:\Windows\Cursors\aero_unavail.cur",
    "SizeNS": r"C:\Windows\Cursors\aero_ns.cur",
    "SizeWE": r"C:\Windows\Cursors\aero_ew.cur",
    "SizeNWSE": r"C:\Windows\Cursors\aero_nwse.cur",
    "SizeNESW": r"C:\Windows\Cursors\aero_nesw.cur",
    "SizeAll": r"C:\Windows\Cursors\aero_move.cur",
    "UpArrow": r"C:\Windows\Cursors\aero_up.cur",
    "Hand": r"C:\Windows\Cursors\aero_link.cur",
    "Pin": r"C:\Windows\Cursors\aero_pin.cur",
    "Person": r"C:\Windows\Cursors\aero_person.cur",
}

# 固定显示在主题列表顶部的内置主题
SYSTEM_DEFAULT_THEME = {
    "name": "🖥️ Windows 系统默认",
    "path": None,
    "builtin": True,
}

# 角色 -> 中文显示名（用于预览窗口）
ROLE_DISPLAY_NAMES = {
    "Arrow": "正常选择",
    "Help": "帮助选择",
    "AppStarting": "后台运行",
    "Wait": "忙碌",
    "Crosshair": "精确选择",
    "IBeam": "文本选择",
    "NWPen": "手写",
    "No": "不可用",
    "SizeNS": "垂直调整大小",
    "SizeWE": "水平调整大小",
    "SizeNWSE": "对角线调整大小 1",
    "SizeNESW": "对角线调整大小 2",
    "SizeAll": "移动",
    "UpArrow": "候选选择",
    "Hand": "链接选择",
    "Pin": "位置选择",
    "Person": "人员选择",
}

# 角色 -> SetSystemCursor 使用的系统光标 ID
SYSTEM_CURSOR_IDS = {
    "Arrow": 32512,        # OCR_NORMAL
    "Help": 32651,         # OCR_HELP
    "AppStarting": 32650,  # OCR_APPSTARTING
    "Wait": 32514,         # OCR_WAIT
    "Crosshair": 32515,    # OCR_CROSS
    "IBeam": 32513,        # OCR_IBEAM
    "NWPen": 32631,        # IDC_NWPEN（部分系统可能不支持）
    "No": 32648,           # OCR_NO
    "SizeNS": 32645,       # OCR_SIZENS
    "SizeWE": 32644,       # OCR_SIZEWE
    "SizeNWSE": 32642,     # OCR_SIZENWSE
    "SizeNESW": 32643,     # OCR_SIZENESW
    "SizeAll": 32646,      # OCR_SIZEALL
    "UpArrow": 32516,      # OCR_UP
    "Hand": 32649,         # OCR_HAND
    "Pin": 32671,          # IDC_PIN
    "Person": 32672,       # IDC_PERSON
}

# 文件名（不含扩展名，统一转小写）-> 注册表角色
FILE_STEM_TO_ROLE = {
    "normal": "Arrow",
    "link": "Hand",
    "busy": "Wait",
    "precision": "Crosshair",
    "text": "IBeam",
    "handwriting": "NWPen",
    "unavailable": "No",
    "vertical": "SizeNS",
    "horizontal": "SizeWE",
    "diagonal1": "SizeNWSE",
    "diagonal2": "SizeNESW",
    "move": "SizeAll",
    "alternate": "UpArrow",
    "help": "Help",
    "pin": "Pin",
    "person": "Person",
    "working": "AppStarting",  # 补全第 17 个角色
}


def get_current_cursors():
    """读取并返回 17 个标准角色当前在注册表中的路径。"""
    current = {}

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CURSORS_KEY) as key:
        index = 0
        while True:
            try:
                name, value, value_type = winreg.EnumValue(key, index)
            except OSError:
                break
            index += 1

            # 跳过 (Default) 和 Scheme Source 等非光标角色
            if name == "" or name.lower() == "scheme source":
                continue

            # 只保留 17 个标准角色
            if name.lower() in [role.lower() for role in CURSOR_ROLES]:
                current[name.lower()] = value

    return current


def list_current_cursors():
    """读取并打印 17 个光标角色当前对应的文件路径。"""
    current = get_current_cursors()
    for role in CURSOR_ROLES:
        path = current.get(role.lower())
        print(f"{role:<12} {path if path else '(未设置)'}")


def set_cursor(role, filepath):
    """把指定角色写入注册表（filepath 必须是绝对路径）。"""
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, CURSORS_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, role, 0, winreg.REG_SZ, filepath)


def refresh_cursors(role, filepath):
    """用 SetSystemCursor 立即替换一个系统光标（仅当前会话生效）。"""
    cursor_id = SYSTEM_CURSOR_IDS.get(role)
    if cursor_id is None:
        raise ValueError(f"尚未配置角色 {role} 的系统光标 ID")

    # LR_DEFAULTSIZE：按系统当前光标尺寸加载，而不是按 .ani 原图尺寸
    hcursor = user32.LoadImageW(
        None,
        filepath,
        IMAGE_CURSOR,
        0,
        0,
        LR_LOADFROMFILE | LR_DEFAULTSIZE,
    )
    if not hcursor:
        raise OSError(
            f"LoadImageW 加载失败（错误码 {ctypes.get_last_error()}）：{filepath}"
        )

    if not user32.SetSystemCursor(hcursor, cursor_id):
        error_code = ctypes.get_last_error()
        # 失败时句柄仍归调用方所有，需要释放
        user32.DestroyCursor(hcursor)
        raise OSError(f"SetSystemCursor 失败（错误码 {error_code}）：{role}")

    # 成功后系统会接管并销毁 hcursor，不能再调用 DestroyCursor


def scan_and_map(folder_path, verbose=True):
    """扫描文件夹中的 .cur/.ani，自动匹配注册表角色并返回映射。"""
    folder_path = os.path.abspath(folder_path)

    if not os.path.isdir(folder_path):
        if verbose:
            print(f"[错误] 文件夹不存在：{folder_path}")
            print('获取绝对路径：资源管理器里 Shift + 右键文件夹 -> "复制文件地址"')
        return {}

    if verbose:
        print(f"正在扫描：{folder_path}\n")

    mapped = {}
    unrecognized = []
    cursor_file_count = 0

    for filename in sorted(os.listdir(folder_path)):
        if not filename.lower().endswith((".cur", ".ani")):
            continue

        full_path = os.path.join(folder_path, filename)
        if not os.path.isfile(full_path):
            continue

        cursor_file_count += 1
        stem = os.path.splitext(filename)[0]
        role = FILE_STEM_TO_ROLE.get(stem.lower())

        if role is None:
            unrecognized.append(filename)
            continue

        if role in mapped:
            if verbose:
                print(
                    f"[冲突] {role} 同时匹配到 "
                    f"{os.path.basename(mapped[role])} 和 {filename}，保留第一个"
                )
            continue

        mapped[role] = full_path

    if verbose:
        for role in CURSOR_ROLES:
            if role in mapped:
                print(f"[已匹配] {role:<12} <- {os.path.basename(mapped[role])}")

        missing = [role for role in CURSOR_ROLES if role not in mapped]
        print()
        if missing:
            print(f"[缺失角色] {', '.join(missing)}")
        else:
            print("[缺失角色] 无")

        if unrecognized:
            print(f"[未识别文件] {', '.join(unrecognized)}")

        print(
            f"\n共找到 {cursor_file_count} 个光标文件，"
            f"匹配 {len(mapped)} 个角色，缺失 {len(missing)} 个角色。"
        )

    return mapped


def scan_theme_files(folder_path):
    """扫描主题文件夹，返回用于文字预览的文件明细和完整度信息。"""
    folder_path = os.path.abspath(folder_path)
    info = {
        "path": folder_path,
        "exists": os.path.isdir(folder_path),
        "files": [],
        "matched": {},
        "missing": list(CURSOR_ROLES),
        "unrecognized": [],
    }

    if not info["exists"]:
        return info

    matched = {}
    unrecognized = []
    files = []

    for filename in sorted(os.listdir(folder_path)):
        if not filename.lower().endswith((".cur", ".ani")):
            continue

        full_path = os.path.join(folder_path, filename)
        if not os.path.isfile(full_path):
            continue

        stem = os.path.splitext(filename)[0]
        role = FILE_STEM_TO_ROLE.get(stem.lower())
        files.append({"filename": filename, "role": role})

        if role is None:
            unrecognized.append(filename)
        elif role not in matched:
            matched[role] = filename

    info["files"] = files
    info["matched"] = matched
    info["unrecognized"] = unrecognized
    info["missing"] = [
        role for role in CURSOR_ROLES if role not in matched
    ]
    return info


def apply_theme(folder_path, verbose=True):
    """应用整套主题。返回 (匹配数量, 失败列表)；未找到有效光标时返回 None。"""
    mapping = scan_and_map(folder_path, verbose=verbose)
    if not mapping:
        return None

    missing = [role for role in CURSOR_ROLES if role not in mapping]
    if verbose and missing:
        print(f"\n[注意] 本主题缺少 {len(missing)} 个角色：{', '.join(missing)}")

    # 1. 写入注册表，保证注销/重启后仍然生效
    for role, filepath in mapping.items():
        set_cursor(role, filepath)

    if verbose:
        print("\n注册表写入完成。")

    # 2. 立即替换当前会话的系统光标
    failed = []
    for role, filepath in mapping.items():
        try:
            refresh_cursors(role, filepath)
        except (OSError, ValueError) as exc:
            failed.append((role, str(exc)))

    applied_count = len(mapping) - len(failed)

    if verbose:
        if failed:
            print("\n[部分角色未能立即生效]")
            for role, reason in failed:
                print(f"  - {role}: {reason}")
            print("这些角色已写入注册表，注销或重启后会生效。")
            print(
                f"\n⚠️ 已应用 {applied_count}/{len(mapping)} 个角色，"
                "其余角色重新登录后生效。"
            )
        else:
            print(f"\n✅ 光标主题已成功应用！（{applied_count} 个角色）")

    return len(mapping), failed


def apply_system_default_theme():
    """应用内置 Windows 默认光标。返回 (数量, 失败列表)。"""
    failed = []

    for role, filepath in SYSTEM_DEFAULT_CURSOR_PATHS.items():
        set_cursor(role, filepath)

    for role, filepath in SYSTEM_DEFAULT_CURSOR_PATHS.items():
        try:
            refresh_cursors(role, filepath)
        except (OSError, ValueError) as exc:
            failed.append((role, str(exc)))

    return len(SYSTEM_DEFAULT_CURSOR_PATHS), failed


def read_cursor_registry_values():
    """读取 17 个标准角色在注册表中的值与类型。"""
    values = {}

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CURSORS_KEY) as key:
            for role in CURSOR_ROLES:
                try:
                    value, value_type = winreg.QueryValueEx(key, role)
                except (FileNotFoundError, OSError):
                    continue
                values[role] = {"value": value, "type": value_type}
    except OSError:
        logging.exception("读取光标注册表失败")

    return values


def backup_current_cursors(reason="manual"):
    """把当前光标配置快照到 backups/，返回备份文件路径；失败返回 None。"""
    values = read_cursor_registry_values()
    if not values:
        logging.warning("当前光标注册表为空，跳过备份")
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    backup_file = BACKUP_DIR / f"cursors_{timestamp}.json"
    data = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "reason": reason,
        "values": values,
    }

    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        with open(backup_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except OSError:
        logging.exception("写入光标备份失败：%s", backup_file)
        return None

    _prune_backups()
    logging.info("已备份当前光标配置：%s（%s）", backup_file, reason)
    return backup_file


def _prune_backups():
    """只保留最新的若干份备份。"""
    try:
        backups = sorted(BACKUP_DIR.glob("cursors_*.json"), reverse=True)
        for old_file in backups[MAX_BACKUP_FILES:]:
            old_file.unlink(missing_ok=True)
    except OSError:
        logging.exception("清理旧光标备份失败")


def get_latest_backup():
    """返回最新一份备份文件路径；没有备份时返回 None。"""
    if not BACKUP_DIR.exists():
        return None

    try:
        backups = sorted(BACKUP_DIR.glob("cursors_*.json"), reverse=True)
    except OSError:
        logging.exception("读取备份目录失败：%s", BACKUP_DIR)
        return None

    return backups[0] if backups else None


def get_latest_backup_info():
    """返回 (备份路径, 创建时间文字)；没有备份时返回 None。"""
    backup_path = get_latest_backup()
    if backup_path is None:
        return None

    created_at = None
    try:
        with open(backup_path, "r", encoding="utf-8") as f:
            created_at = json.load(f).get("created_at")
    except (OSError, json.JSONDecodeError):
        logging.exception("读取备份信息失败：%s", backup_path)

    return backup_path, created_at or backup_path.stem


def restore_cursors_from_backup(backup_path=None):
    """从备份恢复光标。返回 (成功数量, 失败列表, 备份路径)。"""
    path = Path(backup_path) if backup_path else get_latest_backup()
    if path is None:
        return 0, [], None

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        logging.exception("读取光标备份失败：%s", path)
        return 0, [], path

    values = data.get("values")
    if not isinstance(values, dict):
        logging.error("备份文件格式不正确：%s", path)
        return 0, [], path

    restored = 0
    failed = []

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            CURSORS_KEY,
            0,
            winreg.KEY_SET_VALUE,
        )
    except OSError as exc:
        logging.exception("打开光标注册表失败")
        return 0, [(role, str(exc)) for role in CURSOR_ROLES], path

    try:
        for role in CURSOR_ROLES:
            entry = values.get(role)
            if not isinstance(entry, dict):
                continue

            filepath = entry.get("value")
            value_type = entry.get("type", winreg.REG_SZ)

            if not isinstance(filepath, str) or not filepath:
                continue

            if not os.path.isfile(filepath):
                failed.append((role, "备份中的光标文件不存在"))
                continue

            try:
                winreg.SetValueEx(key, role, 0, value_type, filepath)
                refresh_cursors(role, filepath)
                restored += 1
            except (OSError, ValueError) as exc:
                failed.append((role, str(exc)))
                logging.exception("恢复光标角色失败：%s", role)
    finally:
        winreg.CloseKey(key)

    logging.info(
        "已从备份恢复光标：%s（成功 %d，失败 %d）",
        path,
        restored,
        len(failed),
    )
    return restored, failed, path


# ---------------------------------------------------------------------------
# 主题列表的本地存储（第 1 步：只做 themes.json 读写，不涉及界面）
# ---------------------------------------------------------------------------

def load_themes():
    """读取 themes.json，返回主题列表；文件不存在时返回空列表。"""
    if not THEMES_FILE.exists():
        return []

    try:
        with open(THEMES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[错误] 无法读取 {THEMES_FILE.name}：{exc}")
        return []

    if not isinstance(data, dict) or not isinstance(data.get("themes"), list):
        print(f"[错误] {THEMES_FILE.name} 格式不正确。")
        return []

    return data["themes"]


def save_themes(themes):
    """把主题列表写入 themes.json（先写临时文件，再替换，避免半截文件）。"""
    data = {"themes": themes}
    temp_file = THEMES_FILE.with_name(THEMES_FILE.name + ".tmp")

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

    os.replace(temp_file, THEMES_FILE)


def load_settings():
    """读取 settings.json；文件不存在或损坏时返回空字典。"""
    if not SETTINGS_FILE.exists():
        return {}

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        logging.exception("读取 settings.json 失败")
        return {}

    if not isinstance(data, dict):
        logging.warning("settings.json 格式不正确")
        return {}

    return data


def save_settings(settings):
    """把设置写入 settings.json（原子写入）。"""
    temp_file = SETTINGS_FILE.with_name(SETTINGS_FILE.name + ".tmp")

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
        f.write("\n")

    os.replace(temp_file, SETTINGS_FILE)


def load_appearance_mode():
    """读取保存的外观模式，默认深色。"""
    mode = load_settings().get("appearance", "dark")
    return mode if mode in APPEARANCE_LABELS else "dark"


def save_appearance_mode(mode):
    """保存外观模式。"""
    settings = load_settings()
    settings["appearance"] = mode
    save_settings(settings)


def load_apply_notice_seconds():
    """读取应用完成提示的停留时间，默认 4 秒。"""
    value = load_settings().get(
        "apply_notice_seconds", DEFAULT_APPLY_NOTICE_SECONDS
    )
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_APPLY_NOTICE_SECONDS

    if value in APPLY_NOTICE_OPTIONS.values():
        return value
    return DEFAULT_APPLY_NOTICE_SECONDS


def save_apply_notice_seconds(seconds):
    """保存应用完成提示的停留时间。"""
    settings = load_settings()
    settings["apply_notice_seconds"] = int(seconds)
    save_settings(settings)


def normalize_theme_path(folder_path):
    """统一成绝对路径，便于去重和比较。"""
    return str(Path(folder_path).expanduser().resolve())


def collect_theme_folders(raw_paths):
    """把拖入的路径整理成主题文件夹列表，自动忽略重复项。

    支持拖入文件夹，或直接拖入 .cur/.ani 文件（取其所在文件夹）。
    """
    folders = []
    seen = set()

    for raw in raw_paths:
        text = str(raw).strip().strip('"')
        if not text:
            continue

        path = Path(text)
        if path.is_file() and path.suffix.lower() in (".cur", ".ani"):
            path = path.parent
        if not path.is_dir():
            continue

        key = os.path.normcase(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        folders.append(str(path))

    return folders


def add_theme(folder_path, name=None):
    """新增主题；如果同一路径已存在，直接返回已有条目。"""
    path = normalize_theme_path(folder_path)
    if not Path(path).is_dir():
        raise ValueError(f"文件夹不存在：{path}")

    themes = load_themes()
    for theme in themes:
        if os.path.normcase(theme.get("path", "")) == os.path.normcase(path):
            return theme

    theme = {
        "name": (name or "").strip() or Path(path).name,
        "path": path,
        "last_used": None,
    }
    themes.append(theme)
    save_themes(themes)
    return theme


def delete_theme(folder_path):
    """按路径删除主题；返回是否真的删除了条目。"""
    path = normalize_theme_path(folder_path)
    themes = load_themes()
    kept = [
        theme
        for theme in themes
        if os.path.normcase(theme.get("path", "")) != os.path.normcase(path)
    ]

    if len(kept) == len(themes):
        return False

    save_themes(kept)
    return True


def rename_theme(folder_path, new_name):
    """只更新主题的显示名称（name），不动 path；返回是否找到该主题。"""
    if not str(folder_path or "").strip():
        return False

    new_name = (new_name or "").strip()
    if not new_name:
        return False

    path = normalize_theme_path(folder_path)
    themes = load_themes()

    found = False
    for theme in themes:
        if os.path.normcase(theme.get("path", "")) == os.path.normcase(path):
            theme["name"] = new_name
            found = True
            break

    if found:
        save_themes(themes)

    return found


def relocate_theme(old_path, new_path):
    """把失效主题指向新文件夹，保留名称与上次使用时间。

    返回 (theme, error)：成功时 theme 为新条目、error 为 None；
    失败时 theme 为 None、error 为失败原因。
    """
    new_path = normalize_theme_path(new_path)
    if not Path(new_path).is_dir():
        return None, "选择的文件夹不存在。"

    themes = load_themes()
    old_key = os.path.normcase(normalize_theme_path(old_path))

    target = None
    for theme in themes:
        if os.path.normcase(theme.get("path", "")) == old_key:
            target = theme
            break

    if target is None:
        return None, "主题列表里找不到这一项，可能已经被删除。"

    for theme in themes:
        if theme is target:
            continue
        if os.path.normcase(theme.get("path", "")) == os.path.normcase(new_path):
            return None, "这个文件夹已经在主题列表里了。"

    target["path"] = new_path
    save_themes(themes)
    return target, None


def mark_theme_used(folder_path):
    """更新主题的上次使用时间；返回是否找到该主题。"""
    path = normalize_theme_path(folder_path)
    themes = load_themes()
    now = datetime.now().astimezone().isoformat(timespec="seconds")

    found = False
    for theme in themes:
        if os.path.normcase(theme.get("path", "")) == os.path.normcase(path):
            theme["last_used"] = now
            found = True
            break

    if found:
        save_themes(themes)

    return found


def test_theme_storage():
    """第 1 步验证：新增 -> 读取 -> 更新时间。"""
    print(f"themes.json 位置：{THEMES_FILE}")
    print(f"当前主题数量：{len(load_themes())}")

    # 仅供开发测试：改成你自己的光标主题文件夹
    folder = r"C:\Path\To\Your\Cursor\Theme"
    theme = add_theme(folder, name="示例主题")
    print(f"\n已加入（或已存在）：{theme['name']}")
    print(f"路径：{theme['path']}")

    print("\n读取全部主题：")
    for item in load_themes():
        last_used = item.get("last_used") or "从未使用"
        print(f"  - {item['name']} | {last_used} | {item['path']}")

    mark_theme_used(folder)
    print("\n更新时间后：")
    for item in load_themes():
        last_used = item.get("last_used") or "从未使用"
        print(f"  - {item['name']} | {last_used}")

    print("\n存储逻辑测试完成。")


def paths_equal(path_a, path_b):
    """比较两个光标路径是否指向同一个文件（忽略大小写和斜杠差异）。"""
    a = os.path.normcase(os.path.abspath(os.path.expandvars(str(path_a))))
    b = os.path.normcase(os.path.abspath(os.path.expandvars(str(path_b))))
    return a == b


def theme_is_active(folder_path, current=None):
    """通过注册表判断某个主题当前是否正在使用。"""
    if not folder_path:
        return False

    mapping = scan_and_map(folder_path, verbose=False)
    if not mapping:
        return False

    if current is None:
        current = get_current_cursors()

    for role, path in mapping.items():
        current_path = current.get(role.lower())
        if not current_path or not paths_equal(current_path, path):
            return False

    return True


def system_default_is_active(current=None):
    """判断当前注册表是否就是内置 Windows 默认光标。"""
    if current is None:
        current = get_current_cursors()

    optional_blank_roles = {"Crosshair", "IBeam"}

    for role, default_path in SYSTEM_DEFAULT_CURSOR_PATHS.items():
        current_path = current.get(role.lower())

        if not current_path:
            # Windows 默认方案的 Crosshair/IBeam 可能写成空值
            if role in optional_blank_roles:
                continue
            return False

        if not paths_equal(current_path, default_path):
            return False

    return True


def format_last_used(value):
    """把 ISO 时间转成 YYYY-MM-DD HH:MM。"""
    if not value:
        return "从未使用"
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(value)


def log_uncaught_exception(exc_type, exc_value, exc_tb):
    logging.critical("未捕获异常", exc_info=(exc_type, exc_value, exc_tb))


def log_thread_exception(args):
    logging.critical(
        "后台线程未捕获异常",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


def get_icon_path():
    """返回 icon.ico 路径；打包后优先在 PyInstaller 解压目录中查找。"""
    candidates = []

    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        candidates.append(Path(bundle_dir) / ICON_FILE_NAME)

    candidates.append(APP_DIR / ICON_FILE_NAME)

    for path in candidates:
        if path.exists():
            return path

    return None


def set_app_user_model_id():
    """让 Windows 把本程序当作独立应用，任务栏才会使用窗口自己的图标。"""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "CursorHub.CursorHub.App"
        )
    except Exception:
        logging.exception("设置 AppUserModelID 失败")


def apply_window_icon(window):
    """给窗口设置自定义图标；iconbitmap 失败时回退到 iconphoto。"""
    icon_path = get_icon_path()
    if icon_path is None:
        logging.warning("未找到图标文件 %s，使用系统默认图标", ICON_FILE_NAME)
        return

    try:
        # 必须在 CTk 的 200ms 延迟设置默认图标之前调用，
        # iconbitmap() 内部会标记 _iconbitmap_method_called，避免被覆盖。
        window.iconbitmap(str(icon_path))
        return
    except Exception:
        logging.exception(
            "iconbitmap 设置图标失败，尝试 iconphoto：%s", icon_path
        )

    if Image is None:
        return

    try:
        from PIL import ImageTk

        with Image.open(icon_path) as source:
            photo = ImageTk.PhotoImage(
                source.resize((64, 64), Image.Resampling.LANCZOS)
            )

        window.iconphoto(True, photo)
        # 保留引用，避免 PhotoImage 被垃圾回收后图标消失
        window._icon_photo = photo
    except Exception:
        logging.exception("iconphoto 设置图标失败：%s", icon_path)


class SettingsWindow(ctk.CTkToplevel):
    """设置窗口；以后新增设置项都往这个类里扩展。"""

    def __init__(
        self,
        master,
        current_mode="dark",
        notice_seconds=DEFAULT_APPLY_NOTICE_SECONDS,
        on_change=None,
        on_notice_change=None,
        on_help=None,
        on_cursors_restored=None,
        on_close=None,
    ):
        super().__init__(master, fg_color=("#F4F4F5", "#18181B"))
        apply_window_icon(self)

        self.on_change = on_change
        self.on_notice_change = on_notice_change
        self.on_help = on_help
        self.on_cursors_restored = on_cursors_restored
        self.on_close = on_close
        self.current_mode = current_mode
        self.notice_seconds = notice_seconds

        self.title("设置")
        self.geometry("440x540")
        self.resizable(False, False)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._safe_lift)

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=26, pady=24)

        heading = ctk.CTkLabel(
            container,
            text="外观模式",
            font=ctk.CTkFont(
                family="Microsoft YaHei UI", size=15, weight="bold"
            ),
            text_color=("#111827", "#F4F4F5"),
            anchor="w",
        )
        heading.pack(anchor="w")

        hint = ctk.CTkLabel(
            container,
            text="选择后主窗口和本窗口会立即切换。",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
            text_color=("#6B7280", "#A1A1AA"),
            anchor="w",
        )
        hint.pack(anchor="w", pady=(4, 14))

        self.appearance_control = ctk.CTkSegmentedButton(
            container,
            values=list(APPEARANCE_LABELS.values()),
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            selected_color=("#93C5FD", "#1D4ED8"),
            selected_hover_color=("#60A5FA", "#1E40AF"),
            unselected_color=("#E5E7EB", "#3F3F46"),
            unselected_hover_color=("#D1D5DB", "#52525B"),
            text_color=("#111827", "#F4F4F5"),
            command=self._change_appearance,
        )
        self.appearance_control.pack(fill="x")
        self.appearance_control.set(
            APPEARANCE_LABELS.get(current_mode, "深色")
        )

        self.feedback_label = ctk.CTkLabel(
            container,
            text=(
                "当前外观："
                f"{APPEARANCE_LABELS.get(current_mode, '深色')}"
            ),
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
            text_color=("#15803D", "#4ADE80"),
            anchor="w",
        )
        self.feedback_label.pack(anchor="w", pady=(12, 0))

        notice_heading = ctk.CTkLabel(
            container,
            text="应用完成提示",
            font=ctk.CTkFont(
                family="Microsoft YaHei UI", size=15, weight="bold"
            ),
            text_color=("#111827", "#F4F4F5"),
            anchor="w",
        )
        notice_heading.pack(anchor="w", pady=(20, 0))

        notice_hint = ctk.CTkLabel(
            container,
            text="应用主题后，结果会显示在对应卡片的按钮下方。",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
            text_color=("#6B7280", "#A1A1AA"),
            anchor="w",
        )
        notice_hint.pack(anchor="w", pady=(4, 10))

        self.notice_control = ctk.CTkSegmentedButton(
            container,
            values=list(APPLY_NOTICE_OPTIONS.keys()),
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            selected_color=("#93C5FD", "#1D4ED8"),
            selected_hover_color=("#60A5FA", "#1E40AF"),
            unselected_color=("#E5E7EB", "#3F3F46"),
            unselected_hover_color=("#D1D5DB", "#52525B"),
            text_color=("#111827", "#F4F4F5"),
            command=self._change_notice,
        )
        self.notice_control.pack(fill="x")
        self.notice_control.set(
            NOTICE_SECONDS_TO_LABEL.get(
                notice_seconds,
                NOTICE_SECONDS_TO_LABEL[DEFAULT_APPLY_NOTICE_SECONDS],
            )
        )

        save_hint = ctk.CTkLabel(
            container,
            text="设置会自动保存到 settings.json。",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=11),
            text_color=("#9CA3AF", "#71717A"),
            anchor="w",
        )
        save_hint.pack(anchor="w", pady=(12, 0))

        backup_heading = ctk.CTkLabel(
            container,
            text="光标备份",
            font=ctk.CTkFont(
                family="Microsoft YaHei UI", size=15, weight="bold"
            ),
            text_color=("#111827", "#F4F4F5"),
            anchor="w",
        )
        backup_heading.pack(anchor="w", pady=(14, 0))

        backup_hint = ctk.CTkLabel(
            container,
            text="应用主题前会自动备份当前光标，最多保留 20 份。",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
            text_color=("#6B7280", "#A1A1AA"),
            anchor="w",
        )
        backup_hint.pack(anchor="w", pady=(2, 6))

        backup_buttons = ctk.CTkFrame(container, fg_color="transparent")
        backup_buttons.pack(fill="x")

        self.backup_button = ctk.CTkButton(
            backup_buttons,
            text="立即备份",
            width=110,
            height=32,
            corner_radius=8,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            fg_color=("#E5E7EB", "#3F3F46"),
            hover_color=("#D1D5DB", "#52525B"),
            text_color=("#111827", "#F4F4F5"),
            command=self._backup_now,
        )
        self.backup_button.pack(side="left")

        self.restore_button = ctk.CTkButton(
            backup_buttons,
            text="恢复上次",
            width=110,
            height=32,
            corner_radius=8,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            fg_color=("#FEF3C7", "#422006"),
            hover_color=("#FDE68A", "#713F12"),
            text_color=("#92400E", "#FBBF24"),
            command=self._restore_last,
        )
        self.restore_button.pack(side="left", padx=(10, 0))

        self.backup_status_label = ctk.CTkLabel(
            container,
            text="",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=11),
            text_color=("#9CA3AF", "#71717A"),
            anchor="w",
        )
        self.backup_status_label.pack(anchor="w", pady=(6, 0))
        self._refresh_backup_status()

        self.help_button = ctk.CTkButton(
            container,
            text="❓ 使用说明",
            width=120,
            height=32,
            corner_radius=8,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            fg_color=("#E5E7EB", "#3F3F46"),
            hover_color=("#D1D5DB", "#52525B"),
            text_color=("#111827", "#F4F4F5"),
            command=self._open_help,
        )
        self.help_button.pack(anchor="w", pady=(10, 0))

    def _refresh_backup_status(self):
        info = get_latest_backup_info()

        if info is None:
            self.backup_status_label.configure(text="还没有备份")
            self.restore_button.configure(state="disabled")
            return

        _backup_path, created_at = info
        self.backup_status_label.configure(text=f"上次备份：{created_at}")
        self.restore_button.configure(state="normal")

    def _backup_now(self):
        backup_file = backup_current_cursors(reason="manual")

        if backup_file is None:
            messagebox.showerror(
                "备份失败",
                "无法写入备份文件，请查看日志。",
                parent=self,
            )
            return

        self._refresh_backup_status()
        messagebox.showinfo(
            "备份完成",
            f"当前光标配置已备份：\n{backup_file}",
            parent=self,
        )

    def _restore_last(self):
        info = get_latest_backup_info()

        if info is None:
            messagebox.showinfo(
                "没有备份",
                "还没有可恢复的光标备份。",
                parent=self,
            )
            return

        backup_path, created_at = info
        confirmed = messagebox.askyesno(
            "恢复光标",
            f"将把光标恢复到备份状态：\n{created_at}\n\n是否继续？",
            parent=self,
        )
        if not confirmed:
            return

        restored, failed, _ = restore_cursors_from_backup(backup_path)
        self._refresh_backup_status()

        if restored == 0 and not failed:
            messagebox.showerror(
                "恢复失败",
                "备份文件内容无效，请查看日志。",
                parent=self,
            )
            return

        if failed:
            messagebox.showwarning(
                "部分恢复",
                f"已恢复 {restored} 个角色，{len(failed)} 个失败：\n"
                + "、".join(role for role, _reason in failed),
                parent=self,
            )
        else:
            messagebox.showinfo(
                "恢复完成",
                f"已恢复 {restored} 个光标角色。",
                parent=self,
            )

        if self.on_cursors_restored:
            self.on_cursors_restored(restored, failed)

    def _open_help(self):
        if self.on_help:
            self.on_help()

    def _change_appearance(self, label):
        mode = LABEL_TO_APPEARANCE.get(label, "dark")
        self.current_mode = mode
        ctk.set_appearance_mode(mode)
        logging.info("外观模式切换为：%s", mode)
        self.feedback_label.configure(text=f"已切换为：{label}")

        if self.on_change:
            self.on_change(mode)

    def _change_notice(self, label):
        seconds = APPLY_NOTICE_OPTIONS.get(
            label, DEFAULT_APPLY_NOTICE_SECONDS
        )
        self.notice_seconds = seconds
        logging.info("应用完成提示停留时间：%s", label)

        if self.on_notice_change:
            self.on_notice_change(seconds)

    def _safe_lift(self):
        if self.winfo_exists():
            self.lift()

    def _on_close(self):
        try:
            self.grab_release()
        except Exception:
            pass

        try:
            if self.on_close:
                self.on_close()
        finally:
            self.destroy()


class HelpWindow(ctk.CTkToplevel):
    """使用说明与关于窗口。"""

    def __init__(self, master, on_close=None):
        super().__init__(master, fg_color=("#F4F4F5", "#18181B"))
        apply_window_icon(self)

        self.on_close = on_close

        self.title("使用说明 / 关于")
        self.geometry("660x640")
        self.minsize(560, 480)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._safe_lift)

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=24, pady=20)

        title_row = ctk.CTkFrame(container, fg_color="transparent")
        title_row.pack(fill="x")

        ctk.CTkLabel(
            title_row,
            text="📖 使用说明",
            font=ctk.CTkFont(
                family="Microsoft YaHei UI", size=20, weight="bold"
            ),
            text_color=("#111827", "#F4F4F5"),
            anchor="w",
        ).pack(side="left")

        ctk.CTkLabel(
            title_row,
            text=f"CursorHub v{__version__}",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
            text_color=("#6B7280", "#A1A1AA"),
        ).pack(side="right")

        scroll = ctk.CTkScrollableFrame(
            container,
            fg_color=("#FFFFFF", "#202023"),
            corner_radius=10,
            scrollbar_button_color=("#D4D4D8", "#3F3F46"),
            scrollbar_button_hover_color=("#A1A1AA", "#52525B"),
        )
        scroll.pack(fill="both", expand=True, pady=(14, 12))

        self._section(
            scroll,
            "快速开始",
            "1. 点击右上角「➕ 添加新主题」，选择包含 .cur / .ani 的文件夹；"
            "也可以直接把文件夹拖进窗口。\n"
            "2. 点击卡片上的「应用」，整套光标会写入当前用户注册表并立即生效。\n"
            "3. 「🖼️ 预览」可查看 Normal 光标图片、文件明细和角色完整度；"
            "「✏️ 重命名」只修改显示名称。\n"
            "4. 右上角「🔄 刷新」或 F5 可重新检测主题文件夹是否失效。\n"
            "5. 顶部搜索框可按名称或路径过滤；检测到失效主题时，"
            "右上角会出现「🧹 清理失效」按钮。",
        )

        self._section(
            scroll,
            "数据文件",
            f"• themes.json：收藏的主题列表\n{THEMES_FILE}\n\n"
            f"• settings.json：外观与提示设置\n{SETTINGS_FILE}\n\n"
            f"• CursorHub.log：运行日志\n{LOG_FILE}\n\n"
            f"• backups 文件夹：应用主题前自动备份的光标配置（最多 20 份）\n"
            f"{BACKUP_DIR}\n\n"
            "移动程序时请把整个文件夹一起移动；删除程序时直接删除整个文件夹即可。",
        )

        self._section(
            scroll,
            "注意事项",
            "• 请放在有写入权限的文件夹，不要放在 Program Files 等受保护目录。\n"
            "• 首次运行若出现 SmartScreen「未知发布者」提示，"
            "选择「更多信息 → 仍要运行」。\n"
            "• 程序只改写当前用户（HKEY_CURRENT_USER）的光标配置，"
            "不需要管理员权限。\n"
            "• 个别角色（如 NWPen / Pin / Person）可能需要注销或重启后才生效。\n"
            "• 应用主题前会自动备份当前光标，可在设置 →「光标备份」里恢复。\n"
            "• 主题文件版权归原作者所有，本程序不附带任何光标主题。",
        )

        self._section(
            scroll,
            "快捷键",
            "• F5：刷新主题列表\n"
            "• 拖拽文件夹到窗口：添加主题",
        )

        self._section(
            scroll,
            "常见问题",
            "• 应用后光标没变化：确认主题文件有效；部分角色需注销或重启；"
            "可查看日志。\n"
            "• 卡片显示「路径失效」：文件夹被移动或删除，"
            "点击卡片上的「📂 重新选择」。\n"
            "• 预览无法显示：.cur / .ani 文件可能损坏，列表仍会显示角色信息。\n"
            "• 找不到主题列表：themes.json 与 exe 不在同一目录，或 exe 被移动过。",
        )

        buttons = ctk.CTkFrame(container, fg_color="transparent")
        buttons.pack(fill="x")

        ctk.CTkButton(
            buttons,
            text="🌐 GitHub 页面",
            width=130,
            height=34,
            corner_radius=8,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            fg_color="#2563EB",
            hover_color="#1D4ED8",
            text_color="#FFFFFF",
            command=lambda: webbrowser.open(GITHUB_URL),
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            buttons,
            text="📂 打开数据目录",
            width=140,
            height=34,
            corner_radius=8,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            fg_color=("#E5E7EB", "#3F3F46"),
            hover_color=("#D1D5DB", "#52525B"),
            text_color=("#111827", "#F4F4F5"),
            command=lambda: self._open_path(
                APP_DIR, "程序目录不存在。"
            ),
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            buttons,
            text="📄 打开日志",
            width=120,
            height=34,
            corner_radius=8,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            fg_color=("#E5E7EB", "#3F3F46"),
            hover_color=("#D1D5DB", "#52525B"),
            text_color=("#111827", "#F4F4F5"),
            command=lambda: self._open_path(
                LOG_FILE, "还没有生成日志文件，先在程序里操作一次再试。"
            ),
        ).pack(side="left")

    def _section(self, parent, title, body):
        ctk.CTkLabel(
            parent,
            text=title,
            font=ctk.CTkFont(
                family="Microsoft YaHei UI", size=14, weight="bold"
            ),
            text_color=("#111827", "#F4F4F5"),
            anchor="w",
        ).pack(anchor="w", padx=16, pady=(14, 4))

        ctk.CTkLabel(
            parent,
            text=body,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
            text_color=("#374151", "#D4D4D8"),
            justify="left",
            anchor="w",
            wraplength=560,
        ).pack(anchor="w", padx=16, pady=(0, 8))

    def _open_path(self, path, missing_text):
        if not Path(path).exists():
            messagebox.showinfo("提示", missing_text, parent=self)
            return
        try:
            os.startfile(str(path))
        except Exception as exc:
            logging.exception("无法打开路径：%s", path)
            messagebox.showerror(
                "打开失败",
                f"无法打开：\n{path}\n\n{exc}",
                parent=self,
            )

    def _safe_lift(self):
        if self.winfo_exists():
            self.lift()

    def _on_close(self):
        try:
            if self.on_close:
                self.on_close()
        finally:
            self.destroy()


def find_preview_cursor_files(folder_path):
    """返回可用于图片预览的 Normal 光标文件，.ani 优先、.cur 兜底。"""
    if not folder_path or not os.path.isdir(folder_path):
        return []

    ani_path = None
    cur_path = None

    try:
        filenames = sorted(os.listdir(folder_path))
    except OSError:
        logging.exception("无法列出主题文件夹：%s", folder_path)
        return []

    for filename in filenames:
        stem, ext = os.path.splitext(filename)
        if stem.lower() != "normal":
            continue

        ext = ext.lower()
        if ext == ".ani" and ani_path is None:
            ani_path = os.path.join(folder_path, filename)
        elif ext == ".cur" and cur_path is None:
            cur_path = os.path.join(folder_path, filename)

    return [path for path in (ani_path, cur_path) if path]


def extract_first_ani_frame(ani_data):
    """从 .ani 的 RIFF 数据中取出第一帧（一整个 .cur / .ico 文件字节）。"""
    if (
        len(ani_data) < 12
        or ani_data[0:4] != b"RIFF"
        or ani_data[8:12] != b"ACON"
    ):
        raise ValueError("不是有效的 ANI 文件")

    total = len(ani_data)
    pos = 12

    while pos + 8 <= total:
        chunk_id = ani_data[pos:pos + 4]
        chunk_size = struct.unpack_from("<I", ani_data, pos + 4)[0]
        chunk_end = pos + 8 + chunk_size

        if (
            chunk_id == b"LIST"
            and chunk_size >= 4
            and ani_data[pos + 8:pos + 12] == b"fram"
        ):
            inner = pos + 12
            list_end = min(chunk_end, total)
            while inner + 8 <= list_end:
                frame_id = ani_data[inner:inner + 4]
                frame_size = struct.unpack_from("<I", ani_data, inner + 4)[0]

                if frame_id in (b"icon", b"cur "):
                    frame_end = inner + 8 + frame_size
                    if frame_end > total:
                        raise ValueError("ANI 帧数据不完整")
                    return ani_data[inner + 8:frame_end]

                inner += 8 + frame_size + (frame_size & 1)

        pos = chunk_end + (chunk_size & 1)

    raise ValueError("ANI 文件中没有找到图像帧")


def extract_ani_frames(ani_data):
    """解析 .ani 的全部帧与显示时序，返回 [(cur/ico 字节, 毫秒), ...]。"""
    if (
        len(ani_data) < 12
        or ani_data[0:4] != b"RIFF"
        or ani_data[8:12] != b"ACON"
    ):
        raise ValueError("不是有效的 ANI 文件")

    total = len(ani_data)
    pos = 12
    frames = []
    rates = []
    sequence = []
    default_rate = 6
    steps = 0

    while pos + 8 <= total:
        chunk_id = ani_data[pos:pos + 4]
        chunk_size = struct.unpack_from("<I", ani_data, pos + 4)[0]
        chunk_end = pos + 8 + chunk_size

        if chunk_id == b"anih" and chunk_size >= 36:
            values = struct.unpack_from("<9I", ani_data, pos + 8)
            steps = values[2]
            # iDispRate 的单位同样是 1/60 秒
            default_rate = values[7] or default_rate
        elif chunk_id == b"rate":
            count = min(chunk_size // 4, (total - pos - 8) // 4)
            if count > 0:
                rates = list(struct.unpack_from(f"<{count}I", ani_data, pos + 8))
        elif chunk_id == b"seq ":
            count = min(chunk_size // 4, (total - pos - 8) // 4)
            if count > 0:
                sequence = list(
                    struct.unpack_from(f"<{count}I", ani_data, pos + 8)
                )
        elif (
            chunk_id == b"LIST"
            and chunk_size >= 4
            and ani_data[pos + 8:pos + 12] == b"fram"
        ):
            inner = pos + 12
            list_end = min(chunk_end, total)

            while inner + 8 <= list_end:
                frame_id = ani_data[inner:inner + 4]
                frame_size = struct.unpack_from("<I", ani_data, inner + 4)[0]
                frame_end = inner + 8 + frame_size

                if frame_id in (b"icon", b"cur "):
                    if frame_end > total:
                        raise ValueError("ANI 帧数据不完整")
                    frames.append(ani_data[inner + 8:frame_end])

                inner = frame_end + (frame_size & 1)

        pos = chunk_end + (chunk_size & 1)

    if not frames:
        raise ValueError("ANI 文件中没有图像帧")

    frame_count = len(frames)
    step_count = steps or len(sequence) or frame_count

    if sequence:
        order = sequence[:step_count]
    else:
        order = [index % frame_count for index in range(step_count)]

    if not order:
        order = list(range(frame_count))

    if not rates:
        rates = [default_rate] * len(order)
    elif len(rates) < len(order):
        rates = rates + [rates[-1]] * (len(order) - len(rates))

    animation = []
    for index, frame_index in enumerate(order):
        frame_data = frames[frame_index % frame_count]
        jiffies = rates[index] if index < len(rates) else default_rate
        duration_ms = max(20, int(jiffies * 1000 / 60))
        animation.append((frame_data, duration_ms))

    return animation


def load_ani_animation(filepath, max_frames=90):
    """读取 .ani 并按显示时序返回 (PIL 帧列表, 每帧毫秒)。"""
    with open(filepath, "rb") as f:
        data = f.read()

    steps = extract_ani_frames(data)[:max_frames]
    if not steps:
        raise ValueError("ANI 文件中没有可显示帧")

    cache = {}
    images = []
    durations = []

    for frame_data, duration_ms in steps:
        image = cache.get(frame_data)
        if image is None:
            image = compose_preview_image(
                decode_cursor_image_data(frame_data)
            )
            cache[frame_data] = image

        images.append(image)
        durations.append(duration_ms)

    return images, durations


def decode_cursor_image_data(data):
    """解码 .cur/.ico 数据，兼容 PNG 压缩的多尺寸光标帧（PNG-in-CUR）。"""
    # 1. 先走 Pillow 原生路径：传统 BMP 帧和 PNG-in-ICO 都能处理
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            return source.convert("RGBA")
    except Exception:
        pass

    # 2. 手动解析 CUR/ICO 目录，逐个尺寸尝试解码
    if len(data) < 6:
        raise ValueError("光标数据太短")

    reserved, image_type, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0 or image_type not in (1, 2) or count <= 0:
        raise ValueError("不是有效的 CUR/ICO 数据")

    entries = []
    for index in range(count):
        entry_offset = 6 + index * 16
        if entry_offset + 16 > len(data):
            break

        (
            width,
            height,
            colors,
            _reserved,
            field1,
            field2,
            size,
            image_offset,
        ) = struct.unpack_from("<BBBBHHII", data, entry_offset)

        actual_width = width or 256
        actual_height = height or 256

        if size <= 0 or image_offset + size > len(data):
            continue

        entries.append(
            (
                actual_width * actual_height,
                actual_width,
                actual_height,
                colors,
                field1,
                field2,
                size,
                image_offset,
            )
        )

    # 从最大尺寸开始尝试，某个尺寸损坏时自动换下一个
    for (
        _area,
        width,
        height,
        colors,
        field1,
        field2,
        size,
        image_offset,
    ) in sorted(entries, reverse=True):
        entry_data = data[image_offset:image_offset + size]

        try:
            if entry_data.startswith(PNG_SIGNATURE):
                # PNG-in-CUR：Pillow 的 CUR 插件不支持，直接按 PNG 解码
                with Image.open(io.BytesIO(entry_data)) as source:
                    source.load()
                    image = source.convert("RGBA")
            else:
                # 传统 BMP/DIB 帧：包成单尺寸 CUR/ICO 再交给 Pillow
                header = struct.pack("<HHH", 0, image_type, 1)
                entry = struct.pack(
                    "<BBBBHHII",
                    width if width < 256 else 0,
                    height if height < 256 else 0,
                    colors,
                    0,
                    field1,
                    field2,
                    size,
                    22,
                )
                with Image.open(
                    io.BytesIO(header + entry + entry_data)
                ) as source:
                    source.load()
                    image = source.convert("RGBA")
        except Exception:
            continue

        logging.info(
            "使用 CUR 多尺寸解码：%dx%d（PNG=%s）",
            width,
            height,
            entry_data.startswith(PNG_SIGNATURE),
        )
        return image

    raise ValueError("无法解码光标图像")


def load_ani_frame_with_ani_file(filepath):
    """用 ani_file 提取第一帧到临时文件，再用 Pillow 打开并清理临时文件。"""
    if ani_file_lib is None:
        raise RuntimeError("ani_file 未安装")

    with ani_file_lib.open(filepath, "rb") as ani:
        frames = ani.getframesdata()

    if not frames:
        raise ValueError("ANI 文件中没有图像帧")

    frame_data = frames[0]
    if len(frame_data) < 4:
        raise ValueError("ANI 帧数据不完整")

    # 帧数据本身是一个完整的 .ico 或 .cur 文件
    image_type = struct.unpack_from("<H", frame_data, 2)[0]
    suffix = ".ico" if image_type == 1 else ".cur"

    fd, temp_path = tempfile.mkstemp(prefix="cursorhub_ani_", suffix=suffix)
    os.close(fd)

    try:
        with open(temp_path, "wb") as f:
            f.write(frame_data)

        try:
            with Image.open(temp_path) as source:
                source.load()
                image = source.convert("RGBA")
        except Exception:
            # PNG-in-CUR 等 Pillow 原生 CUR 插件不支持的帧
            logging.info(
                "Pillow 原生 CUR 解码失败，改用 PNG/多尺寸解码：%s",
                filepath,
            )
            image = decode_cursor_image_data(frame_data)

        logging.info("使用 ani_file 解析 ANI 第一帧：%s", filepath)
        return image
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            logging.exception("删除 ANI 临时预览文件失败：%s", temp_path)


def load_cursor_preview_image(filepath):
    """读取 .cur / .ani 并返回 RGBA 图像；.ani 只取第一帧。"""
    lower_path = filepath.lower()

    if lower_path.endswith(".cur"):
        # .cur 先读入内存，再交给兼容 PNG-in-CUR 的解码器
        with open(filepath, "rb") as f:
            data = f.read()
        return decode_cursor_image_data(data)

    # .ani：先试 ani_file 库，失败再回退到内置 RIFF 解析
    if ani_file_lib is not None:
        try:
            return load_ani_frame_with_ani_file(filepath)
        except Exception:
            logging.exception(
                "ani_file 解析失败，回退内置解析：%s", filepath
            )

    with open(filepath, "rb") as f:
        data = f.read()

    data = extract_first_ani_frame(data)
    return decode_cursor_image_data(data)


def compose_preview_image(image, size=PREVIEW_IMAGE_SIZE):
    """裁掉透明边缘，等比缩放后居中放到方形透明画布上。"""
    image = image.convert("RGBA")

    bbox = image.getbbox()
    if bbox:
        image = image.crop(bbox)

    if image.width <= 0 or image.height <= 0:
        raise ValueError("光标图像为空")

    padding = max(2, size // 16)
    max_side = size - padding * 2
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(
        image,
        ((size - image.width) // 2, (size - image.height) // 2),
        image,
    )
    return canvas


def create_fallback_cursor_image(size=PREVIEW_IMAGE_SIZE):
    """用 PIL 画一个灰色箭头，作为图片无法读取时的占位图。"""
    scale = 4
    canvas_side = size * scale
    image = Image.new("RGBA", (canvas_side, canvas_side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    arrow = [
        (17, 6),
        (17, 49),
        (26, 40),
        (32, 56),
        (40, 52),
        (34, 36),
        (48, 35),
    ]
    points = [(x * scale, y * scale) for x, y in arrow]
    draw.polygon(
        points,
        fill=(156, 163, 175, 255),
        outline=(75, 85, 99, 255),
        width=2 * scale,
    )

    return image.resize((size, size), Image.Resampling.LANCZOS)


def get_preview_image(folder_path):
    """返回 (PIL 图像, 来源文件名, 提示文字)；读取失败时用灰色箭头兜底。"""
    if Image is None:
        return None, None, "未安装 Pillow，无法显示图片预览"

    files = find_preview_cursor_files(folder_path)
    if not files:
        if not folder_path or not os.path.isdir(folder_path):
            message = "主题文件夹不存在，已显示默认图标"
        else:
            message = "未找到 Normal.cur / Normal.ani，已显示默认图标"
        return create_fallback_cursor_image(), None, message

    for filepath in files:
        try:
            image = load_cursor_preview_image(filepath)
            return (
                compose_preview_image(image),
                os.path.basename(filepath),
                None,
            )
        except Exception:
            logging.exception("读取光标图片失败：%s", filepath)

    return (
        create_fallback_cursor_image(),
        None,
        "无法提取预览",
    )


class PreviewWindow(ctk.CTkToplevel):
    """主题预览窗口：顶部是真实光标图片，下方是文件明细。"""

    def __init__(self, master, theme, on_close=None):
        super().__init__(master, fg_color=("#F4F4F5", "#18181B"))
        apply_window_icon(self)

        self.theme = theme
        self.on_close = on_close

        name = theme.get("name") or "未命名主题"
        path = theme.get("path", "")

        self.title(f"主题预览 - {name}")
        self.geometry("640x800")
        self.minsize(540, 620)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._safe_lift)

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=24, pady=20)

        heading = ctk.CTkLabel(
            container,
            text="👁️ 主题预览",
            font=ctk.CTkFont(
                family="Microsoft YaHei UI", size=22, weight="bold"
            ),
            text_color=("#111827", "#F4F4F5"),
            anchor="w",
        )
        heading.pack(anchor="w")

        name_label = ctk.CTkLabel(
            container,
            text=name,
            font=ctk.CTkFont(
                family="Microsoft YaHei UI", size=14, weight="bold"
            ),
            text_color=("#2563EB", "#60A5FA"),
            anchor="w",
        )
        name_label.pack(anchor="w", pady=(4, 0))

        path_label = ctk.CTkLabel(
            container,
            text=path,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=11),
            text_color=("#6B7280", "#A1A1AA"),
            anchor="w",
            justify="left",
            wraplength=570,
        )
        path_label.pack(anchor="w", pady=(2, 10))

        self._create_image_preview(container, path)

        info = scan_theme_files(path)

        if not info["exists"]:
            summary_text = "⚠️ 主题文件夹不存在"
            summary_color = ("#B45309", "#FBBF24")
        elif not info["files"]:
            summary_text = "❌ 没有找到 .cur / .ani 文件"
            summary_color = ("#B91C1C", "#F87171")
        else:
            matched_count = len(info["matched"])
            total = len(CURSOR_ROLES)

            if info["missing"]:
                summary_text = (
                    f"完整度：{matched_count}/{total} 个角色"
                    f"（缺失 {len(info['missing'])} 个）"
                )
                summary_color = ("#B45309", "#FBBF24")
            else:
                summary_text = (
                    f"完整度：{matched_count}/{total} 个角色"
                    "　✅ 全部齐全"
                )
                summary_color = ("#15803D", "#4ADE80")

        summary_label = ctk.CTkLabel(
            container,
            text=summary_text,
            font=ctk.CTkFont(
                family="Microsoft YaHei UI", size=13, weight="bold"
            ),
            text_color=summary_color,
            anchor="w",
        )
        summary_label.pack(anchor="w", pady=(0, 6))

        if info["missing"]:
            missing_text = "缺失角色：" + "、".join(
                ROLE_DISPLAY_NAMES.get(role, role)
                for role in info["missing"]
            )
            missing_label = ctk.CTkLabel(
                container,
                text=missing_text,
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=11),
                text_color=("#B45309", "#FBBF24"),
                anchor="w",
                justify="left",
                wraplength=570,
            )
            missing_label.pack(anchor="w", pady=(0, 6))

        if info["unrecognized"]:
            unrecognized_text = "未识别文件：" + "、".join(
                info["unrecognized"]
            )
            unrecognized_label = ctk.CTkLabel(
                container,
                text=unrecognized_text,
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=11),
                text_color=("#B45309", "#FBBF24"),
                anchor="w",
                justify="left",
                wraplength=570,
            )
            unrecognized_label.pack(anchor="w", pady=(0, 6))

        close_button = ctk.CTkButton(
            container,
            text="关闭",
            width=90,
            height=34,
            corner_radius=8,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            fg_color=("#E5E7EB", "#3F3F46"),
            hover_color=("#D1D5DB", "#52525B"),
            text_color=("#111827", "#F4F4F5"),
            command=self._on_close,
        )
        close_button.pack(side="bottom", anchor="e", pady=(12, 0))

        list_frame = ctk.CTkScrollableFrame(
            container,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color=("#D4D4D8", "#3F3F46"),
            scrollbar_button_hover_color=("#A1A1AA", "#52525B"),
        )
        list_frame.pack(fill="both", expand=True, pady=(12, 0))

        if not info["files"]:
            empty = ctk.CTkLabel(
                list_frame,
                text="没有可显示的光标文件。",
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
                text_color=("#6B7280", "#A1A1AA"),
            )
            empty.pack(pady=40)
        else:
            for file_info in info["files"]:
                self._create_file_row(list_frame, file_info)

            # 初始预览的是 Normal 文件，把对应行标记为选中
            if self.initial_preview_source:
                initial_row = self.rows_by_filename.get(
                    self.initial_preview_source
                )
                if initial_row is not None:
                    if Image is not None:
                        self._preview_file_clicked(
                            self.initial_preview_source, initial_row
                        )
                    else:
                        self._select_row(initial_row)

    def _create_file_row(self, parent, file_info):
        filename = file_info["filename"]

        row = ctk.CTkFrame(
            parent,
            corner_radius=8,
            fg_color=ROW_BASE_COLOR,
            border_width=1,
            border_color=ROW_BORDER_COLOR,
        )
        row.pack(fill="x", padx=4, pady=4)
        row.grid_columnconfigure(0, weight=1)
        self.rows_by_filename[filename] = row

        file_label = ctk.CTkLabel(
            row,
            text=file_info["filename"],
            font=ctk.CTkFont(
                family="Microsoft YaHei UI", size=13, weight="bold"
            ),
            text_color=("#111827", "#F4F4F5"),
            anchor="w",
        )
        file_label.grid(
            row=0, column=0, sticky="w", padx=(14, 8), pady=10
        )

        role = file_info.get("role")
        if role:
            role_text = (
                f"→ {ROLE_DISPLAY_NAMES.get(role, role)}（{role}）"
            )
            role_color = ("#15803D", "#4ADE80")
        else:
            role_text = "→ ❓ 未识别"
            role_color = ("#B45309", "#FBBF24")

        role_label = ctk.CTkLabel(
            row,
            text=role_text,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
            text_color=role_color,
            anchor="e",
        )
        role_label.grid(
            row=0, column=1, sticky="e", padx=(8, 14), pady=10
        )

        if Image is not None:
            self._make_row_clickable(row, filename, [file_label, role_label])

    def _make_row_clickable(self, row, filename, labels):
        """给一行绑定 hover 变色和点击切换预览。"""
        def on_enter(event):
            try:
                row.configure(fg_color=ROW_HOVER_COLOR)
            except Exception:
                pass

        def on_leave(event):
            # 在行内的两个标签之间移动时不要闪回原色
            if not self._pointer_inside(row):
                try:
                    row.configure(fg_color=ROW_BASE_COLOR)
                except Exception:
                    pass

        def on_click(event):
            self._preview_file_clicked(filename, row)

        for widget in [row, *labels]:
            widget.bind("<Enter>", on_enter, add="+")
            widget.bind("<Leave>", on_leave, add="+")
            widget.bind("<Button-1>", on_click, add="+")

        # 鼠标变成手型（CTkFrame 的可见背景是内部 canvas）
        try:
            row._canvas.configure(cursor="hand2")
        except Exception:
            pass

        for label in labels:
            try:
                label.configure(cursor="hand2")
            except Exception:
                pass

    @staticmethod
    def _pointer_inside(widget):
        """判断鼠标当前是否还在指定控件（或其子控件）上。"""
        x, y = widget.winfo_pointerxy()
        under = widget.winfo_containing(x, y)

        while under is not None:
            if under is widget:
                return True
            under = under.master

        return False

    def _preview_file_clicked(self, filename, row):
        """点击文件行：加载并切换顶部图片，.ani 自动播放动画。"""
        if Image is None:
            return

        self._stop_animation()
        full_path = os.path.join(self.folder_path, filename)

        if filename.lower().endswith(".ani"):
            try:
                self._start_animation(filename, full_path)
            except Exception:
                logging.exception("加载光标动画失败：%s", full_path)
            else:
                self._select_row(row)
                return

        try:
            image = load_cursor_preview_image(full_path)
            image = compose_preview_image(image)
        except Exception:
            logging.exception("预览光标文件失败：%s", full_path)
            self._set_preview_image(
                create_fallback_cursor_image(),
                "无法提取预览",
                is_warning=True,
            )
        else:
            self._set_preview_image(
                image, self._format_preview_caption(filename)
            )

        self._select_row(row)

    def _start_animation(self, filename, full_path):
        """解析 .ani 的帧数据并开始循环播放。"""
        frames, durations = load_ani_animation(full_path)

        if len(frames) < 2:
            image = compose_preview_image(
                load_cursor_preview_image(full_path)
            )
            self._set_preview_image(
                image, self._format_preview_caption(filename)
            )
            return

        self.animation_frames = frames
        self.animation_durations = durations
        self.animation_index = 0
        self.animation_caption = f"{filename} · 动画 · {len(frames)} 帧"
        self._render_animation_frame()

    def _render_animation_frame(self):
        """显示当前动画帧，并按该帧时长安排下一帧。"""
        try:
            if not self.winfo_exists() or not self.animation_frames:
                return
        except Exception:
            return

        index = self.animation_index
        self._set_preview_image(
            self.animation_frames[index],
            self.animation_caption,
        )

        delay = self.animation_durations[index]
        self.animation_job = self.after(delay, self._advance_animation)

    def _advance_animation(self):
        self.animation_job = None

        if not self.animation_frames:
            return

        self.animation_index = (
            self.animation_index + 1
        ) % len(self.animation_frames)
        self._render_animation_frame()

    def _stop_animation(self):
        """停止动画并清空帧缓存。"""
        if self.animation_job is not None:
            try:
                self.after_cancel(self.animation_job)
            except Exception:
                pass

        self.animation_job = None
        self.animation_frames = []
        self.animation_durations = []
        self.animation_index = 0
        self.animation_caption = ""

    @staticmethod
    def _format_preview_caption(filename):
        """生成图片下方的说明文字。"""
        if not filename:
            return ""
        if filename.lower().endswith(".ani"):
            return f"{filename} · 第一帧"
        return filename

    def _set_preview_image(self, image, caption, is_warning=False):
        """更新顶部图片和说明；必须保留 CTkImage 引用，否则会被垃圾回收。"""
        self.current_img = ctk.CTkImage(
            light_image=image,
            dark_image=image,
            size=(PREVIEW_IMAGE_SIZE, PREVIEW_IMAGE_SIZE),
        )
        self.preview_image_label.configure(image=self.current_img, text="")
        self.preview_caption_label.configure(
            text=caption,
            text_color=(
                ("#B45309", "#FBBF24")
                if is_warning
                else ("#9CA3AF", "#71717A")
            ),
        )

    def _select_row(self, row):
        """高亮当前正在预览的文件行。"""
        try:
            if (
                self.selected_row is not None
                and self.selected_row.winfo_exists()
            ):
                self.selected_row.configure(
                    border_width=1,
                    border_color=ROW_BORDER_COLOR,
                )
        except Exception:
            pass

        self.selected_row = row

        try:
            row.configure(
                border_width=2,
                border_color=ROW_SELECTED_BORDER_COLOR,
            )
        except Exception:
            logging.exception("更新选中行样式失败")

    def _create_image_preview(self, container, folder_path):
        """在窗口顶部显示光标图片，并保存可动态更新的控件引用。"""
        self.folder_path = folder_path
        self.current_img = None
        self.selected_row = None
        self.rows_by_filename = {}
        self.initial_preview_source = None
        self.animation_frames = []
        self.animation_durations = []
        self.animation_index = 0
        self.animation_job = None
        self.animation_caption = ""

        image_box = ctk.CTkFrame(container, fg_color="transparent")
        image_box.pack(pady=(4, 8))

        image_frame = ctk.CTkFrame(
            image_box,
            width=96,
            height=96,
            corner_radius=12,
            fg_color=("#FFFFFF", "#27272A"),
            border_width=1,
            border_color=("#E4E4E7", "#3F3F46"),
        )
        image_frame.pack()
        image_frame.pack_propagate(False)

        self.preview_image_label = ctk.CTkLabel(
            image_frame,
            text="",
        )
        self.preview_image_label.pack(expand=True)

        self.preview_caption_label = ctk.CTkLabel(
            container,
            text="",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=11),
            text_color=("#9CA3AF", "#71717A"),
            anchor="center",
            justify="center",
            wraplength=570,
        )
        self.preview_caption_label.pack(anchor="center", pady=(0, 6))

        if Image is None:
            self.preview_image_label.configure(
                text="图片预览\n不可用",
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
                text_color=("#B45309", "#FBBF24"),
                justify="center",
            )
            self.preview_caption_label.configure(
                text="⚠️ 未安装 Pillow，无法显示图片预览。请运行：pip install Pillow",
                text_color=("#B45309", "#FBBF24"),
            )
            return

        image, source_name, message = get_preview_image(folder_path)
        self.initial_preview_source = source_name

        if image is None:
            self.preview_image_label.configure(
                text="图片预览\n不可用",
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
                text_color=("#B45309", "#FBBF24"),
                justify="center",
            )
            return

        if message:
            self._set_preview_image(
                image, f"⚠️ {message}", is_warning=True
            )
        else:
            self._set_preview_image(
                image, self._format_preview_caption(source_name)
            )

    def _safe_lift(self):
        if self.winfo_exists():
            self.lift()

    def _on_close(self):
        self._stop_animation()

        try:
            self.grab_release()
        except Exception:
            pass

        try:
            if self.on_close:
                self.on_close()
        finally:
            self.destroy()


class RenameDialog(ctk.CTkToplevel):
    """重命名主题弹窗：点“确定”后 self.result 为新名字；取消/关闭为 None。"""

    def __init__(self, master, current_name):
        super().__init__(master, fg_color=("#F4F4F5", "#18181B"))
        apply_window_icon(self)

        self.result = None

        self.title("重命名主题")
        self.geometry("440x210")
        self.resizable(False, False)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=24, pady=20)

        label = ctk.CTkLabel(
            container,
            text="请输入新的显示名称：",
            font=ctk.CTkFont(
                family="Microsoft YaHei UI", size=14, weight="bold"
            ),
            text_color=("#111827", "#F4F4F5"),
            anchor="w",
        )
        label.pack(anchor="w")

        self.entry = ctk.CTkEntry(
            container,
            height=34,
            corner_radius=8,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            text_color=("#111827", "#F4F4F5"),
        )
        self.entry.pack(fill="x", pady=(10, 0))
        self.entry.insert(0, current_name)
        # 全选当前名字，直接打字即可覆盖
        self.entry.select_range(0, "end")

        # 预留一行固定高度，提示文字出现时按钮不会跳动
        self.hint_label = ctk.CTkLabel(
            container,
            text="",
            height=18,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=11),
            text_color=("#B91C1C", "#F87171"),
            anchor="w",
        )
        self.hint_label.pack(anchor="w", pady=(6, 0))

        buttons = ctk.CTkFrame(container, fg_color="transparent")
        buttons.pack(fill="x", pady=(8, 0))

        cancel_button = ctk.CTkButton(
            buttons,
            text="取消",
            width=88,
            height=34,
            corner_radius=8,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            fg_color=("#E5E7EB", "#3F3F46"),
            hover_color=("#D1D5DB", "#52525B"),
            text_color=("#111827", "#F4F4F5"),
            command=self._on_cancel,
        )
        cancel_button.pack(side="right")

        ok_button = ctk.CTkButton(
            buttons,
            text="确定",
            width=88,
            height=34,
            corner_radius=8,
            font=ctk.CTkFont(
                family="Microsoft YaHei UI", size=13, weight="bold"
            ),
            fg_color="#2563EB",
            hover_color="#1D4ED8",
            text_color="#FFFFFF",
            command=self._on_ok,
        )
        ok_button.pack(side="right", padx=(0, 10))

        self.entry.bind("<Return>", lambda event: self._on_ok())
        self.entry.bind("<Escape>", lambda event: self._on_cancel())

        # 等窗口真正显示后再抢焦点，避免 Toplevel 创建初期的焦点问题
        self.after(120, self._focus_entry)

    def _focus_entry(self):
        if self.winfo_exists():
            self.lift()
            self.entry.focus_set()

    def _on_ok(self):
        name = self.entry.get().strip()
        if not name:
            # 空名字不修改，弹窗保持打开并提示
            self.hint_label.configure(text="⚠️ 名称不能为空，请输入后再点确定")
            return
        self.result = name
        self.destroy()

    def _on_cancel(self):
        self.result = None
        self.destroy()


class ThemeManagerApp:
    """CustomTkinter 主题管理面板。"""

    def __init__(self, root, appearance_mode="dark"):
        self.root = root
        self.appearance_mode = appearance_mode
        self.apply_queue = queue.Queue()
        self.applying = False
        self.action_buttons = []
        self.disabled_action_buttons = set()
        self.settings_window = None
        self.preview_window = None
        self.help_window = None
        self._status_after_id = None
        self.apply_notice_seconds = load_apply_notice_seconds()
        self.notice = None
        self.notice_label = None
        self._notice_after_id = None
        self.displayed_invalid_keys = set()
        self._was_foreground = True
        self.search_query = ""
        self._search_after_id = None

        self.root.report_callback_exception = self.report_callback_exception
        self.root.title("CursorHub · 鼠标光标主题管理")
        self.root.geometry("880x640")
        self.root.minsize(760, 520)

        # 顶部：标题 + 添加按钮
        header = ctk.CTkFrame(self.root, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(22, 14))

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left", fill="x", expand=True)

        title_label = ctk.CTkLabel(
            title_box,
            text="🎨 CursorHub",
            font=ctk.CTkFont(
                family="Microsoft YaHei UI", size=24, weight="bold"
            ),
            text_color=("#111827", "#F4F4F5"),
            anchor="w",
        )
        title_label.pack(anchor="w")

        self.subtitle_label = ctk.CTkLabel(
            title_box,
            text="收藏的主题保存在 themes.json，点击卡片上的“应用”即可切换。",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
            text_color=("#6B7280", "#A1A1AA"),
            anchor="w",
        )
        self.subtitle_label.pack(anchor="w", pady=(4, 0))

        self.add_button = ctk.CTkButton(
            header,
            text="➕ 添加新主题",
            width=150,
            height=40,
            corner_radius=10,
            font=ctk.CTkFont(
                family="Microsoft YaHei UI", size=14, weight="bold"
            ),
            fg_color="#2563EB",
            hover_color="#1D4ED8",
            text_color="#FFFFFF",
            command=self.add_theme_clicked,
        )
        self.add_button.pack(side="right", padx=(16, 0))

        self.refresh_button = ctk.CTkButton(
            header,
            text="🔄 刷新",
            width=96,
            height=40,
            corner_radius=10,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            fg_color=("#E5E7EB", "#3F3F46"),
            hover_color=("#D1D5DB", "#52525B"),
            text_color=("#111827", "#F4F4F5"),
            command=self.refresh_clicked,
        )
        self.refresh_button.pack(side="right")

        self.cleanup_button = ctk.CTkButton(
            header,
            text="🧹 清理失效",
            width=120,
            height=40,
            corner_radius=10,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            fg_color=("#FEF3C7", "#422006"),
            hover_color=("#FDE68A", "#713F12"),
            text_color=("#92400E", "#FBBF24"),
            command=self.cleanup_invalid_clicked,
        )

        # 搜索栏：按名称或路径过滤卡片
        search_row = ctk.CTkFrame(self.root, fg_color="transparent")
        search_row.pack(fill="x", padx=24, pady=(0, 8))

        self.search_entry = ctk.CTkEntry(
            search_row,
            height=36,
            corner_radius=8,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            text_color=("#111827", "#F4F4F5"),
            placeholder_text="🔍 搜索主题名称或路径",
        )
        self.search_entry.pack(side="left", fill="x", expand=True)
        self.search_entry.bind("<KeyRelease>", self._on_search_key)
        self.search_entry.bind("<Escape>", lambda event: self.clear_search_clicked())

        self.search_clear_button = ctk.CTkButton(
            search_row,
            text="清除",
            width=58,
            height=36,
            corner_radius=8,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
            fg_color=("#E5E7EB", "#3F3F46"),
            hover_color=("#D1D5DB", "#52525B"),
            text_color=("#111827", "#F4F4F5"),
            command=self.clear_search_clicked,
        )
        self.search_clear_button.pack(side="left", padx=(8, 0))

        # 主体：可滚动主题卡片列表
        self.cards_frame = ctk.CTkScrollableFrame(
            self.root,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color=("#D4D4D8", "#3F3F46"),
            scrollbar_button_hover_color=("#A1A1AA", "#52525B"),
        )
        self.cards_frame.pack(fill="both", expand=True, padx=20, pady=(0, 8))

        # 底部栏：左下角设置按钮 + 状态文字
        bottom_bar = ctk.CTkFrame(self.root, fg_color="transparent")
        bottom_bar.pack(fill="x", padx=20, pady=(0, 14))
        bottom_bar.grid_columnconfigure(1, weight=1)

        self.settings_button = ctk.CTkButton(
            bottom_bar,
            text="⚙️ 设置",
            width=100,
            height=34,
            corner_radius=8,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            fg_color=("#E5E7EB", "#3F3F46"),
            hover_color=("#D1D5DB", "#52525B"),
            text_color=("#111827", "#F4F4F5"),
            command=self.open_settings,
        )
        self.settings_button.grid(
            row=0, column=0, sticky="w", padx=(0, 12)
        )

        self.status_label = ctk.CTkLabel(
            bottom_bar,
            text="",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
            text_color=("#6B7280", "#A1A1AA"),
            anchor="w",
        )
        self.status_label.grid(row=0, column=1, sticky="w", padx=(16, 0))

        # 保证按钮始终在其他底部控件之上，避免被提示文字挡住点击
        self.settings_button.lift()

        self._drop_hint_active = False
        self._setup_drag_and_drop()
        self.root.bind("<F5>", lambda event: self.refresh_clicked())
        self.refresh_cards()
        self._report_invalid_paths()
        self.root.after(1000, self._poll_foreground)

    def _setup_drag_and_drop(self):
        """启用从资源管理器拖入文件夹；失败时只记录日志，不影响其他功能。"""
        try:
            import tkinterdnd2
            from tkinterdnd2 import TkinterDnD
        except ImportError:
            logging.info("未安装 tkinterdnd2，拖拽添加功能不可用")
            return

        try:
            TkinterDnD.require(self.root)

            # CTk 根窗口不是 tkinter.BaseWidget，需要手动挂载 DnDWrapper 方法
            for name in (
                "drop_target_register",
                "drop_target_unregister",
                "dnd_bind",
                "_dnd_bind",
                "_substitute_dnd",
            ):
                setattr(
                    self.root,
                    name,
                    types.MethodType(
                        getattr(TkinterDnD.DnDWrapper, name), self.root
                    ),
                )
            self.root._subst_format_dnd = (
                TkinterDnD.DnDWrapper._subst_format_dnd
            )
            self.root._subst_format_str_dnd = (
                TkinterDnD.DnDWrapper._subst_format_str_dnd
            )

            self.root.drop_target_register(tkinterdnd2.DND_FILES)
            self.root.dnd_bind("<<DropEnter>>", self._on_drop_enter)
            self.root.dnd_bind("<<DropLeave>>", self._on_drop_leave)
            self.root.dnd_bind("<<Drop>>", self._on_drop)

            self.subtitle_label.configure(
                text=(
                    "把主题文件夹拖进窗口即可添加 · "
                    "点击卡片上的“应用”即可切换。"
                )
            )
            logging.info("已启用拖拽添加主题")
        except Exception:
            logging.exception("启用拖拽添加失败，可继续使用“添加新主题”按钮")

    def _set_drop_highlight(self, active):
        if active:
            self.cards_frame.configure(
                border_width=2, border_color=("#2563EB", "#60A5FA")
            )
        else:
            self.cards_frame.configure(border_width=0)

    def _on_drop_enter(self, event):
        if not self.applying:
            self._drop_hint_active = True
            self._set_drop_highlight(True)
            self.show_status("松开鼠标即可添加主题文件夹", timeout=None)
        return "copy"

    def _on_drop_leave(self, event):
        self._set_drop_highlight(False)
        if self._drop_hint_active:
            self._drop_hint_active = False
            self._clear_status()
        return "copy"

    def _on_drop(self, event):
        self._set_drop_highlight(False)
        self._drop_hint_active = False

        try:
            raw_paths = self.root.tk.splitlist(event.data)
        except Exception:
            raw_paths = [event.data]

        folders = collect_theme_folders(raw_paths)
        if not folders:
            self.show_status(
                "⚠️ 请拖入包含 .cur / .ani 文件的主题文件夹", timeout=5000
            )
            return "copy"

        if self.applying:
            self.show_status("⏳ 正在应用主题，请稍后再拖入", timeout=5000)
            return "copy"

        added = duplicated = invalid = 0
        errors = []

        for folder in folders:
            try:
                status, theme, matched = self._add_folder_to_library(folder)
            except Exception as exc:
                logging.exception("拖拽添加主题失败：%s", folder)
                errors.append(f"{Path(folder).name}：{exc}")
                continue

            if status == "added":
                added += 1
                logging.info(
                    "拖拽添加主题：%s（%d 个角色）", folder, matched
                )
            elif status == "duplicate":
                duplicated += 1
            else:
                invalid += 1

        parts = []
        if added:
            parts.append(f"已添加 {added} 个主题")
        if duplicated:
            parts.append(f"{duplicated} 个已在列表中")
        if invalid:
            parts.append(f"{invalid} 个没有光标文件")
        if errors:
            parts.append(f"{len(errors)} 个失败")

        self.show_status(
            ("✅ " if added else "⚠️ ") + "｜".join(parts), timeout=7000
        )

        if errors:
            messagebox.showerror(
                "部分主题添加失败",
                "以下文件夹无法添加：\n\n" + "\n".join(errors),
                parent=self.root,
            )

        self.refresh_cards()
        return "copy"

    def _report_invalid_paths(self, manual=False):
        """统计文件夹已失效的主题，并在底部提示。"""
        invalid = self._current_invalid_keys()
        if invalid:
            self.show_status(
                f"⚠️ {len(invalid)} 个主题的文件夹已失效，"
                "可点击卡片上的“重新选择”，或使用「清理失效」批量移除。",
                timeout=9000,
            )
        elif manual:
            self.show_status("✅ 已刷新，所有主题路径有效", timeout=3000)

    def refresh_clicked(self):
        """重新检测所有主题的文件夹是否有效，无需重启程序。"""
        self.refresh_cards()
        self._report_invalid_paths(manual=True)
        logging.info("手动刷新主题列表")

    def _on_search_key(self, event=None):
        """输入搜索词后稍作延迟再刷新，避免每敲一个字都重建卡片。"""
        if self._search_after_id is not None:
            try:
                self.root.after_cancel(self._search_after_id)
            except Exception:
                pass

        self._search_after_id = self.root.after(150, self._apply_search)

    def _apply_search(self):
        self._search_after_id = None
        query = self.search_entry.get().strip()

        if query == self.search_query:
            return

        self.search_query = query
        self.refresh_cards()
        logging.info("搜索主题：%s", query or "(清除)")

    def clear_search_clicked(self):
        if self._search_after_id is not None:
            try:
                self.root.after_cancel(self._search_after_id)
            except Exception:
                pass
            self._search_after_id = None

        if self.search_entry.get():
            self.search_entry.delete(0, "end")

        if self.search_query:
            self.search_query = ""
            self.refresh_cards()

    def _theme_matches_query(self, theme):
        """主题名称或路径是否匹配当前搜索词；空搜索词匹配全部。"""
        query = self.search_query.casefold()
        if not query:
            return True

        name = (theme.get("name") or "").casefold()
        path = (theme.get("path") or "").casefold()
        return query in name or query in path

    def _update_cleanup_button(self):
        """有失效主题时才显示「清理失效」按钮，并更新数量。"""
        count = len(self.displayed_invalid_keys)

        try:
            is_packed = bool(self.cleanup_button.pack_info())
        except Exception:
            is_packed = False

        if not count:
            if is_packed:
                self.cleanup_button.pack_forget()
            return

        self.cleanup_button.configure(text=f"🧹 清理失效 ({count})")
        if not is_packed:
            self.cleanup_button.pack(side="right", padx=(0, 8))

    def cleanup_invalid_clicked(self):
        """一次性从列表中移除所有文件夹已失效的主题。"""
        if self.applying:
            return

        invalid = self._current_invalid_keys()
        if not invalid:
            self.refresh_cards()
            self.show_status("✅ 没有失效主题需要清理", timeout=3000)
            return

        themes = load_themes()
        names = [
            theme.get("name") or Path(theme.get("path", "")).name
            for theme in themes
            if os.path.normcase(theme.get("path", "")) in invalid
        ]
        preview = "、".join(names[:6])
        if len(names) > 6:
            preview += f" 等 {len(names)} 个"

        confirmed = messagebox.askyesno(
            "清理失效主题",
            f"将从列表中移除 {len(names)} 个文件夹已失效的主题：\n\n"
            f"{preview}\n\n"
            "只会移除卡片，不会删除文件夹，也不会改变当前光标。",
            parent=self.root,
        )
        if not confirmed:
            return

        kept = [
            theme
            for theme in themes
            if os.path.normcase(theme.get("path", "")) not in invalid
        ]

        try:
            save_themes(kept)
        except OSError as exc:
            logging.exception("清理失效主题失败")
            messagebox.showerror(
                "清理失败",
                f"无法更新 themes.json：\n\n{exc}",
                parent=self.root,
            )
            return

        removed = len(themes) - len(kept)
        logging.info("已清理 %d 个失效主题", removed)
        self.refresh_cards()
        self.show_status(f"🧹 已清理 {removed} 个失效主题", timeout=5000)

    def _current_invalid_keys(self):
        """返回当前实际失效的主题路径集合。"""
        invalid = set()
        for theme in load_themes():
            path = theme.get("path", "")
            if path and not Path(path).is_dir():
                invalid.add(os.path.normcase(path))
        return invalid

    def _is_window_foreground(self):
        """主窗口（含子控件）是否处于系统前台。"""
        try:
            hwnd = int(self.root.wm_frame(), 16)
        except Exception:
            return True

        foreground = user32.GetForegroundWindow()
        if not foreground:
            return False
        return user32.GetAncestor(foreground, 2) == hwnd

    def _poll_foreground(self):
        """每秒检查一次；从其他程序切回时自动检测失效主题。"""
        try:
            is_foreground = self._is_window_foreground()
        except Exception:
            is_foreground = True

        if is_foreground and not self._was_foreground:
            self._on_app_focus()
        self._was_foreground = is_foreground
        self.root.after(1000, self._poll_foreground)

    def _on_app_focus(self):
        """切回程序时检查主题路径，仅在失效状态变化时重建列表。"""
        if self.applying:
            return

        invalid = self._current_invalid_keys()
        if invalid == self.displayed_invalid_keys:
            return

        self.refresh_cards()
        if invalid:
            self.show_status(
                f"⚠️ {len(invalid)} 个主题的文件夹已失效，"
                "可点击卡片上的“重新选择”，或使用「清理失效」批量移除。",
                timeout=9000,
            )
        else:
            self.show_status("✅ 主题路径已恢复", timeout=3000)

    def report_callback_exception(self, exc_type, exc_value, exc_tb):
        """捕获界面回调里未处理的异常，写入日志并提示用户。"""
        logging.critical("界面回调异常", exc_info=(exc_type, exc_value, exc_tb))
        try:
            messagebox.showerror(
                "程序错误",
                f"界面操作出现异常：\n\n{exc_type.__name__}: {exc_value}\n\n"
                f"详情已写入日志：\n{LOG_FILE}",
                parent=self.root,
            )
        except Exception:
            pass

    def show_status(self, text, timeout=5000):
        """底部状态提示；timeout 毫秒后自动消失，None 表示常驻。"""
        if self._status_after_id is not None:
            try:
                self.root.after_cancel(self._status_after_id)
            except Exception:
                pass
            self._status_after_id = None

        self.status_label.configure(text=text)

        if timeout is not None:
            self._status_after_id = self.root.after(
                timeout, self._clear_status
            )

    def _clear_status(self):
        self._status_after_id = None
        self.status_label.configure(text="")

    def _theme_notice_key(self, theme):
        """给主题生成稳定的标识，用于把提示显示在对应卡片上。"""
        if theme.get("builtin"):
            return "__builtin__"
        return os.path.normcase(theme.get("path", ""))

    def _set_card_notice(self, theme, text, level="ok"):
        """在卡片按钮下方显示结果，停留时间由设置决定。"""
        if self._notice_after_id is not None:
            try:
                self.root.after_cancel(self._notice_after_id)
            except Exception:
                pass
            self._notice_after_id = None

        self.notice = {
            "key": self._theme_notice_key(theme),
            "text": text,
            "level": level,
        }
        self.refresh_cards()

        seconds = self.apply_notice_seconds
        if seconds and seconds > 0:
            self._notice_after_id = self.root.after(
                seconds * 1000, self._clear_card_notice
            )

    def _clear_card_notice(self):
        self._notice_after_id = None
        self.notice = None
        if self.notice_label is not None:
            try:
                self.notice_label.destroy()
            except Exception:
                pass
            self.notice_label = None

    def _on_settings_closed(self):
        self.settings_window = None

    def _on_preview_closed(self):
        self.preview_window = None

    def open_preview(self, theme):
        """打开主题文字预览窗口。"""
        if self.preview_window is not None:
            try:
                if self.preview_window.winfo_exists():
                    self.preview_window.destroy()
            except Exception:
                pass
            self.preview_window = None

        self.preview_window = PreviewWindow(
            self.root,
            theme,
            on_close=self._on_preview_closed,
        )

    def open_settings(self):
        """打开设置窗口；已经打开时把它恢复到前台。"""
        if self.settings_window is not None:
            try:
                exists = self.settings_window.winfo_exists()
            except Exception:
                exists = False

            if exists:
                self.settings_window.deiconify()
                self.settings_window.lift()
                self.settings_window.focus_force()
                return

            self.settings_window = None

        self.settings_window = SettingsWindow(
            self.root,
            current_mode=self.appearance_mode,
            notice_seconds=self.apply_notice_seconds,
            on_change=self.on_appearance_change,
            on_notice_change=self.on_apply_notice_change,
            on_help=self.open_help,
            on_cursors_restored=self.on_cursors_restored,
            on_close=self._on_settings_closed,
        )

    def on_cursors_restored(self, restored, failed):
        """设置窗口恢复光标后，在主窗口状态栏给出反馈。"""
        if failed:
            self.show_status(
                f"⚠️ 已恢复 {restored} 个角色，{len(failed)} 个失败",
                timeout=8000,
            )
        else:
            self.show_status(
                f"✅ 已恢复上次光标（{restored} 个角色）",
                timeout=5000,
            )

    def open_help(self):
        """打开使用说明窗口；已经打开时把它恢复到前台。"""
        if self.help_window is not None:
            try:
                exists = self.help_window.winfo_exists()
            except Exception:
                exists = False

            if exists:
                self.help_window.deiconify()
                self.help_window.lift()
                self.help_window.focus_force()
                return

            self.help_window = None

        self.help_window = HelpWindow(
            self.root,
            on_close=self._on_help_closed,
        )

    def _on_help_closed(self):
        self.help_window = None

    def on_apply_notice_change(self, seconds):
        """保存应用完成提示的停留时间。"""
        self.apply_notice_seconds = seconds

        # 当前已有提示时，按新时长重新计时
        if self.notice is not None:
            if self._notice_after_id is not None:
                try:
                    self.root.after_cancel(self._notice_after_id)
                except Exception:
                    pass
                self._notice_after_id = None
            if seconds and seconds > 0:
                self._notice_after_id = self.root.after(
                    seconds * 1000, self._clear_card_notice
                )

        try:
            save_apply_notice_seconds(seconds)
        except Exception as exc:
            logging.exception("保存应用提示设置失败")
            messagebox.showerror(
                "保存设置失败",
                f"提示停留时间已更新，但写入 settings.json 失败：\n\n{exc}",
                parent=self.root,
            )

    def on_appearance_change(self, mode):
        self.appearance_mode = mode
        try:
            save_appearance_mode(mode)
        except Exception as exc:
            logging.exception("保存外观设置失败")
            messagebox.showerror(
                "保存设置失败",
                f"外观已经切换，但写入 settings.json 失败：\n\n{exc}",
                parent=self.root,
            )

    def refresh_cards(self):
        """重新读取 themes.json，按搜索词过滤后渲染卡片。"""
        for widget in self.cards_frame.winfo_children():
            widget.destroy()
        self.action_buttons.clear()
        self.disabled_action_buttons.clear()
        self.notice_label = None

        all_themes = sorted(
            load_themes(),
            key=lambda item: item.get("last_used") or "",
            reverse=True,
        )
        current = get_current_cursors()

        self.displayed_invalid_keys = {
            os.path.normcase(theme.get("path", ""))
            for theme in all_themes
            if theme.get("path") and not Path(theme["path"]).is_dir()
        }
        self._update_cleanup_button()

        themes = [
            theme for theme in all_themes if self._theme_matches_query(theme)
        ]
        builtin_matches = self._theme_matches_query(SYSTEM_DEFAULT_THEME)

        # 固定在列表最上方的 Windows 系统默认主题
        if builtin_matches:
            self._create_card(
                SYSTEM_DEFAULT_THEME,
                system_default_is_active(current),
            )

        if not themes:
            if not self.search_query:
                empty_text = (
                    "还没有收藏的主题。\n"
                    "点击右上角的“➕ 添加新主题”开始吧。"
                )
            elif not builtin_matches:
                empty_text = (
                    f"没有找到匹配「{self.search_query}」的主题。\n"
                    "试试其他关键词，或点击右侧“清除”。"
                )
            else:
                return

            empty = ctk.CTkLabel(
                self.cards_frame,
                text=empty_text,
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=15),
                text_color=("#6B7280", "#A1A1AA"),
                justify="center",
            )
            empty.pack(pady=40)
            return

        for theme in themes:
            path = theme.get("path", "")
            is_active = bool(path) and theme_is_active(path, current)
            self._create_card(theme, is_active)

    def _create_card(self, theme, is_active):
        is_builtin = bool(theme.get("builtin"))
        theme_path = "" if is_builtin else theme.get("path", "")
        path_exists = is_builtin or (
            bool(theme_path) and Path(theme_path).is_dir()
        )

        if path_exists:
            card_fg = ("#FFFFFF", "#27272A")
            name_color = ("#111827", "#F4F4F5")
        else:
            card_fg = ("#F3F4F6", "#1F1F23")
            name_color = ("#9CA3AF", "#71717A")

        if not path_exists:
            border_color = ("#D4D4D8", "#3F3F46")
        elif is_active:
            border_color = ("#16A34A", "#22C55E")
        else:
            border_color = ("#E4E4E7", "#3F3F46")

        card = ctk.CTkFrame(
            self.cards_frame,
            corner_radius=12,
            fg_color=card_fg,
            border_width=2 if is_active and path_exists else 1,
            border_color=border_color,
        )
        card.pack(fill="x", padx=6, pady=6)
        card.grid_columnconfigure(0, weight=1)

        info = ctk.CTkFrame(card, fg_color="transparent")
        info.grid(row=0, column=0, sticky="w", padx=(18, 8), pady=14)

        name_row = ctk.CTkFrame(info, fg_color="transparent")
        name_row.pack(anchor="w")

        name_label = ctk.CTkLabel(
            name_row,
            text=theme.get("name") or "未命名主题",
            font=ctk.CTkFont(
                family="Microsoft YaHei UI", size=15, weight="bold"
            ),
            text_color=name_color,
            anchor="w",
            wraplength=430,
            justify="left",
        )
        name_label.pack(side="left")

        if is_active:
            badge = ctk.CTkLabel(
                name_row,
                text="✅ 当前使用中",
                font=ctk.CTkFont(
                    family="Microsoft YaHei UI", size=12, weight="bold"
                ),
                text_color=("#15803D", "#4ADE80"),
            )
            badge.pack(side="left", padx=(12, 0))

        if not path_exists:
            missing_badge = ctk.CTkLabel(
                name_row,
                text="⚠️ 路径失效",
                font=ctk.CTkFont(
                    family="Microsoft YaHei UI", size=12, weight="bold"
                ),
                text_color=("#B45309", "#FBBF24"),
            )
            missing_badge.pack(side="left", padx=(12, 0))

        if is_builtin:
            meta_text = "系统内置主题 · 一键恢复 Windows 默认光标"
        else:
            last_used = format_last_used(theme.get("last_used"))
            meta_text = f"上次使用：{last_used}"
            if not path_exists:
                meta_text += "　文件夹已不存在，请重新选择或删除"

        meta_label = ctk.CTkLabel(
            info,
            text=meta_text,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
            text_color=(
                ("#6B7280", "#A1A1AA")
                if path_exists
                else ("#B45309", "#FBBF24")
            ),
            anchor="w",
        )
        meta_label.pack(anchor="w", pady=(4, 0))

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=0, column=1, sticky="e", padx=(8, 18), pady=14)

        card_action_buttons = []

        if path_exists:
            apply_fg = "#2563EB"
            apply_hover = "#1D4ED8"
            apply_text_color = "#FFFFFF"
        else:
            apply_fg = ("#E5E7EB", "#3F3F46")
            apply_hover = ("#E5E7EB", "#3F3F46")
            apply_text_color = ("#9CA3AF", "#71717A")

        apply_button = ctk.CTkButton(
            actions,
            text="重新应用" if is_active else "应用",
            width=88,
            height=34,
            corner_radius=8,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            fg_color=apply_fg,
            hover_color=apply_hover,
            text_color=apply_text_color,
            state="normal" if path_exists else "disabled",
            command=lambda t=theme: self.apply_theme_clicked(t),
        )
        apply_button.pack(side="left", padx=(0, 8))
        card_action_buttons.append(apply_button)
        if not path_exists:
            self.disabled_action_buttons.add(apply_button)

        if not is_builtin:
            rename_button = ctk.CTkButton(
                actions,
                text="✏️ 重命名",
                width=100,
                height=34,
                corner_radius=8,
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
                fg_color=("#E5E7EB", "#3F3F46"),
                hover_color=("#D1D5DB", "#52525B"),
                text_color=("#111827", "#F4F4F5"),
                command=lambda t=theme: self.rename_theme_clicked(t),
            )
            rename_button.pack(side="left", padx=(0, 8))
            card_action_buttons.append(rename_button)

            if path_exists:
                preview_button = ctk.CTkButton(
                    actions,
                    text="🖼️ 预览",
                    width=88,
                    height=34,
                    corner_radius=8,
                    font=ctk.CTkFont(
                        family="Microsoft YaHei UI", size=13
                    ),
                    fg_color=("#E5E7EB", "#3F3F46"),
                    hover_color=("#D1D5DB", "#52525B"),
                    text_color=("#111827", "#F4F4F5"),
                    command=lambda t=theme: self.open_preview(t),
                )
            else:
                preview_button = ctk.CTkButton(
                    actions,
                    text="📂 重新选择",
                    width=100,
                    height=34,
                    corner_radius=8,
                    font=ctk.CTkFont(
                        family="Microsoft YaHei UI", size=13
                    ),
                    fg_color=("#FEF3C7", "#422006"),
                    hover_color=("#FDE68A", "#57330A"),
                    text_color=("#B45309", "#FBBF24"),
                    command=lambda t=theme: self.relocate_theme_clicked(t),
                )
            preview_button.pack(side="left", padx=(0, 8))
            card_action_buttons.append(preview_button)

            delete_button = ctk.CTkButton(
                actions,
                text="删除",
                width=72,
                height=34,
                corner_radius=8,
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
                fg_color=("#DC2626", "#B91C1C"),
                hover_color=("#B91C1C", "#991B1B"),
                text_color="#FFFFFF",
                command=lambda t=theme: self.delete_theme_clicked(t),
            )
            delete_button.pack(side="left")
            card_action_buttons.append(delete_button)

        self.action_buttons.extend(card_action_buttons)

        if self.notice and self.notice["key"] == self._theme_notice_key(theme):
            self.notice_label = ctk.CTkLabel(
                card,
                text=self.notice["text"],
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
                text_color=(
                    ("#15803D", "#4ADE80")
                    if self.notice["level"] == "ok"
                    else ("#B45309", "#FBBF24")
                ),
                anchor="e",
            )
            self.notice_label.grid(
                row=1, column=1, sticky="e", padx=(8, 18), pady=(0, 12)
            )

    def _add_folder_to_library(self, folder):
        """扫描并收藏一个文件夹；返回 (status, theme, 匹配角色数)。

        status 为 added / duplicate / invalid。
        """
        mapping = scan_and_map(folder, verbose=False)
        if not mapping:
            return "invalid", None, 0

        before = len(load_themes())
        theme = add_theme(folder)
        after = len(load_themes())
        status = "added" if after > before else "duplicate"
        return status, theme, len(mapping)

    def add_theme_clicked(self):
        if self.applying:
            return

        folder = filedialog.askdirectory(
            title="选择包含光标文件的主题文件夹"
        )
        if not folder:
            return

        try:
            status, theme, matched = self._add_folder_to_library(folder)
        except Exception as exc:
            logging.exception("添加主题失败：%s", folder)
            messagebox.showerror(
                "添加失败",
                f"保存主题失败：\n\n{exc}",
                parent=self.root,
            )
            return

        if status == "invalid":
            messagebox.showerror(
                "添加失败",
                "这个文件夹里没有找到有效的 .cur / .ani 光标文件。",
                parent=self.root,
            )
            return

        if status == "duplicate":
            self.show_status(
                f"该主题已经在列表中：{theme['name']}", timeout=4000
            )
        else:
            self.show_status(
                f"已添加主题：{theme['name']}（匹配 {matched} 个角色）",
                timeout=4000,
            )

        self.refresh_cards()

    def delete_theme_clicked(self, theme):
        if self.applying:
            return

        name = theme.get("name") or "未命名主题"
        is_active = theme_is_active(theme.get("path", ""))

        message = (
            f"确定从列表中删除「{name}」吗？\n\n"
            "只会移除卡片，不会删除文件夹，也不会改变当前光标。"
        )
        if is_active:
            message += "\n\n注意：该主题当前正在使用中。"

        if not messagebox.askyesno("删除主题", message, parent=self.root):
            return

        try:
            delete_theme(theme.get("path", ""))
        except Exception as exc:
            logging.exception("删除主题失败：%s", theme.get("path", ""))
            messagebox.showerror(
                "删除失败",
                f"无法更新 themes.json：\n\n{exc}",
                parent=self.root,
            )
            return

        self.show_status(f"已删除：{name}", timeout=4000)
        self.refresh_cards()

    def rename_theme_clicked(self, theme):
        if self.applying:
            return

        old_name = theme.get("name") or ""

        dialog = RenameDialog(self.root, old_name)
        # 阻塞等待弹窗关闭；关闭后 dialog.result 是新名字或 None
        self.root.wait_window(dialog)
        new_name = dialog.result

        # 取消、留空或名字没变，都不做修改
        if not new_name or new_name == old_name:
            return

        try:
            renamed = rename_theme(theme.get("path", ""), new_name)
        except Exception as exc:
            logging.exception("重命名主题失败：%s", theme.get("path", ""))
            messagebox.showerror(
                "重命名失败",
                f"无法更新 themes.json：\n\n{exc}",
                parent=self.root,
            )
            return

        if not renamed:
            self.show_status("❌ 重命名失败：列表中找不到该主题", timeout=4000)
            self.refresh_cards()
            return

        logging.info("主题已重命名：%s -> %s", old_name or "(空)", new_name)
        self.show_status(f"已重命名：{new_name}", timeout=4000)
        self.refresh_cards()

    def relocate_theme_clicked(self, theme):
        """为路径失效的主题重新选择一个光标文件夹。"""
        if self.applying:
            return

        old_path = theme.get("path", "")
        name = theme.get("name") or "未命名主题"

        initial_dir = None
        if old_path:
            parent = Path(old_path).parent
            if parent.is_dir():
                initial_dir = str(parent)

        folder = filedialog.askdirectory(
            title=f"重新选择「{name}」的光标文件夹",
            initialdir=initial_dir,
        )
        if not folder:
            return

        mapping = scan_and_map(folder, verbose=False)
        if not mapping:
            messagebox.showerror(
                "重新选择失败",
                "这个文件夹里没有找到有效的 .cur / .ani 光标文件。",
                parent=self.root,
            )
            return

        new_theme, error = relocate_theme(old_path, folder)
        if new_theme is None:
            messagebox.showerror(
                "重新选择失败",
                error or "无法更新 themes.json。",
                parent=self.root,
            )
            return

        logging.info(
            "主题路径已重新定位：%s -> %s", old_path, new_theme["path"]
        )
        self.show_status(
            f"✅ 已重新定位：{name}（匹配 {len(mapping)} 个角色）",
            timeout=5000,
        )
        self.refresh_cards()

    def apply_theme_clicked(self, theme):
        if self.applying:
            return

        if not theme.get("builtin"):
            path = theme.get("path", "")
            if not Path(path).is_dir():
                messagebox.showerror(
                    "无法应用",
                    f"主题文件夹不存在：\n{path}",
                    parent=self.root,
                )
                return

        self.applying = True
        self._set_busy_state(True)
        self.show_status(
            f"⏳ 正在应用：{theme.get('name', '未命名主题')} …",
            timeout=None,
        )

        worker = threading.Thread(
            target=self._apply_worker,
            args=(theme,),
            daemon=True,
        )
        worker.start()
        self.root.after(100, self._poll_apply_result)

    def _set_busy_state(self, busy):
        state = "disabled" if busy else "normal"
        self.add_button.configure(state=state)
        self.refresh_button.configure(state=state)
        self.cleanup_button.configure(state=state)
        self.search_entry.configure(state=state)
        self.search_clear_button.configure(state=state)
        for button in self.action_buttons:
            if not busy and button in self.disabled_action_buttons:
                continue
            button.configure(state=state)

    def _apply_worker(self, theme):
        """后台线程：只做实际工作，不直接操作界面控件。"""
        is_builtin = bool(theme.get("builtin"))
        path = theme.get("path", "")
        name = theme.get("name", "未命名主题")
        log_target = "内置 Windows 默认" if is_builtin else path
        logging.info("开始应用主题：%s（%s）", name, log_target)

        # 应用前先快照当前光标配置，设置里可一键恢复
        try:
            backup_current_cursors(reason=f"apply:{name}")
        except Exception:
            logging.exception("应用前自动备份光标失败")

        try:
            if is_builtin:
                result = apply_system_default_theme()
            else:
                result = apply_theme(path, verbose=False)
                if result is not None:
                    mark_theme_used(path)
        except Exception as exc:
            logging.exception("应用主题失败：%s", log_target)
            self.apply_queue.put(("error", theme, str(exc)))
            return

        if result is None:
            logging.warning("主题没有有效光标文件：%s", log_target)
            self.apply_queue.put(("empty", theme, None))
            return

        matched, failed = result
        if failed:
            logging.warning(
                "主题部分应用：%s，失败角色：%s",
                log_target,
                [role for role, _ in failed],
            )
        else:
            logging.info("主题应用成功：%s（%d 个角色）", log_target, matched)

        self.apply_queue.put(("success", theme, (matched, failed)))

    def _poll_apply_result(self):
        """主线程轮询后台结果，保证所有界面操作都在主线程执行。"""
        try:
            kind, theme, payload = self.apply_queue.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll_apply_result)
            return

        self.applying = False
        self._set_busy_state(False)

        name = theme.get("name", "未命名主题")

        if kind == "success":
            matched, failed = payload
            applied = matched - len(failed)

            self._clear_status()

            if failed:
                self._set_card_notice(
                    theme,
                    f"⚠️ 已应用 {applied}/{matched} 个角色，"
                    "其余注销或重启后生效",
                    "warn",
                )
            else:
                self._set_card_notice(
                    theme,
                    f"✅ 已应用 {matched} 个角色，光标已更新",
                )
            return

        self.refresh_cards()

        if kind == "empty":
            self.show_status("❌ 未找到有效光标文件", timeout=6000)
            messagebox.showerror(
                "应用失败",
                "这个主题文件夹里没有找到有效的 .cur / .ani 文件。",
                parent=self.root,
            )
        else:
            self.show_status("❌ 应用失败", timeout=6000)
            messagebox.showerror(
                "应用失败",
                f"应用「{name}」时出错：\n\n{payload}",
                parent=self.root,
            )


def main():
    sys.excepthook = log_uncaught_exception
    threading.excepthook = log_thread_exception
    logging.info("启动主题管理面板，数据文件：%s", THEMES_FILE)

    appearance_mode = load_appearance_mode()
    ctk.set_appearance_mode(appearance_mode)
    logging.info(
        "外观模式：%s",
        APPEARANCE_LABELS.get(appearance_mode, appearance_mode),
    )

    set_app_user_model_id()

    root = ctk.CTk(fg_color=("#F4F4F5", "#18181B"))
    apply_window_icon(root)
    ThemeManagerApp(root, appearance_mode=appearance_mode)
    try:
        root.mainloop()
    finally:
        logging.info("主题管理面板已关闭")


if __name__ == "__main__":
    if "--test-storage" in sys.argv:
        test_theme_storage()
    else:
        try:
            main()
        except Exception:
            logging.exception("程序启动失败")
            raise
