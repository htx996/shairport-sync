# UGREEN NAS Shairport Sync AirPlay 2 Panel

这是给 UGREEN NASync DXP4800 Pro（x86_64 / UGOS Pro / Debian 系）准备的非官方 shairport-sync AirPlay 2 接收端容器和 Web 配置面板。

## 交付内容

```text
.
├── docker-compose.yml
├── docker-compose.published.yml
├── build/
│   ├── Dockerfile
│   └── entrypoint.sh
├── webui/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app.py
│   ├── test_conf.py
│   └── templates/index.html
├── config/
│   ├── shairport-sync.conf
│   └── model.env
├── data/
│   └── settings.json
├── NOTICE
└── README.md
```

`config/` 和 `data/` 是宿主机运行数据，已经在 `.dockerignore` 里排除，不会被打进镜像。

## 启动

在 NAS 上进入项目目录：

```bash
docker compose up -d --build
```

面板默认监听宿主机 `8099`：

```text
http://NAS_IP:8099
```

首次启动时如果 `config/` 是空目录，AirPlay 容器会自动写入一个可启动的默认 `shairport-sync.conf` 和 `model.env`。进面板后再选择实际网口、音频输出和图标，保存并重启生效。

如果要启用 HTTP Basic 认证，在 `docker-compose.yml` 的 `webui.environment` 里设置：

```yaml
PANEL_USER: admin
PANEL_PASSWORD: "换成强密码"
```

## 已发布镜像部署

`docker-compose.published.yml` 默认使用 Docker Hub 镜像名：

```yaml
docker.io/${DOCKERHUB_NAMESPACE:-hanfu1997}/airplay:latest
docker.io/${DOCKERHUB_NAMESPACE:-hanfu1997}/airplay-panel:latest
```

发布到 Docker Hub 后可直接运行；如果以后换 namespace，再覆盖 `DOCKERHUB_NAMESPACE`：

```bash
docker compose -f docker-compose.published.yml up -d
```

## GitHub 编译并推送 Docker Hub

已经提供 GitHub Actions 工作流：

```text
.github/workflows/docker-publish.yml
```

触发方式：

- 推送到 `main` 或 `master`
- 推送 `v*` tag
- 手动运行 workflow
- PR 会只测试和构建，不推送 Docker Hub

发布顺序：

1. 跑 WebUI 配置生成测试。
2. 解析两份 compose YAML 并检查 host 网络、`SYS_NICE`、`/dev/snd`。
3. 下载最新上游 `shairport.c`，确认 `config.model = strdup("ShairportSync")` 仍能被补丁命中。
4. 构建 `linux/amd64` 的 shairport-sync 镜像。
5. 构建 `linux/amd64` 的 WebUI 镜像。
6. 只有前面成功，并且 Docker Hub secrets 存在，才推送到 Docker Hub。

在 GitHub 仓库的 `Settings -> Secrets and variables -> Actions` 添加：

```text
DOCKERHUB_USERNAME=你的 Docker Hub 用户名
DOCKERHUB_TOKEN=你的 Docker Hub access token
DOCKERHUB_NAMESPACE=镜像 namespace，可选；不填时使用 DOCKERHUB_USERNAME
```

推送后的镜像名：

```text
docker.io/hanfu1997/airplay:latest
docker.io/hanfu1997/airplay-panel:latest
docker.io/hanfu1997/airplay:<commit-sha>
docker.io/hanfu1997/airplay-panel:<commit-sha>
```

## 图标机制和限制

AirPlay 选择器不读取你提供的图片，它读取接收端上报的 model identifier，再由 iOS / macOS / tvOS 使用系统内置资源显示图标。

Docker 构建时会把 shairport-sync 源码里的硬编码：

```c
config.model = strdup("ShairportSync");
```

改成读取运行时环境变量：

```c
{ const char *_sps_m = getenv("SPS_MODEL");
  config.model = strdup((_sps_m && *_sps_m) ? _sps_m : "ShairportSync"); }
```

构建脚本会在 `sed` 前后用 `grep` 自检。没命中会直接失败，并打印 `config.model` 实际所在行，避免静默产出未打补丁的镜像。

可选标识符：

