from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import subprocess
import time
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
PANEL_HOST = os.environ.get("PANEL_HOST", "0.0.0.0")
PANEL_PORT = int(os.environ.get("PANEL_PORT", "8099"))

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

DEFAULTS: dict[str, Any] = {
    "name": "UGREEN AirPlay",
    "model": "AirPort10,115",
    "interface": "",
    "audio_device": "default",
    "mixer_control_name": "",
    "volume_percent": 70,
    "volume_range_db": 60,
    "volume_max_db": 0.0,
    "ignore_volume_control": "no",
    "additional_config": "",
}

IFACE_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
ALSA_DEVICE_RE = re.compile(r"^(default|(?:plug)?hw:[A-Za-z0-9_.:-]+(?:,[A-Za-z0-9_.:-]+)?|sysdefault(?::[A-Za-z0-9_.:-]+)?)$")

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


def normalize_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    source = {**DEFAULTS, **(raw or {})}

    name = clean_inline_text(source.get("name"))
    if not name:
        name = DEFAULTS["name"]
    name = name[:50]

    model = clean_inline_text(source.get("model"))
    if model not in MODEL_IDS:
        model = DEFAULTS["model"]

    interface = clean_inline_text(source.get("interface"))
    if interface and not IFACE_RE.match(interface):
        interface = ""

    audio_device = clean_inline_text(source.get("audio_device"))
    if not audio_device:
        audio_device = DEFAULTS["audio_device"]
    if not ALSA_DEVICE_RE.match(audio_device):
        audio_device = DEFAULTS["audio_device"]

    mixer_control_name = clean_inline_text(source.get("mixer_control_name"))[:80]
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
        "additional_config": additional_config,
    }


def format_float(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text if "." in text else f"{text}.0"


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
    if settings["interface"]:
        lines.append(f'  interface = "{escape_libconfig_string(settings["interface"])}";')
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
    excluded_prefixes = ("docker", "br-", "veth")
    interfaces: list[dict[str, Any]] = []
    if not net_dir.exists():
        return interfaces

    for item in sorted(net_dir.iterdir(), key=lambda p: p.name):
        name = item.name
        if name == "lo" or name.startswith(excluded_prefixes):
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


def discover_audio_devices() -> list[dict[str, Any]]:
    cards = parse_cards()
    pcm_text = read_text_file(Path("/proc/asound/pcm"))
    devices: list[dict[str, Any]] = [{"id": "default", "label": "default · ALSA 默认输出", "card": None, "device": None}]
    seen = {"default"}

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
        devices.append({"id": device_id, "label": label, "card": card_number, "device": device_number})
        seen.add(device_id)
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
    physical_interfaces = [iface for iface in interfaces if iface["name"]]
    if len(physical_interfaces) > 1 and not settings["interface"]:
        warnings.append("检测到多个物理网口，必须选择实际接线的广播网口，否则可能看得到但连不上。")
    if settings["interface"] and settings["interface"] not in {iface["name"] for iface in physical_interfaces}:
        warnings.append("当前保存的网口不在检测列表里，请重新选择。")
    if len(audio_devices) <= 1:
        warnings.append("没有检测到明确的 ALSA 播放设备；HDMI 只有接上显示/功放后才会出现，USB DAC 请确认已插入。")
    elif settings["audio_device"] not in {device["id"] for device in audio_devices}:
        warnings.append("当前保存的音频设备不在检测列表里，请重新选择 USB DAC 或 HDMI 输出。")
    if settings["model"] != "ShairportSync":
        warnings.append("图标只是机型标识伪装，不会获得 Siri、HomePod 立体声组队或 Apple TV 默认输出能力。")
    return warnings


def restart_shairport() -> dict[str, Any]:
    if docker is None:
        return {"ok": False, "error": "Docker SDK is not installed in the WebUI container."}
    try:
        client = docker.from_env()
        container = client.containers.get(CONTAINER_NAME)
        container.restart(timeout=int(os.environ.get("RESTART_TIMEOUT", "10")))
        return {"ok": True, "container": CONTAINER_NAME}
    except Exception as exc:  # pragma: no cover - depends on host Docker state.
        return {"ok": False, "error": str(exc)}


def read_container_logs(lines: int = 120) -> dict[str, Any]:
    if docker is None:
        return {"ok": False, "logs": "", "error": "Docker SDK is not installed in the WebUI container."}
    try:
        client = docker.from_env()
        container = client.containers.get(CONTAINER_NAME)
        output = container.logs(tail=max(20, min(lines, 500)), timestamps=True)
        return {"ok": True, "logs": output.decode("utf-8", errors="replace")}
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
            "interfaces": interfaces,
            "audio_devices": audio_devices,
            "mixer_controls": mixer_controls,
            "warnings": build_warnings(settings, interfaces, audio_devices),
            "config": read_text_file(CONF_PATH),
            "model_env": read_text_file(MODEL_ENV_PATH),
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
    save_settings(settings)
    return jsonify(apply_hardware_volume(settings))


if __name__ == "__main__":  # pragma: no cover
    app.run(host=PANEL_HOST, port=PANEL_PORT)
