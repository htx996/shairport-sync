from __future__ import annotations

import hmac
import json
import math
import os
import re
import shutil
import struct
import subprocess
import time
import wave
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template, request

try:
    import docker
except ImportError:  # pragma: no cover - the Docker SDK is present in the container image.
    docker = None


APP_DIR = Path(__file__).resolve().parent
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
CONF_PATH = CONFIG_DIR / "shairport-sync.conf"
MODEL_ENV_PATH = CONFIG_DIR / "model.env"
SETTINGS_PATH = DATA_DIR / "settings.json"
CONTAINER_NAME = os.environ.get("SHAIRPORT_CONTAINER", "shairport-sync")
AIRPLAY_IMAGE_HINTS = tuple(
    item.strip().lower()
    for item in os.environ.get("SHAIRPORT_IMAGE_HINTS", "hanfu1997/airplay").split(",")
    if item.strip()
)
PANEL_HOST = os.environ.get("PANEL_HOST", "0.0.0.0")
PANEL_PORT = int(os.environ.get("PANEL_PORT", "8099"))
UPSTREAM_VERSION_PATHS = (APP_DIR / "upstream-versions.json", APP_DIR.parent / "upstream-versions.json")


def clean_version(value: Any) -> str:
    text = str(value or "").strip().lstrip("vV").strip()
    return re.sub(r"[^0-9A-Za-z._+-]", "", text)[:32]


def load_shairport_sync_version() -> str:
    env_version = clean_version(os.environ.get("SHAIRPORT_SYNC_VERSION"))
    if env_version:
        return env_version
    for path in UPSTREAM_VERSION_PATHS:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        version = clean_version(data.get("shairport_sync", {}).get("version"))
        if version:
            return version
    return "5.2.2"


SHAIRPORT_SYNC_VERSION = load_shairport_sync_version()

MODELS = [
    {
        "id": "ShairportSync",
        "label": "通用扬声器",
        "detail": "默认标识符",
    },
    {
        "id": "AirPort10,115",
        "label": "AirPort Express",
        "detail": "兼容性最好，推荐",
    },
    {
        "id": "AudioAccessory5,1",
        "label": "HomePod mini",
        "detail": "伪装图标",
    },
    {
        "id": "AudioAccessory1,2",
        "label": "HomePod 一代",
        "detail": "伪装图标",
    },
    {
        "id": "AudioAccessory6,1",
        "label": "HomePod 二代",
        "detail": "伪装图标",
    },
    {
        "id": "AppleTV6,2",
        "label": "Apple TV 4K",
        "detail": "伪装图标",
    },
]
MODEL_IDS = {item["id"] for item in MODELS}
EQ_BANDS = [
    {"id": "60", "label": "60 Hz", "freq": 60.0},
    {"id": "150", "label": "150 Hz", "freq": 150.0},
    {"id": "400", "label": "400 Hz", "freq": 400.0},
    {"id": "1000", "label": "1 kHz", "freq": 1000.0},
    {"id": "2400", "label": "2.4 kHz", "freq": 2400.0},
    {"id": "6000", "label": "6 kHz", "freq": 6000.0},
    {"id": "12000", "label": "12 kHz", "freq": 12000.0},
]
EQ_BAND_IDS = tuple(band["id"] for band in EQ_BANDS)
EQ_SAMPLE_RATES = (44100, 48000)
EQ_IR_LENGTH = 1025
EQ_PRESETS = {"flat", "bass", "vocal", "bright", "night", "custom"}

DEFAULTS: dict[str, Any] = {
    "name": "AirPlay",
    "model": "AirPort10,115",
    "interface": "",
    "audio_device": "default",
    "mixer_control_name": "",
    "volume_percent": 70,
    "volume_range_db": 60,
    "volume_max_db": 0.0,
    "ignore_volume_control": "no",
    "equalizer_enabled": "no",
    "equalizer_preset": "flat",
    "equalizer_bands": {band_id: 0.0 for band_id in EQ_BAND_IDS},
    "additional_config": "",
}