| 标识符 | 显示图标 |
|---|---|
| `ShairportSync` | 通用扬声器 |
| `AirPort10,115` | AirPort Express，推荐首选 |
| `AudioAccessory5,1` | HomePod mini |
| `AudioAccessory1,2` | HomePod 一代 |
| `AudioAccessory6,1` | HomePod 二代 |
| `AppleTV6,2` | Apple TV 4K |

这只是图标伪装，不会让设备变成真正的 HomePod / AirPort Express / Apple TV。不会获得 Siri、HomePod 立体声组队、设为 Apple TV 默认输出等能力。连接后图标有时退回通用扬声器是 Apple 客户端行为，服务端无法强制修复。

AirPlay 1 `_raop._tcp` 的 `am=` 字段仍是编译期静态 TXT 记录。默认不改；确实需要时可传 build arg：

```yaml
args:
  RAOP_AM_MODEL: "AirPort10,115"
```

## 网络和 avahi

AirPlay 2 需要 host 网络：

```yaml
network_mode: host
```

UGOS / 群晖这类 NAS 通常已经有宿主机 `avahi-daemon`。compose 默认挂载：

```yaml
- /var/run/dbus:/var/run/dbus
- /var/run/avahi-daemon/socket:/var/run/avahi-daemon/socket
```

容器启动时：

- 如果检测到 `/var/run/dbus/system_bus_socket`，复用宿主机 D-Bus / avahi，不启动容器内 avahi。
- 如果没有检测到，容器会自己启动 `dbus-daemon` 和 `avahi-daemon`。

启动日志会明确打印走的是哪条路。设备在 AirPlay 列表里时有时无时，先看这里是否出现双 avahi 或 5353 争用。

DXP4800 Pro 有双网口。面板会从 `/sys/class/net/` 列出物理网口，排除 `lo`、`docker*`、`br-*`、`veth*`，并显示 `operstate`。检测到多个网口时，请选择实际接线的那个，否则 Apple 设备可能看得到但连不上。

## 音频输出

DXP4800 Pro 没有 3.5mm 音频口，只能用 USB DAC 或 HDMI。

面板会读取：

```text
/proc/asound/cards
/proc/asound/pcm
```

并列出 `hw:1,0 · USB Audio DAC` 这类播放设备。HDMI 音频只有在 HDMI 那头真的接了显示器、功放或采集设备后才会出现在 ALSA 里。

如果想实时调硬件音量，必须选择当前声卡实际存在的 Mixer 控件，例如：

```text
PCM
Master
Digital
```

WebUI 容器安装了 `alsa-utils`，会用 `amixer` 应用硬件音量。

## 配置生成规则

面板不会用正则修改旧配置，而是每次生成完整 `shairport-sync.conf`。覆盖前会备份为：

```text
config/shairport-sync.conf.bak
```

写入前会做归一化和边界收敛：

- 设备名称最长 50 字符
- 字符串中的 `"` 和 `\` 会转义
- `volume_range_db` 夹到 30-150
- `volume_max_db` 夹到不高于 0
- `ignore_volume_control` 写成 `"yes"` / `"no"`
- 非法机型标识会回退到推荐默认值

附加配置文本框会原样追加到配置文件末尾，适合手动写 mqtt / dsp 等高级段落。这里的内容不做语义解析，请只写合法 libconfig。

## 验证

配置生成测试：

```bash
python3 -m pip install -r webui/requirements.txt
PYTHONPATH=webui pytest webui/test_conf.py
```

compose 语法检查：

```bash
docker compose config
```

启动后看日志：

```bash
docker logs shairport-sync
```

应能看到：

```text
shairport-sync model identifier: AirPort10,115
avahi mode: using host D-Bus/Avahi sockets; container avahi-daemon will not be started
```

在面板里切换图标、保存并重启后，再看日志里的 model identifier 是否跟着变化。

## 已知限制

- 图标由 Apple 客户端决定，服务端只能改变 model identifier。
- 图标伪装不会带来 Apple 专有能力。
- AirPlay 2 依赖 nqptp 独占 UDP 319/320；如果宿主机已有其他 PTP 服务会失败。
- host 网络下不能同时让宿主和容器各跑一套 avahi。
- 多网口必须指定实际接线网口。
- HDMI 音频设备不出现时，通常是 HDMI 端没有实际连接。
- 目标机器是 x86_64；Dockerfile 会拒绝非 amd64 构建，避免 QEMU 慢速模拟编译。
