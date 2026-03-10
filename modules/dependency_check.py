import importlib
import re
import sys
import subprocess
from pathlib import Path
from typing import Dict, List


_STD_OR_LOCAL_PREFIX = {
    "core",
    "modules",
    "plugins",
    "config",
    "__future__",
}

_PIP_NAME_MAP = {
    "cv2": "opencv-python",
    "PIL": "pillow",
    "yaml": "pyyaml",
    "duckduckgo_search": "duckduckgo-search",
    "screen_brightness_control": "screen-brightness-control",
}


def _extract_imports(py_text: str) -> List[str]:
    modules: List[str] = []
    for line in py_text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m1 = re.match(r"import\s+([A-Za-z0-9_\.]+)", s)
        if m1:
            modules.append(m1.group(1).split(".")[0])
            continue
        m2 = re.match(r"from\s+([A-Za-z0-9_\.]+)\s+import\s+", s)
        if m2:
            modules.append(m2.group(1).split(".")[0])
            continue
    return modules


def scan_missing_dependencies(plugins_dir: str = "./plugins") -> List[Dict[str, str]]:
    base = Path(plugins_dir)
    if not base.exists():
        return []

    missing: Dict[str, Dict[str, str]] = {}
    for py in base.glob("*/plugin.py"):
        plugin_name = py.parent.name
        text = py.read_text(encoding="utf-8", errors="replace")
        for mod in _extract_imports(text):
            if mod in _STD_OR_LOCAL_PREFIX:
                continue
            if mod.startswith("_"):
                continue
            try:
                importlib.import_module(mod)
            except Exception:
                pip_name = _PIP_NAME_MAP.get(mod, mod)
                key = f"{mod}:{pip_name}"
                if key not in missing:
                    missing[key] = {
                        "module": mod,
                        "package": pip_name,
                        "plugins": plugin_name,
                    }
                else:
                    prev = missing[key]["plugins"]
                    if plugin_name not in prev.split(","):
                        missing[key]["plugins"] = f"{prev},{plugin_name}"

    rows = list(missing.values())
    rows.sort(key=lambda x: x["module"])
    return rows


def build_install_command(rows: List[Dict[str, str]]) -> str:
    pkgs = sorted({r["package"] for r in rows if r.get("package")})
    if not pkgs:
        return ""
    return f'"{sys.executable}" -m pip install ' + " ".join(pkgs)


def install_missing(rows: List[Dict[str, str]], timeout: int = 600) -> Dict[str, str]:
    pkgs = sorted({r["package"] for r in rows if r.get("package")})
    if not pkgs:
        return {"ok": "1", "message": "没有缺失依赖"}

    cmd = [sys.executable, "-m", "pip", "install"] + pkgs
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=int(timeout),
        )
        out = (proc.stdout or "")[-6000:]
        err = (proc.stderr or "")[-6000:]
        if proc.returncode == 0:
            return {"ok": "1", "message": f"安装成功\n{out}"}
        return {"ok": "0", "message": f"安装失败(code={proc.returncode})\n{err or out}"}
    except Exception as e:
        return {"ok": "0", "message": f"安装异常: {e}"}
