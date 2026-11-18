from pathlib import Path

from app.discovery.gem_config import load_gem_config

CONFIG = Path(__file__).resolve().parents[3] / "config" / "gem_marketplace.yaml"


def test_shipped_config_lists_look_alikes_for_control_valve() -> None:
    config = load_gem_config(CONFIG)
    assert "flow control valve" in config.excludes_for("control_valve")


def test_type_without_excludes_gives_none() -> None:
    config = load_gem_config(CONFIG)
    assert config.excludes_for("thermocouple") == []
    assert config.excludes_for("not_configured") == []
