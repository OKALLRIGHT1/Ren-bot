"""Check public hand-written READMEs and optional local generated docs.

The five bundled plugins use MANUAL README markers. Generator unit tests run
only in checkouts that contain dev_scripts/gen_plugin_readme.py; generated
plugin documentation still requires that generator for freshness checks.
"""

import importlib.util
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN = os.path.join(REPO, "dev_scripts", "gen_plugin_readme.py")
PLUGIN_ROOT = os.path.join(REPO, "plugins")


def _plugin_names():
    return sorted(
        name for name in os.listdir(PLUGIN_ROOT)
        if os.path.isfile(os.path.join(PLUGIN_ROOT, name, "config.json"))
        and os.path.isfile(os.path.join(PLUGIN_ROOT, name, "plugin.py"))
    )


def _load_gen():
    if not os.path.isfile(GEN):
        pytest.skip("Optional local README generator is not included in this checkout")
    spec = importlib.util.spec_from_file_location("gen_plugin_readme", GEN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gen_plugin_readme"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_every_plugin_has_a_readme():
    names = _plugin_names()
    assert names, "No bundled plugins found"
    missing = [
        name
        for name in names
        if not os.path.isfile(os.path.join(PLUGIN_ROOT, name, "README.md"))
    ]
    assert missing == [], (
        f"这些插件缺 README：{missing}\n"
        "运行 python dev_scripts/gen_plugin_readme.py 生成。"
    )


def test_generated_readmes_are_up_to_date():
    generated = []
    for name in _plugin_names():
        path = os.path.join(PLUGIN_ROOT, name, "README.md")
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
        if "<!-- MANUAL README -->" in content:
            assert "怎么用" in content, f"{name} 的用法说明被清空了"
        else:
            generated.append(name)
    if not generated:
        return  # Public core plugins use hand-written READMEs.
    assert os.path.isfile(GEN), (
        f"Generated READMEs require {GEN}: {generated}. "
        "Restore the generator or maintain these READMEs manually."
    )
    result = subprocess.run(
        [sys.executable, GEN, "--check"],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert result.returncode == 0, (
        "README 与插件定义不一致：\n"
        f"{result.stdout}\n{result.stderr}\n"
        "运行 python dev_scripts/gen_plugin_readme.py 重新生成。"
    )


def test_generator_is_idempotent(tmp_path):
    """Re-rendering the same plugin twice must produce identical bytes."""
    gen = _load_gen()
    names = gen.plugin_names()
    assert names, "未发现任何插件"
    risk = gen._risk_flags()
    for name in names[:5]:
        pdir = os.path.join(gen.PLUGIN_ROOT, name)
        cfg = gen._load_config(os.path.join(pdir, "config.json"))
        probe = gen._probe_sources(pdir)
        first = gen.render(name, cfg, probe, risk)
        second = gen.render(name, cfg, gen._probe_sources(pdir), risk)
        assert first == second, f"{name} 的渲染结果不稳定"


def test_hand_written_tail_is_preserved():
    """Content after the KEEP marker must never be clobbered."""
    gen = _load_gen()
    body = "# X\n\n" + gen.KEEP_MARKER
    existing = body + "\n\n## 手写\n\n保留我。\n"
    tail = gen._split_keep(existing)
    assert "保留我。" in tail
    assert (body + tail) == existing


def test_blank_keep_tail_is_normalised():
    """A tail of only whitespace collapses, so re-runs don't grow newlines."""
    gen = _load_gen()
    assert gen._split_keep("# X\n\n" + gen.KEEP_MARKER + "\n\n\n\n") == "\n"
    assert gen._split_keep("no marker here") == "\n"


@pytest.mark.parametrize(
    "name",
    ["workspace_ops", "system_monitor", "timer", "qq_reminder", "task_manager"],
)
def test_security_and_gotcha_notes_survive(name):
    """These READMEs carry hand-written caveats; guard against losing them."""
    path = os.path.join(REPO, "plugins", name, "README.md")
    if not os.path.isfile(path):
        pytest.skip(f"{name} 不存在")
    content = open(path, encoding="utf-8").read()
    if "<!-- MANUAL README -->" in content:
        assert "怎么用" in content, f"{name} 的用法说明被清空了"
        return
    gen = _load_gen()
    assert gen.KEEP_MARKER in content
    tail = content.split(gen.KEEP_MARKER, 1)[1]
    assert tail.strip(), f"{name} 的手写补充被清空了"
