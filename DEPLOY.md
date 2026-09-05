# 部署指南

本文档面向 UGREEN NASync DXP4800 Pro / UGOS Pro，也适用于能运行 Docker Compose 的 x86_64 Debian 系 NAS。

## 镜像

当前发布镜像：

```text
hanfu1997/airplay:latest
hanfu1997/airplay-panel:latest
```

两个镜像职责不同：

- `hanfu1997/airplay`：AirPlay 2 接收端，包含 nqptp 和打了 `SPS_MODEL` 补丁的 shairport-sync。
- `hanfu1997/airplay-panel`：Web 配置面板，负责生成配置、调硬件音量、重启接收端。

## 两种部署方式

- 方法一：Docker Compose 部署。推荐，配置最完整，也最容易复现。
- 方法二：GUI 图形界面搜索镜像部署。适合只想在 NAS Docker 管理器里操作的情况，但必须手动补齐 host 网络、设备、挂载、权限和环境变量。

## 方法一：Docker Compose 部署（推荐）

先在 NAS 上创建一个持久化目录。下面用 `/volume1/docker/airplay` 举例；如果你的 UGOS 实际路径不同，把它替换成你自己的真实路径。

```bash
APP_DIR=/volume1/docker/airplay
mkdir -p "$APP_DIR/config" "$APP_DIR/data"
cd "$APP_DIR"
```

创建 `docker-compose.yml`：

```yaml
services:
  shairport-sync:
    image: hanfu1997/airplay:latest
    pull_policy: always
    container_name: airplay
    platform: linux/amd64
    network_mode: host
    restart: unless-stopped
    cap_add:
      - SYS_NICE
    devices:
      - /dev/snd:/dev/snd
    volumes:
      - ./config:/config
      - /var/run/dbus:/var/run/dbus
      - /var/run/avahi-daemon/socket:/var/run/avahi-daemon/socket
    environment:
      TZ: Asia/Shanghai
    logging:
      options:
        max-size: "200k"
        max-file: "5"

  webui:
    image: hanfu1997/airplay-panel:latest
    pull_policy: always
    container_name: airplay-panel
    platform: linux/amd64
    network_mode: host
    restart: unless-stopped
    devices:
      - /dev/snd:/dev/snd
    volumes:
      - ./config:/config
      - ./data:/data
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      TZ: Asia/Shanghai
      SHAIRPORT_CONTAINER: airplay
      PANEL_HOST: 0.0.0.0
      PANEL_PORT: "18090"
      # PANEL_USER: admin
      # PANEL_PASSWORD: "请替换为你自己的密码"
    depends_on:
      - shairport-sync
    logging:
      options:
        max-size: "200k"
        max-file: "5"
```

部署前先检查 YAML：

```bash
docker compose config
```

启动：

```bash
docker compose up -d
```

查看日志：

```bash
docker compose logs -f
```

打开面板：

```text
http://NAS_IP:18090
```

第一次进入面板后建议按顺序设置：

1. 选择 AirPlay 图标。
2. 选择 ALSA 输出设备，例如 `hw:1,0 · US05 · USB Audio`。
3. 如果要播放端控制音量，保持 `忽略 iOS 音量` 关闭。
4. `音量范围` 建议先用 `60 dB`；如果播放端音量归零后仍能听到，可以改成 `90 dB`。
5. 点击保存并重启。

## 环境变量说明

AirPlay 接收端容器：

```text
TZ=Asia/Shanghai
```

Web 面板容器：

```text
TZ=Asia/Shanghai
SHAIRPORT_CONTAINER=airplay
PANEL_HOST=0.0.0.0
PANEL_PORT=18090
```

可选认证：

```text
PANEL_USER=admin
PANEL_PASSWORD=换成强密码
```

不要在 compose 或 GUI 里手工配置 `SPS_MODEL`。图标型号由 Web 面板写入 `config/model.env`，接收端启动时读取它。

## 方法一补充：使用仓库自带 compose 文件

如果你是从 GitHub 克隆本仓库，也可以直接使用：

```bash
git clone https://github.com/htx996/shairport-sync.git
cd shairport-sync
docker compose -f docker-compose.published.yml config
docker compose -f docker-compose.published.yml up -d
```

`docker-compose.published.yml` 默认使用：

```text
hanfu1997/airplay:latest
hanfu1997/airplay-panel:latest
```

## 验证声卡直通和重新部署

USB 声卡先连接 NAS，并用宿主机的 `aplay -l` 确认已识别。再检查两个容器的实际设备映射和声卡列表：

```bash
docker inspect airplay airplay-panel --format '{{.Name}} devices={{json .HostConfig.Devices}}'
docker exec airplay aplay -l
docker exec airplay-panel aplay -l
```

两个容器的设备映射都应包含 `/dev/snd`。只有 `/dev/fuse` 不代表已映射音频设备。如果宿主机能识别 USB 声卡，但容器看不到，请确认两个服务都有 `devices: ["/dev/snd:/dev/snd"]`，再在原项目目录重新创建容器：

```bash
docker compose config --quiet
docker compose up -d --force-recreate --pull never --no-build
```

