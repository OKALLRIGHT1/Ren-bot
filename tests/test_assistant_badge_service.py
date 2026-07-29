import base64
import json
from pathlib import Path

from services.gui_api.characters_service import CharactersService


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _catalog(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "active_id": "suzu",
                "characters": {
                    "suzu": {
                        "name": "Suzu",
                        "current_costume": "uniform",
                        "costumes": {
                            "uniform": {"path": "uniform/model.model3.json"},
                            "winter": {"path": "winter/model.model3.json"},
                        },
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _service(tmp_path: Path) -> tuple[CharactersService, Path]:
    catalog = tmp_path / "data" / "characters.json"
    catalog.parent.mkdir(parents=True)
    _catalog(catalog)
    return CharactersService(catalog), catalog


def _png(tmp_path: Path, name: str = "avatar.png") -> Path:
    source = tmp_path / name
    source.write_bytes(PNG_BYTES)
    return source


def test_character_badge_is_copied_and_resolved_for_inheriting_costume(tmp_path: Path):
    service, catalog = _service(tmp_path)
    source = _png(tmp_path)

    imported = service.import_badge("suzu", str(source), scale=1.25, offset_y=-0.2)

    assert imported["ok"] is True
    raw = json.loads(catalog.read_text(encoding="utf-8"))
    stored = raw["characters"]["suzu"]["assistant_badge"]
    assert stored["path"].startswith("data/assistant_badges/")
    assert Path(tmp_path, stored["path"]).read_bytes() == PNG_BYTES
    source.unlink()

    resolved = service.get_badge("suzu", "uniform")
    assert resolved["ok"] is True
    assert resolved["data"]["source"] == "character"
    assert resolved["data"]["badge"]["scale"] == 1.25
    assert resolved["data"]["badge"]["offset_y"] == -0.2
    assert base64.b64decode(
        resolved["data"]["image_data_url"].split(",", 1)[1]
    ) == PNG_BYTES


def test_costume_badge_overrides_character_and_clear_restores_inheritance(tmp_path: Path):
    service, _ = _service(tmp_path)
    character_image = _png(tmp_path, "character.png")
    costume_image = _png(tmp_path, "costume.png")
    service.import_badge("suzu", str(character_image))

    imported = service.import_badge(
        "suzu",
        str(costume_image),
        costume_name="winter",
        scale=9,
        offset_x=-8,
        offset_y=3,
    )
    assert imported["ok"] is True

    override = service.get_badge("suzu", "winter")["data"]
    assert override["source"] == "costume"
    assert override["badge"]["scale"] == 3.0
    assert override["badge"]["offset_x"] == -1.0
    assert override["badge"]["offset_y"] == 1.0

    cleared = service.clear_badge("suzu", costume_name="winter")
    assert cleared["ok"] is True
    inherited = service.get_badge("suzu", "winter")["data"]
    assert inherited["source"] == "character"


def test_current_badge_uses_active_character_and_costume(tmp_path: Path):
    service, _ = _service(tmp_path)
    service.import_badge("suzu", str(_png(tmp_path)))

    current = service.get_current_badge()

    assert current["ok"] is True
    assert current["data"]["character_id"] == "suzu"
    assert current["data"]["costume_name"] == "uniform"
    assert current["data"]["source"] == "character"


def test_badge_import_rejects_unknown_costume_and_invalid_image(tmp_path: Path):
    service, _ = _service(tmp_path)
    invalid = tmp_path / "not-an-image.png"
    invalid.write_text("hello", encoding="utf-8")

    assert service.import_badge("suzu", str(invalid))["error"] == "invalid_image"
    truncated = tmp_path / "truncated.png"
    truncated.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert service.import_badge("suzu", str(truncated))["error"] == "invalid_image"
    assert (
        service.import_badge(
            "suzu", str(_png(tmp_path)), costume_name="missing"
        )["error"]
        == "costume_not_found"
    )


def test_missing_badge_resolves_as_none(tmp_path: Path):
    service, _ = _service(tmp_path)

    result = service.get_badge("suzu", "uniform")

    assert result == {
        "ok": True,
        "data": {
            "character_id": "suzu",
            "costume_name": "uniform",
            "source": "none",
            "badge": None,
            "image_data_url": "",
        },
    }
