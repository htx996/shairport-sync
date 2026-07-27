import re
from pathlib import Path
from types import SimpleNamespace

from app import (
    DEFAULTS,
    choose_shairport_container,
    escape_libconfig_string,
    generate_config,
    normalize_settings,
    read_text_file,
)


def top_level_general_count(config_text):
    return len(re.findall(r"(?m)^general\s*=", config_text))


def test_unicode_and_special_chars_are_escaped():
    settings = normalize_settings(
        {
            "name": '客厅 "HomePod" \\ {测试} 🎵',
            "audio_device": "hw:1,0",
            "mixer_control_name": "PCM",
        }
    )
    config_text = generate_config(settings)

    assert 'name = "客厅 \\"HomePod\\" \\\\ {测试} 🎵";' in config_text
    assert 'output_device = "hw:1,0";' in config_text
    assert "mixer_control_name = \"PCM\";" in config_text
    assert top_level_general_count(config_text) == 1


def test_injection_device_name_cannot_escape_string_literal():
    malicious = 'evil"; }; general = { name = "pwned'
    config_text = generate_config({"name": malicious})

    assert 'evil\\"; }; general = { name = \\"pwned' in config_text
    assert top_level_general_count(config_text) == 1
    assert re.search(r'(?m)^general\s*=\s*\{\s*name\s*=\s*"pwned"', config_text) is None


def test_volume_range_is_clamped_to_valid_bounds():
    too_low = normalize_settings({"volume_range_db": -1})
    too_high = normalize_settings({"volume_range_db": 999})

    assert too_low["volume_range_db"] == 30
    assert too_high["volume_range_db"] == 150
    assert "volume_range_db = 30;" in generate_config(too_low)
    assert "volume_range_db = 150;" in generate_config(too_high)


def test_volume_max_is_clamped_to_zero_or_lower():
    too_high = normalize_settings({"volume_max_db": 18})
    too_low = normalize_settings({"volume_max_db": -999})

    assert too_high["volume_max_db"] == 0.0
    assert too_low["volume_max_db"] == -144.0
    assert "volume_max_db = 0.0;" in generate_config(too_high)
    assert "volume_max_db = -144.0;" in generate_config(too_low)


def test_invalid_enums_and_devices_fall_back():
    settings = normalize_settings(
        {
            "model": "DefinitelyNotAHomePod",
            "ignore_volume_control": "maybe",
            "audio_device": 'hw:1,0"; bad',
            "interface": "eth0;bad",
        }
    )

    assert settings["model"] == DEFAULTS["model"]
    assert settings["ignore_volume_control"] == DEFAULTS["ignore_volume_control"]
    assert settings["audio_device"] == DEFAULTS["audio_device"]
    assert settings["interface"] == ""


def test_empty_strings_use_safe_defaults():
    settings = normalize_settings(
        {
            "name": "",
            "model": "",
            "audio_device": "",
            "volume_range_db": "",
            "volume_max_db": "",
        }
    )

    assert settings["name"] == DEFAULTS["name"]
    assert settings["model"] == DEFAULTS["model"]
    assert settings["audio_device"] == DEFAULTS["audio_device"]
    assert settings["volume_range_db"] == DEFAULTS["volume_range_db"]
    assert settings["volume_max_db"] == DEFAULTS["volume_max_db"]


def test_escape_libconfig_string_handles_backslash_before_quote():
    assert escape_libconfig_string(r'bad \" name') == r"bad \\\" name"


def test_read_text_file_tolerates_sysfs_read_errors(monkeypatch):
    def raise_oserror(*args, **kwargs):
        raise OSError("Invalid argument")

    monkeypatch.setattr(Path, "read_text", raise_oserror)

    assert read_text_file(Path("/sys/class/net/eth0/speed")) == ""


def fake_container(name, image, service="", status="running"):
    labels = {}
    if service:
        labels["com.docker.compose.service"] = service
    return SimpleNamespace(
        name=name,
        status=status,
        labels=labels,
        image=SimpleNamespace(tags=[image]),
        attrs={"Config": {"Image": image, "Labels": labels}},
    )


def test_container_discovery_prefers_receiver_over_panel():
    receiver = fake_container(
        "airplay-shairport-sync-1",
        "docker.io/hanfu1997/airplay:latest",
        "shairport-sync",
    )
    panel = fake_container(
        "airplay-webui-1",
        "docker.io/hanfu1997/airplay-panel:latest",
        "webui",
    )

    assert choose_shairport_container([panel, receiver]) is receiver


def test_container_discovery_uses_image_when_name_is_rewritten():
    receiver = fake_container(
        "airplay-panel-shairport-sync-1",
        "hanfu1997/airplay:latest",
    )

    assert choose_shairport_container([receiver]) is receiver