先确认第一条校验成功，再执行第二条。这次使用已下载的镜像，短暂重启服务；保持原项目目录以及 `./config`、`./data` 挂载路径不变。使用仓库文件时，两条命令都要在 `docker compose` 后加上 `-f docker-compose.published.yml`。只点“重启”不会应用修改后的设备映射。

GUI 部署也应在设备设置中添加 `/dev/snd -> /dev/snd`，重新创建后使用上述命令核实。容器识别声卡后刷新 Web 页面，按设备名称选择 USB 音频输出；编号可能随插拔或重启变化，不要固定沿用旧编号。

## 更新镜像

本项目会每小时自动检测上游 `mikebrady/shairport-sync` 和 `mikebrady/nqptp` 的新 commit，同时读取 `shairport-sync` 最新 Release 版本号。检测到 commit 或官方版本号变化后会更新 `upstream-versions.json`，再触发 GitHub Actions 重新编译并发布 Docker Hub 镜像。

如果上游没有变化，不会重复编译。如果上游改动导致 `SPS_MODEL` 补丁匹配失败，Actions 会失败并停止发布，避免推送不可用镜像。

发布配置为两个镜像设置了 `pull_policy: always`，部署时会检查并拉取远端镜像。仅修复设备映射、使用本地镜像时，可使用上面的 `--pull never --no-build` 命令覆盖本次拉取策略。

进入 compose 所在目录：

```bash
docker compose pull
docker compose up -d --force-recreate
```

注意：更新镜像不会覆盖你已经保存过的 `config/` 和 `data/`。如果旧配置里已经写入过不想要的高级段落，进入 Web 面板点一次保存并重启，让面板重新生成配置。

确认配置里没有启用 DSP 卷积：

```bash
grep -nE 'dsp|convolution|equalizer' ./config/shairport-sync.conf
```

正常情况下应没有输出。

## 方法二：GUI 图形界面搜索镜像下载部署

如果你想在 UGOS / NAS Docker 图形界面里直接搜索镜像，也可以，但必须把网络、设备、挂载和容器名配置完整。只下载镜像不会自动创建可用服务。

先在镜像页面搜索并下载：

```text
hanfu1997/airplay
hanfu1997/airplay-panel
```

### 容器一：AirPlay 接收端

创建容器：

```text
容器名：airplay
镜像：hanfu1997/airplay:latest
网络：host
重启策略：unless-stopped / 总是重启
平台：linux/amd64
```

环境变量：

```text
TZ=Asia/Shanghai
```

设备：

```text
/dev/snd -> /dev/snd
```

权限：

```text
SYS_NICE
```

如果图形界面没有单独的 `SYS_NICE` 选项，首选改用 Compose 部署；有些 NAS UI 的“特权模式”也能绕过这个问题，但权限范围更大，不作为首选。

目录挂载：

```text
你的持久化目录/config -> /config
/var/run/dbus -> /var/run/dbus
/var/run/avahi-daemon/socket -> /var/run/avahi-daemon/socket
```

### 容器二：Web 面板

创建容器：

```text
容器名：airplay-panel
镜像：hanfu1997/airplay-panel:latest
网络：host
重启策略：unless-stopped / 总是重启
平台：linux/amd64
```

设备：

```text
/dev/snd -> /dev/snd
```

目录挂载：

```text
你的持久化目录/config -> /config
你的持久化目录/data -> /data
/var/run/docker.sock -> /var/run/docker.sock
```

环境变量：

```text
TZ=Asia/Shanghai
SHAIRPORT_CONTAINER=airplay
PANEL_HOST=0.0.0.0
PANEL_PORT=18090
```

可选认证：

```text
PANEL_USER=admin
PANEL_PASSWORD=换成强密码
```

启动两个容器后访问：

```text
http://NAS_IP:18090
```

## 必须满足的运行条件

- 必须使用 host 网络，否则 AirPlay 2 / mDNS 发现容易失败。
- `/dev/snd` 必须直通，否则容器看不到 USB DAC / HDMI 音频设备。
- `shairport-sync` 容器需要 `SYS_NICE`，否则音频线程可能提不了优先级。
- `/var/run/dbus` 和 `/var/run/avahi-daemon/socket` 建议挂载宿主机路径，避免 host 网络下双 avahi 抢 UDP 5353。
- `nqptp` 需要独占 UDP 319 和 320。
- Web 面板需要挂载 `/var/run/docker.sock`，否则不能一键重启 `shairport-sync` 容器。

## 常用排查命令

检查播放设备：

```bash
aplay -l
```

查看接收端日志：

```bash
docker logs -f --tail=120 airplay
```

查看 Web 面板日志：

```bash
docker logs -f --tail=120 airplay-panel
```

检查是否生成配置：

```bash
ls -l ./config
cat ./config/shairport-sync.conf
cat ./config/model.env
```

检查图标标识：

```bash
docker logs --tail=80 airplay | grep 'model identifier'
```

检查 avahi 模式：

```bash
docker logs --tail=80 airplay | grep 'avahi mode'
```
