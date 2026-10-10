from narrate.config import chain, load_config, profile
from narrate.engines import ENGINES


def test_defaults_merge_and_chain(config):
    cfg = load_config(config)
    assert profile(cfg, "dry-run")["gpu_share"] == 1.0
    assert profile(cfg, "dry-run")["format"] == "m4b"
    assert chain(cfg, "cloud-voice") == ["cloud-voice", "local-voice", "dry-run"]


def test_engines_describe_themselves():
    assert ENGINES["gemini"].remote and ENGINES["speechify"].remote
    assert not ENGINES["breeze"].remote and ENGINES["breeze"].uses_gpu
    assert not ENGINES["tone"].remote and not ENGINES["tone"].uses_gpu