ALSA_DEVICE_RE = re.compile(r"^(default|(?:plug)?hw:[A-Za-z0-9_.:-]+(?:,[A-Za-z0-9_.:-]+)?|sysdefault(?::[A-Za-z0-9_.:-]+)?)$")
BRIDGE_NAME_RE = re.compile(r"^br\d+$")

app = Flask(__name__, template_folder=str(APP_DIR / "templates"))


def _auth_response() -> Response:
    return Response(
        "Authentication required\n",
        401,
        {"WWW-Authenticate": 'Basic realm="Shairport Sync Panel"'},
    )


@app.before_request
def require_basic_auth() -> Response | None:
    expected_password = os.environ.get("PANEL_PASSWORD")
    if not expected_password:
        return None

    expected_user = os.environ.get("PANEL_USER", "admin")
    auth = request.authorization
    if not auth:
        return _auth_response()

    user_ok = hmac.compare_digest(auth.username or "", expected_user)
    password_ok = hmac.compare_digest(auth.password or "", expected_password)
    if not (user_ok and password_ok):
        return _auth_response()
    return None


def clean_inline_text(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("\x00", "").replace("\r", " ").replace("\n", " ").strip()


def escape_libconfig_string(value: Any) -> str:
    text = clean_inline_text(value)
    return text.replace("\\", "\\\\").replace('"', '\\"')


def clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def clamp_float(value: Any, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def normalize_yes_no(value: Any, default: str = "no") -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    text = clean_inline_text(value).lower()
    if text in {"yes", "true", "1", "on"}:
        return "yes"
    if text in {"no", "false", "0", "off"}:
        return "no"
    return default


def normalize_equalizer_bands(value: Any) -> dict[str, float]:
    raw = value if isinstance(value, dict) else {}
    normalized: dict[str, float] = {}
    for band_id in EQ_BAND_IDS:
        normalized[band_id] = clamp_float(raw.get(band_id), -12.0, 12.0, 0.0)
    return normalized


def is_excluded_interface_name(name: str) -> bool:
    excluded_prefixes = ("docker", "br-", "bridge", "veth", "virbr", "tap", "tun")
    return name == "lo" or name.startswith(excluded_prefixes) or BRIDGE_NAME_RE.match(name) is not None


def normalize_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    source = {**DEFAULTS, **(raw or {})}

    name = clean_inline_text(source.get("name"))
    if not name:
        name = DEFAULTS["name"]
    name = name[:50]

    model = clean_inline_text(source.get("model"))
    if model not in MODEL_IDS:
        model = DEFAULTS["model"]

    interface = ""

    audio_device = clean_inline_text(source.get("audio_device"))
    if not audio_device:
        audio_device = DEFAULTS["audio_device"]
    if not ALSA_DEVICE_RE.match(audio_device):
        audio_device = DEFAULTS["audio_device"]

    mixer_control_name = clean_inline_text(source.get("mixer_control_name"))[:80]
    equalizer_preset = clean_inline_text(source.get("equalizer_preset")).lower()
    if equalizer_preset not in EQ_PRESETS:
        equalizer_preset = DEFAULTS["equalizer_preset"]
    additional_config = str(source.get("additional_config") or "").replace("\x00", "")
    additional_config = additional_config[:65535]

    return {
        "name": name,
        "model": model,
        "interface": interface,
        "audio_device": audio_device,
        "mixer_control_name": mixer_control_name,
        "volume_percent": clamp_int(source.get("volume_percent"), 0, 100, DEFAULTS["volume_percent"]),
        "volume_range_db": clamp_int(source.get("volume_range_db"), 30, 150, DEFAULTS["volume_range_db"]),
        "volume_max_db": clamp_float(source.get("volume_max_db"), -144.0, 0.0, DEFAULTS["volume_max_db"]),
        "ignore_volume_control": normalize_yes_no(source.get("ignore_volume_control"), DEFAULTS["ignore_volume_control"]),
        # The WebUI convolution equalizer is intentionally disabled. It can cause
        # underruns in the real-time AirPlay 2 audio path on NAS deployments.
        "equalizer_enabled": "no",
        "equalizer_preset": equalizer_preset,
        "equalizer_bands": normalize_equalizer_bands(source.get("equalizer_bands")),
        "additional_config": additional_config,
    }


def format_float(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text if "." in text else f"{text}.0"


def equalizer_ir_config_paths() -> list[str]:
    return [str(CONFIG_DIR / f"equalizer_{sample_rate}.wav") for sample_rate in EQ_SAMPLE_RATES]


def interpolate_equalizer_gain_db(frequency: float, bands: dict[str, float]) -> float:
    points = [(float(band["freq"]), float(bands[band["id"]])) for band in EQ_BANDS]
    if frequency <= points[0][0]:
        return points[0][1]
    for (left_freq, left_gain), (right_freq, right_gain) in zip(points, points[1:]):
        if frequency <= right_freq:
            position = (math.log(frequency) - math.log(left_freq)) / (math.log(right_freq) - math.log(left_freq))
            return left_gain + ((right_gain - left_gain) * position)
    return points[-1][1]


def generate_equalizer_impulse(sample_rate: int, bands: dict[str, float], length: int = EQ_IR_LENGTH) -> list[float]:
    half = (length - 1) // 2
    response = []
    for index in range(half + 1):
        frequency = (index * sample_rate) / length
        gain_db = interpolate_equalizer_gain_db(max(1.0, frequency), bands)
        response.append(10 ** (gain_db / 20.0))

    impulse: list[float] = []
    center = (length - 1) / 2.0
    for index in range(length):
        offset = index - center
        value = response[0]
        for bin_index in range(1, half + 1):
            value += 2.0 * response[bin_index] * math.cos((2.0 * math.pi * bin_index * offset) / length)
        impulse.append(value / length)

    peak = max(abs(value) for value in impulse) or 1.0
    scale = min(0.95 / peak, 1.0)
    return [value * scale for value in impulse]


def write_wav_mono(path: Path, sample_rate: int, samples: list[float]) -> None:
    frames = bytearray()
    for sample in samples:
        clamped = max(-1.0, min(1.0, sample))
        frames.extend(struct.pack("<h", int(round(clamped * 32767))))
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(bytes(frames))


def write_equalizer_ir_files(settings: dict[str, Any]) -> list[Path]:
    settings = normalize_settings(settings)
    if settings["equalizer_enabled"] != "yes":
        return []
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for sample_rate in EQ_SAMPLE_RATES:
        path = CONFIG_DIR / f"equalizer_{sample_rate}.wav"
        write_wav_mono(path, sample_rate, generate_equalizer_impulse(sample_rate, settings["equalizer_bands"]))
        written.append(path)
    return written


def generate_config(raw_settings: dict[str, Any] | None) -> str:
    settings = normalize_settings(raw_settings)
    lines = [
        "// Generated by shairport-sync WebUI. Use the panel for normal edits.",
        "general =",
        "{",
        f'  name = "{escape_libconfig_string(settings["name"])}";',
        '  output_backend = "alsa";',
        '  mdns_backend = "avahi";',
        f'  ignore_volume_control = "{settings["ignore_volume_control"]}";',
        f'  volume_range_db = {settings["volume_range_db"]};',
        f'  volume_max_db = {format_float(float(settings["volume_max_db"]))};',
    ]
    lines.extend(
        [
            "};",
            "",
            "alsa =",
            "{",
            f'  output_device = "{escape_libconfig_string(settings["audio_device"])}";',
        ]
    )
    if settings["mixer_control_name"]:
        lines.append(f'  mixer_control_name = "{escape_libconfig_string(settings["mixer_control_name"])}";')
    lines.extend(["};", ""])

    if settings["equalizer_enabled"] == "yes":
        ir_files = ",".join(equalizer_ir_config_paths())
        lines.extend(
            [
                "dsp =",
                "{",
                '  convolution_enabled = "yes";',
                "  convolution_thread_pool_size = 1;",
                f'  convolution_ir_files = "{escape_libconfig_string(ir_files)}";',
                "  convolution_gain = -3.0;",
                "  convolution_max_length_in_seconds = 0.05;",
                "};",
                "",
            ]
        )

    extra = settings["additional_config"].strip()
    if extra:
        lines.extend(
            [
                "// Additional configuration from the WebUI advanced text box.",
                extra,
                "",
            ]
        )
    return "\n".join(lines)


def read_model_env() -> str | None:
    if not MODEL_ENV_PATH.exists():
        return None
    for line in MODEL_ENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "SPS_MODEL":
            model = value.strip()
            return model if model in MODEL_IDS else None
    return None


def load_settings() -> dict[str, Any]:
    data: dict[str, Any] = {}
    if SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    model = read_model_env()
    if model:
        data["model"] = model
    return normalize_settings(data)


def save_settings(settings: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_model_env(settings: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_ENV_PATH.write_text(f"SPS_MODEL={settings['model']}\n", encoding="utf-8")


def write_config(settings: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    write_equalizer_ir_files(settings)
    if CONF_PATH.exists():
        shutil.copy2(CONF_PATH, CONF_PATH.with_name(CONF_PATH.name + ".bak"))
    tmp_path = CONF_PATH.with_name(CONF_PATH.name + ".tmp")
    tmp_path.write_text(generate_config(settings), encoding="utf-8")
    tmp_path.replace(CONF_PATH)


def read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, OSError):
        return ""


def discover_interfaces() -> list[dict[str, Any]]:
    net_dir = Path(os.environ.get("NET_CLASS_DIR", "/sys/class/net"))
    interfaces: list[dict[str, Any]] = []
    if not net_dir.exists():
        return interfaces

    for item in sorted(net_dir.iterdir(), key=lambda p: p.name):
        name = item.name
        if is_excluded_interface_name(name) or (item / "bridge").exists():
            continue
        operstate = read_text_file(item / "operstate").strip() or "unknown"
        address = read_text_file(item / "address").strip()
        carrier = read_text_file(item / "carrier").strip()
        speed = read_text_file(item / "speed").strip()
        interfaces.append(
            {
                "name": name,
                "operstate": operstate,
                "up": operstate == "up",
                "address": address,
                "carrier": carrier,
                "speed": speed if speed and speed != "-1" else "",
            }
        )
    return interfaces


def parse_cards() -> dict[int, dict[str, str]]:
    cards: dict[int, dict[str, str]] = {}
    cards_text = read_text_file(Path("/proc/asound/cards"))
    for line in cards_text.splitlines():
        match = re.match(r"^\s*(\d+)\s+\[([^\]]+)\]\s*:\s*([^-]+)-\s*(.+)$", line)
        if not match:
            continue
        card_number = int(match.group(1))
        cards[card_number] = {
            "id": match.group(2).strip(),
            "driver": match.group(3).strip(),
            "name": match.group(4).strip(),
        }
    return cards


def add_audio_device(devices: list[dict[str, Any]], seen: set[str], card_number: int, device_number: int, label: str) -> None:
    device_id = f"hw:{card_number},{device_number}"
    if device_id in seen:
        return
    devices.append({"id": device_id, "label": label, "card": card_number, "device": device_number})
    seen.add(device_id)


def discover_aplay_devices() -> list[dict[str, Any]]:
    try:
        completed = subprocess.run(["aplay", "-l"], capture_output=True, text=True, timeout=5, check=False)
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    return parse_aplay_devices(completed.stdout)


def parse_aplay_devices(text: str) -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    seen: set[str] = set()
    pattern = re.compile(
        r"^\s*card\s+(\d+):\s*([^\[]+?)\s*\[([^\]]+)\],\s*device\s+(\d+):\s*([^\[]+?)\s*\[([^\]]+)\]"
    )
    for line in text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        card_number = int(match.group(1))
        device_number = int(match.group(4))
        card_label = match.group(3).strip() or match.group(2).strip()
        pcm_label = match.group(6).strip() or match.group(5).strip()
        label = f"hw:{card_number},{device_number} · {card_label}"
        if pcm_label and pcm_label not in label:
            label = f"{label} · {pcm_label}"
        add_audio_device(devices, seen, card_number, device_number, label)
    return devices


def discover_audio_devices() -> list[dict[str, Any]]:
    cards = parse_cards()
    pcm_text = read_text_file(Path("/proc/asound/pcm"))
    devices: list[dict[str, Any]] = [{"id": "default", "label": "default · ALSA 默认输出", "card": None, "device": None}]
    seen = {"default"}

    for device in discover_aplay_devices():
        devices.append(device)
        seen.add(device["id"])

    for line in pcm_text.splitlines():
        if "playback" not in line:
            continue
        match = re.match(r"^\s*(\d+)-(\d+):\s*(.*?)\s*:\s*(.*?)\s*:\s*playback", line)
        if not match:
            continue
        card_number = int(match.group(1))
        device_number = int(match.group(2))
        device_id = f"hw:{card_number},{device_number}"
        if device_id in seen:
            continue
        card = cards.get(card_number, {})
        pcm_label = (match.group(4) or match.group(3)).strip()
        card_label = card.get("name") or card.get("id") or f"Card {card_number}"
        label = f"{device_id} · {card_label}"
        if pcm_label and pcm_label not in label:
            label = f"{label} · {pcm_label}"
        add_audio_device(devices, seen, card_number, device_number, label)
    return devices


def card_from_alsa_device(device: str) -> str | None:
    match = re.match(r"^(?:plug)?hw:([^,:\s]+)", device or "")
    if match:
        return match.group(1)
    return None


def discover_mixer_controls(audio_device: str) -> list[str]:
    card = card_from_alsa_device(audio_device)
    command = ["amixer"]
    if card:
        command.extend(["-c", card])
    command.append("scontrols")
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    controls: list[str] = []
    for line in completed.stdout.splitlines():
        match = re.search(r"Simple mixer control '(.+)',\d+", line)
        if match:
            controls.append(match.group(1))
    return controls


def build_warnings(settings: dict[str, Any], interfaces: list[dict[str, Any]], audio_devices: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    _ = interfaces
    if len(audio_devices) <= 1:
        warnings.append("没有检测到明确的 ALSA 播放设备；HDMI 只有接上显示/功放后才会出现，USB DAC 请确认已插入。")
    elif settings["audio_device"] not in {device["id"] for device in audio_devices}:
        warnings.append("当前保存的音频设备不在检测列表里，请重新选择 USB DAC 或 HDMI 输出。")
    if settings["model"] != "ShairportSync":
        warnings.append("图标只是机型标识伪装，不会获得 Siri、HomePod 立体声组队或 Apple TV 默认输出能力。")
    return warnings


def docker_container_name(container: Any) -> str:
    return str(getattr(container, "name", "") or "").lstrip("/")


def docker_container_labels(container: Any) -> dict[str, str]:
    labels = getattr(container, "labels", None)
    if isinstance(labels, dict):
        return {str(key): str(value) for key, value in labels.items()}
    attrs = getattr(container, "attrs", {}) or {}
    config = attrs.get("Config", {}) if isinstance(attrs, dict) else {}
    labels = config.get("Labels", {}) if isinstance(config, dict) else {}
    return {str(key): str(value) for key, value in (labels or {}).items()}


def docker_container_image_refs(container: Any) -> list[str]:
    refs: list[str] = []
    image = getattr(container, "image", None)
    tags = getattr(image, "tags", None)
    if tags:
        refs.extend(str(tag) for tag in tags)
    attrs = getattr(container, "attrs", {}) or {}
    config = attrs.get("Config", {}) if isinstance(attrs, dict) else {}
    image_ref = config.get("Image") if isinstance(config, dict) else None
    if image_ref:
        refs.append(str(image_ref))
    return list(dict.fromkeys(refs))


def shairport_container_score(container: Any) -> int:
    name = docker_container_name(container).lower()
    labels = docker_container_labels(container)
    service = labels.get("com.docker.compose.service", "").lower()
    image_refs = [ref.lower() for ref in docker_container_image_refs(container)]
    if service == "webui" or "webui" in name:
        return -1
    if any("airplay-panel" in ref or "webui" in ref for ref in image_refs):
        return -1

    score = 0
    if service == "shairport-sync":
        score += 100
    if any(hint in ref for hint in AIRPLAY_IMAGE_HINTS for ref in image_refs):
        score += 80
    if name == CONTAINER_NAME.lower() or name.endswith(f"-{CONTAINER_NAME.lower()}-1"):
        score += 50
    if getattr(container, "status", "") == "running":
        score += 10
    return score


def choose_shairport_container(containers: list[Any]) -> Any | None:
    scored = [
        (shairport_container_score(container), docker_container_name(container), container)
        for container in containers
    ]
    candidates = [item for item in scored if item[0] > 0]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][2]


def resolve_shairport_container(client: Any) -> tuple[Any | None, dict[str, Any]]:
    try:
        return client.containers.get(CONTAINER_NAME), {
            "configured_container": CONTAINER_NAME,
            "container": CONTAINER_NAME,
            "resolved_by": "configured name",
        }
    except Exception as direct_exc:  # pragma: no cover - depends on host Docker state.
        try:
            containers = client.containers.list(all=True)
        except Exception as list_exc:  # pragma: no cover - depends on host Docker state.
            return None, {"error": f"{direct_exc}; also failed to list containers: {list_exc}"}

    container = choose_shairport_container(containers)
    if container is None:
        names = ", ".join(docker_container_name(item) for item in containers[:20])
        return None, {
            "error": f"No such container: {CONTAINER_NAME}. Visible containers: {names or '(none)'}",
            "configured_container": CONTAINER_NAME,
        }

    return container, {
        "configured_container": CONTAINER_NAME,
        "container": docker_container_name(container),
        "resolved_by": "auto discovery",
    }


def restart_shairport() -> dict[str, Any]:
    if docker is None:
        return {"ok": False, "error": "Docker SDK is not installed in the WebUI container."}
    try:
        client = docker.from_env()
        container, target = resolve_shairport_container(client)
        if container is None:
            return {"ok": False, **target}
        container.restart(timeout=int(os.environ.get("RESTART_TIMEOUT", "10")))
        return {"ok": True, **target}
    except Exception as exc:  # pragma: no cover - depends on host Docker state.
        return {"ok": False, "error": str(exc)}


def read_container_logs(lines: int = 120) -> dict[str, Any]:
    if docker is None:
        return {"ok": False, "logs": "", "error": "Docker SDK is not installed in the WebUI container."}
    try:
        client = docker.from_env()
        container, target = resolve_shairport_container(client)
        if container is None:
            return {"ok": False, "logs": "", **target}
        output = container.logs(tail=max(20, min(lines, 500)), timestamps=True)
        return {"ok": True, "logs": output.decode("utf-8", errors="replace"), **target}
    except Exception as exc:  # pragma: no cover - depends on host Docker state.
        return {"ok": False, "logs": "", "error": str(exc)}


def apply_hardware_volume(settings: dict[str, Any]) -> dict[str, Any]:
    control = settings.get("mixer_control_name")
    if not control:
        return {"ok": False, "error": "未选择硬件音量控件。"}
    card = card_from_alsa_device(settings.get("audio_device", ""))
    command = ["amixer"]
    if card:
        command.extend(["-c", card])
    command.extend(["sset", control, f"{settings['volume_percent']}%"])
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
    except FileNotFoundError:
        return {"ok": False, "error": "容器内没有 amixer，请确认 webui 镜像安装了 alsa-utils。"}
    except subprocess.SubprocessError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": completed.returncode == 0,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "returncode": completed.returncode,
    }


def request_payload() -> dict[str, Any]:
    if request.is_json:
        return request.get_json(silent=True) or {}
    return request.form.to_dict()


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return clean_inline_text(value).lower() in {"true", "1", "yes", "on"}


@app.get("/")
def index() -> str:
    return render_template("index.html")


@app.get("/api/state")
def api_state() -> Response:
    settings = load_settings()
    interfaces = discover_interfaces()
    audio_devices = discover_audio_devices()
    mixer_controls = discover_mixer_controls(settings["audio_device"])
    return jsonify(
        {
            "settings": settings,
            "models": MODELS,
            "equalizer_bands": EQ_BANDS,
            "interfaces": interfaces,
            "audio_devices": audio_devices,
            "mixer_controls": mixer_controls,
            "warnings": build_warnings(settings, interfaces, audio_devices),
            "config": read_text_file(CONF_PATH),
            "model_env": read_text_file(MODEL_ENV_PATH),
            "version": SHAIRPORT_SYNC_VERSION,
            "time": int(time.time()),
        }
    )


@app.post("/api/preview")
def api_preview() -> Response:
    settings = normalize_settings(request_payload())
    interfaces = discover_interfaces()
    audio_devices = discover_audio_devices()
    return jsonify(
        {
            "settings": settings,
            "config": generate_config(settings),
            "warnings": build_warnings(settings, interfaces, audio_devices),
        }
    )


@app.post("/api/save")
def api_save() -> Response:
    payload = request_payload()
    settings = normalize_settings(payload)
    write_config(settings)
    write_model_env(settings)
    save_settings(settings)

    volume_result = None
    if settings.get("mixer_control_name"):
        volume_result = apply_hardware_volume(settings)

    restart_result = None
    if truthy(payload.get("restart")):
        restart_result = restart_shairport()

    interfaces = discover_interfaces()
    audio_devices = discover_audio_devices()
    return jsonify(
        {
            "ok": True,
            "settings": settings,
            "config": generate_config(settings),
            "warnings": build_warnings(settings, interfaces, audio_devices),
            "volume": volume_result,
            "restart": restart_result,
        }
    )


@app.post("/api/restart")
def api_restart() -> Response:
    return jsonify(restart_shairport())


@app.get("/api/logs")
def api_logs() -> Response:
    lines = clamp_int(request.args.get("lines"), 20, 500, 120)
    return jsonify(read_container_logs(lines))


@app.get("/api/config")
def api_config() -> Response:
    return jsonify({"config": read_text_file(CONF_PATH), "model_env": read_text_file(MODEL_ENV_PATH)})


@app.get("/api/mixers")
def api_mixers() -> Response:
    audio_device = normalize_settings({"audio_device": request.args.get("device", "default")})["audio_device"]
    return jsonify({"audio_device": audio_device, "mixer_controls": discover_mixer_controls(audio_device)})


@app.post("/api/volume")
def api_volume() -> Response:
    settings = normalize_settings({**load_settings(), **request_payload()})
    # Realtime mixer changes are previews; only /api/save commits panel settings.
    return jsonify(apply_hardware_volume(settings))


if __name__ == "__main__":  # pragma: no cover
    app.run(host=PANEL_HOST, port=PANEL_PORT)
