#!/usr/bin/env python3
"""
Rekordbox Vault (v4.2) — Universal (Windows & macOS Ventura 13.7+)
Exporta e importa playlists e samples selecionados (.zip + áudios + Samples do Rekordbox + XML)
entre Windows e Mac sem pendrive físico, com ícones de controle rápido e Tutorial integrado.
"""

from __future__ import annotations

import ctypes
import datetime
import glob
import http.server
import json
import os
import platform
import re
import shutil
import socket
import string
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.parse
import webbrowser
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

IS_WIN = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

APP_VERSION = "v0.3.0"
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    _meipass = Path(sys._MEIPASS)
    APP_DIR = (_meipass / "app") if (_meipass / "app" / "ui.html").exists() else _meipass
    APP_ROOT = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
    APP_ROOT = APP_DIR.parent

UI_FILE = APP_DIR / "ui.html"
GITHUB_REPO = "DyFuchs/rb-vault"

# Unified Rekordbox_Vault folder system under Documents
VAULT_HOME = Path.home() / "Documents" / "Rekordbox_Vault"
CACHE_DIR = VAULT_HOME / ".cache_xml"
VAULT_PACKAGES_DIR = VAULT_HOME / "Pacotes"
VAULT_CREATED_DIR = VAULT_PACKAGES_DIR / "Pacotes Criados"
VAULT_IMPORTED_DIR = VAULT_PACKAGES_DIR / "Pacotes Importados"
VAULT_PROFILES_DIR = VAULT_HOME / "Perfis"
VAULT_BACKUPS_DIR = VAULT_HOME / "Backups"
VAULT_UPDATES_DIR = VAULT_HOME / "Atualizacoes"
SETTINGS_FILE = VAULT_PROFILES_DIR / "vault_settings.json"

for _vd in (VAULT_CREATED_DIR, VAULT_IMPORTED_DIR, VAULT_PROFILES_DIR, VAULT_BACKUPS_DIR, VAULT_UPDATES_DIR):
    try:
        _vd.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

AUDIO_EXTS = {".mp3", ".wav", ".aiff", ".aif", ".flac", ".m4a", ".aac", ".ogg", ".alac", ".mp4"}

try:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    CACHE_DIR = Path(tempfile.gettempdir()) / "rekordbox_vault_cache"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)




_PROGRESS_LOCK = threading.Lock()
_PROGRESS_JOBS: dict[str, dict] = {}


def set_job_progress(job_id: str | None, pct: int, message: str = "") -> None:
    if not job_id:
        return
    clamped = max(0, min(100, int(pct)))
    with _PROGRESS_LOCK:
        _PROGRESS_JOBS[job_id] = {"pct": clamped, "message": message, "ts": time.time()}
        if len(_PROGRESS_JOBS) > 40:
            oldest = sorted(_PROGRESS_JOBS.items(), key=lambda kv: kv[1]["ts"])[:15]
            for k, _ in oldest:
                _PROGRESS_JOBS.pop(k, None)


def get_job_progress(job_id: str) -> dict:
    with _PROGRESS_LOCK:
        return dict(_PROGRESS_JOBS.get(job_id) or {"pct": 0, "message": ""})


def default_vault_settings() -> dict:
    return {
        "advancedMode": False,
        "theme": "studio-dark",
        "fontScale": 100,
        "appZoom": 100,
        "appWidth": 860,
        "lastImportBrowseDir": "",
        "activeProfile": "",
        "profiles": [],
        "galleryCreatedDir": str(VAULT_CREATED_DIR),
        "galleryImportedDir": str(VAULT_IMPORTED_DIR),
        "galleryVisibleCols": ["name", "origin", "date", "playlists", "samples", "size", "tags"],
        "galleryCustomCols": [],
        "galleryTagsCatalog": ["Show", "Baile", "Set", "Backup"],
        "galleryMeta": {},
    }


def load_vault_settings() -> dict:
    base = default_vault_settings()
    if SETTINGS_FILE.exists():
        try:
            raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                base.update(raw)
        except Exception:
            pass
    if not isinstance(base.get("profiles"), list):
        base["profiles"] = []
    return base


def save_vault_settings(data: dict) -> dict:
    cur = load_vault_settings()
    for k in ("advancedMode", "theme", "fontScale", "appZoom", "appWidth", "lastImportBrowseDir", "activeProfile", "profiles", "galleryCreatedDir", "galleryImportedDir", "galleryVisibleCols", "galleryCustomCols", "galleryTagsCatalog", "galleryMeta"):
        if k in data:
            cur[k] = data[k]
    VAULT_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    # Sync individual user profile files inside Perfis/
    try:
        valid_files = {"vault_settings.json"}
        for prof in cur.get("profiles") or []:
            pname = str(prof.get("name") or "").strip()
            if not pname:
                continue
            safe_fn = re.sub(r"[^a-zA-Z0-9_-]+", "_", pname).strip("_") or "perfil"
            fname = f"perfil_{safe_fn}.json"
            valid_files.add(fname)
            (VAULT_PROFILES_DIR / fname).write_text(json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8")
        for existing in VAULT_PROFILES_DIR.glob("perfil_*.json"):
            if existing.name not in valid_files:
                existing.unlink(missing_ok=True)
    except Exception:
        pass
    return cur


def now_str() -> str:
    return datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")


def format_bytes(n: int) -> str:
    n = int(n or 0)
    if n >= 1024 ** 3:
        return f"{n / (1024 ** 3):.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / (1024 ** 2):.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


def get_desktop_dir() -> Path:
    return Path.home() / "Desktop"


def get_music_dir() -> Path:
    return Path.home() / "Music"


_WIN_NO_WINDOW: dict = {"creationflags": 0x08000000} if IS_WIN else {}


# ---------------------------------------------------------------------------
# Rekordbox Process Control (Status, Launch, Close, Quick Restart)
# ---------------------------------------------------------------------------
def is_rekordbox_running() -> bool:
    if IS_WIN:
        res = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq rekordbox.exe", "/NH"],
            capture_output=True, text=True, **_WIN_NO_WINDOW,
        )
        return "rekordbox.exe" in res.stdout.lower()
    if IS_MAC:
        res = subprocess.run(["pgrep", "-x", "rekordbox"], capture_output=True, text=True)
        return res.returncode == 0
    return False


def stop_rekordbox(include_main: bool = True, graceful: bool = False, allow_force: bool = True) -> bool:
    if IS_WIN:
        if include_main:
            if graceful or not allow_force:
                subprocess.run(["taskkill", "/IM", "rekordbox.exe"], capture_output=True, **_WIN_NO_WINDOW)
                for _ in range(48):
                    if not is_rekordbox_running():
                        time.sleep(0.9)
                        break
                    time.sleep(0.25)
            if allow_force and is_rekordbox_running():
                subprocess.run(["taskkill", "/IM", "rekordbox.exe", "/F"], capture_output=True, **_WIN_NO_WINDOW)
                time.sleep(0.5)
        if allow_force or not is_rekordbox_running():
            subprocess.run(["taskkill", "/IM", "rekordboxAgent.exe", "/F"], capture_output=True, **_WIN_NO_WINDOW)
            subprocess.run(["taskkill", "/IM", "rekordboxAgent-win32-x64.exe", "/F"], capture_output=True, **_WIN_NO_WINDOW)
        return not is_rekordbox_running()
    if IS_MAC:
        if include_main:
            subprocess.run(["osascript", "-e", 'quit app "rekordbox"'], capture_output=True)
            wait_steps = 48 if (graceful or not allow_force) else 6
            for _ in range(wait_steps):
                if not is_rekordbox_running():
                    if graceful or not allow_force:
                        time.sleep(0.9)
                    break
                time.sleep(0.25)
            if allow_force and is_rekordbox_running():
                subprocess.run(["pkill", "-x", "rekordbox"], capture_output=True)
                time.sleep(0.5)
        if allow_force or not is_rekordbox_running():
            subprocess.run(["pkill", "-f", "rekordboxAgent"], capture_output=True)
        return not is_rekordbox_running()
    return True


def find_rekordbox_executable() -> str:
    if IS_WIN:
        patterns = [
            r"C:\Program Files\rekordbox\rekordbox*\rekordbox.exe",
            r"C:\Program Files\Pioneer\rekordbox*\rekordbox.exe",
            r"C:\Program Files (x86)\Pioneer\rekordbox*\rekordbox.exe",
        ]
        found = []
        for pat in patterns:
            found.extend(glob.glob(pat))
        if found:
            found.sort(reverse=True)
            return found[0]
        return ""
    if IS_MAC:
        patterns = [
            "/Applications/rekordbox*/rekordbox.app",
            "/Applications/rekordbox.app",
        ]
        found = []
        for pat in patterns:
            found.extend(glob.glob(pat))
        if found:
            found.sort(reverse=True)
            return found[0]
        return "rekordbox"
    return ""


def launch_rekordbox() -> str:
    exe = find_rekordbox_executable()
    if IS_WIN:
        if not exe or not Path(exe).exists():
            raise RuntimeError("Executável do Rekordbox não encontrado em C:\\Program Files\\rekordbox ou Pioneer.")
        subprocess.Popen([exe], creationflags=0x00000008)  # DETACHED_PROCESS
        return exe
    if IS_MAC:
        if exe and Path(exe).exists():
            subprocess.Popen(["open", exe])
        else:
            subprocess.Popen(["open", "-a", "rekordbox"])
        return exe or "rekordbox"
    raise RuntimeError("Sistema não suportado para abrir o Rekordbox.")


def restart_rekordbox() -> str:
    stop_rekordbox(include_main=True)
    time.sleep(1.5)
    return launch_rekordbox()


def get_os_rekordbox_roots() -> dict:
    if IS_WIN:
        appdata = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
        pioneer = appdata / "Pioneer"
        return {
            "os": "windows",
            "db_root": pioneer / "rekordbox",
            "config_root": pioneer,
        }
    lib = Path.home() / "Library"
    return {
        "os": "macos",
        "db_root": lib / "Pioneer" / "rekordbox",
        "config_root": lib / "Application Support" / "Pioneer",
    }


def get_default_rekordbox_xml_path() -> Path:
    if IS_WIN:
        appdata = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
        return appdata / "rekordbox" / "rekordbox" / "rekordbox.xml"
    return Path.home() / "Library" / "rekordbox" / "rekordbox.xml"


def get_configured_rekordbox_xml_paths() -> list[Path]:
    """Reads all currently configured *.xml Imported Library paths from rekordbox3.settings."""
    roots = get_os_rekordbox_roots()
    config_root = Path(roots["config_root"])
    found: list[Path] = []
    seen = set()
    for folder_name in ("rekordbox7", "rekordbox6", "rekordbox"):
        sf = config_root / folder_name / "rekordbox3.settings"
        if not sf.exists():
            continue
        try:
            raw = sf.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(r'<VALUE\s+name="([^"]*)"\s+val="([^"]*\.xml)"', raw, flags=re.IGNORECASE):
                k_name = m.group(1).lower()
                val = m.group(2).strip()
                if not val:
                    continue
                if any(tok in k_name for tok in ("import", "xml", "bridge", "itunes")):
                    if "itunes" in k_name and "rekordbox" not in val.lower():
                        continue
                    p = Path(val).expanduser()
                    norm = str(p).lower()
                    if norm not in seen:
                        seen.add(norm)
                        found.append(p)
        except Exception:
            continue
    return found


def configure_rekordbox_xml_path(xml_file_path: Path) -> tuple[bool, str]:
    """
    Mirrors xml_file_path directly to:
    1) Every XML path currently configured in rekordbox3.settings (works live even if Rekordbox is open!)
    2) Standard OS default Rekordbox XML locations (Windows & macOS)
    And updates rekordbox3.settings if Rekordbox is closed.
    """
    targets: list[Path] = []
    targets.extend(get_configured_rekordbox_xml_paths())
    default_xml = get_default_rekordbox_xml_path()
    targets.append(default_xml)
    if IS_MAC:
        targets.append(Path.home() / "Library" / "Application Support" / "rekordbox" / "rekordbox.xml")
        targets.append(Path.home() / "Library" / "Pioneer" / "rekordbox" / "rekordbox.xml")

    mirrored_paths: list[str] = []
    seen = set()
    for t in targets:
        try:
            norm = str(t).lower()
            if norm in seen:
                continue
            seen.add(norm)
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(xml_file_path, t)
            mirrored_paths.append(str(t))
        except Exception:
            pass

    mirrored_to = mirrored_paths[0] if mirrored_paths else ""
    roots = get_os_rekordbox_roots()
    config_root: Path = roots["config_root"]
    updated_settings = False
    target_posix = (Path(mirrored_to) if mirrored_to else xml_file_path).resolve().as_posix()

    if not is_rekordbox_running():
        for folder_name in ("rekordbox7", "rekordbox6", "rekordbox"):
            settings_file = config_root / folder_name / "rekordbox3.settings"
            if not settings_file.exists():
                continue
            try:
                raw = settings_file.read_text(encoding="utf-8", errors="ignore")
                mod = re.sub(
                    r'(val=")[^"]*rekordbox[^"]*\.xml(")',
                    lambda m: m.group(1) + target_posix + m.group(2),
                    raw,
                    flags=re.IGNORECASE,
                )
                if 'name="ImportedLibrary"' in mod:
                    mod = re.sub(
                        r'(name="ImportedLibrary"\s+val=")[^"]*(")',
                        lambda m: m.group(1) + target_posix + m.group(2),
                        mod,
                    )
                if mod != raw:
                    settings_file.write_text(mod, encoding="utf-8")
                    updated_settings = True
            except Exception:
                pass
    return (bool(mirrored_to) or updated_settings, mirrored_to)


# ---------------------------------------------------------------------------
# Automatic Rekordbox Sampler Detection + Custom Folder Scan (Selective)
# ---------------------------------------------------------------------------
def parse_rekordbox_settings_sampler_dirs(config_root: Path | None = None) -> list[Path]:
    if config_root is None:
        config_root = Path(get_os_rekordbox_roots()["config_root"])
    found: list[Path] = []
    seen = set()

    for folder_name in ("rekordbox7", "rekordbox6", "rekordbox"):
        sf = config_root / folder_name / "rekordbox3.settings"
        if not sf.exists():
            continue
        try:
            raw = sf.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(r'<VALUE\s+name="([^"]*)"\s+val="([^"]*)"', raw, flags=re.IGNORECASE):
                k_name = m.group(1).lower()
                val = m.group(2).strip()
                if not val or ("sampler" not in k_name and "sample" not in k_name):
                    continue
                if val.lower().endswith((".xml", ".db", ".txt", ".ini")):
                    continue
                p = Path(val).expanduser()
                if p.exists() and p.is_dir():
                    norm = str(p.resolve()).lower()
                    if norm not in seen:
                        seen.add(norm)
                        found.append(p)
        except Exception:
            continue
    return found


def get_default_sampler_candidate_dirs() -> list[Path]:
    roots = get_os_rekordbox_roots()
    db_root = Path(roots["db_root"])
    home = Path.home()
    candidates = [
        db_root / "Sampler",
        db_root / "share" / "Sampler",
        home / "Music" / "rekordbox" / "Sampler",
        home / "Music" / "PioneerDJ" / "Sampler",
        home / "Documents" / "rekordbox" / "Sampler",
        home / "Documents" / "PioneerDJ" / "Sampler",
    ]
    out = []
    seen = set()
    for c in candidates:
        if c.exists() and c.is_dir():
            norm = str(c.resolve()).lower()
            if norm not in seen:
                seen.add(norm)
                out.append(c)
    return out


def _safe_rel_bank(name: str) -> str:
    parts = [re.sub(r'[<>:"\\|?*]+', "_", p.strip()).strip(" .") for p in (name or "").replace("\\", "/").split("/")]
    parts = [p for p in parts if p and p not in (".", "..")]
    return "/".join(parts) if parts else "Sampler"


def scan_custom_samples_folder(folder_path: Path) -> list[dict]:
    if not folder_path.exists() or not folder_path.is_dir():
        return []
    items: list[dict] = []
    for fp in sorted(folder_path.rglob("*"), key=lambda p: p.as_posix().lower()):
        if not fp.is_file() or fp.suffix.lower() not in AUDIO_EXTS:
            continue
        try:
            st = fp.stat()
            rel_parent = fp.parent.relative_to(folder_path).as_posix()
            bank = _safe_rel_bank(f"Meus_Samples/{rel_parent}" if rel_parent != "." else "Meus_Samples")
            items.append({
                "path": str(fp.resolve()),
                "name": fp.name,
                "bank": bank,
                "source": "custom",
                "sizeLabel": format_bytes(st.st_size),
            })
        except OSError:
            continue
    return items




def _is_factory_rekordbox_sample(fp_str: str, source: str) -> bool:
    """Identifies built-in/stock Rekordbox factory samples vs. user's custom loaded samples."""
    norm = (fp_str or "").replace("\\", "/").lower()
    factory_markers = (
        "/pioneer/rekordbox/sampler/",
        "/pioneer/rekordbox/share/sampler/",
        "/rekordbox/sampler/",
        "/pioneerdj/sampler/",
        "/osc_sampler/",
        "/program files/rekordbox/",
        "/program files/pioneer/",
        "/applications/rekordbox",
        "/4-floor/",
        "/breaks kit/",
    )
    if any(m in norm for m in factory_markers):
        return True
    # Only loaded pads from SamplerSettings1.xml (outside factory dirs) count as user's loaded custom samples
    return source != "sampler_xml"



_RB_PATH_SENSITIVE_KEYS = (
    "directory", "path", "file", "library", "audio", "driver", "device",
    "record", "capture", "visualizer", "pioneer", "masterdb", "itunes",
    "bridge", "import", "export", "folder", "location", "sampler",
    "token", "password", "email", "account", "login", "auth", "license", "cloud",
)


def _get_active_rb_config_dir(config_root_override: Path | None = None) -> Path | None:
    """
    Returns the active Rekordbox settings directory (rekordbox7 or rekordbox6).
    Requires rekordbox3.settings or an FX XML inside the folder so Pioneer/rekordbox
    (the master.db folder whose mtime updates on shutdown) NEVER overrides rekordbox6/7.
    """
    config_root = config_root_override if config_root_override is not None else Path(get_os_rekordbox_roots()["config_root"])
    cands = []
    for fn in ("rekordbox7", "rekordbox6", "rekordbox"):
        d = config_root / fn
        if not d.exists() or not d.is_dir():
            continue
        sf = d / "rekordbox3.settings"
        has_fx = any(
            fp.is_file() and fp.name.lower().endswith(".xml") and "fx" in fp.name.lower()
            for fp in d.iterdir()
        )
        if not sf.exists() and not has_fx:
            continue
        mt = sf.stat().st_mtime if sf.exists() else d.stat().st_mtime
        priority = 2 if fn in ("rekordbox7", "rekordbox6") else 1
        cands.append((priority, mt, d))
    if not cands:
        return None
    cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return cands[0][2]


