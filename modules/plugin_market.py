"""Fetch and install optional plugins from the Ren-bot plugin repository.

The catalog is a static index.json. Each plugin is downloaded as its own
folder. Installed copies live outside the main plugins directory, so an
install does not change the app repository.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import quote

CATALOG_URL = "https://raw.githubusercontent.com/OKALLRIGHT1/Ren-bot-plugin/main/index.json"
REPOSITORY = "OKALLRIGHT1/Ren-bot-plugin"
REPOSITORY_REF = "main"
TREE_URL = (
    f"https://api.github.com/repos/{REPOSITORY}/git/trees/{REPOSITORY_REF}?recursive=1"
)
RAW_BASE = f"https://raw.githubusercontent.com/{REPOSITORY}/{REPOSITORY_REF}"
ZIP_URL = f"https://codeload.github.com/{REPOSITORY}/zip/refs/heads/{REPOSITORY_REF}"
MARKET_DIR_NAME = "installed_plugins"
CATALOG_CACHE_NAME = "plugin_market_catalog.json"
MAX_FILE_BYTES = 40 * 1024 * 1024
MAX_PLUGIN_BYTES = 250 * 1024 * 1024
_USER_AGENT = "Ren-bot-plugin-market"


class PluginMarketError(Exception):
    """A catalog or install problem that can be shown to the user."""


class PluginMarketFetchError(PluginMarketError):
    """The repository could not be read. Callers may try another download path."""


def default_installed_root(data_dir: str = "data") -> str:
    return os.path.join(data_dir, MARKET_DIR_NAME)


def default_catalog_cache_path(data_dir: str = "data") -> str:
    return os.path.join(data_dir, CATALOG_CACHE_NAME)


def is_safe_plugin_id(plugin_id: str) -> bool:
    text = str(plugin_id or "")
    if not text or len(text) > 64:
        return False
    if text.startswith(".") or text.startswith("_"):
        return False
    return all(char.isalnum() or char in {"_", "-"} for char in text)


def parse_catalog(payload: object) -> list[dict]:
    if not isinstance(payload, dict):
        raise PluginMarketError("插件清单格式不对")
    if payload.get("schema") != 1:
        raise PluginMarketError("不认识这份插件清单")
    items = payload.get("plugins")
    if not isinstance(items, list):
        raise PluginMarketError("插件清单里没有插件列表")

    listings = []
    seen = set()
    for raw in items:
        if not isinstance(raw, dict):
            continue
        plugin_id = str(raw.get("id") or "").strip()
        path = str(raw.get("path") or plugin_id).strip().replace("\\", "/")
        trigger = str(raw.get("trigger") or "").strip()
        if not is_safe_plugin_id(plugin_id) or path != plugin_id or not trigger:
            continue
        if plugin_id in seen:
            continue
        seen.add(plugin_id)
        listings.append(
            {
                "id": plugin_id,
                "name": str(raw.get("name") or plugin_id).strip() or plugin_id,
                "trigger": trigger,
                "version": str(raw.get("version") or "0.0.0").strip() or "0.0.0",
                "type": str(raw.get("type") or "").strip(),
                "summary": str(raw.get("summary") or "").strip(),
                "path": path,
            }
        )
    return listings


def http_get(url: str, timeout: float = 30) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/vnd.github+json, application/json, */*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise PluginMarketFetchError("GitHub 暂时限制了访问，过一会再刷新") from exc
        raise PluginMarketFetchError(f"读取插件仓库失败（{exc.code}）") from exc
    except urllib.error.URLError as exc:
        raise PluginMarketFetchError("连不上插件仓库") from exc


def fetch_catalog(get_bytes: Callable[[str], bytes], url: str = CATALOG_URL) -> tuple[dict, list[dict]]:
    try:
        payload = json.loads(get_bytes(url).decode("utf-8-sig"))
    except PluginMarketError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PluginMarketError("插件清单无法解析") from exc
    return payload, parse_catalog(payload)


def load_cached_catalog(path: str) -> list[dict]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    try:
        return parse_catalog(payload)
    except PluginMarketError:
        return []


def save_cached_catalog(path: str, payload: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def github_tree_entries(get_bytes: Callable[[str], bytes]) -> list[dict]:
    try:
        payload = json.loads(get_bytes(TREE_URL).decode("utf-8-sig"))
    except PluginMarketError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PluginMarketFetchError("插件文件列表无法解析") from exc
    if not isinstance(payload, dict):
        raise PluginMarketError("插件文件列表格式不对")
    if payload.get("truncated"):
        raise PluginMarketError("插件文件太多，这次没能列全")
    tree = payload.get("tree")
    if not isinstance(tree, list):
        raise PluginMarketError("插件文件列表格式不对")
    return [item for item in tree if isinstance(item, dict)]


def github_raw_url(path: str) -> str:
    return RAW_BASE + "/" + quote(str(path or ""), safe="/")


def read_installed_version(plugin_dir: Path) -> str:
    config_path = plugin_dir / "config.json"
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "0.0.0"
    if not isinstance(payload, dict):
        return "0.0.0"
    return str(payload.get("version") or "0.0.0").strip() or "0.0.0"


def market_status(
    listing: dict,
    *,
    bundled_ids: Iterable[str],
    bundled_triggers: Iterable[str],
    installed_root: str,
) -> str:
    plugin_id = str(listing.get("id") or "")
    trigger = str(listing.get("trigger") or "")
    if plugin_id in set(bundled_ids) or trigger in set(bundled_triggers):
        return "builtin"
    folder = Path(installed_root) / plugin_id
    if not (folder / "plugin.py").is_file() or not (folder / "config.json").is_file():
        return "absent"
    if read_installed_version(folder) != str(listing.get("version") or "0.0.0"):
        return "update"
    return "installed"


def merge_plugin_config(old: dict, new: dict) -> dict:
    """Keep the new plugin metadata, but preserve settings the user already changed."""
    merged = dict(new)
    old_settings = old.get("settings") if isinstance(old.get("settings"), dict) else {}
    new_settings = merged.get("settings") if isinstance(merged.get("settings"), dict) else {}
    merged_settings = dict(new_settings)
    for key, old_value in old_settings.items():
        new_value = merged_settings.get(key)
        if isinstance(old_value, dict) and isinstance(new_value, dict):
            updated = dict(new_value)
            if "default" in old_value:
                updated["default"] = old_value.get("default")
            merged_settings[key] = updated
        elif key in merged_settings:
            merged_settings[key] = old_value
        else:
            merged_settings[key] = old_value
    merged["settings"] = merged_settings
    return merged


def _plugin_files(tree: list[dict], plugin_id: str) -> list[dict]:
    prefix = plugin_id + "/"
    selected = []
    claimed = 0
    for item in tree:
        if str(item.get("type") or "") != "blob":
            continue
        path = str(item.get("path") or "").replace("\\", "/")
        if not path.startswith(prefix):
            continue
        relative = path[len(prefix) :]
        parts = [part for part in relative.split("/") if part]
        if (
            not parts
            or any(part in {"..", ".", "__pycache__", ".git"} or part.startswith(".") for part in parts)
            or relative.endswith(".pyc")
        ):
            continue
        size = item.get("size")
        try:
            declared = int(size) if size is not None else 0
        except (TypeError, ValueError):
            declared = 0
        if declared > MAX_FILE_BYTES:
            raise PluginMarketError("有文件太大，已停止安装")
        claimed += declared
        if claimed > MAX_PLUGIN_BYTES:
            raise PluginMarketError("这个插件太大，已停止安装")
        selected.append({"path": path, "rel": "/".join(parts), "size": declared})
    return selected


def _preserve_user_files(old: Path, staging: Path) -> None:
    old_config = old / "config.json"
    new_config = staging / "config.json"
    if old_config.is_file() and new_config.is_file():
        old_payload = json.loads(old_config.read_text(encoding="utf-8-sig"))
        new_payload = json.loads(new_config.read_text(encoding="utf-8-sig"))
        if isinstance(old_payload, dict) and isinstance(new_payload, dict):
            merged = merge_plugin_config(old_payload, new_payload)
            new_config.write_text(
                json.dumps(merged, ensure_ascii=False, indent=4) + "\n",
                encoding="utf-8",
            )
    old_data = old / "data"
    if old_data.is_dir():
        destination = staging / "data"
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(old_data, destination)


def _read_trigger(plugin_dir: Path) -> str:
    try:
        payload = json.loads((plugin_dir / "config.json").read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PluginMarketError("插件配置无法读取") from exc
    if not isinstance(payload, dict):
        raise PluginMarketError("插件配置格式不对")
    trigger = str(payload.get("trigger") or "").strip()
    if not trigger:
        raise PluginMarketError("插件配置缺少触发词")
    return trigger


def _replace_tree(staging: Path, destination: Path) -> None:
    backup = destination.parent / f".{destination.name}.bak"
    if backup.exists():
        shutil.rmtree(backup)
    moved_old = False
    try:
        if destination.exists():
            destination.rename(backup)
            moved_old = True
        staging.rename(destination)
    except OSError as exc:
        if moved_old and backup.exists() and not destination.exists():
            backup.rename(destination)
        raise PluginMarketError("插件目录正在使用，这次没能换上去") from exc
    if backup.exists():
        shutil.rmtree(backup)


def install_listing(
    listing: dict,
    *,
    installed_root: str,
    bundled_ids: Iterable[str],
    bundled_triggers: Iterable[str],
    fetch_tree: Callable[[], list[dict]],
    fetch_bytes: Callable[[str], bytes],
    progress: Optional[Callable[[int, int], None]] = None,
) -> str:
    plugin_id = str(listing.get("id") or "")
    trigger = str(listing.get("trigger") or "")
    if not is_safe_plugin_id(plugin_id):
        raise PluginMarketError("插件编号不合法")
    if plugin_id in set(bundled_ids) or trigger in set(bundled_triggers):
        raise PluginMarketError("这个插件已经在主程序里，不用再装")

    root = Path(installed_root)
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f".{plugin_id}.staging"
    destination = root / plugin_id
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    try:
        files = _plugin_files(fetch_tree(), plugin_id)
        if not files:
            raise PluginMarketError("仓库里没有这个插件的文件")
        total = len(files)
        downloaded = 0
        byte_count = 0

        def _one(entry: dict) -> int:
            data = fetch_bytes(entry["path"])
            if not isinstance(data, (bytes, bytearray)):
                raise PluginMarketError("插件文件下载结果不对")
            if len(data) > MAX_FILE_BYTES:
                raise PluginMarketError("有文件太大，已停止安装")
            target = staging / entry["rel"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            return len(data)

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(_one, entry) for entry in files]
            for future in as_completed(futures):
                byte_count += future.result()
                if byte_count > MAX_PLUGIN_BYTES:
                    raise PluginMarketError("这个插件太大，已停止安装")
                downloaded += 1
                if progress is not None:
                    progress(downloaded, total)

        if not (staging / "plugin.py").is_file() or not (staging / "config.json").is_file():
            raise PluginMarketError("下载结果里没有插件入口")
        installed_trigger = _read_trigger(staging)
        if installed_trigger != trigger:
            raise PluginMarketError("插件配置里的触发词和清单不一致")
        if destination.exists():
            _preserve_user_files(destination, staging)
        _replace_tree(staging, destination)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    return str(destination)


def _finalize_install(listing: dict, staging: Path, destination: Path) -> None:
    trigger = str(listing.get("trigger") or "")
    if not (staging / "plugin.py").is_file() or not (staging / "config.json").is_file():
        raise PluginMarketError("下载结果里没有插件入口")
    if _read_trigger(staging) != trigger:
        raise PluginMarketError("插件配置里的触发词和清单不一致")
    if destination.exists():
        _preserve_user_files(destination, staging)
    _replace_tree(staging, destination)


def install_from_github(
    listing: dict,
    *,
    installed_root: str,
    bundled_ids: Iterable[str],
    bundled_triggers: Iterable[str],
    progress: Optional[Callable[[int, int], None]] = None,
    get_bytes: Callable[..., bytes] = http_get,
) -> str:
    """Install one catalog entry. File downloads are preferred; a zip is the fallback."""
    try:
        return install_listing(
            listing,
            installed_root=installed_root,
            bundled_ids=bundled_ids,
            bundled_triggers=bundled_triggers,
            fetch_tree=lambda: github_tree_entries(get_bytes),
            fetch_bytes=lambda path: get_bytes(github_raw_url(path), timeout=60),
            progress=progress,
        )
    except PluginMarketFetchError:
        return _install_listing_from_zip(
            listing,
            installed_root=installed_root,
            bundled_ids=bundled_ids,
            bundled_triggers=bundled_triggers,
            get_bytes=get_bytes,
            progress=progress,
        )


def _install_listing_from_zip(
    listing: dict,
    *,
    installed_root: str,
    bundled_ids: Iterable[str],
    bundled_triggers: Iterable[str],
    get_bytes: Callable[..., bytes],
    progress: Optional[Callable[[int, int], None]] = None,
) -> str:
    plugin_id = str(listing.get("id") or "")
    trigger = str(listing.get("trigger") or "")
    if not is_safe_plugin_id(plugin_id):
        raise PluginMarketError("插件编号不合法")
    if plugin_id in set(bundled_ids) or trigger in set(bundled_triggers):
        raise PluginMarketError("这个插件已经在主程序里，不用再装")

    if progress is not None:
        progress(0, 1)
    try:
        payload = get_bytes(ZIP_URL, timeout=120)
    except TypeError:
        payload = get_bytes(ZIP_URL)
    if not isinstance(payload, (bytes, bytearray)):
        raise PluginMarketFetchError("插件压缩包下载结果不对")
    if len(payload) > MAX_PLUGIN_BYTES:
        raise PluginMarketError("这个插件仓库太大，已停止安装")

    root = Path(installed_root)
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f".{plugin_id}.staging"
    destination = root / plugin_id
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    prefix = f"{plugin_id}/"
    extracted = 0
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                name = str(info.filename or "").replace("\\", "/")
                marker = "/" + prefix
                if marker not in "/" + name:
                    continue
                relative = name.split(marker, 1)[1]
                parts = [part for part in relative.split("/") if part]
                if (
                    not parts
                    or any(part in {"..", ".", "__pycache__", ".git"} or part.startswith(".") for part in parts)
                    or relative.endswith(".pyc")
                ):
                    continue
                if info.file_size > MAX_FILE_BYTES:
                    raise PluginMarketError("有文件太大，已停止安装")
                target = staging.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
                extracted += target.stat().st_size
                if extracted > MAX_PLUGIN_BYTES:
                    raise PluginMarketError("这个插件太大，已停止安装")
        if progress is not None:
            progress(1, 1)
        _finalize_install(listing, staging, destination)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    return str(destination)


def uninstall_plugin(plugin_id: str, installed_root: str) -> None:
    if not is_safe_plugin_id(plugin_id):
        raise PluginMarketError("插件编号不合法")
    root = Path(installed_root).resolve()
    target = (root / plugin_id).resolve()
    if os.path.commonpath([str(root), str(target)]) != str(root) or target == root:
        raise PluginMarketError("不能删除这个位置")
    if not target.is_dir():
        raise PluginMarketError("本机没有这个插件")
    shutil.rmtree(target)
