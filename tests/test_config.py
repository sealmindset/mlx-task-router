from __future__ import annotations

from mlx_task_router.config import DEFAULT_GEARS, GearProfile, _build_gears


class TestGearProfile:
    def test_defaults(self):
        gp = GearProfile(name="test", model="test-model", description="desc")
        assert gp.max_tokens == 8192

    def test_custom_max_tokens(self):
        gp = GearProfile(name="test", model="m", description="d", max_tokens=4096)
        assert gp.max_tokens == 4096


class TestDefaultGears:
    def test_three_gears_exist(self):
        assert "eco" in DEFAULT_GEARS
        assert "sport" in DEFAULT_GEARS
        assert "track" in DEFAULT_GEARS

    def test_eco_smaller_tokens(self):
        assert DEFAULT_GEARS["eco"].max_tokens < DEFAULT_GEARS["track"].max_tokens

    def test_track_largest_tokens(self):
        assert DEFAULT_GEARS["track"].max_tokens == 16384


class TestBuildGears:
    def test_returns_all_gears(self):
        gears = _build_gears()
        assert set(gears.keys()) == {"eco", "sport", "track"}

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("GEAR_ECO_MODEL", "custom/model")
        gears = _build_gears()
        assert gears["eco"].model == "custom/model"

    def test_no_override_uses_default(self):
        gears = _build_gears()
        assert gears["eco"].model == DEFAULT_GEARS["eco"].model
