"""Integer-level refinement never crosses neutral or invents a global minimum."""

from services.worker.app.refinement_jobs import next_level


def test_brightness_binary_search_preserves_direction_and_terminates():
    recipe = {"parameters": {"algorithm": "brightness_integer_v1"}}
    for start in (0, 1000):
        best, passing = start, 500
        visited = []
        while (probe := next_level(recipe, best, passing)) is not None:
            assert min(best, passing) < probe < max(best, passing)
            visited.append(probe)
            # Synthetic monotonic threshold at distance 100 from neutral.
            if abs(probe - 500) >= 100:
                best = probe
            else:
                passing = probe
        assert len(visited) <= 10
        assert abs(best - 500) == 100


def test_clutter_removes_exact_suffix_layer_and_stops_at_one_layer():
    recipe = {"parameters": {"algorithm": "rectangles_v1"}}
    best = 1000
    for layers in range(10, 0, -1):
        best = next_level(recipe, best, 0)
        assert best is not None and 1 + best // 100 == layers
    assert next_level(recipe, best, 0) is None
    assert next_level({"parameters": {"algorithm": "supplied_background_v1"}}, 1000, 0) is None


def test_clutter_raster_reduction_replays_identical_prefix_layers(monkeypatch):
    from counterforge_contracts.mutations import Parameters
    from counterforge_mutations import deterministic
    from PIL import Image

    original_draw = deterministic.ImageDraw.Draw
    layers = []

    class RecordedDraw:
        def __init__(self, image):
            self.draw = original_draw(image)

        def rectangle(self, bounds, *, fill):
            layers.append((bounds, fill))
            self.draw.rectangle(bounds, fill=fill)

    monkeypatch.setattr(deterministic.ImageDraw, "Draw", RecordedDraw)
    source = Image.new("RGB", (64, 64), "red")
    deterministic.transform(
        source, Parameters(algorithm="rectangles_v1", level=700, brightness_numerator=1200), 42
    )
    original_layers = list(layers)
    layers.clear()
    first = deterministic.transform(
        source, Parameters(algorithm="rectangles_v1", level=699, brightness_numerator=1199), 42
    )
    assert len(original_layers) == 8 and len(layers) == 7
    assert layers == original_layers[:-1]
    layers.clear()
    replay = deterministic.transform(
        source, Parameters(algorithm="rectangles_v1", level=699, brightness_numerator=1199), 42
    )
    assert replay.tobytes() == first.tobytes()


def test_live_retest_target_requires_credentials_before_dispatch():
    import pytest
    from pydantic import SecretStr

    from services.api.app.config import Settings
    from services.api.app.errors import ServiceError
    from services.worker.app.refinement_jobs import require_mode

    settings = Settings(
        provider_mode="LIVE", run_meta_live_tests=True, model_api_key=SecretStr("test-only-initial")
    ).model_copy(update={"model_api_key": SecretStr("")})
    with pytest.raises(ServiceError, match="credentials"):
        require_mode(settings, {"adapter_key": "META_SPARK"})
