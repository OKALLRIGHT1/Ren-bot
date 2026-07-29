from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from services.gui_api.meme_pack_service import MemePackGuiService


@dataclass
class FakeAsset:
    id: int
    file_name: str
    file_path: str
    description: str = ""
    tags: List[str] = field(default_factory=list)
    emotion: str = ""
    enabled: bool = True
    banned: bool = False
    usage_count: int = 0


class FakeMemeStore:
    def __init__(self) -> None:
        self.assets: Dict[int, FakeAsset] = {
            1: FakeAsset(
                id=1,
                file_name="a.png",
                file_path="plugins/meme_pack/assets/a.png",
                description="可爱",
                tags=["可爱"],
                emotion="happy",
                usage_count=3,
            )
        }

    def search_assets(self, query: str = "", *, include_disabled: bool = True, limit: int = 500):
        rows = list(self.assets.values())
        clean = str(query or "").strip().lower()
        if clean:
            rows = [
                row
                for row in rows
                if clean in f"{row.file_name} {row.description} {row.emotion} {' '.join(row.tags)}".lower()
            ]
        if not include_disabled:
            rows = [row for row in rows if row.enabled and not row.banned]
        return rows[:limit]

    def get_asset(self, asset_id: int):
        return self.assets.get(int(asset_id))

    def update_asset(self, asset_id: int, *, description: str, tags, emotion: str, enabled: bool, banned: bool) -> bool:
        asset = self.assets.get(int(asset_id))
        if not asset:
            return False
        asset.description = description
        asset.tags = list(tags)
        asset.emotion = emotion
        asset.enabled = enabled
        asset.banned = banned
        return True

    def set_enabled(self, asset_ids: Iterable[int], enabled: bool) -> int:
        count = 0
        for asset_id in asset_ids:
            asset = self.assets.get(int(asset_id))
            if asset:
                asset.enabled = enabled
                count += 1
        return count

    def delete_assets(self, asset_ids: Iterable[int], *, delete_files: bool = False) -> int:
        count = 0
        for asset_id in list(asset_ids):
            if self.assets.pop(int(asset_id), None) is not None:
                count += 1
        return count

    def stats(self) -> dict[str, int]:
        rows = list(self.assets.values())
        return {
            "total": len(rows),
            "enabled": sum(1 for row in rows if row.enabled and not row.banned),
            "banned": sum(1 for row in rows if row.banned),
            "usage_count": sum(row.usage_count for row in rows),
        }


class FakePlugin:
    def __init__(self, store: FakeMemeStore) -> None:
        self._store = store

    def get_store(self):
        return self._store


class StoreObjectPlugin:
    def __init__(self, store: FakeMemeStore) -> None:
        self._store = None
        self._resolved_store = store

    def _store_obj(self):
        return self._resolved_store


class FakePluginManager:
    def __init__(self, plugin) -> None:
        self.plugins = {"meme_pack": plugin}


def test_list_and_update_meme():
    store = FakeMemeStore()
    service = MemePackGuiService(store=store)
    listed = service.list_assets()
    assert listed["ok"] is True
    assert listed["data"]["count"] == 1
    assert listed["data"]["stats"]["total"] == 1
    updated = service.update_asset(
        {
            "id": 1,
            "description": "调侃",
            "tags": ["调侃", "可爱"],
            "emotion": "doubt",
            "enabled": True,
            "banned": False,
        }
    )
    assert updated["ok"] is True
    assert updated["data"]["description"] == "调侃"
    assert "调侃" in updated["data"]["tags"]


def test_enable_and_delete():
    store = FakeMemeStore()
    service = MemePackGuiService(store=store)
    disabled = service.set_enabled([1], False)
    assert disabled["ok"] is True
    assert store.assets[1].enabled is False
    deleted = service.delete_assets([1], delete_files=False)
    assert deleted["ok"] is True
    assert deleted["data"]["deleted"] == 1


def test_plugin_manager_resolves_real_meme_plugin_store_interface():
    store = FakeMemeStore()
    service = MemePackGuiService(
        plugin_manager=FakePluginManager(StoreObjectPlugin(store))
    )

    listed = service.list_assets()

    assert listed["ok"] is True
    assert listed["data"]["count"] == 1
