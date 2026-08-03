from __future__ import annotations

import launcher


def test_external_process_restores_frozen_dll_search_path(monkeypatch):
    calls = []
    monkeypatch.setattr(launcher, "_frozen_bundle_dir", lambda: r"C:\Temp\_MEI123")
    monkeypatch.setattr(launcher, "_set_dll_directory", calls.append)

    with launcher._external_process_dll_search():
        assert calls == [None]

    assert calls == [None, r"C:\Temp\_MEI123"]