def detect_rekordbox_config_extras(config_root_override: Path | None = None) -> dict:
    cfg_dir = _get_active_rb_config_dir(config_root_override)
    config_root = config_root_override if config_root_override is not None else Path(get_os_rekordbox_roots()["config_root"])
    prefs_files: list[Path] = []
    fx_files: list[Path] = []
    midi_files: list[Path] = []

    if cfg_dir is not None:
        for fn in ("rekordbox3.settings", "browseSetting.xml", "PlaySettings.xml"):
            fp = cfg_dir / fn
            if fp.exists() and fp.is_file():
                prefs_files.append(fp)

        for fp in cfg_dir.iterdir():
            if not fp.is_file() or ".bak_" in fp.name.lower():
                continue
            nl = fp.name.lower()
            if nl.endswith(".xml") and ("fx" in nl or "effect" in nl):
                fx_files.append(fp)
        pad_dir = cfg_dir / "pad"
        if pad_dir.exists() and pad_dir.is_dir():
            for fp in pad_dir.rglob("*"):
                if fp.is_file() and ".bak_" not in fp.name.lower():
                    fx_files.append(fp)

        for search_dir in (cfg_dir, config_root / "rekordbox"):
            if not search_dir.exists():
                continue
            for sub in ("MidiMappings", "KeyMappings"):
                sd = search_dir / sub
                if sd.exists() and sd.is_dir():
                    for fp in sd.rglob("*"):
                        if fp.is_file() and fp not in midi_files:
                            midi_files.append(fp)
            for fp in search_dir.glob("*.midi.csv"):
                if fp.is_file() and fp not in midi_files:
                    midi_files.append(fp)

    return {
        "configDir": str(cfg_dir) if cfg_dir else "",
        "prefsCount": len(prefs_files),
        "fxCount": len(fx_files),
        "midiCount": len(midi_files),
        "prefsFiles": prefs_files,
        "fxFiles": fx_files,
        "midiFiles": midi_files,
    }


def _bundle_rb_config_extras(
    staging: Path,
    include_prefs: bool,
    include_fx: bool,
    include_midi: bool,
    config_root_override: Path | None = None,
) -> dict:
    extras = detect_rekordbox_config_extras(config_root_override)
    cfg_dir = Path(extras["configDir"]) if extras["configDir"] else None
    out = {"prefs": 0, "fx": 0, "midi": 0}
    if not cfg_dir:
        return out

    def copy_group(files: list[Path], group_name: str) -> int:
        cnt = 0
        dst_base = staging / "rb_config" / group_name
        for fp in files:
            try:
                try:
                    rel = fp.relative_to(cfg_dir)
                except ValueError:
                    rel = Path(fp.name)
                dst = dst_base / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                if group_name == "prefs" and fp.name.lower() == "rekordbox3.settings":
                    raw_s = fp.read_text(encoding="utf-8", errors="ignore")
                    sanitized = re.sub(
                        r'(<VALUE\s+name="[^"]*(?:token|password|email|account|login|auth|license)[^"]*"\s+val=")[^"]*("\s*/>)',
                        r'\1\2',
                        raw_s,
                        flags=re.IGNORECASE,
                    )
                    dst.write_text(sanitized, encoding="utf-8")
                else:
                    shutil.copy2(fp, dst)
                cnt += 1
            except Exception:
                continue
        return cnt

    if include_prefs:
        out["prefs"] = copy_group(extras["prefsFiles"], "prefs")
    if include_fx:
        out["fx"] = copy_group(extras["fxFiles"], "fx")
    if include_midi:
        out["midi"] = copy_group(extras["midiFiles"], "midi")
    return out


def _safe_merge_rekordbox3_settings(src_settings: Path, dst_settings: Path) -> None:
    """
    Merges portable UI/controller/analysis preferences from src_settings into dst_settings
    while preserving all local OS/machine paths & audio device entries in dst_settings.
    """
    if not dst_settings.exists():
        shutil.copy2(src_settings, dst_settings)
        return
    try:
        src_raw = src_settings.read_text(encoding="utf-8", errors="ignore")
        dst_raw = dst_settings.read_text(encoding="utf-8", errors="ignore")
        src_vals: dict[str, str] = {}
        for m in re.finditer(r'<VALUE\s+name="([^"]+)"\s+val="([^"]*)"\s*/>', src_raw):
            k, v = m.group(1), m.group(2)
            kl = k.lower()
            if any(tok in kl for tok in _RB_PATH_SENSITIVE_KEYS):
                continue
            if ":/" in v or ":\\" in v or v.startswith("/Users/") or v.startswith("/Applications/"):
                continue
            src_vals[k] = v

        mod = dst_raw
        for k, v in src_vals.items():
            pat = re.compile(rf'(<VALUE\s+name="{re.escape(k)}"\s+val=")[^"]*("\s*/>)')
            if pat.search(mod):
                mod = pat.sub(lambda m, val=v: m.group(1) + val + m.group(2), mod, count=1)
        if mod != dst_raw:
            dst_settings.write_text(mod, encoding="utf-8")
    except Exception:
        pass


def apply_rb_config_extras_to_rekordbox(
    work_dir: Path,
    apply_prefs: bool,
    apply_fx: bool,
    apply_midi: bool,
    config_root_override: Path | None = None,
) -> dict:
    rb_cfg_src = work_dir / "rb_config"
    applied = {"prefs": 0, "fx": 0, "midi": 0}
    if not rb_cfg_src.exists():
        return applied

    config_root = config_root_override if config_root_override is not None else Path(get_os_rekordbox_roots()["config_root"])
    target_dirs = []
    for fn in ("rekordbox7", "rekordbox6"):
        d = config_root / fn
        if d.exists() and d.is_dir():
            target_dirs.append(d)
    if not target_dirs:
        fb = config_root / "rekordbox6"
        fb.mkdir(parents=True, exist_ok=True)
        target_dirs.append(fb)

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    def restore_group(group_name: str, is_prefs: bool = False) -> int:
        gdir = rb_cfg_src / group_name
        if not gdir.exists():
            return 0
        cnt = 0
        for fp in gdir.rglob("*"):
            if not fp.is_file():
                continue
            rel = fp.relative_to(gdir)
            for tdir in target_dirs:
                dst = tdir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    try:
                        bak_f = VAULT_BACKUPS_DIR / f"backup_{stamp}" / tdir.name / rel
                        bak_f.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(dst, bak_f)
                    except Exception:
                        pass
                if is_prefs and fp.name.lower() == "rekordbox3.settings":
                    _safe_merge_rekordbox3_settings(fp, dst)
                else:
                    shutil.copy2(fp, dst)
            cnt += 1
        return cnt

    if apply_prefs:
        applied["prefs"] = restore_group("prefs", is_prefs=True)
    if apply_fx:
        applied["fx"] = restore_group("fx", is_prefs=False)
    if apply_midi:
        applied["midi"] = restore_group("midi", is_prefs=False)
    return applied


def find_sampler_settings_xml_files(config_root_override: Path | None = None) -> list[Path]:
    config_root = config_root_override if config_root_override is not None else Path(get_os_rekordbox_roots()["config_root"])
    found: list[Path] = []
    for folder_name in ("rekordbox7", "rekordbox6", "rekordbox"):
        sf = config_root / folder_name / "SamplerSettings1.xml"
        if sf.exists() and sf.is_file():
            found.append(sf)
    found.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return found


def _format_sampler_edits_label(attrib: dict) -> str:
    parts = []
    if str(attrib.get("loop", "0")) == "1":
        parts.append("Loop")
    elif str(attrib.get("gate", "0")) == "1":
        parts.append("Gate")
    else:
        parts.append("One-Shot")
    gain = attrib.get("gain")
    if gain is not None and str(gain) != "":
        parts.append(f"Gain {gain}")
    if str(attrib.get("sync", "0")) == "1":
        parts.append("Sync")
    if str(attrib.get("mt", "0")) == "1":
        parts.append("MT")
    return " • ".join(parts)


def parse_sampler_settings_xml(xml_path: Path) -> tuple[dict, list[dict]]:
    """
    Parses Rekordbox SamplerSettings1.xml.
    Returns (global_props, slots_list) where each slot preserves ALL original XML attributes.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()
    global_props = {}
    for val_el in root.findall("VALUE"):
        k = val_el.attrib.get("name")
        v = val_el.attrib.get("val")
        if k and v is not None:
            global_props[k] = v

    slots = []
    sampler_set = root.find(".//SamplerSet")
    if sampler_set is not None:
        for tr in sampler_set.findall("Track"):
            attrib = dict(tr.attrib)
            url = (attrib.get("url") or "").strip()
            if not url:
                continue
            try:
                idx_i = int(attrib.get("idx", "0"))
            except ValueError:
                idx_i = 0
            bank_num = (idx_i // 16) + 1
            slot_num = (idx_i % 16) + 1
            clean_url = urllib.parse.unquote(url)
            if clean_url.startswith("file://localhost"):
                clean_url = clean_url[len("file://localhost"):]
            elif clean_url.startswith("file://"):
                clean_url = clean_url[len("file://"):]
            if re.match(r"^/[A-Za-z]:/", clean_url):
                clean_url = clean_url[1:]
            fp = Path(os.path.normpath(clean_url)).expanduser()
            slots.append({
                "idx": idx_i,
                "bankLabel": f"Banco {bank_num} • Slot {slot_num}",
                "slotName": attrib.get("name") or fp.stem,
                "url": url,
                "path": str(fp),
                "exists": fp.exists() and fp.is_file(),
                "editsLabel": _format_sampler_edits_label(attrib),
                "slotAttribs": attrib,
                "sourceXml": str(xml_path),
            })
    slots.sort(key=lambda x: x["idx"])
    return global_props, slots


def build_sampler_settings_xml_element(global_props: dict, track_attribs_list: list[dict]) -> ET.Element:
    props = ET.Element("PROPERTIES")
    defaults = {
        "visibleBankL": global_props.get("visibleBankL", "0"),
        "quantize": global_props.get("quantize", "0"),
        "sync": global_props.get("sync", "0"),
        "master_bpm": global_props.get("master_bpm", "120.0"),
    }
    for k, v in defaults.items():
        ET.SubElement(props, "VALUE", {"name": k, "val": str(v)})
    val_set = ET.SubElement(props, "VALUE", {"name": "SamplerSet"})
    ss = ET.SubElement(val_set, "SamplerSet")
    for k, v in defaults.items():
        ET.SubElement(ss, "VALUE", {"name": k, "val": str(v)})
    for att in sorted(track_attribs_list, key=lambda a: int(a.get("idx", "0") or 0)):
        clean_att = {str(k): _sanitize_xml_text(v) for k, v in att.items()}
        ET.SubElement(ss, "Track", clean_att)
    return props


def apply_sampler_settings_to_rekordbox(
    imported_global_props: dict,
    imported_tracks: list[dict],
    config_root_override: Path | None = None,
) -> list[str]:
    """
    Merges imported Sampler pads (with rewritten local POSIX URLs and all edits intact)
    into Rekordbox's SamplerSettings1.xml across active config folders, backing up first.
    """
    if not imported_tracks:
        return []
    config_root = config_root_override if config_root_override is not None else Path(get_os_rekordbox_roots()["config_root"])
    target_dirs = []
    for folder_name in ("rekordbox7", "rekordbox6"):
        d = config_root / folder_name
        if d.exists() and d.is_dir():
            target_dirs.append(d)
    if not target_dirs:
        fallback = config_root / "rekordbox6"
        fallback.mkdir(parents=True, exist_ok=True)
        target_dirs.append(fallback)

    written: list[str] = []
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    by_idx_new = {str(t.get("idx", "0")): dict(t) for t in imported_tracks}

    for tdir in target_dirs:
        sf = tdir / "SamplerSettings1.xml"
        merged_props = dict(imported_global_props or {})
        merged_by_idx: dict[str, dict] = {}
        if sf.exists():
            try:
                bak_dir = VAULT_BACKUPS_DIR / f"backup_{stamp}" / tdir.name
                bak_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sf, bak_dir / "SamplerSettings1.xml")
                ex_props, ex_slots = parse_sampler_settings_xml(sf)
                for k, v in ex_props.items():
                    merged_props.setdefault(k, v)
                for sl in ex_slots:
                    merged_by_idx[str(sl["idx"])] = dict(sl["slotAttribs"])
            except Exception:
                pass
        merged_by_idx.update(by_idx_new)
        root_el = build_sampler_settings_xml_element(merged_props, list(merged_by_idx.values()))
        _indent_xml(root_el)
        xml_body = ET.tostring(root_el, encoding="unicode").replace(" />", "/>")
        with open(sf, "w", encoding="utf-8", newline="\n") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n\n' + xml_body + "\n")
        written.append(str(sf))
    return written


def _safe_copy2(src_p: Path, dst_p: Path) -> None:
    """Copies src_p to dst_p, silently skipping if both resolve to the exact same file."""
    try:
        if dst_p.exists() and src_p.resolve() == dst_p.resolve():
            return
    except Exception:
        pass
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_p, dst_p)


def _safe_copytree(src_dir: Path, dst_dir: Path) -> None:
    """Recursively copies src_dir to dst_dir, safely skipping identical paths."""
    try:
        if dst_dir.exists() and src_dir.resolve() == dst_dir.resolve():
            return
    except Exception:
        pass
    for fp in src_dir.rglob("*"):
        if not fp.is_file():
            continue
        rel = fp.relative_to(src_dir)
        _safe_copy2(fp, dst_dir / rel)


def _lookup_local_master_db_content_ids() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """
    Read-only snapshot lookup of the destination machine's master.db to map
    normalized POSIX paths and filenames to real local DjmdContent.ID values.
    """
    by_path: dict[str, str] = {}
    by_name: dict[str, str] = {}
    path_by_id: dict[str, str] = {}
    try:
        from pyrekordbox import Rekordbox6Database
    except Exception:
        return by_path, by_name, path_by_id

    roots = get_os_rekordbox_roots()
    db_root = Path(roots["db_root"])
    master_db = db_root / "master.db"
    if not master_db.exists():
        return by_path, by_name, path_by_id

    snap_tmp_ctx = tempfile.TemporaryDirectory(prefix="rbvault_dbsnap_id_")
    db = None
    try:
        snap_dir = Path(snap_tmp_ctx.name)
        snap_db = snap_dir / "master.db"
        shutil.copy2(master_db, snap_db)
        for side in ("master.db-wal", "master.db-shm"):
            sp = db_root / side
            if sp.exists():
                try:
                    shutil.copy2(sp, snap_dir / side)
                except Exception:
                    pass
        db = Rekordbox6Database(path=str(snap_db))
        for tr in db.get_content():
            if getattr(tr, "rb_local_deleted", 0):
                continue
            cid = str(getattr(tr, "ID", "") or "")
            fp = str(getattr(tr, "FolderPath", "") or "").replace("\\", "/")
            if not cid or not fp:
                continue
            fp_nfc = unicodedata.normalize("NFC", fp).lower()
            by_path[fp_nfc] = cid
            path_by_id[cid] = fp
            fname = unicodedata.normalize("NFC", Path(fp).name).lower()
            if fname and fname not in by_name:
                by_name[fname] = cid
            clean_fname = re.sub(r"^slot\d+_", "", fname)
            if clean_fname and clean_fname not in by_name:
                by_name[clean_fname] = cid
    except Exception:
        pass
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass
        try:
            snap_tmp_ctx.cleanup()
        except Exception:
            pass
    return by_path, by_name, path_by_id

def _bundle_sampler_bpm_and_anlz(
    staging: Path,
    exported_pad_tracks: list[dict],
    pad_src_paths: dict[str, Path],
) -> dict[str, dict]:
    """
    Reads source master.db (via read-only snapshot) to bundle each exported Sampler pad's
    BPM, Length, and ANLZ beatgrid/waveform files (ANLZ0000.DAT/.EXT/.2EX) into staging/sampler_anlz/.
    """
    result: dict[str, dict] = {}
    if not exported_pad_tracks:
        return result
    try:
        from pyrekordbox import Rekordbox6Database
    except Exception:
        return result

    roots = get_os_rekordbox_roots()
    db_root = Path(roots["db_root"])
    master_db = db_root / "master.db"
    share_root = db_root / "share"
    if not master_db.exists():
        return result

    snap_ctx = tempfile.TemporaryDirectory(prefix="rbvault_dbsnap_exp_samp_")
    db = None
    try:
        snap_dir = Path(snap_ctx.name)
        snap_db = snap_dir / "master.db"
        shutil.copy2(master_db, snap_db)
        for side in ("master.db-wal", "master.db-shm"):
            sp = db_root / side
            if sp.exists():
                try:
                    shutil.copy2(sp, snap_dir / side)
                except Exception:
                    pass

        db = Rekordbox6Database(path=str(snap_db))
        by_id: dict[str, object] = {}
        by_path: dict[str, object] = {}
        by_name: dict[str, object] = {}
        for tr in db.get_content():
            if getattr(tr, "rb_local_deleted", 0):
                continue
            cid = str(getattr(tr, "ID", "") or "")
            fp = str(getattr(tr, "FolderPath", "") or "").replace("\\", "/")
            if cid:
                by_id[cid] = tr
            if fp:
                by_path[fp.lower()] = tr
                fn = Path(fp).name.lower()
                if fn and fn not in by_name:
                    by_name[fn] = tr

        for att in exported_pad_tracks:
            rel_u = str(att.get("url") or "")
            idx_s = str(att.get("idx") or "0")
            orig_id = str(att.get("id") or "")
            src_p = pad_src_paths.get(rel_u)
            tr = (
                by_id.get(orig_id)
                or (by_path.get(src_p.resolve().as_posix().lower()) if src_p else None)
                or (by_name.get(src_p.name.lower()) if src_p else None)
            )
            if tr is None:
                continue
            bpm_val = int(getattr(tr, "BPM", 0) or 0)
            len_val = int(getattr(tr, "Length", 0) or 0)
            anlz_rel = str(getattr(tr, "AnalysisDataPath", "") or "").replace("\\", "/").lstrip("/")
            meta_entry: dict = {"bpm": bpm_val, "length": len_val}
            if anlz_rel:
                src_dat = share_root / Path(*anlz_rel.split("/"))
                if src_dat.exists() and src_dat.parent.is_dir():
                    slot_anlz_rel = f"sampler_anlz/slot_{idx_s}"
                    dst_anlz_dir = staging / "sampler_anlz" / f"slot_{idx_s}"
                    dst_anlz_dir.mkdir(parents=True, exist_ok=True)
                    copied_files = []
                    for af in src_dat.parent.iterdir():
                        if af.is_file() and af.name.upper().startswith("ANLZ"):
                            shutil.copy2(af, dst_anlz_dir / af.name)
                            copied_files.append(af.name)
                    if copied_files:
                        meta_entry["anlzDir"] = slot_anlz_rel
                        meta_entry["anlzDatName"] = src_dat.name
                        meta_entry["anlzFiles"] = sorted(copied_files)
            if bpm_val > 0 or meta_entry.get("anlzDir"):
                result[rel_u] = meta_entry
    except Exception as exc:
        print(f"[AVISO] Falha ao exportar BPM/Beatgrid dos samples: {exc}", flush=True)
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass
        try:
            snap_ctx.cleanup()
        except Exception:
            pass
    return result


def _sync_samples_in_master_db(
    sample_sync_jobs: list[dict],
    work_dir: Path,
) -> dict[str, str]:
    """
    Safely registers missing sample audio files AND applies exported BPM, Length, and
    ANLZ Beatgrid files (ANLZ0000.DAT/.EXT/.2EX) into the destination machine's master.db
    and share/PIONEER/USBANLZ/ (with automatic master.db backup first).
    Returns {posix_path_lower: local_content_id}.
    """
    import uuid

    resolved_ids: dict[str, str] = {}
    if not sample_sync_jobs:
        return resolved_ids
    if is_rekordbox_running():
        return resolved_ids
    try:
        from pyrekordbox import Rekordbox6Database
    except Exception:
        return resolved_ids

    roots = get_os_rekordbox_roots()
    db_root = Path(roots["db_root"])
    master_db = db_root / "master.db"
    share_root = db_root / "share"
    if not master_db.exists():
        return resolved_ids

    try:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        bak_dir = VAULT_BACKUPS_DIR / f"backup_{stamp}" / "db"
        bak_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(master_db, bak_dir / "master.db")
    except Exception:
        pass

    db = None
    try:
        try:
            db = Rekordbox6Database(path=str(master_db))
        except Exception:
            db = Rekordbox6Database()

        existing_by_id: dict[str, object] = {}
        for tr in db.get_content():
            if not getattr(tr, "rb_local_deleted", 0):
                existing_by_id[str(getattr(tr, "ID", "") or "")] = tr

        dirty = False
        for job in sample_sync_jobs:
            spath: Path = job["path"]
            stitle: str = job["title"]
            existing_id: str = job.get("existingId") or ""
            analysis_meta: dict = job.get("analysisMeta") or {}
            posix_p = spath.resolve().as_posix()

            item = existing_by_id.get(existing_id) if existing_id else None
            if item is None and hasattr(db, "add_content"):
                try:
                    try:
                        item = db.add_content(posix_p, Title=stitle)
                    except TypeError:
                        item = db.add_content(posix_p)
                        if stitle and hasattr(item, "Title"):
                            item.Title = stitle
                    dirty = True
                except Exception as e_add:
                    print(f"[AVISO] Falha ao registrar sample {spath.name} no master.db: {e_add}", flush=True)
                    continue

            if item is None:
                continue

            cid = str(getattr(item, "ID", "") or "")
            if cid:
                resolved_ids[posix_p.lower()] = cid

            # Apply BPM & Length if exported
            bpm_val = int(analysis_meta.get("bpm") or 0)
            len_val = int(analysis_meta.get("length") or 0)
            if bpm_val > 0 and hasattr(item, "BPM"):
                item.BPM = bpm_val
                dirty = True
            if len_val > 0 and hasattr(item, "Length") and not int(getattr(item, "Length", 0) or 0):
                item.Length = len_val
                dirty = True

            # Copy ANLZ Beatgrid / Waveform files & link AnalysisDataPath
            anlz_dir_rel = str(analysis_meta.get("anlzDir") or "")
            anlz_dat_name = str(analysis_meta.get("anlzDatName") or "ANLZ0000.DAT")
            if anlz_dir_rel:
                src_anlz_dir = work_dir / Path(*anlz_dir_rel.split("/"))
                if src_anlz_dir.exists() and src_anlz_dir.is_dir():
                    cur_adp = str(getattr(item, "AnalysisDataPath", "") or "").replace("\\", "/").strip()
                    if cur_adp and cur_adp.lower().endswith(".dat"):
                        target_adp = cur_adp if cur_adp.startswith("/") else ("/" + cur_adp)
                    else:
                        u_str = str(uuid.uuid4())
                        target_adp = f"/PIONEER/USBANLZ/{u_str[:3]}/{u_str[3:]}/{anlz_dat_name}"
                    dst_dat = share_root / Path(*target_adp.lstrip("/").split("/"))
                    dst_dat.parent.mkdir(parents=True, exist_ok=True)
                    copied_any = False
                    for af in src_anlz_dir.iterdir():
                        if af.is_file() and af.name.upper().startswith("ANLZ"):
                            shutil.copy2(af, dst_dat.parent / af.name)
                            copied_any = True
                    if copied_any and hasattr(item, "AnalysisDataPath"):
                        item.AnalysisDataPath = target_adp
                        if hasattr(item, "Analysed"):
                            try:
                                item.Analysed = 1
                            except Exception:
                                pass
                        dirty = True

        if dirty:
            db.commit()
    except Exception as e_db:
        print(f"[AVISO] Falha ao sincronizar BPM/Beatgrid dos samples no master.db: {e_db}", flush=True)
        try:
            if db is not None and hasattr(db, "rollback"):
                db.rollback()
        except Exception:
            pass
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass
    return resolved_ids




def detect_rekordbox_sampler_files(config_root_override: Path | None = None) -> dict:
    """
    Collects all audio files from Rekordbox's configured Sampler library:
    1) Live Sampler bank/slot assignments in master.db (via pyrekordbox SQLite introspection)
    2) Sampler directories configured in rekordbox3.settings
    3) Default Rekordbox Sampler folders (if present and containing audio files)
    """
    items: list[dict] = []
    seen_paths: set[str] = set()
    db_count = 0
    pads_count = 0
    sampler_xml_used = ""
    sampler_global_props: dict = {}
    settings_dirs_used: list[str] = []
    db_checked = False
    db_available = check_pyrekordbox_available()

    # 0. Read live loaded Sampler Pads & Edits from SamplerSettings1.xml (highest priority!)
    for sf in find_sampler_settings_xml_files(config_root_override):
        try:
            gprops, slots = parse_sampler_settings_xml(sf)
            if slots:
                sampler_xml_used = str(sf)
                sampler_global_props = gprops
                # Pre-index master.db content by ID and filename if any pad path is missing
                missing_any = any(not (Path(sl["path"]).exists() and Path(sl["path"]).is_file()) for sl in slots)
                db_path_by_id: dict[str, Path] = {}
                db_path_by_name: dict[str, Path] = {}
                if missing_any and db_available and config_root_override is None:
                    try:
                        from pyrekordbox import Rekordbox6Database
                        roots = get_os_rekordbox_roots()
                        db_root = Path(roots["db_root"])
                        try:
                            db_tmp = Rekordbox6Database()
                        except Exception:
                            mdb = db_root / "master.db"
                            db_tmp = Rekordbox6Database(path=str(mdb)) if mdb.exists() else None
                        if db_tmp is not None:
                            try:
                                for tr in db_tmp.get_content():
                                    if getattr(tr, "rb_local_deleted", 0):
                                        continue
                                    fp_s = str(getattr(tr, "FolderPath", "") or "")
                                    if not fp_s:
                                        continue
                                    cand_fp = Path(os.path.normpath(fp_s))
                                    if cand_fp.exists() and cand_fp.is_file():
                                        tid_s = str(getattr(tr, "ID", "") or "")
                                        if tid_s:
                                            db_path_by_id[tid_s] = cand_fp
                                        db_path_by_name.setdefault(cand_fp.name.lower(), cand_fp)
                            finally:
                                try:
                                    db_tmp.close()
                                except Exception:
                                    pass
                    except Exception:
                        pass

                for sl in slots:
                    fp = Path(sl["path"])
                    exists_ok = fp.exists() and fp.is_file()
                    if not exists_ok:
                        tid_lookup = str(sl["slotAttribs"].get("id") or "")
                        resolved = db_path_by_id.get(tid_lookup) or db_path_by_name.get(fp.name.lower())
                        if resolved is not None and resolved.exists():
                            fp = resolved
                            exists_ok = True
                    sz_lbl = format_bytes(fp.stat().st_size) if exists_ok else f"Não encontrado ({sl['url']})"
                    if exists_ok:
                        seen_paths.add(str(fp.resolve()).lower())
                    items.append({
                        "path": str(fp.resolve()) if exists_ok else sl["url"],
                        "name": f'{sl["slotName"]} ({fp.name})' if sl["slotName"].lower() != fp.stem.lower() else fp.name,
                        "bank": sl["bankLabel"],
                        "source": "sampler_xml",
                        "sizeLabel": sz_lbl,
                        "editsLabel": sl["editsLabel"],
                        "slotAttribs": sl["slotAttribs"],
                        "exists": exists_ok,
                        "isFactory": _is_factory_rekordbox_sample(str(fp), "sampler_xml"),
                    })
                    pads_count += 1
                break
        except Exception:
            continue

    def add_file(fp: Path, bank: str, source: str):
        nonlocal db_count
        try:
            if not fp.exists() or not fp.is_file():
                return
            if fp.suffix.lower() not in AUDIO_EXTS:
                return
            norm = str(fp.resolve()).lower()
            if norm in seen_paths:
                return
            seen_paths.add(norm)
            st = fp.stat()
            items.append({
                "path": str(fp.resolve()),
                "name": fp.name,
                "bank": _safe_rel_bank(bank),
                "source": source,
                "sizeLabel": format_bytes(st.st_size),
                "isFactory": _is_factory_rekordbox_sample(str(fp), source),
            })
            if source == "db":
                db_count += 1
        except OSError:
            pass

    # 1. Inspect master.db Sampler tables via pyrekordbox (read-only)
    if db_available and config_root_override is None:
        try:
            from pyrekordbox import Rekordbox6Database
            from sqlalchemy import text as sql_text

            roots = get_os_rekordbox_roots()
            db_root = Path(roots["db_root"])
            try:
                db = Rekordbox6Database()
            except Exception:
                master_db = db_root / "master.db"
                db = Rekordbox6Database(path=str(master_db)) if master_db.exists() else None

            if db is not None:
                db_checked = True
                try:
                    sess = getattr(db, "session", None)
                    if sess is not None:
                        tables = [
                            row[0]
                            for row in sess.execute(
                                sql_text("SELECT name FROM sqlite_master WHERE type='table' AND lower(name) LIKE '%sampler%'")
                            ).fetchall()
                        ]
                        bank_names: dict[str, str] = {}
                        for tname in tables:
                            cols = [r[1] for r in sess.execute(sql_text(f'PRAGMA table_info("{tname}")')).fetchall()]
                            if "ID" in cols and "Name" in cols and "ContentID" not in cols:
                                for rid, rname in sess.execute(sql_text(f'SELECT ID, Name FROM "{tname}"')).fetchall():
                                    if rid is not None and rname:
                                        bank_names[str(rid)] = str(rname)

                        for tname in tables:
                            cols = [r[1] for r in sess.execute(sql_text(f'PRAGMA table_info("{tname}")')).fetchall()]
                            if "ContentID" not in cols:
                                continue
                            bank_col = next((c for c in ("SamplerID", "BankID", "PlaylistID") if c in cols), None)
                            if bank_col:
                                q = (
                                    f'SELECT DISTINCT c.FolderPath, s."{bank_col}" '
                                    f'FROM "{tname}" s JOIN djmdContent c ON c.ID = s.ContentID '
                                    f'WHERE COALESCE(c.rb_local_deleted, 0) = 0'
                                )
                            else:
                                q = (
                                    f'SELECT DISTINCT c.FolderPath, "" '
                                    f'FROM "{tname}" s JOIN djmdContent c ON c.ID = s.ContentID '
                                    f'WHERE COALESCE(c.rb_local_deleted, 0) = 0'
                                )
                            for fpath, bid in sess.execute(sql_text(q)).fetchall():
                                if fpath:
                                    b_label = bank_names.get(str(bid), "Banco_Sampler") if bid else "Banco_Sampler"
                                    add_file(Path(str(fpath)), b_label, "db")

                        for tr in db.get_content():
                            if getattr(tr, "rb_local_deleted", 0):
                                continue
                            fp_str = str(getattr(tr, "FolderPath", "") or "")
                            if not fp_str:
                                continue
                            if "/sampler/" in fp_str.replace("\\", "/").lower():
                                add_file(Path(fp_str), "Colecao_Sampler", "db")
                finally:
                    try:
                        db.close()
                    except Exception:
                        pass
        except Exception:
            pass

    # 2. Scan directories configured in rekordbox3.settings
    for sdir in parse_rekordbox_settings_sampler_dirs(config_root_override):
        before = len(items)
        for fp in sorted(sdir.rglob("*"), key=lambda p: p.as_posix().lower()):
            if fp.is_file():
                rel_parent = fp.parent.relative_to(sdir).as_posix()
                bank = f"Pasta_Rekordbox/{rel_parent}" if rel_parent != "." else "Pasta_Rekordbox"
                add_file(fp, bank, "settings")
        if len(items) > before or sdir.exists():
            settings_dirs_used.append(str(sdir))

    # 3. Scan default Rekordbox Sampler directories
    if config_root_override is None:
        for ddir in get_default_sampler_candidate_dirs():
            before = len(items)
            for fp in sorted(ddir.rglob("*"), key=lambda p: p.as_posix().lower()):
                if fp.is_file():
                    rel_parent = fp.parent.relative_to(ddir).as_posix()
                    bank = f"Pasta_Padrao/{rel_parent}" if rel_parent != "." else "Pasta_Padrao"
                    add_file(fp, bank, "default")
            if len(items) > before:
                settings_dirs_used.append(str(ddir))

    return {
        "totalFiles": len(items),
        "padsCount": pads_count,
        "samplerXmlPath": sampler_xml_used,
        "samplerGlobalProps": sampler_global_props,
        "dbFilesCount": db_count,
        "folderFilesCount": max(0, len(items) - db_count - pads_count),
        "dbChecked": db_checked,
        "dbAvailable": db_available,
        "settingsDirs": settings_dirs_used,
        "files": items,
    }


# ---------------------------------------------------------------------------
# Fast Built-in Explorer (/api/fs/list) + Optional Native OS Picker
# ---------------------------------------------------------------------------
_SHORTCUTS_CACHE: list[dict] | None = None


def get_fs_shortcuts() -> list[dict]:
    global _SHORTCUTS_CACHE
    if _SHORTCUTS_CACHE is not None:
        return _SHORTCUTS_CACHE
    home = Path.home()
    shortcuts = []
    for label, p in [
        ("Desktop", home / "Desktop"),
        ("Downloads", home / "Downloads"),
        ("Músicas", home / "Music"),
        ("Documentos", home / "Documents"),
        ("Usuário", home),
    ]:
        if p.exists():
            shortcuts.append({"label": label, "path": str(p)})

    if IS_WIN:
        try:
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            for idx, letter in enumerate(string.ascii_uppercase):
                if mask & (1 << idx):
                    shortcuts.append({"label": f"Disco ({letter}:)", "path": f"{letter}:\\"})
        except Exception:
            pass
    elif IS_MAC:
        vols = Path("/Volumes")
        if vols.exists():
            try:
                for entry in os.scandir(vols):
                    if entry.is_dir(follow_symlinks=False):
                        shortcuts.append({"label": f"HD: {entry.name}", "path": entry.path})
            except OSError:
                pass
    _SHORTCUTS_CACHE = shortcuts
    return shortcuts



def reveal_in_file_manager(target_path: str) -> bool:
    p = Path(target_path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"Arquivo ou pasta não encontrado: {target_path}")
    resolved = p.resolve()
    if IS_WIN:
        norm_p = os.path.normpath(str(resolved))
        if resolved.is_file():
            subprocess.Popen(f'explorer /select,"{norm_p}"')
        else:
            subprocess.Popen(f'explorer "{norm_p}"')
        return True
    if IS_MAC:
        if resolved.is_file():
            subprocess.Popen(["open", "-R", str(resolved)])
        else:
            subprocess.Popen(["open", str(resolved)])
        return True
    return False


def list_filesystem(target_path: str = "", mode: str = "folder", ext: str = "", include_shortcuts: bool = False) -> dict:
    home = Path.home()
    cur = Path(target_path).expanduser() if target_path else home
    try:
        if not cur.exists() or not cur.is_dir():
            cur = home
    except OSError:
        cur = home

    parent = str(cur.parent) if cur.parent != cur else ""
    ext_norm = ("." + ext.lstrip(".").lower()) if ext else ""
    folders, files = [], []
    try:
        with os.scandir(cur) as it:
            for entry in it:
                name = entry.name
                if name.startswith("."):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        folders.append({"name": name, "path": entry.path, "type": "dir"})
                    elif mode == "file":
                        if not ext_norm or name.lower().endswith(ext_norm):
                            st = entry.stat(follow_symlinks=False)
                            files.append({
                                "name": name,
                                "path": entry.path,
                                "type": "file",
                                "sizeLabel": format_bytes(st.st_size),
                                "mtime": st.st_mtime,
                            })
                except OSError:
                    continue
    except OSError:
        pass

    folders.sort(key=lambda x: x["name"].lower())
    files.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    return {
        "currentPath": str(cur),
        "parentPath": parent,
        "shortcuts": get_fs_shortcuts() if include_shortcuts else None,
        "folders": folders,
        "files": files,
    }


def native_pick_folder(title: str = "Selecione uma pasta", initial_dir: str = "") -> str:
    init_p = Path(initial_dir).expanduser() if initial_dir else None
    if init_p and init_p.is_file():
        init_p = init_p.parent
    valid_init = str(init_p) if (init_p and init_p.exists() and init_p.is_dir()) else ""
    if IS_MAC:
        safe_t = title.replace('"', '\\"')
        loc_part = f' default location POSIX file "{valid_init.replace(chr(34), "")}"' if valid_init else ""
        script = f'tell application "System Events" to activate\nPOSIX path of (choose folder with prompt "{safe_t}"{loc_part})'
        res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        return res.stdout.strip().rstrip("/") if res.returncode == 0 else ""
    if IS_WIN:
        init_ps = f"$d.SelectedPath = '{valid_init.replace(chr(39), chr(39)*2)}'; " if valid_init else ""
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$f = New-Object System.Windows.Forms.Form; $f.TopMost = $true; $f.StartPosition = 'CenterScreen'; "
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
            f"$d.Description = '{title.replace(chr(39), '')}'; {init_ps}"
            "if ($d.ShowDialog($f) -eq 'OK') { Write-Output $d.SelectedPath }; $f.Dispose()"
        )
        res = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, text=True, **_WIN_NO_WINDOW)
        return res.stdout.strip()
    return ""


def native_pick_file(title: str = "Selecione um arquivo", ext: str = "zip", initial_dir: str = "") -> str:
    init_p = Path(initial_dir).expanduser() if initial_dir else None
    if init_p and init_p.is_file():
        init_p = init_p.parent
    valid_init = str(init_p) if (init_p and init_p.exists() and init_p.is_dir()) else ""
    if IS_MAC:
        safe_t = title.replace('"', '\\"')
        filt = f' of type {{"{ext}"}}' if ext else ""
        loc_part = f' default location POSIX file "{valid_init.replace(chr(34), "")}"' if valid_init else ""
        script = f'tell application "System Events" to activate\nPOSIX path of (choose file with prompt "{safe_t}"{filt}{loc_part})'
        res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        return res.stdout.strip() if res.returncode == 0 else ""
    if IS_WIN:
        filt = f"Arquivos (*.{ext})|*.{ext}|Todos (*.*)|*.*" if ext else "Todos (*.*)|*.*"
        init_ps = f"$d.InitialDirectory = '{valid_init.replace(chr(39), chr(39)*2)}'; " if valid_init else ""
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$f = New-Object System.Windows.Forms.Form; $f.TopMost = $true; $f.StartPosition = 'CenterScreen'; "
            "$d = New-Object System.Windows.Forms.OpenFileDialog; "
            f"$d.Title = '{title.replace(chr(39), '')}'; "
            f"$d.Filter = '{filt}'; {init_ps}"
            "if ($d.ShowDialog($f) -eq 'OK') { Write-Output $d.FileName }; $f.Dispose()"
        )
        res = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, text=True, **_WIN_NO_WINDOW)
        return res.stdout.strip()
    return ""


# ---------------------------------------------------------------------------
# Read-Only Direct master.db -> XML Export (for selecting playlists without opening Rekordbox)
# ---------------------------------------------------------------------------
def check_pyrekordbox_available() -> bool:
    try:
        import pyrekordbox  # noqa: F401
        return True
    except Exception:
        return False


def install_pyrekordbox_and_key() -> list[str]:
    logs = []
    in_venv = (sys.prefix != getattr(sys, "base_prefix", sys.prefix))
    pip_cmd = [sys.executable, "-m", "pip", "install", "--upgrade"]
    if not in_venv:
        pip_cmd.append("--user")
    res = subprocess.run(pip_cmd + ["pyrekordbox", "sqlcipher3-wheels"], capture_output=True, text=True, **_WIN_NO_WINDOW)
    if res.returncode != 0:
        res = subprocess.run(pip_cmd + ["pyrekordbox"], capture_output=True, text=True, **_WIN_NO_WINDOW)
    if res.returncode != 0:
        raise RuntimeError(f"Falha ao instalar pyrekordbox:\n{res.stderr or res.stdout}")
    logs.append("Módulo pyrekordbox (e sqlcipher3-wheels) instalado.")
    res_k = subprocess.run([sys.executable, "-m", "pyrekordbox", "download-key"], capture_output=True, text=True, **_WIN_NO_WINDOW)
    if res_k.returncode == 0:
        logs.append("Chave do banco Rekordbox configurada automaticamente.")
    return logs


def generate_xml_from_master_db(output_xml_path: Path, job_id: str | None = None) -> dict:
    try:
        from pyrekordbox import Rekordbox6Database
    except ImportError as exc:
        raise RuntimeError("PYREKORDBOX_MISSING") from exc

    roots = get_os_rekordbox_roots()
    db_root = Path(roots["db_root"])
    share_root = db_root / "share"

    master_db = db_root / "master.db"
    snap_tmp_ctx = None
    db = None
    if master_db.exists():
        # Copy master.db (+ WAL/SHM if present) to an isolated snapshot so we NEVER lock
        # or interfere with a running Rekordbox instance.
        snap_tmp_ctx = tempfile.TemporaryDirectory(prefix="rbvault_dbsnap_")
        snap_dir = Path(snap_tmp_ctx.name)
        snap_db = snap_dir / "master.db"
        shutil.copy2(master_db, snap_db)
        for side in ("master.db-wal", "master.db-shm"):
            side_p = db_root / side
            if side_p.exists():
                try:
                    shutil.copy2(side_p, snap_dir / side)
                except Exception:
                    pass
        try:
            db = Rekordbox6Database(path=str(snap_db))
        except Exception:
            db = Rekordbox6Database(path=str(master_db))
    else:
        db = Rekordbox6Database()

    try:
        dj_root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
        ET.SubElement(dj_root, "PRODUCT", {"Name": "rekordbox", "Version": "7.0.0", "Company": "AlphaTheta"})
        collection_el = ET.SubElement(dj_root, "COLLECTION", {"Entries": "0"})

        set_job_progress(job_id, 6, "Lendo faixas do banco do Rekordbox...")
        all_tracks = list(db.get_content())
        total_tr = max(1, len(all_tracks))
        tracks_written = 0
        valid_track_ids = set()

        for tr_idx, tr in enumerate(all_tracks):
            if job_id and (tr_idx % 12 == 0 or tr_idx == total_tr - 1):
                pct = 8 + int(((tr_idx + 1) / total_tr) * 74)
                set_job_progress(job_id, pct, f"Lendo faixas ({tr_idx + 1}/{total_tr})...")
            if getattr(tr, "rb_local_deleted", 0):
                continue
            tid = str(getattr(tr, "ID", "") or "")
            folder_path = str(getattr(tr, "FolderPath", "") or "")
            if not tid or not folder_path:
                continue

            artist_obj = getattr(tr, "Artist", None)
            album_obj = getattr(tr, "Album", None)
            genre_obj = getattr(tr, "Genre", None)
            key_obj = getattr(tr, "Key", None)

            bpm_raw = getattr(tr, "BPM", 0) or 0
            bpm_str = f"{(float(bpm_raw) / 100.0):.2f}" if bpm_raw else "0.00"
            rating_raw = int(getattr(tr, "Rating", 0) or 0)
            rating_xml = str(min(255, rating_raw * 51)) if rating_raw <= 5 else str(rating_raw)

            attrs = {
                "TrackID": tid,
                "Name": str(getattr(tr, "Title", "") or ""),
                "Artist": str(getattr(artist_obj, "Name", "") or getattr(tr, "ArtistName", "") or ""),
                "Album": str(getattr(album_obj, "Name", "") or ""),
                "Genre": str(getattr(genre_obj, "Name", "") or ""),
                "TotalTime": str(int(getattr(tr, "Length", 0) or 0)),
                "AverageBpm": bpm_str,
                "Tonality": str(getattr(key_obj, "ScaleName", "") or ""),
                "Rating": rating_xml,
                "Comments": str(getattr(tr, "Commnt", "") or ""),
                "Location": local_path_to_xml_location(folder_path),
            }
            tr_el = ET.SubElement(collection_el, "TRACK", attrs)
            valid_track_ids.add(tid)
            tracks_written += 1

            track_len_sec = int(getattr(tr, "Length", 0) or 0)
            anlz_rel = str(getattr(tr, "AnalysisDataPath", "") or "").lstrip("/\\")
            anlz_path = (share_root / anlz_rel) if (anlz_rel and share_root.exists()) else None
            if anlz_path and anlz_path.exists():
                try:
                    from pyrekordbox.anlz import AnlzFile
                    anlz = AnlzFile.parse_file(str(anlz_path))
                    qtz = anlz.get("beat_grid")
                    if qtz is not None and len(qtz) >= 3:
                        beats, bpms, times_raw = qtz[0], qtz[1], qtz[2]
                        t_vals = [float(x) for x in times_raw] if len(times_raw) else []
                        max_t = max(t_vals) if t_vals else 0.0
                        # pyrekordbox beat_grid returns seconds; only divide by 1000 if clearly in ms
                        is_ms = (track_len_sec > 0 and max_t > track_len_sec * 10) or (track_len_sec == 0 and max_t > 10000)
                        last_bpm = None
                        for b_num, b_bpm, b_t in zip(beats, bpms, t_vals):
                            if last_bpm is None or abs(float(b_bpm) - float(last_bpm)) > 0.15:
                                t_sec = (b_t / 1000.0) if is_ms else b_t
                                ET.SubElement(tr_el, "TEMPO", {
                                    "Inizio": f"{t_sec:.3f}",
                                    "Bpm": f"{float(b_bpm):.2f}",
                                    "Metro": "4/4",
                                    "Battito": str(int(b_num) if int(b_num) in (1, 2, 3, 4) else 1),
                                })
                                last_bpm = b_bpm
                except Exception:
                    pass

            cues_written = 0
            try:
                cues = getattr(tr, "Cues", None) or []
                for cue in cues:
                    if getattr(cue, "rb_local_deleted", 0):
                        continue
                    in_ms = getattr(cue, "InMsec", None)
                    if in_ms is None:
                        continue
                    out_ms = getattr(cue, "OutMsec", 0) or 0
                    kind = int(getattr(cue, "Kind", 0) or 0)
                    num = -1 if kind == 0 else (kind - 1)
                    is_loop = bool(out_ms and float(out_ms) > float(in_ms))
                    cue_attrs = {
                        "Name": str(getattr(cue, "Comment", "") or ""),
                        "Type": "4" if is_loop else "0",
                        "Start": f"{(float(in_ms) / 1000.0):.3f}",
                        "Num": str(num),
                    }
                    if is_loop:
                        cue_attrs["End"] = f"{(float(out_ms) / 1000.0):.3f}"
                    cr = getattr(cue, "ColorRed", None)
                    cg = getattr(cue, "ColorGreen", None)
                    cb = getattr(cue, "ColorBlue", None)
                    if cr is not None and cg is not None and cb is not None and int(cr) >= 0 and int(cg) >= 0 and int(cb) >= 0:
                        cue_attrs["Red"] = str(int(cr))
                        cue_attrs["Green"] = str(int(cg))
                        cue_attrs["Blue"] = str(int(cb))
                    elif num >= 0:
                        # Standard Rekordbox Hot Cue green so Rekordbox macOS/Win always displays the pad
                        cue_attrs["Red"] = "40"
                        cue_attrs["Green"] = "226"
                        cue_attrs["Blue"] = "20"
                    ET.SubElement(tr_el, "POSITION_MARK", cue_attrs)
                    cues_written += 1
            except Exception:
                pass

            # Fallback: if djmdCue returned 0 cues, check ANLZ .EXT/.DAT cue lists (PCO2/PCOB)
            if cues_written == 0 and anlz_path:
                try:
                    from pyrekordbox.anlz import AnlzFile
                    for cand_p in (anlz_path.with_suffix(".EXT"), anlz_path):
                        if not cand_p.exists():
                            continue
                        anlz_c = AnlzFile.parse_file(str(cand_p))
                        for tag_key in ("cue_list2", "cue_list"):
                            try:
                                clist = anlz_c.get(tag_key)
                            except Exception:
                                clist = None
                            if not clist:
                                continue
                            entries = getattr(clist, "entries", None) or (clist if isinstance(clist, list) else [])
                            for ce in entries:
                                t_ms = getattr(ce, "time", None)
                                if t_ms is None:
                                    continue
                                hot = int(getattr(ce, "hot_cue", 0) or 0)
                                num = -1 if hot == 0 else (hot - 1)
                                loop_t = getattr(ce, "loop_time", 0) or 0
                                is_loop = bool(loop_t and float(loop_t) > float(t_ms))
                                ca = {
                                    "Name": str(getattr(ce, "comment", "") or ""),
                                    "Type": "4" if is_loop else "0",
                                    "Start": f"{(float(t_ms) / 1000.0):.3f}",
                                    "Num": str(num),
                                }
                                if is_loop:
                                    ca["End"] = f"{(float(loop_t) / 1000.0):.3f}"
                                if num >= 0:
                                    r_v = getattr(ce, "color_red", None)
                                    g_v = getattr(ce, "color_green", None)
                                    b_v = getattr(ce, "color_blue", None)
                                    if r_v is not None and g_v is not None and b_v is not None and int(r_v) >= 0:
                                        ca["Red"], ca["Green"], ca["Blue"] = str(int(r_v)), str(int(g_v)), str(int(b_v))
                                    else:
                                        ca["Red"], ca["Green"], ca["Blue"] = "40", "226", "20"
                                ET.SubElement(tr_el, "POSITION_MARK", ca)
                                cues_written += 1
                        if cues_written > 0:
                            break
                except Exception:
                    pass

        collection_el.attrib["Entries"] = str(tracks_written)

        set_job_progress(job_id, 85, "Montando estrutura de playlists...")
        all_pls = [p for p in db.get_playlist() if not getattr(p, "rb_local_deleted", 0)]
        children_map: dict[str, list] = {}
        for pl in all_pls:
            pid = str(getattr(pl, "ParentID", "root") or "root")
            children_map.setdefault(pid, []).append(pl)
        for k in children_map:
            children_map[k].sort(key=lambda x: (int(getattr(x, "Seq", 0) or 0), str(getattr(x, "Name", "") or "").lower()))

        playlists_el = ET.SubElement(dj_root, "PLAYLISTS")
        root_node = ET.SubElement(playlists_el, "NODE", {"Type": "0", "Name": "ROOT", "Count": "0"})

        def append_children(parent_el: ET.Element, parent_id: str) -> int:
            added = 0
            for pl in children_map.get(parent_id, []):
                pl_id = str(getattr(pl, "ID", ""))
                pl_name = str(getattr(pl, "Name", "Playlist") or "Playlist")
                attr_type = int(getattr(pl, "Attribute", 0) or 0)
                if attr_type == 1:
                    folder_el = ET.SubElement(parent_el, "NODE", {"Type": "0", "Name": pl_name, "Count": "0"})
                    folder_el.attrib["Count"] = str(append_children(folder_el, pl_id))
                    added += 1
                else:
                    song_ids = []
                    song_rows = []
                    try:
                        if hasattr(db, "get_playlist_songs"):
                            song_rows = list(db.get_playlist_songs(PlaylistID=pl.ID))
                    except Exception:
                        song_rows = []
                    if not song_rows:
                        try:
                            song_rows = list(getattr(pl, "Songs", []) or [])
                        except Exception:
                            song_rows = []
                    if song_rows:
                        song_rows = [s for s in song_rows if not getattr(s, "rb_local_deleted", 0)]
                        song_rows.sort(key=lambda s: (int(getattr(s, "TrackNo", 0) or 0), str(getattr(s, "ID", "") or "")))
                        for s in song_rows:
                            cid = str(getattr(s, "ContentID", "") or "")
                            if cid in valid_track_ids:
                                song_ids.append(cid)
                    elif hasattr(db, "get_playlist_contents"):
                        try:
                            for c in db.get_playlist_contents(pl):
                                cid = str(getattr(c, "ID", "") or "")
                                if cid in valid_track_ids:
                                    song_ids.append(cid)
                        except Exception:
                            pass

                    pl_el = ET.SubElement(parent_el, "NODE", {
                        "Name": pl_name, "Type": "1", "KeyType": "0", "Entries": str(len(song_ids)),
                    })
                    for cid in song_ids:
                        ET.SubElement(pl_el, "TRACK", {"Key": cid})
                    added += 1
            return added

        root_node.attrib["Count"] = str(append_children(root_node, "root"))
        output_xml_path.parent.mkdir(parents=True, exist_ok=True)
        write_rekordbox_xml(dj_root, output_xml_path)
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass
        if snap_tmp_ctx is not None:
            try:
                snap_tmp_ctx.cleanup()
            except Exception:
                pass

    set_job_progress(job_id, 96, "Finalizando leitura das playlists...")
    parsed_out = parse_rekordbox_xml_playlists(output_xml_path)
    set_job_progress(job_id, 100, "Playlists carregadas!")
    return parsed_out


# ---------------------------------------------------------------------------
# Selective Playlist & Sample Export / Import (Official rekordbox.xml)
# ---------------------------------------------------------------------------

_XML_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize_xml_text(s: str) -> str:
    return _XML_CTRL_RE.sub("", str(s or ""))


def _infer_rekordbox_kind(path_str: str) -> str:
    ext = Path(path_str).suffix.lower()
    return {
        ".mp3": "MP3 File",
        ".wav": "WAV File",
        ".aif": "AIFF File",
        ".aiff": "AIFF File",
        ".flac": "FLAC File",
        ".m4a": "M4A File",
        ".aac": "AAC File",
        ".alac": "ALAC File",
        ".ogg": "OGG File",
        ".mp4": "MP4 File",
    }.get(ext, "MP3 File")


def _indent_xml(elem: ET.Element, level: int = 0) -> None:
    indent = "\n" + ("  " * level)
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = indent + "  "
        for i, child in enumerate(elem):
            _indent_xml(child, level + 1)
            if not child.tail or not child.tail.strip():
                child.tail = indent + ("  " if i < len(elem) - 1 else "")
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = indent


def _normalize_rekordbox_xml_tree(root: ET.Element) -> None:
    today_iso = datetime.date.today().isoformat()
    for el in root.iter():
        for k in list(el.attrib.keys()):
            el.attrib[k] = _sanitize_xml_text(el.attrib[k])

    collection = root.find("COLLECTION")
    if collection is not None:
        for tr in collection.findall("TRACK"):
            loc = tr.attrib.get("Location", "")
            local_p = xml_location_to_local_path(loc)
            if not tr.attrib.get("Kind"):
                tr.attrib["Kind"] = _infer_rekordbox_kind(local_p)
            if not tr.attrib.get("Size"):
                try:
                    sz = Path(local_p).stat().st_size if local_p and Path(local_p).exists() else 0
                except OSError:
                    sz = 0
                tr.attrib["Size"] = str(sz)
            if not tr.attrib.get("SampleRate"):
                tr.attrib["SampleRate"] = "44100"
            if not tr.attrib.get("DateAdded"):
                tr.attrib["DateAdded"] = today_iso
            # Auto-repair TEMPO timestamps if an older export divided seconds by 1000
            tempos = tr.findall("TEMPO")
            try:
                t_len = float(tr.attrib.get("TotalTime", "0") or 0)
            except ValueError:
                t_len = 0.0
            if len(tempos) >= 3 and t_len >= 30:
                try:
                    inizios = [float(tm.attrib.get("Inizio", "0") or 0) for tm in tempos]
                    if max(inizios) > 0 and max(inizios) < 1.0 and (max(inizios) * 1000.0) <= (t_len + 15.0):
                        for tm, val in zip(tempos, inizios):
                            tm.attrib["Inizio"] = f"{(val * 1000.0):.3f}"
                except ValueError:
                    pass

            for pm in tr.findall("POSITION_MARK"):
                try:
                    num_i = int(pm.attrib.get("Num", "-1"))
                except ValueError:
                    num_i = -1
                if num_i >= 0 and not pm.attrib.get("Red"):
                    pm.attrib["Red"] = "40"
                    pm.attrib["Green"] = "226"
                    pm.attrib["Blue"] = "20"


def write_rekordbox_xml(root: ET.Element, out_path: Path) -> None:
    """
    Writes strict, pretty-printed Rekordbox XML compatible with both macOS & Windows:
    - Double-quoted uppercase header: <?xml version="1.0" encoding="UTF-8"?>
    - Clean 2-space indentation with LF newlines (required by Rekordbox's macOS parser)
    - Sanitized attributes and standard TRACK metadata (Kind, Size, SampleRate, DateAdded)
    """
    _normalize_rekordbox_xml_tree(root)
    _indent_xml(root)
    xml_body = ET.tostring(root, encoding="unicode")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n' + xml_body + "\n")


def xml_location_to_local_path(location: str) -> str:
    if not location:
        return ""
    s = location
    if s.startswith("file://localhost"):
        s = s[len("file://localhost"):]
    elif s.startswith("file://"):
        s = s[len("file://"):]
    s = urllib.parse.unquote(s)
    if re.match(r"^/[A-Za-z]:/", s):
        s = s[1:]
    return s


def local_path_to_xml_location(path_str: str) -> str:
    p = path_str.replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p
    encoded = urllib.parse.quote(p, safe="/:")
    return f"file://localhost{encoded}"


def parse_rekordbox_xml_playlists(xml_path: Path) -> dict:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    collection = root.find("COLLECTION")
    tracks_by_id = {}
    if collection is not None:
        for t in collection.findall("TRACK"):
            tid = t.attrib.get("TrackID")
            if tid:
                tracks_by_id[tid] = t.attrib.get("Location", "")

    playlists = []

    def walk_node(node: ET.Element, prefix: str = ""):
        ntype = node.attrib.get("Type", "")
        name = node.attrib.get("Name", "")
        if ntype == "1":
            full_path = f"{prefix}/{name}" if prefix else name
            keys = [tr.attrib.get("Key") for tr in node.findall("TRACK") if tr.attrib.get("Key")]
            playlists.append({
                "path": full_path,
                "name": name,
                "folder": prefix,
                "trackCount": len(keys),
            })
        else:
            next_prefix = f"{prefix}/{name}" if (prefix and name and name != "ROOT") else (name if name and name != "ROOT" else prefix)
            for child in node.findall("NODE"):
                walk_node(child, next_prefix)

    pl_root = root.find("PLAYLISTS")
    if pl_root is not None:
        for child in pl_root.findall("NODE"):
            walk_node(child, "")

    return {
        "xmlPath": str(xml_path),
        "totalTracks": len(tracks_by_id),
        "playlists": playlists,
    }


def export_selected_playlists(
    xml_path: Path,
    selected_paths: list[str],
    dest_dir: Path,
    pack_name: str,
    include_audio: bool = True,
    include_rekordbox_sampler: bool = False,
    samples_dir: str = "",
    selected_sample_items: list[dict] | None = None,
    sampler_global_props: dict | None = None,
    pack_tags: list[str] | None = None,
    include_rb_prefs: bool = False,
    include_rb_fx: bool = False,
    include_rb_midi: bool = False,
    make_zip: bool = True,
    samples_config_only: bool = False,
    zip_compression: str = "stored",
    sampler_override_files: list[dict] | None = None,
    config_root_override: Path | None = None,
    job_id: str | None = None,
) -> dict:
    selected_set = set(selected_paths)
    has_valid_xml = (xml_path is not None and str(xml_path).strip() not in ("", ".") and xml_path.is_file())
    if has_valid_xml:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    else:
        if selected_set:
            raise ValueError("Carregue as playlists do Rekordbox antes de exportar playlists.")
        root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
        ET.SubElement(root, "PRODUCT", {"Name": "rekordbox", "Version": "7.0.0", "Company": "AlphaTheta"})
        ET.SubElement(root, "COLLECTION", {"Entries": "0"})
        pls_el = ET.SubElement(root, "PLAYLISTS")
        ET.SubElement(pls_el, "NODE", {"Type": "0", "Name": "ROOT", "Count": "0"})
    kept_track_ids = set()

    def prune_node(node: ET.Element, prefix: str = "") -> bool:
        ntype = node.attrib.get("Type", "")
        name = node.attrib.get("Name", "")
        if ntype == "1":
            full_path = f"{prefix}/{name}" if prefix else name
            if full_path in selected_set:
                for tr in node.findall("TRACK"):
                    k = tr.attrib.get("Key")
                    if k:
                        kept_track_ids.add(k)
                return True
            return False
        next_prefix = f"{prefix}/{name}" if (prefix and name and name != "ROOT") else (name if name and name != "ROOT" else prefix)
        kept_children = []
        for child in list(node.findall("NODE")):
            if prune_node(child, next_prefix):
                kept_children.append(child)
            else:
                node.remove(child)
        node.attrib["Count"] = str(len(kept_children))
        return len(kept_children) > 0 or name == "ROOT"

    pl_root = root.find("PLAYLISTS")
    if pl_root is not None:
        for child in list(pl_root.findall("NODE")):
            prune_node(child, "")

    collection = root.find("COLLECTION")
    copied_audio_count = 0
    missing_audio = []

    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_pack = re.sub(r"[^a-zA-Z0-9_-]+", "_", pack_name or "Playlists").strip("_") or "Playlists"
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    folder_name = f"RekordboxPlaylists_{safe_pack}_{stamp}"
    staging = Path(tempfile.mkdtemp(prefix="rb_pl_")) if make_zip else (dest_dir / folder_name)
    staging.mkdir(parents=True, exist_ok=True)
    audio_out = staging / "audio"

    set_job_progress(job_id, 8, "Preparando faixas e áudios...")
    audio_map = {}
    if collection is not None:
        kept_tracks = 0
        total_kept = max(1, len(kept_track_ids))
        for tr in list(collection.findall("TRACK")):
            tid = tr.attrib.get("TrackID")
            if tid not in kept_track_ids:
                collection.remove(tr)
                continue
            kept_tracks += 1
            if job_id:
                set_job_progress(job_id, 10 + int((kept_tracks / total_kept) * 48), f"Copiando áudios ({kept_tracks}/{total_kept})...")
            if include_audio:
                src_local = xml_location_to_local_path(tr.attrib.get("Location", ""))
                src_p = Path(src_local) if src_local else None
                if src_p and src_p.exists() and src_p.is_file():
                    audio_out.mkdir(parents=True, exist_ok=True)
                    dest_fname = f"{tid}_{src_p.name}"
                    shutil.copy2(src_p, audio_out / dest_fname)
                    audio_map[tid] = dest_fname
                    copied_audio_count += 1
                else:
                    missing_audio.append(src_local or tr.attrib.get("Name", tid))
        collection.attrib["Entries"] = str(kept_tracks)

    set_job_progress(job_id, 60, "Empacotando samples selecionados...")
    # Selective Samples copying (including SamplerSettings1.xml pad edits!)
    samples_root = staging / "samples"
    copied_samples_count = 0
    rb_sampler_copied = 0
    exported_pad_tracks: list[dict] = []
    sampler_analysis_map: dict[str, dict] = {}
    sampler_pad_rel_map: dict[str, str] = {}
    pad_src_paths: dict[str, Path] = {}

    if selected_sample_items is not None:
        for item in selected_sample_items:
            src_s = Path(item.get("path", ""))
            if not src_s.exists() or not src_s.is_file():
                continue
            src_kind = item.get("source") or "db"
            slot_attribs = item.get("slotAttribs")
            if src_kind == "sampler_xml" and isinstance(slot_attribs, dict):
                idx_str = str(slot_attribs.get("idx", copied_samples_count))
                clean_src_name = re.sub(r"^(?:slot\d+_)+", "", src_s.name)
                rel_posix = f"Rekordbox_Sampler/Pads/slot{idx_str}_{clean_src_name}"
                if not samples_config_only:
                    dest_sub = samples_root / "Rekordbox_Sampler" / "Pads"
                    dest_sub.mkdir(parents=True, exist_ok=True)
                    dst_file = dest_sub / f"slot{idx_str}_{clean_src_name}"
                    shutil.copy2(src_s, dst_file)
                att_copy = dict(slot_attribs)
                att_copy["url"] = rel_posix
                exported_pad_tracks.append(att_copy)
                sampler_pad_rel_map[idx_str] = rel_posix
                pad_src_paths[rel_posix] = src_s
                rb_sampler_copied += 1
                copied_samples_count += 1
            elif not samples_config_only:
                bank_sub = _safe_rel_bank(item.get("bank") or "Sampler")
                if src_kind == "custom":
                    dest_sub = samples_root / bank_sub
                else:
                    dest_sub = samples_root / "Rekordbox_Sampler" / bank_sub
                    rb_sampler_copied += 1
                dest_sub.mkdir(parents=True, exist_ok=True)
                dst_file = dest_sub / src_s.name
                if dst_file.exists():
                    dst_file = dest_sub / f"{src_s.stem}_{copied_samples_count}{src_s.suffix}"
                shutil.copy2(src_s, dst_file)
                copied_samples_count += 1

    if exported_pad_tracks:
        ss_root = build_sampler_settings_xml_element(sampler_global_props or {}, exported_pad_tracks)
        _indent_xml(ss_root)
        ss_body = ET.tostring(ss_root, encoding="unicode")
        with open(staging / "sampler_settings_export.xml", "w", encoding="utf-8", newline="\n") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n\n' + ss_body + "\n")
        sampler_analysis_map = _bundle_sampler_bpm_and_anlz(staging, exported_pad_tracks, pad_src_paths)
    else:
        if include_rekordbox_sampler:
            sampler_items = (
                sampler_override_files
                if sampler_override_files is not None
                else detect_rekordbox_sampler_files().get("files", [])
            )
            for item in sampler_items:
                src_s = Path(item.get("path", ""))
                if not src_s.exists() or not src_s.is_file():
                    continue
                bank_sub = _safe_rel_bank(item.get("bank") or "Sampler")
                dest_sub = samples_root / "Rekordbox_Sampler" / bank_sub
                dest_sub.mkdir(parents=True, exist_ok=True)
                dst_file = dest_sub / src_s.name
                if dst_file.exists():
                    dst_file = dest_sub / f"{src_s.stem}_{copied_samples_count}{src_s.suffix}"
                shutil.copy2(src_s, dst_file)
                rb_sampler_copied += 1
                copied_samples_count += 1

        if samples_dir and Path(samples_dir).exists():
            user_samp_dest = (samples_root / "Meus_Samples") if rb_sampler_copied > 0 else samples_root
            shutil.copytree(Path(samples_dir), user_samp_dest, dirs_exist_ok=True)
            copied_samples_count = sum(1 for f in samples_root.rglob("*") if f.is_file())

    inc_samples = copied_samples_count > 0

    rb_extras_bundled = _bundle_rb_config_extras(
        staging, include_rb_prefs, include_rb_fx, include_rb_midi, config_root_override
    )
    clean_tags = [str(t).strip() for t in (pack_tags or []) if str(t).strip()]

    out_xml = staging / "rekordbox_playlists.xml"
    write_rekordbox_xml(root, out_xml)

    pack_manifest = {
        "packVersion": "0.0.9",
        "packTags": clean_tags,
        "includesRbPrefs": rb_extras_bundled["prefs"] > 0,
        "includesRbFx": rb_extras_bundled["fx"] > 0,
        "includesRbMidi": rb_extras_bundled["midi"] > 0,
        "packName": pack_name or "Playlists",
        "createdAt": now_str(),
        "sourceOs": "windows" if IS_WIN else "macos",
        "playlists": selected_paths,
        "trackCount": len(kept_track_ids),
        "includesAudio": include_audio and copied_audio_count > 0,
        "includesSamples": inc_samples,
        "includesRekordboxSampler": rb_sampler_copied > 0,
        "rekordboxSamplerCount": rb_sampler_copied,
        "hasSamplerPadEdits": bool(exported_pad_tracks),
        "samplesConfigOnly": bool(samples_config_only and exported_pad_tracks),
        "samplerPadRelMap": sampler_pad_rel_map,
        "samplerAnalysisMap": sampler_analysis_map,
        "totalSamplesCount": copied_samples_count,
        "cueCount": len(collection.findall(".//POSITION_MARK")) if collection is not None else 0,
        "copiedAudioCount": copied_audio_count,
        "missingAudioCount": len(missing_audio),
        "audioMap": audio_map,
    }
    (staging / "playlist_pack.json").write_text(json.dumps(pack_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    final_output = str(staging)
    if make_zip:
        set_job_progress(job_id, 74, "Compactando pacote .zip...")
        pack_sub = dest_dir / safe_pack
        pack_sub.mkdir(parents=True, exist_ok=True)
        zip_path = pack_sub / f"{folder_name}.zip"
        all_stage_files = [fp for fp in staging.rglob("*") if fp.is_file()]
        total_zf = max(1, len(all_stage_files))
        comp_mode = (zip_compression or "stored").lower()
        zf_kwargs: dict = {"compression": zipfile.ZIP_STORED}
        if comp_mode == "balanced":
            zf_kwargs = {"compression": zipfile.ZIP_DEFLATED, "compresslevel": 6}
        elif comp_mode == "high":
            try:
                import bz2  # noqa: F401
                zf_kwargs = {"compression": zipfile.ZIP_BZIP2, "compresslevel": 9}
            except Exception:
                zf_kwargs = {"compression": zipfile.ZIP_DEFLATED, "compresslevel": 9}
        elif comp_mode == "extreme":
            try:
                import lzma  # noqa: F401
                zf_kwargs = {"compression": zipfile.ZIP_LZMA}
            except Exception:
                zf_kwargs = {"compression": zipfile.ZIP_DEFLATED, "compresslevel": 9}
        with zipfile.ZipFile(zip_path, "w", **zf_kwargs) as zf:
            for zi_idx, fp in enumerate(all_stage_files):
                zf.write(fp, fp.relative_to(staging).as_posix())
                if job_id:
                    set_job_progress(job_id, 75 + int(((zi_idx + 1) / total_zf) * 23), f"Compactando [{comp_mode}] ({zi_idx + 1}/{total_zf})...")
        shutil.rmtree(staging, ignore_errors=True)
        final_output = str(zip_path)
    set_job_progress(job_id, 100, "Pacote pronto!")

    return {
        "outputPath": final_output,
        "playlistCount": len(selected_paths),
        "trackCount": len(kept_track_ids),
        "copiedAudioCount": copied_audio_count,
        "missingAudioCount": len(missing_audio),
        "includesSamples": inc_samples,
        "totalSamplesCount": copied_samples_count,
        "rekordboxSamplerCount": rb_sampler_copied,
        "packTags": clean_tags,
        "includesRbPrefs": rb_extras_bundled["prefs"] > 0,
        "includesRbFx": rb_extras_bundled["fx"] > 0,
        "includesRbMidi": rb_extras_bundled["midi"] > 0,
    }


def _list_pack_samples(work_dir: Path) -> list[dict]:
    samples_dir = work_dir / "samples"
    if not samples_dir.exists() and (work_dir / "Samples").exists():
        samples_dir = work_dir / "Samples"
    ss_exp = work_dir / "sampler_settings_export.xml"
    if not samples_dir.exists():
        # Config-only folder: list pads directly from sampler_settings_export.xml
        if ss_exp.exists():
            try:
                _, slots = parse_sampler_settings_xml(ss_exp)
                return [{
                    "relPath": sl["url"],
                    "name": sl["slotName"],
                    "group": sl["bankLabel"],
                    "editsLabel": sl["editsLabel"],
                    "hasPadEdits": True,
                    "configOnly": True,
                    "sizeLabel": "Só config",
                } for sl in slots]
            except Exception:
                pass
        return []
    pad_meta_by_rel: dict[str, dict] = {}
    ss_exp = work_dir / "sampler_settings_export.xml"
    if ss_exp.exists():
        try:
            _, slots = parse_sampler_settings_xml(ss_exp)
            for sl in slots:
                pad_meta_by_rel[sl["url"]] = sl
        except Exception:
            pass

    out = []
    for fp in sorted(samples_dir.rglob("*"), key=lambda p: p.as_posix().lower()):
        if not fp.is_file():
            continue
        rel = fp.relative_to(samples_dir).as_posix()
        parent = fp.parent.relative_to(samples_dir).as_posix()
        pmeta = pad_meta_by_rel.get(rel)
        if pmeta:
            out.append({
                "relPath": rel,
                "name": pmeta["slotName"],
                "group": pmeta["bankLabel"],
                "editsLabel": pmeta["editsLabel"],
                "hasPadEdits": True,
                "sizeLabel": format_bytes(fp.stat().st_size),
            })
        else:
            out.append({
                "relPath": rel,
                "name": fp.name,
                "group": "" if parent == "." else parent,
                "editsLabel": "",
                "hasPadEdits": False,
                "sizeLabel": format_bytes(fp.stat().st_size),
            })
    return out



def _resolve_pack_root(source_path: Path, tmp_dirs: list[Path]) -> tuple[Path, Path]:
    """
    Resolves a Rekordbox Vault pack from:
    - a normal .zip
    - a Google Drive folder download .zip that wraps another .zip inside a subfolder
    - an already-extracted folder (even with nested subfolders)
    Returns (work_dir, xml_file).
    """
    work_dir = source_path
    if source_path.is_file() and source_path.suffix.lower() == ".zip":
        t1 = Path(tempfile.mkdtemp(prefix="rb_pl_z1_"))
        tmp_dirs.append(t1)
        with zipfile.ZipFile(source_path, "r") as zf:
            zf.extractall(t1)
        work_dir = t1

    def find_xml_in(d: Path) -> Path | None:
        for pref in ("rekordbox_playlists.xml", "rekordbox_import.xml"):
            direct = d / pref
            if direct.exists():
                return direct
        for cand in sorted(d.rglob("*.xml"), key=lambda x: (len(x.parts), x.name.lower())):
            if "__MACOSX" in cand.parts or cand.name.startswith("._"):
                continue
            low_parts = [pt.lower() for pt in cand.parts]
            if "rb_config" in low_parts or "sampler_settings" in cand.name.lower():
                continue
            return cand
        return None

    xml_file = find_xml_in(work_dir)
    if xml_file is not None:
        return xml_file.parent, xml_file

    # Valid samples/configs-only folder without playlist XML
    if (work_dir / "playlist_pack.json").exists() or (work_dir / "sampler_settings_export.xml").exists() or (work_dir / "rb_config").exists():
        return work_dir, None

    # Unwrap nested .zip (e.g. Google Drive folder download wrapping the Vault .zip)
    for depth in range(2):
        nested_zips = [
            z for z in sorted(work_dir.rglob("*.zip"), key=lambda x: x.stat().st_size, reverse=True)
            if "__MACOSX" not in z.parts and not z.name.startswith("._")
        ]
        if not nested_zips:
            break
        t_next = Path(tempfile.mkdtemp(prefix=f"rb_pl_z{depth + 2}_"))
        tmp_dirs.append(t_next)
        with zipfile.ZipFile(nested_zips[0], "r") as zf:
            zf.extractall(t_next)
        work_dir = t_next
        xml_file = find_xml_in(work_dir)
        if xml_file is not None:
            return xml_file.parent, xml_file
        if (work_dir / "playlist_pack.json").exists() or (work_dir / "sampler_settings_export.xml").exists() or (work_dir / "rb_config").exists():
            return work_dir, None

    raise ValueError("Nenhum arquivo XML ou configuração de pacote encontrado.")


def _fast_inspect_zip_pack(source_path: Path) -> dict | None:
    """Inspect a Vault .zip directly from its central directory without extracting audio files."""
    try:
        with zipfile.ZipFile(source_path, "r") as zf:
            infos = [zi for zi in zf.infolist() if not zi.is_dir() and "__MACOSX" not in zi.filename and not Path(zi.filename).name.startswith("._")]
            xml_info = None
            for zi in infos:
                if Path(zi.filename).name.lower() == "rekordbox_playlists.xml":
                    xml_info = zi
                    break
            if xml_info is None:
                cands = [zi for zi in infos if zi.filename.lower().endswith(".xml") and "sampler_settings" not in zi.filename.lower() and "rb_config/" not in zi.filename.lower()]
                cands.sort(key=lambda z: (z.filename.count("/"), z.filename.lower()))
                if cands:
                    xml_info = cands[0]
            if xml_info is None:
                return None  # Likely a nested .zip; let _resolve_pack_root unwrap it

            prefix = ""
            if "/" in xml_info.filename:
                prefix = xml_info.filename.rsplit("/", 1)[0] + "/"

            # Parse XML via tiny temp file (a few KB)
            with tempfile.NamedTemporaryFile(prefix="rb_xml_insp_", suffix=".xml", delete=False) as tf:
                tf.write(zf.read(xml_info))
                tmp_xml = Path(tf.name)
            try:
                parsed = parse_rekordbox_xml_playlists(tmp_xml)
            finally:
                try:
                    tmp_xml.unlink(missing_ok=True)
                except Exception:
                    pass

            info_map = {zi.filename: zi for zi in infos}
            mf: dict = {}
            mf_zi = info_map.get(prefix + "playlist_pack.json")
            if mf_zi is not None:
                try:
                    mf = json.loads(zf.read(mf_zi).decode("utf-8", errors="replace"))
                except Exception:
                    mf = {}

            samp_xml_zi = info_map.get(prefix + "sampler_settings_export.xml")
            has_pad_edits = samp_xml_zi is not None
            pad_meta_by_rel: dict[str, dict] = {}
            if samp_xml_zi is not None:
                try:
                    root_s = ET.fromstring(zf.read(samp_xml_zi))
                    for tr_el in root_s.findall(".//Track"):
                        rel_u = (tr_el.attrib.get("relPath") or tr_el.attrib.get("url") or "").replace("\\", "/").lstrip("/")
                        if rel_u.lower().startswith("samples/"):
                            rel_u = rel_u[8:]
                        if rel_u:
                            idx_i = int(tr_el.attrib.get("idx") or 0)
                            bank_no = (idx_i // 16) + 1
                            slot_no = (idx_i % 16) + 1
                            parts = []
                            if tr_el.attrib.get("loop") == "1":
                                parts.append("Loop")
                            if tr_el.attrib.get("gate") == "1":
                                parts.append("Gate")
                            if tr_el.attrib.get("sync") == "1":
                                parts.append("Sync")
                            if tr_el.attrib.get("gain"):
                                try:
                                    parts.append(f"Gain {float(tr_el.attrib.get('gain')):.0f}")
                                except Exception:
                                    parts.append(f"Gain {tr_el.attrib.get('gain')}")
                            pad_meta_by_rel[rel_u.lower()] = {
                                "group": f"Banco {bank_no} • Slot {slot_no}",
                                "padName": tr_el.attrib.get("name", ""),
                                "editsLabel": " • ".join(parts),
                            }
                except Exception:
                    pass

            samp_prefix_low = (prefix + "samples/").lower()
            pack_samples: list[dict] = []
            has_prefs = bool(mf.get("includesRbPrefs"))
            has_fx = bool(mf.get("includesRbFx"))
            has_midi = bool(mf.get("includesRbMidi"))
            rb_cfg_prefix_low = (prefix + "rb_config/").lower()

            for zi in infos:
                fn = zi.filename
                fn_low = fn.lower()
                if fn_low.startswith(rb_cfg_prefix_low):
                    sub = fn_low[len(rb_cfg_prefix_low):]
                    if sub.startswith("prefs/"):
                        has_prefs = True
                    elif sub.startswith("fx/"):
                        has_fx = True
                    elif sub.startswith("midi/"):
                        has_midi = True
                elif fn_low.startswith(samp_prefix_low):
                    rel = fn[len(samp_prefix_low):]
                    if not rel or Path(rel).suffix.lower() not in AUDIO_EXTS:
                        continue
                    pm = pad_meta_by_rel.get(rel.lower()) or {}
                    src_tag = "Rekordbox Sampler" if rel.lower().startswith("rekordbox_sampler/") else "Pasta Extra"
                    pack_samples.append({
                        "name": pm.get("padName") or Path(rel).name,
                        "fileName": Path(rel).name,
                        "relPath": rel,
                        "source": src_tag,
                        "group": pm.get("group") or src_tag,
                        "editsLabel": pm.get("editsLabel", ""),
                        "hasPadEdits": bool(pm),
                        "sizeLabel": format_bytes(zi.file_size),
                    })

            if not pack_samples and samp_xml_zi is not None:
                try:
                    root_s2 = ET.fromstring(zf.read(samp_xml_zi))
                    for tr_el in root_s2.findall(".//Track"):
                        rel_u = (tr_el.attrib.get("url") or "").replace("\\", "/").lstrip("/")
                        if not rel_u:
                            continue
                        idx_i = int(tr_el.attrib.get("idx") or 0)
                        bank_no = (idx_i // 16) + 1
                        slot_no = (idx_i % 16) + 1
                        parts = []
                        if tr_el.attrib.get("loop") == "1":
                            parts.append("Loop")
                        if tr_el.attrib.get("gate") == "1":
                            parts.append("Gate")
                        if tr_el.attrib.get("sync") == "1":
                            parts.append("Sync")
                        if tr_el.attrib.get("gain"):
                            parts.append(f"Gain {tr_el.attrib.get('gain')}")
                        pack_samples.append({
                            "name": tr_el.attrib.get("name") or Path(rel_u).name,
                            "fileName": Path(rel_u).name,
                            "relPath": rel_u,
                            "source": "Apenas Config",
                            "group": f"Banco {bank_no} • Slot {slot_no}",
                            "editsLabel": " • ".join(parts),
                            "hasPadEdits": True,
                            "configOnly": True,
                            "sizeLabel": "Só config",
                        })
                except Exception:
                    pass

            return {
                "kind": "pack",
                "sourcePath": str(source_path),
                "packName": mf.get("packName") or source_path.stem,
                "sourceOs": mf.get("sourceOs") or "windows",
                "createdAt": mf.get("createdAt") or "",
                "includesAudio": bool(mf.get("includesAudio")),
                "includesSamples": bool(pack_samples) or bool(mf.get("includesSamples")),
                "rekordboxSamplerCount": int(mf.get("rekordboxSamplerCount") or 0),
                "hasSamplerPadEdits": has_pad_edits,
                "samplesConfigOnly": bool(mf.get("samplesConfigOnly")),
                "packTags": mf.get("packTags") or [],
                "includesRbPrefs": has_prefs,
                "includesRbFx": has_fx,
                "includesRbMidi": has_midi,
                "samples": pack_samples,
                "playlists": parsed["playlists"],
                "totalTracks": parsed["totalTracks"],
            }
    except Exception:
        return None


def inspect_playlist_pack(source_path: Path) -> dict:
    if source_path.is_file() and source_path.suffix.lower() == ".xml":
        parsed = parse_rekordbox_xml_playlists(source_path)
        return {
            "kind": "xml",
            "sourcePath": str(source_path),
            "packName": source_path.stem,
            "sourceOs": "desconhecido",
            "includesAudio": False,
            "includesSamples": False,
            "rekordboxSamplerCount": 0,
            "samples": [],
            "playlists": parsed["playlists"],
            "totalTracks": parsed["totalTracks"],
        }

    if source_path.is_file() and source_path.suffix.lower() == ".zip":
        fast_res = _fast_inspect_zip_pack(source_path)
        if fast_res is not None:
            return fast_res

    tmp_dirs: list[Path] = []
    try:
        work_dir, xml_file = _resolve_pack_root(source_path, tmp_dirs)
        mf_file = work_dir / "playlist_pack.json"
        parsed = parse_rekordbox_xml_playlists(xml_file) if (xml_file is not None and xml_file.exists()) else {"playlists": [], "totalTracks": 0}
        mf = json.loads(mf_file.read_text(encoding="utf-8")) if mf_file.exists() else {}
        pack_samples = _list_pack_samples(work_dir)
        return {
            "kind": "pack",
            "sourcePath": str(source_path),
            "packName": mf.get("packName") or source_path.stem,
            "sourceOs": mf.get("sourceOs") or "windows",
            "createdAt": mf.get("createdAt") or "",
            "includesAudio": bool(mf.get("includesAudio")),
            "includesSamples": bool(pack_samples) or bool(mf.get("includesSamples")),
            "rekordboxSamplerCount": int(mf.get("rekordboxSamplerCount") or 0),
            "hasSamplerPadEdits": (work_dir / "sampler_settings_export.xml").exists(),
            "packTags": mf.get("packTags") or [],
            "includesRbPrefs": bool(mf.get("includesRbPrefs")) or (work_dir / "rb_config" / "prefs").exists(),
            "includesRbFx": bool(mf.get("includesRbFx")) or (work_dir / "rb_config" / "fx").exists(),
            "includesRbMidi": bool(mf.get("includesRbMidi")) or (work_dir / "rb_config" / "midi").exists(),
            "samples": pack_samples,
            "playlists": parsed["playlists"],
            "totalTracks": parsed["totalTracks"],
        }
    finally:
        for td in tmp_dirs:
            if td.exists():
                shutil.rmtree(td, ignore_errors=True)


def import_selected_playlists(
    source_path: Path,
    selected_paths: list[str],
    target_music_dir: Path,
    old_prefix: str = "",
    new_prefix: str = "",
    selected_sample_rel_paths: list[str] | None = None,
    apply_sampler_settings: bool = True,
    apply_rb_prefs: bool = False,
    apply_rb_fx: bool = False,
    apply_rb_midi: bool = False,
    samples_config_only: bool = False,
    reload_only: bool = False,
    config_root_override: Path | None = None,
) -> dict:
    selected_set = set(selected_paths)
    tmp_dirs: list[Path] = []
    try:
        is_raw_xml = source_path.is_file() and source_path.suffix.lower() == ".xml"
        if is_raw_xml:
            xml_file = source_path
            work_dir = source_path.parent
            mf = {}
        else:
            work_dir, xml_file = _resolve_pack_root(source_path, tmp_dirs)
            mf_path = work_dir / "playlist_pack.json"
            mf = json.loads(mf_path.read_text(encoding="utf-8")) if mf_path.exists() else {}

        raw_pack_name = (mf.get("packName") or "").strip()
        if not raw_pack_name:
            if len(selected_paths) == 1:
                raw_pack_name = selected_paths[0].split("/")[-1]
            else:
                raw_pack_name = source_path.stem
        safe_imp_folder = re.sub(r"[^a-zA-Z0-9_-]+", "_", raw_pack_name).strip("_") or "Pacote_Importado"
        # Put each imported package in its own dedicated subfolder (avoid double-nesting if re-importing from same folder)
        if target_music_dir.name.lower() == safe_imp_folder.lower():
            pack_dest_dir = target_music_dir
        else:
            pack_dest_dir = target_music_dir / safe_imp_folder
        pack_dest_dir.mkdir(parents=True, exist_ok=True)
        extracted_audio = 0
        rewritten_paths = 0
        kept_track_ids = set()
        out_xml = ""
        configured = False
        mirrored_xml = ""

        if selected_set:
            tree = ET.parse(xml_file)
            root = tree.getroot()

            def prune_node(node: ET.Element, prefix: str = "") -> bool:
                ntype = node.attrib.get("Type", "")
                name = node.attrib.get("Name", "")
                if ntype == "1":
                    full_path = f"{prefix}/{name}" if prefix else name
                    if full_path in selected_set:
                        for tr in node.findall("TRACK"):
                            k = tr.attrib.get("Key")
                            if k:
                                kept_track_ids.add(k)
                        return True
                    return False
                next_prefix = f"{prefix}/{name}" if (prefix and name and name != "ROOT") else (name if name and name != "ROOT" else prefix)
                kept_children = []
                for child in list(node.findall("NODE")):
                    if prune_node(child, next_prefix):
                        kept_children.append(child)
                    else:
                        node.remove(child)
                node.attrib["Count"] = str(len(kept_children))
                return len(kept_children) > 0 or name == "ROOT"

            pl_root = root.find("PLAYLISTS")
            if pl_root is not None:
                for child in list(pl_root.findall("NODE")):
                    prune_node(child, "")

            audio_src_dir = work_dir / "audio"
            audio_map = mf.get("audioMap") or {}

            collection = root.find("COLLECTION")
            if collection is not None:
                kept_count = 0
                for tr in list(collection.findall("TRACK")):
                    tid = tr.attrib.get("TrackID")
                    if tid not in kept_track_ids:
                        collection.remove(tr)
                        continue
                    kept_count += 1
                    if tid in audio_map and (audio_src_dir / audio_map[tid]).exists():
                        src_audio = audio_src_dir / audio_map[tid]
                        dst_audio = pack_dest_dir / audio_map[tid]
                        if not reload_only:
                            _safe_copy2(src_audio, dst_audio)
                        tr.attrib["Location"] = local_path_to_xml_location(str(dst_audio.resolve()))
                        extracted_audio += 1
                        rewritten_paths += 1
                    elif old_prefix and new_prefix:
                        cur_local = xml_location_to_local_path(tr.attrib.get("Location", ""))
                        old_norm = old_prefix.replace("\\", "/").rstrip("/")
                        cur_norm = cur_local.replace("\\", "/")
                        if cur_norm.lower().startswith(old_norm.lower()):
                            rel = cur_norm[len(old_norm):].lstrip("/")
                            new_full = str(Path(new_prefix) / rel)
                            tr.attrib["Location"] = local_path_to_xml_location(new_full)
                            rewritten_paths += 1
                collection.attrib["Entries"] = str(kept_count)

            out_xml_p = pack_dest_dir / "rekordbox_import.xml"
            write_rekordbox_xml(root, out_xml_p)
            out_xml = str(out_xml_p)
            configured, mirrored_xml = configure_rekordbox_xml_path(out_xml_p)

        extracted_samples = False
        extracted_samples_count = 0
        samples_src = work_dir / "samples"
        if not samples_src.exists() and (work_dir / "Samples").exists():
            samples_src = work_dir / "Samples"
        if not (samples_config_only or reload_only) and samples_src.exists():
            dst_samples = pack_dest_dir / "Samples"
            if selected_sample_rel_paths is None:
                _safe_copytree(samples_src, dst_samples)
                extracted_samples_count = sum(1 for f in samples_src.rglob("*") if f.is_file())
            else:
                sel_rel_set = set(selected_sample_rel_paths)
                for fp in samples_src.rglob("*"):
                    if not fp.is_file():
                        continue
                    rel = fp.relative_to(samples_src).as_posix()
                    if rel in sel_rel_set:
                        dst_f = dst_samples / Path(*rel.split("/"))
                        _safe_copy2(fp, dst_f)
                        extracted_samples_count += 1
            extracted_samples = extracted_samples_count > 0

        applied_sampler_xmls: list[str] = []
        applied_pads_count = 0
        matched_sampler_ids = 0
        unmatched_sampler_ids = 0
        ss_exp_file = work_dir / "sampler_settings_export.xml"
        if apply_sampler_settings and ss_exp_file.exists() and (extracted_samples or samples_config_only or reload_only or bool(mf.get("samplesConfigOnly"))):
            try:
                gprops, slots = parse_sampler_settings_xml(ss_exp_file)
                sel_rel_set = set(selected_sample_rel_paths) if selected_sample_rel_paths is not None else None
                local_by_path, local_by_name, local_path_by_id = _lookup_local_master_db_content_ids() if config_root_override is None else ({}, {}, {})
                sampler_analysis_map = mf.get("samplerAnalysisMap") if isinstance(mf.get("samplerAnalysisMap"), dict) else {}
                pending_entries: list[tuple[dict, Path, str, str]] = []
                sync_jobs: list[dict] = []
                seen_sync: set[str] = set()

                for sl in slots:
                    rel_u = sl["url"]
                    if sel_rel_set is not None and rel_u not in sel_rel_set:
                        continue
                    cand_f = (pack_dest_dir / "Samples" / Path(*rel_u.split("/"))).resolve()
                    if not cand_f.exists() and (work_dir / "Samples" / Path(*rel_u.split("/"))).exists():
                        cand_f = (work_dir / "Samples" / Path(*rel_u.split("/"))).resolve()
                    if not cand_f.exists() and (work_dir / "samples" / Path(*rel_u.split("/"))).exists():
                        cand_f = (work_dir / "samples" / Path(*rel_u.split("/"))).resolve()

                    fname_low = unicodedata.normalize("NFC", Path(rel_u).name).lower()
                    clean_low = re.sub(r"^slot\d+_", "", fname_low)
                    cand_nfc = unicodedata.normalize("NFC", cand_f.as_posix()).lower() if cand_f.exists() else ""
                    local_id = (
                        (local_by_path.get(cand_nfc) if cand_nfc else None)
                        or local_by_name.get(fname_low)
                        or local_by_name.get(clean_low)
                        or ""
                    )
                    # For config-only updates: resolve existing file path from local master.db if not in pack_dest_dir
                    if not cand_f.exists() and local_id and local_path_by_id.get(local_id):
                        db_fp = Path(local_path_by_id[local_id])
                        if db_fp.exists():
                            cand_f = db_fp.resolve()

                    if cand_f.exists() or local_id:
                        att = dict(sl["slotAttribs"])
                        posix_u = cand_f.as_posix() if cand_f.exists() else (local_path_by_id.get(local_id) or rel_u)
                        att["url"] = posix_u
                        ameta = sampler_analysis_map.get(rel_u) or {}
                        if config_root_override is None and posix_u.lower() not in seen_sync:
                            if (not local_id and cand_f.exists()) or ameta:
                                seen_sync.add(posix_u.lower())
                                sync_jobs.append({
                                    "path": cand_f,
                                    "title": att.get("name") or cand_f.stem,
                                    "existingId": local_id,
                                    "analysisMeta": ameta,
                                })
                        pending_entries.append((att, cand_f, posix_u, local_id))

                # Register missing samples + apply BPM & adjusted Beatgrids (ANLZ) into local master.db
                synced_ids = _sync_samples_in_master_db(sync_jobs, work_dir) if sync_jobs else {}

                rewritten_tracks = []
                for att, _ef, posix_u, local_id in pending_entries:
                    final_id = synced_ids.get(posix_u.lower()) or local_id or ""
                    if final_id:
                        att["id"] = final_id
                        matched_sampler_ids += 1
                    else:
                        att["id"] = "0"
                        unmatched_sampler_ids += 1
                    rewritten_tracks.append(att)

                if rewritten_tracks:
                    applied_sampler_xmls = apply_sampler_settings_to_rekordbox(gprops, rewritten_tracks, config_root_override)
                    applied_pads_count = len(rewritten_tracks)
            except Exception as e_samp:
                print(f"[AVISO] Erro ao aplicar pads do Sampler: {e_samp}", flush=True)

        applied_rb_extras = apply_rb_config_extras_to_rekordbox(
            work_dir, apply_rb_prefs, apply_rb_fx, apply_rb_midi, config_root_override
        )
        if (work_dir / "rb_config").exists():
            try:
                _safe_copytree(work_dir / "rb_config", pack_dest_dir / "rb_config")
            except Exception:
                pass
        if ss_exp_file.exists():
            try:
                _safe_copy2(ss_exp_file, pack_dest_dir / "sampler_settings_export.xml")
            except Exception:
                pass
        if (work_dir / "sampler_anlz").exists():
            try:
                _safe_copytree(work_dir / "sampler_anlz", pack_dest_dir / "sampler_anlz")
            except Exception:
                pass
        try:
            imp_mf = {
                "packVersion": "0.0.8",
                "packName": raw_pack_name,
                "createdAt": now_str(),
                "sourceOs": mf.get("sourceOs") or ("windows" if IS_WIN else "macos"),
                "playlists": selected_paths,
                "trackCount": len(kept_track_ids),
                "includesAudio": extracted_audio > 0,
                "includesSamples": extracted_samples,
                "totalSamplesCount": extracted_samples_count,
                "hasSamplerPadEdits": (pack_dest_dir / "sampler_settings_export.xml").exists(),
                "packTags": mf.get("packTags") or [],
                "includesRbPrefs": (pack_dest_dir / "rb_config" / "prefs").exists(),
                "includesRbFx": (pack_dest_dir / "rb_config" / "fx").exists(),
                "includesRbMidi": (pack_dest_dir / "rb_config" / "midi").exists(),
            }
            (pack_dest_dir / "playlist_pack.json").write_text(json.dumps(imp_mf, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        imp_cue_count = 0
        if out_xml and Path(out_xml).exists():
            try:
                imp_cue_count = len(ET.parse(out_xml).getroot().findall(".//POSITION_MARK"))
            except Exception:
                pass

        return {
            "xmlOutputPath": out_xml,
            "mirroredXmlPath": mirrored_xml,
            "playlistCount": len(selected_paths),
            "trackCount": len(kept_track_ids),
            "cueCount": imp_cue_count,
            "extractedAudioCount": extracted_audio,
            "extractedSamples": extracted_samples,
            "extractedSamplesCount": extracted_samples_count,
            "samplesOutputDir": str(pack_dest_dir / "Samples") if extracted_samples else "",
            "rewrittenPaths": rewritten_paths,
            "autoConfiguredRekordbox": configured,
            "appliedSamplerPadsCount": applied_pads_count,
            "matchedSamplerIdsCount": matched_sampler_ids,
            "unmatchedSamplerIdsCount": unmatched_sampler_ids,
            "appliedSamplerXmlPaths": applied_sampler_xmls,
            "appliedRbExtras": applied_rb_extras,
            "importedPackTags": mf.get("packTags") or [],
            "packDestDir": str(pack_dest_dir.resolve()),
        }
    finally:
        for td in tmp_dirs:
            if td.exists():
                shutil.rmtree(td, ignore_errors=True)


# ---------------------------------------------------------------------------
# Local HTTP API Server
# ---------------------------------------------------------------------------

def _quick_inspect_zip_entry(zip_path: Path, origin: str) -> dict | None:
    """Fast in-memory inspection of a Rekordbox Vault .zip (handles nested Google Drive zips too)."""
    try:
        st = zip_path.stat()
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = [n for n in zf.namelist() if "__MACOSX" not in n and not Path(n).name.startswith("._")]
            mf_name = next((n for n in names if n.endswith("playlist_pack.json")), None)
            xml_name = next((n for n in names if n.endswith("rekordbox_playlists.xml")), None)
            if not xml_name:
                xml_name = next((n for n in names if n.lower().endswith(".xml") and "sampler_settings" not in n.lower()), None)

            # Check for nested .zip if outer zip has no xml
            if not xml_name and not mf_name:
                inner_z = next((n for n in names if n.lower().endswith(".zip")), None)
                if inner_z:
                    import io
                    with zipfile.ZipFile(io.BytesIO(zf.read(inner_z)), "r") as izf:
                        inames = [n for n in izf.namelist() if "__MACOSX" not in n]
                        imf = next((n for n in inames if n.endswith("playlist_pack.json")), None)
                        ixml = next((n for n in inames if n.endswith("rekordbox_playlists.xml")), None) or next((n for n in inames if n.lower().endswith(".xml") and "sampler_settings" not in n.lower()), None)
                        if not imf and not ixml:
                            return None
                        mf = json.loads(izf.read(imf).decode("utf-8", errors="ignore")) if imf else {}
                        sample_files = [n for n in inames if "/samples/" in ("/" + n) and not n.endswith("/")]
                        has_pads = any(n.endswith("sampler_settings_export.xml") for n in inames)
                        pls = mf.get("playlists") or []
                        return {
                            "id": str(zip_path.resolve()).lower(),
                            "name": mf.get("packName") or zip_path.stem,
                            "fileName": zip_path.name,
                            "origin": origin,
                            "kind": "zip",
                            "path": str(zip_path.resolve()),
                            "date": mf.get("createdAt") or datetime.datetime.fromtimestamp(st.st_mtime).strftime("%d/%m/%Y %H:%M"),
                            "mtime": st.st_mtime,
                            "sizeBytes": st.st_size,
                            "sizeLabel": format_bytes(st.st_size),
                            "sourceOs": mf.get("sourceOs") or "",
                            "playlistCount": len(pls),
                            "trackCount": int(mf.get("trackCount") or 0),
                            "samplesCount": int(mf.get("totalSamplesCount") or len(sample_files)),
                            "hasSamplerPadEdits": bool(mf.get("hasSamplerPadEdits")) or has_pads,
                            "packTags": mf.get("packTags") or [],
                            "includesRbPrefs": bool(mf.get("includesRbPrefs")),
                            "includesRbFx": bool(mf.get("includesRbFx")),
                            "includesRbMidi": bool(mf.get("includesRbMidi")),
                            "playlistNames": pls,
                        }
                return None

            mf = json.loads(zf.read(mf_name).decode("utf-8", errors="ignore")) if mf_name else {}
            sample_files = [n for n in names if "/samples/" in ("/" + n) and not n.endswith("/")]
            has_pads = any(n.endswith("sampler_settings_export.xml") for n in names)
            pls = mf.get("playlists") or []
            return {
                "id": str(zip_path.resolve()).lower(),
                "name": mf.get("packName") or zip_path.stem,
                "fileName": zip_path.name,
                "origin": origin,
                "kind": "zip",
                "path": str(zip_path.resolve()),
                "date": mf.get("createdAt") or datetime.datetime.fromtimestamp(st.st_mtime).strftime("%d/%m/%Y %H:%M"),
                "mtime": st.st_mtime,
                "sizeBytes": st.st_size,
                "sizeLabel": format_bytes(st.st_size),
                "sourceOs": mf.get("sourceOs") or "",
                "playlistCount": len(pls),
                "trackCount": int(mf.get("trackCount") or 0),
                "samplesCount": int(mf.get("totalSamplesCount") or len(sample_files)),
                "hasSamplerPadEdits": bool(mf.get("hasSamplerPadEdits")) or has_pads,
                            "packTags": mf.get("packTags") or [],
                            "includesRbPrefs": bool(mf.get("includesRbPrefs")),
                            "includesRbFx": bool(mf.get("includesRbFx")),
                            "includesRbMidi": bool(mf.get("includesRbMidi")),
                "playlistNames": pls,
            }
    except Exception:
        return None


def _quick_inspect_folder_entry(folder_path: Path, origin: str) -> dict | None:
    mf_file = folder_path / "playlist_pack.json"
    xml_file = folder_path / "rekordbox_playlists.xml"
    imp_xml = folder_path / "rekordbox_import.xml"
    if not mf_file.exists() and not xml_file.exists() and not imp_xml.exists():
        return None
    try:
        target_xml = xml_file if xml_file.exists() else (imp_xml if imp_xml.exists() else None)
        mf = json.loads(mf_file.read_text(encoding="utf-8")) if mf_file.exists() else {}
        pls = mf.get("playlists") or []
        track_count = int(mf.get("trackCount") or 0)
        if not pls and target_xml and target_xml.exists():
            parsed = parse_rekordbox_xml_playlists(target_xml)
            pls = [pl["path"] for pl in parsed["playlists"]]
            track_count = parsed["totalTracks"]
        samples_dir = folder_path / "samples"
        if not samples_dir.exists():
            samples_dir = folder_path / "Samples"
        samp_cnt = sum(1 for f in samples_dir.rglob("*") if f.is_file()) if samples_dir.exists() else 0
        total_sz = sum(f.stat().st_size for f in folder_path.rglob("*") if f.is_file())
        st = (target_xml or mf_file).stat()
        inferred_name = mf.get("packName") or ""
        if not inferred_name:
            if len(pls) == 1:
                inferred_name = pls[0].split("/")[-1]
            elif len(pls) > 1:
                inferred_name = f"{pls[0].split('/')[-1]} (+{len(pls) - 1})"
            else:
                inferred_name = folder_path.name
        return {
            "id": str(folder_path.resolve()).lower(),
            "name": inferred_name,
            "fileName": (target_xml.name if target_xml and not mf_file.exists() else folder_path.name),
            "origin": origin,
            "kind": "folder",
            "path": str((folder_path if mf_file.exists() or xml_file.exists() else target_xml).resolve()),
            "date": mf.get("createdAt") or datetime.datetime.fromtimestamp(st.st_mtime).strftime("%d/%m/%Y %H:%M"),
            "mtime": st.st_mtime,
            "sizeBytes": total_sz,
            "sizeLabel": format_bytes(total_sz),
            "sourceOs": mf.get("sourceOs") or ("windows" if IS_WIN else "macos"),
            "playlistCount": len(pls),
            "trackCount": track_count,
            "samplesCount": int(mf.get("totalSamplesCount") or samp_cnt),
            "hasSamplerPadEdits": (folder_path / "sampler_settings_export.xml").exists(),
                            "packTags": mf.get("packTags") or [],
                            "includesRbPrefs": bool(mf.get("includesRbPrefs")),
                            "includesRbFx": bool(mf.get("includesRbFx")),
                            "includesRbMidi": bool(mf.get("includesRbMidi")),
            "playlistNames": pls,
        }
    except Exception:
        return None


def scan_gallery_packages(created_dir_str: str, imported_dir_str: str) -> dict:
    items: list[dict] = []
    seen_ids: set[str] = set()

    for origin, dir_str in (("Criado", created_dir_str), ("Importado", imported_dir_str)):
        if not dir_str:
            continue
        d = Path(dir_str).expanduser()
        if not d.exists() or not d.is_dir():
            continue

        # Check if the directory itself is an imported pack (e.g. RekordboxVault_Importados/rekordbox_import.xml)
        self_entry = _quick_inspect_folder_entry(d, origin)
        if self_entry and self_entry["id"] not in seen_ids:
            seen_ids.add(self_entry["id"])
            items.append(self_entry)

        try:
            with os.scandir(d) as it:
                for entry in it:
                    if entry.name.startswith("."):
                        continue
                    ep = Path(entry.path)
                    info = None
                    if entry.is_file() and ep.suffix.lower() == ".zip":
                        info = _quick_inspect_zip_entry(ep, origin)
                    elif entry.is_dir():
                        info = _quick_inspect_folder_entry(ep, origin)
                        if info is None:
                            # Also support subfolder-per-package containing .zip (e.g. Pacotes Criados/<Nome>/*.zip)
                            for sub_z in sorted(ep.glob("*.zip"), key=lambda z: z.stat().st_mtime, reverse=True):
                                z_info = _quick_inspect_zip_entry(sub_z, origin)
                                if z_info and z_info["id"] not in seen_ids:
                                    seen_ids.add(z_info["id"])
                                    items.append(z_info)
                    if info and info["id"] not in seen_ids:
                        seen_ids.add(info["id"])
                        items.append(info)
        except OSError:
            pass

    items.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    return {
        "createdDir": created_dir_str,
        "importedDir": imported_dir_str,
        "items": items,
    }



def _update_zip_manifest_name(zip_path: Path, new_name: str) -> None:
    tmp_zip = zip_path.with_suffix(".tmp_rename.zip")
    with zipfile.ZipFile(zip_path, "r") as zin, zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_STORED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith("playlist_pack.json"):
                try:
                    mf = json.loads(data.decode("utf-8", errors="ignore"))
                    mf["packName"] = new_name
                    data = json.dumps(mf, ensure_ascii=False, indent=2).encode("utf-8")
                except Exception:
                    pass
            zout.writestr(item, data)
    shutil.move(str(tmp_zip), str(zip_path))


def gallery_rename_package(target_path_str: str, new_name: str) -> dict:
    clean_name = (new_name or "").strip()
    if not clean_name:
        raise ValueError("Informe o novo nome do pacote.")
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", clean_name).strip("_") or "Pacote"
    p = Path(target_path_str).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError("Pacote não encontrado no disco.")

    old_id = str(p).lower()
    if p.is_file() and p.suffix.lower() == ".zip":
        _update_zip_manifest_name(p, clean_name)
        new_zip = p.parent / f"{safe}.zip"
        if new_zip.resolve() != p:
            if new_zip.exists():
                stamp = datetime.datetime.now().strftime("%H%M%S")
                new_zip = p.parent / f"{safe}_{stamp}.zip"
            p.rename(new_zip)
            p = new_zip.resolve()
        new_path = p
    elif p.is_dir():
        mf_p = p / "playlist_pack.json"
        if mf_p.exists():
            try:
                mf = json.loads(mf_p.read_text(encoding="utf-8"))
                mf["packName"] = clean_name
                mf_p.write_text(json.dumps(mf, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        new_dir = p.parent / safe
        if new_dir.resolve() != p:
            if new_dir.exists():
                stamp = datetime.datetime.now().strftime("%H%M%S")
                new_dir = p.parent / f"{safe}_{stamp}"
            p.rename(new_dir)
            p = new_dir.resolve()
        new_path = p
    else:
        # Legacy single XML file inside a folder
        new_xml = p.parent / f"{safe}.xml"
        if new_xml.resolve() != p and not new_xml.exists():
            p.rename(new_xml)
            p = new_xml.resolve()
        new_path = p

    # Migrate galleryMeta (tags & custom columns) to new ID
    s = load_vault_settings()
    meta = s.get("galleryMeta") or {}
    new_id = str(new_path).lower()
    if old_id in meta and old_id != new_id:
        meta[new_id] = meta.pop(old_id)
        save_vault_settings({"galleryMeta": meta})

    return {"newPath": str(new_path), "newName": clean_name}


def gallery_duplicate_package(target_path_str: str, new_name: str) -> dict:
    clean_name = (new_name or "").strip()
    if not clean_name:
        raise ValueError("Informe o nome para a cópia do pacote.")
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", clean_name).strip("_") or "Pacote_Copia"
    p = Path(target_path_str).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError("Pacote não encontrado no disco.")

    old_id = str(p).lower()
    stamp = datetime.datetime.now().strftime("%H%M%S")
    if p.is_file() and p.suffix.lower() == ".zip":
        dst = p.parent / f"{safe}.zip"
        if dst.exists():
            dst = p.parent / f"{safe}_{stamp}.zip"
        shutil.copy2(p, dst)
        _update_zip_manifest_name(dst, clean_name)
        new_path = dst.resolve()
    elif p.is_dir():
        dst = p.parent / safe
        if dst.exists():
            dst = p.parent / f"{safe}_{stamp}"
        shutil.copytree(p, dst)
        mf_p = dst / "playlist_pack.json"
        if mf_p.exists():
            try:
                mf = json.loads(mf_p.read_text(encoding="utf-8"))
                mf["packName"] = clean_name
                mf_p.write_text(json.dumps(mf, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        new_path = dst.resolve()
    else:
        dst = p.parent / f"{safe}{p.suffix}"
        if dst.exists():
            dst = p.parent / f"{safe}_{stamp}{p.suffix}"
        shutil.copy2(p, dst)
        new_path = dst.resolve()

    s = load_vault_settings()
    meta = s.get("galleryMeta") or {}
    if old_id in meta:
        meta[str(new_path).lower()] = json.loads(json.dumps(meta[old_id]))
        save_vault_settings({"galleryMeta": meta})

    return {"newPath": str(new_path), "newName": clean_name}


def gallery_delete_package(target_path_str: str) -> bool:
    p = Path(target_path_str).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError("Pacote não encontrado no disco.")
    old_id = str(p).lower()
    if p.is_dir():
        shutil.rmtree(p)
    else:
        parent = p.parent
        p.unlink()
        # Clean up empty package subfolder inside Pacotes Criados if empty
        try:
            if parent.exists() and parent != VAULT_CREATED_DIR and parent != VAULT_IMPORTED_DIR and not any(parent.iterdir()):
                parent.rmdir()
        except Exception:
            pass

    s = load_vault_settings()
    meta = s.get("galleryMeta") or {}
    if old_id in meta:
        meta.pop(old_id, None)
        save_vault_settings({"galleryMeta": meta})
    return True



# ---------------------------------------------------------------------------
# GitHub Releases Auto-Updater (Item 40)
# ---------------------------------------------------------------------------
def _parse_semver(tag: str) -> tuple[int, ...]:
    cleaned = re.sub(r"^[^0-9]*", "", str(tag or "").strip())
    nums = [int(x) for x in re.findall(r"\d+", cleaned)[:4]]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def list_downloaded_updates() -> list[dict]:
    VAULT_UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []
    for fp in sorted(VAULT_UPDATES_DIR.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
        if not fp.is_file() or fp.name.startswith("."):
            continue
        st = fp.stat()
        items.append({
            "name": fp.name,
            "path": str(fp.resolve()),
            "sizeBytes": st.st_size,
            "sizeLabel": format_bytes(st.st_size),
            "date": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%d/%m/%Y %H:%M"),
        })
    return items


def _pick_best_release_asset(assets: list[dict]) -> dict | None:
    if not assets:
        return None
    mach = platform.machine().lower()
    is_arm = ("arm" in mach) or ("aarch64" in mach)

    def _score(a: dict) -> int:
        n = str(a.get("name") or "").lower()
        if IS_WIN:
            if n.endswith(".exe") and "setup" in n:
                return 100
            if n.endswith(".exe"):
                return 90
            if n.endswith(".zip") and "win" in n:
                return 60
        elif IS_MAC:
            if n.endswith(".dmg"):
                if is_arm and ("applesilicon" in n or "arm64" in n or "arm" in n):
                    return 100
                if (not is_arm) and ("intel" in n or "x86" in n or "x64" in n):
                    return 100
                return 75
            if n.endswith(".zip") and "mac" in n:
                return 55
        if n.endswith(".zip"):
            return 30
        return 0

    ranked = sorted(assets, key=_score, reverse=True)
    return ranked[0] if _score(ranked[0]) > 0 else None


def check_github_updates(repo: str = "") -> dict:
    import urllib.request
    import urllib.error

    target_repo = (repo or GITHUB_REPO).strip().strip("/")
    api_url = f"https://api.github.com/repos/{target_repo}/releases/latest"
    req = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"RekordboxVault/{APP_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as he:
        if he.code == 404:
            return {
                "repo": target_repo,
                "currentVersion": APP_VERSION,
                "latestVersion": APP_VERSION,
                "hasUpdate": False,
                "noReleasesYet": True,
                "releaseUrl": f"https://github.com/{target_repo}/releases",
                "downloadedFiles": list_downloaded_updates(),
            }
        raise RuntimeError(f"GitHub retornou erro HTTP {he.code}.") from he
    except Exception as exc:
        raise RuntimeError(f"Não foi possível consultar atualizações no GitHub: {exc}") from exc

    latest_tag = str(data.get("tag_name") or data.get("name") or "").strip()
    has_update = bool(latest_tag) and (_parse_semver(latest_tag) > _parse_semver(APP_VERSION))
    best_asset = _pick_best_release_asset(data.get("assets") or [])
    return {
        "repo": target_repo,
        "currentVersion": APP_VERSION,
        "latestVersion": latest_tag or APP_VERSION,
        "hasUpdate": has_update,
        "noReleasesYet": False,
        "releaseName": str(data.get("name") or latest_tag or ""),
        "releaseNotes": str(data.get("body") or "").strip()[:1200],
        "releaseUrl": str(data.get("html_url") or f"https://github.com/{target_repo}/releases"),
        "assetName": str(best_asset.get("name") or "") if best_asset else "",
        "assetUrl": str(best_asset.get("browser_download_url") or "") if best_asset else "",
        "assetSize": format_bytes(int(best_asset.get("size") or 0)) if best_asset else "",
        "downloadedFiles": list_downloaded_updates(),
    }


def download_github_update_asset(asset_url: str, asset_name: str, job_id: str | None = None) -> dict:
    import urllib.request

    if not asset_url.startswith("https://"):
        raise ValueError("URL de download inválida.")
    VAULT_UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(asset_name or "update.bin").name)
    dst_path = VAULT_UPDATES_DIR / safe_name
    tmp_path = VAULT_UPDATES_DIR / (safe_name + ".part")

    req = urllib.request.Request(asset_url, headers={"User-Agent": f"RekordboxVault/{APP_VERSION}"})
    set_job_progress(job_id, 5, f"Baixando {safe_name}...")
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(tmp_path, "wb") as out_f:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out_f.write(chunk)
                done += len(chunk)
                if total > 0:
                    pct = 5 + int((done / total) * 93)
                    set_job_progress(job_id, pct, f"Baixando {safe_name} ({format_bytes(done)} / {format_bytes(total)})...")
    shutil.move(str(tmp_path), str(dst_path))
    set_job_progress(job_id, 100, "Download concluído!")
    return {
        "fileName": dst_path.name,
        "filePath": str(dst_path.resolve()),
        "sizeLabel": format_bytes(dst_path.stat().st_size),
    }


def open_downloaded_update(file_path: str) -> None:
    p = Path(file_path).resolve()
    if not p.exists() or not str(p).startswith(str(VAULT_UPDATES_DIR.resolve())):
        raise FileNotFoundError("Arquivo de atualização não encontrado.")
    if IS_WIN:
        os.startfile(str(p))  # type: ignore[attr-defined]
    elif IS_MAC:
        subprocess.Popen(["open", str(p)])
    else:
        webbrowser.open(p.as_uri())


def delete_downloaded_update(file_path: str) -> None:
    p = Path(file_path).resolve()
    if not str(p).startswith(str(VAULT_UPDATES_DIR.resolve())):
        raise ValueError("Caminho fora da pasta de atualizações.")
    if p.exists() and p.is_file():
        p.unlink()


class VaultHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            html = UI_FILE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return
        if path == "/api/status":
            return self._send_json({
                "ok": True,
                "version": APP_VERSION,
                "platform": f"macOS {platform.mac_ver()[0]}" if IS_MAC else f"Windows {platform.release()}",
                "isRunning": is_rekordbox_running(),
                "directDbReady": check_pyrekordbox_available(),
                "defaultExportDest": str(VAULT_CREATED_DIR),
                "defaultImportDest": str(VAULT_IMPORTED_DIR),
                "defaultXmlPath": str(get_default_rekordbox_xml_path()),
            })
        if path == "/api/progress":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            jid = (qs.get("jobId") or [""])[0]
            return self._send_json({"ok": True, "progress": get_job_progress(jid)})
        if path == "/api/settings":
            return self._send_json({"ok": True, "settings": load_vault_settings()})
        self._send_json({"ok": False, "error": "Not found"}, 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            body = self._read_json()

            if path == "/api/settings":
                saved = save_vault_settings(body)
                return self._send_json({"ok": True, "settings": saved})

            if path == "/api/fs/list":
                data = list_filesystem(
                    target_path=body.get("path", ""),
                    mode=body.get("mode", "folder"),
                    ext=body.get("ext", ""),
                    include_shortcuts=bool(body.get("includeShortcuts", False)),
                )
                return self._send_json({"ok": True, "data": data})

            if path == "/api/gallery/rename":
                res = gallery_rename_package(body.get("path", ""), body.get("newName", ""))
                return self._send_json({"ok": True, "result": res})

            if path == "/api/gallery/duplicate":
                res = gallery_duplicate_package(body.get("path", ""), body.get("newName", ""))
                return self._send_json({"ok": True, "result": res})

            if path == "/api/gallery/delete":
                gallery_delete_package(body.get("path", ""))
                return self._send_json({"ok": True})

            if path == "/api/gallery/scan":
                s = load_vault_settings()
                cdir = body.get("createdDir") if "createdDir" in body else s.get("galleryCreatedDir", "")
                idir = body.get("importedDir") if "importedDir" in body else s.get("galleryImportedDir", "")
                return self._send_json({"ok": True, "data": scan_gallery_packages(cdir, idir)})

            if path == "/api/fs/reveal":
                reveal_in_file_manager(body.get("path", ""))
                return self._send_json({"ok": True})

            if path == "/api/pick-folder":
                return self._send_json({"ok": True, "path": native_pick_folder(body.get("title", "Selecione uma pasta"), body.get("initialDir", ""))})

            if path == "/api/pick-file":
                return self._send_json({"ok": True, "path": native_pick_file(body.get("title", "Selecione um arquivo"), body.get("ext", "zip"), body.get("initialDir", ""))})

            if path == "/api/rb/open":
                exe = launch_rekordbox()
                return self._send_json({"ok": True, "exe": exe})

            if path == "/api/rb/restart":
                exe = restart_rekordbox()
                return self._send_json({"ok": True, "exe": exe})

            if path == "/api/rb/close":
                closed = stop_rekordbox(
                    include_main=True,
                    graceful=bool(body.get("graceful", False)),
                    allow_force=bool(body.get("allowForce", True)),
                )
                return self._send_json({"ok": True, "closed": closed})

            if path == "/api/rb/extras-detect":
                ex = detect_rekordbox_config_extras()
                return self._send_json({
                    "ok": True,
                    "data": {
                        "configDir": ex["configDir"],
                        "prefsCount": ex["prefsCount"],
                        "fxCount": ex["fxCount"],
                        "midiCount": ex["midiCount"],
                    },
                })

            if path == "/api/sampler/detect":
                info = detect_rekordbox_sampler_files()
                return self._send_json({"ok": True, "data": info})

            if path == "/api/sampler/scan-folder":
                fp = Path(body.get("folderPath", ""))
                items = scan_custom_samples_folder(fp)
                return self._send_json({"ok": True, "files": items})

            if path == "/api/db/setup":
                logs = install_pyrekordbox_and_key()
                return self._send_json({"ok": True, "logs": logs, "directDbReady": check_pyrekordbox_available()})

            if path == "/api/db/export-xml":
                out_dir = Path(body.get("destDir") or CACHE_DIR)
                out_name = body.get("fileName") or "rekordbox_auto.xml"
                jid = body.get("jobId") or None
                set_job_progress(jid, 2, "Conectando ao banco do Rekordbox...")
                data = generate_xml_from_master_db(out_dir / out_name, job_id=jid)
                return self._send_json({"ok": True, "data": data})

            if path == "/api/xml/parse":
                raw_xml_s = (body.get("xmlPath") or "").strip()
                xml_p = Path(raw_xml_s) if raw_xml_s else None
                if not xml_p.exists():
                    return self._send_json({"ok": False, "error": "Arquivo XML não encontrado."}, 400)
                return self._send_json({"ok": True, "data": parse_rekordbox_xml_playlists(xml_p)})

            if path == "/api/xml/export":
                xml_p = Path(body.get("xmlPath", ""))
                selected = body.get("selectedPaths") or []
                selected_samples = body.get("selectedSampleItems", None)
                pack_name_in = (body.get("packName") or "").strip()
                if not pack_name_in:
                    return self._send_json({"ok": False, "error": "Digite um nome para o pacote antes de exportar."}, 400)
                inc_prefs = bool(body.get("includeRbPrefs", False))
                inc_fx = bool(body.get("includeRbFx", False))
                inc_midi = bool(body.get("includeRbMidi", False))
                if not selected and not selected_samples and not (inc_prefs or inc_fx or inc_midi):
                    return self._send_json({"ok": False, "error": "Selecione ao menos uma playlist, sample ou configuração do Rekordbox."}, 400)
                res = export_selected_playlists(
                    xml_path=xml_p,
                    selected_paths=selected,
                    dest_dir=Path(body.get("destDir") or VAULT_CREATED_DIR),
                    pack_name=pack_name_in,
                    include_audio=bool(body.get("includeAudio", True)),
                    include_rekordbox_sampler=bool(body.get("includeRekordboxSampler", False)),
                    samples_dir=body.get("samplesDir", ""),
                    selected_sample_items=selected_samples,
                    sampler_global_props=body.get("samplerGlobalProps") or {},
                    pack_tags=body.get("packTags") or [],
                    include_rb_prefs=inc_prefs,
                    include_rb_fx=inc_fx,
                    include_rb_midi=inc_midi,
                    make_zip=bool(body.get("makeZip", True)),
                    samples_config_only=bool(body.get("samplesConfigOnly", False)),
                    zip_compression=str(body.get("zipCompression") or "stored"),
                    job_id=body.get("jobId") or None,
                )
                return self._send_json({"ok": True, "result": res})

            if path == "/api/xml/inspect-import":
                src = Path(body.get("sourcePath", ""))
                if not src.exists():
                    return self._send_json({"ok": False, "error": "Arquivo não encontrado."}, 400)
                return self._send_json({"ok": True, "data": inspect_playlist_pack(src)})

            if path == "/api/xml/import":
                src = Path(body.get("sourcePath", ""))
                selected = body.get("selectedPaths") or []
                selected_samples = body.get("selectedSampleRelPaths", None)
                ap_prefs = bool(body.get("applyRbPrefs", False))
                ap_fx = bool(body.get("applyRbFx", False))
                ap_midi = bool(body.get("applyRbMidi", False))
                if not selected and not selected_samples and not (ap_prefs or ap_fx or ap_midi):
                    return self._send_json({"ok": False, "error": "Selecione ao menos uma playlist, sample ou configuração para importar."}, 400)
                res = import_selected_playlists(
                    source_path=src,
                    selected_paths=selected,
                    target_music_dir=Path(body.get("targetMusicDir") or VAULT_IMPORTED_DIR),
                    old_prefix=body.get("oldPrefix", ""),
                    new_prefix=body.get("newPrefix", ""),
                    selected_sample_rel_paths=selected_samples,
                    apply_sampler_settings=bool(body.get("applySamplerSettings", True)),
                    apply_rb_prefs=ap_prefs,
                    apply_rb_fx=ap_fx,
                    apply_rb_midi=ap_midi,
                    samples_config_only=bool(body.get("samplesConfigOnly", False)),
                    reload_only=bool(body.get("reloadOnly", False)),
                )
                return self._send_json({"ok": True, "result": res})

            if path == "/api/updates/check":
                return self._send_json({"ok": True, "data": check_github_updates(body.get("repo", ""))})

            if path == "/api/updates/list":
                return self._send_json({"ok": True, "files": list_downloaded_updates(), "dir": str(VAULT_UPDATES_DIR)})

            if path == "/api/updates/download":
                res = download_github_update_asset(body.get("assetUrl", ""), body.get("assetName", ""), body.get("jobId") or None)
                return self._send_json({"ok": True, "result": res})

            if path == "/api/updates/open":
                open_downloaded_update(body.get("path", ""))
                return self._send_json({"ok": True})

            if path == "/api/updates/delete":
                delete_downloaded_update(body.get("path", ""))
                return self._send_json({"ok": True})

            if path == "/api/app/quit":
                threading.Timer(0.35, lambda: os._exit(0)).start()
                return self._send_json({"ok": True})

            if path == "/api/heartbeat":
                _touch_heartbeat()
                return self._send_json({"ok": True})

            if path == "/api/client-bye":
                _mark_client_bye()
                return self._send_json({"ok": True})

            self._send_json({"ok": False, "error": "Endpoint desconhecido"}, 404)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, 500)


_HEARTBEAT_LOCK = threading.Lock()
_LAST_HEARTBEAT = 0.0
_HEARTBEAT_SEEN = False
_CLIENT_BYE_AT = 0.0


def _touch_heartbeat() -> None:
    global _LAST_HEARTBEAT, _HEARTBEAT_SEEN, _CLIENT_BYE_AT
    with _HEARTBEAT_LOCK:
        _LAST_HEARTBEAT = time.monotonic()
        _HEARTBEAT_SEEN = True
        _CLIENT_BYE_AT = 0.0


def _mark_client_bye() -> None:
    global _CLIENT_BYE_AT
    with _HEARTBEAT_LOCK:
        _CLIENT_BYE_AT = time.monotonic()


def _write_ready_file(ready_file: str | None, url: str) -> None:
    if not ready_file:
        return
    try:
        Path(ready_file).write_text(f"READY {url}\n", encoding="utf-8")
    except Exception:
        pass


def _start_browser_watchdog(server: http.server.ThreadingHTTPServer) -> None:
    if os.environ.get("VAULT_NO_WATCHDOG") == "1":
        return

    def _watch():
        while True:
            time.sleep(1.5)
            with _HEARTBEAT_LOCK:
                seen = _HEARTBEAT_SEEN
                last = _LAST_HEARTBEAT
                bye = _CLIENT_BYE_AT
            if not seen:
                continue
            now = time.monotonic()
            if (bye > 0 and (now - bye) >= 4.0 and last < bye) or ((now - last) >= 14.0):
                try:
                    server.shutdown()
                except Exception:
                    pass
                os._exit(0)

    threading.Thread(target=_watch, daemon=True).start()


def _ensure_safe_stdio() -> None:
    """Ensure sys.stdout and sys.stderr are writable even under Windows pythonw.exe."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def main():
    import argparse
    import inspect
    import traceback

    _ensure_safe_stdio()
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("RekordboxVault.Desktop.App.v016")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="Rekordbox Vault Local Server")
    parser.add_argument("--desktop", action="store_true", help="Abre em janela nativa (pywebview)")
    parser.add_argument("--browser", action="store_true", help="Forca abertura no navegador")
    parser.add_argument("--ready-file", default="", help="Arquivo de sinalizacao para a splash screen")
    args, _ = parser.parse_known_args()

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), VaultHandler)
        url = f"http://127.0.0.1:{port}/"
        print(f"Rekordbox Vault {APP_VERSION} rodando em {url}", flush=True)

        use_desktop = not args.browser
        srv_thread = None
        if use_desktop:
            try:
                import webview  # type: ignore

                srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
                srv_thread.start()

                window_kwargs = {
                    "width": 1240,
                    "height": 840,
                    "min_size": (960, 660),
                    "background_color": "#0b0d12",
                }
                sig_cw = inspect.signature(webview.create_window)
                safe_cw = {k: v for k, v in window_kwargs.items() if k in sig_cw.parameters}
                win_obj = webview.create_window(f"Rekordbox Vault {APP_VERSION}", url, **safe_cw)

                def _apply_win_icon_bg():
                    if not IS_WIN:
                        return
                    icon_ico = Path(__file__).resolve().parent / "icon.ico"
                    if not icon_ico.exists():
                        return

                    target_hwnd = 0
                    # 1) Set native .NET WinForms Form.Icon directly via pywebview BrowserView.instances
                    for delay in (0.15, 0.35, 0.7, 1.2):
                        time.sleep(delay)
                        try:
                            form = None
                            try:
                                from webview.platforms.winforms import BrowserView  # type: ignore
                                form = BrowserView.instances.get(getattr(win_obj, "uid", ""))
                            except Exception:
                                form = None
                            if form is None:
                                form = getattr(win_obj, "native", None) or getattr(win_obj, "gui", None)
                            if form is not None:
                                import System.Drawing  # type: ignore
                                from System import Action  # type: ignore
                                ico_obj = System.Drawing.Icon(str(icon_ico))
                                def _set_form_ico():
                                    form.Icon = ico_obj
                                    form.ShowIcon = True
                                if getattr(form, "InvokeRequired", False):
                                    form.Invoke(Action(_set_form_ico))
                                else:
                                    _set_form_ico()
                                try:
                                    target_hwnd = int(form.Handle.ToInt64())
                                except Exception:
                                    pass
                                print(f"[OK] WinForms BrowserView Form.Icon aplicado (hwnd={target_hwnd}).", flush=True)
                                break
                        except Exception as e_wf:
                            if delay >= 1.2:
                                print(f"[INFO] WinForms Form.Icon fallback: {e_wf}", flush=True)

                    # 2) Also apply 64-bit-safe Win32 WM_SETICON + SetClassLongPtrW on all process HWNDs
                    try:
                        import ctypes
                        from ctypes import wintypes

                        user32 = ctypes.windll.user32
                        IMAGE_ICON = 1
                        LR_LOADFROMFILE = 0x0010
                        LR_DEFAULTSIZE = 0x0040
                        WM_SETICON = 0x0080
                        ICON_SMALL = 0
                        ICON_BIG = 1
                        GCLP_HICON = -14
                        GCLP_HICONSM = -34

                        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
                        user32.LoadImageW.restype = ctypes.c_void_p
                        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, ctypes.c_void_p]
                        user32.SendMessageW.restype = ctypes.c_ssize_t
                        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
                        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
                        user32.IsWindowVisible.argtypes = [wintypes.HWND]
                        user32.IsWindowVisible.restype = wintypes.BOOL

                        set_cls = getattr(user32, "SetClassLongPtrW", getattr(user32, "SetClassLongW", None))
                        if set_cls is not None:
                            set_cls.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
                            set_cls.restype = ctypes.c_void_p

                        hicon_big = user32.LoadImageW(None, str(icon_ico), IMAGE_ICON, 48, 48, LR_LOADFROMFILE)
                        hicon_small = user32.LoadImageW(None, str(icon_ico), IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
                        if not hicon_big:
                            hicon_big = user32.LoadImageW(None, str(icon_ico), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
                        if not hicon_small:
                            hicon_small = hicon_big

                        my_pid = os.getpid()
                        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
                        user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
                        user32.EnumWindows.restype = wintypes.BOOL

                        def _apply_once() -> bool:
                            found = [False]
                            def _cb(hwnd, _lp):
                                pid = wintypes.DWORD()
                                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                                if pid.value == my_pid and user32.IsWindowVisible(hwnd):
                                    if hicon_small:
                                        user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_small)
                                    if hicon_big:
                                        user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_big)
                                    if set_cls is not None:
                                        try:
                                            if hicon_big:
                                                set_cls(hwnd, GCLP_HICON, hicon_big)
                                            if hicon_small:
                                                set_cls(hwnd, GCLP_HICONSM, hicon_small)
                                        except Exception:
                                            pass
                                    found[0] = True
                                return True
                            user32.EnumWindows(WNDENUMPROC(_cb), 0)
                            return found[0]

                        if target_hwnd:
                            if hicon_small:
                                user32.SendMessageW(target_hwnd, WM_SETICON, ICON_SMALL, hicon_small)
                            if hicon_big:
                                user32.SendMessageW(target_hwnd, WM_SETICON, ICON_BIG, hicon_big)
                            if set_cls is not None:
                                try:
                                    if hicon_big:
                                        set_cls(target_hwnd, GCLP_HICON, hicon_big)
                                    if hicon_small:
                                        set_cls(target_hwnd, GCLP_HICONSM, hicon_small)
                                except Exception:
                                    pass
                        for delay in (0.15, 0.4, 0.9, 1.8):
                            time.sleep(delay)
                            if _apply_once():
                                print("[OK] Win32 WM_SETICON aplicado com sucesso.", flush=True)
                                time.sleep(0.5)
                                _apply_once()
                                break
                    except Exception as e_w32:
                        print(f"[INFO] Win32 icon fallback erro: {e_w32}", flush=True)

                def _on_ready():
                    try:
                        import pyi_splash  # type: ignore
                        pyi_splash.close()
                    except Exception:
                        pass
                    _write_ready_file(args.ready_file, url)
                    if IS_WIN:
                        threading.Thread(target=_apply_win_icon_bg, daemon=True).start()

                start_kwargs: dict = {"func": _on_ready}
                # Only pass icon= on Linux (GTK/Qt); Windows WinForms & macOS handle icons via bundle/exe
                if platform.system() == "Linux":
                    icon_png = Path(__file__).resolve().parent / "icon.png"
                    sig_st = inspect.signature(webview.start)
                    if "icon" in sig_st.parameters and icon_png.exists():
                        start_kwargs["icon"] = str(icon_png)

                t_start = time.monotonic()
                webview.start(**start_kwargs)
                elapsed = time.monotonic() - t_start
                if elapsed >= 1.0:
                    try:
                        server.shutdown()
                        server.server_close()
                    except Exception:
                        pass
                    os._exit(0)
                else:
                    print(f"[INFO] webview.start() retornou muito rapido ({elapsed:.2f}s); abrindo no navegador.", flush=True)
            except Exception as exc:
                print(f"[INFO] Janela nativa indisponivel ({exc}); usando navegador com desligamento automatico.", flush=True)
                traceback.print_exc()

        _start_browser_watchdog(server)
        _write_ready_file(args.ready_file, url)
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
        if srv_thread is not None and srv_thread.is_alive():
            try:
                while srv_thread.is_alive():
                    srv_thread.join(timeout=0.5)
            except KeyboardInterrupt:
                pass
            finally:
                try:
                    server.shutdown()
                    server.server_close()
                except Exception:
                    pass
        else:
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
    except Exception as exc:
        traceback.print_exc()
        if args.ready_file:
            try:
                Path(args.ready_file).write_text(f"ERROR {exc}\n", encoding="utf-8")
            except Exception:
                pass
        sys.exit(1)


if __name__ == "__main__":
    main()
