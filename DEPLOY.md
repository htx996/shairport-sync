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
name: shairport-sync-panel

services:
  shairport-sync:
    image: docker.io/hanfu1997/airplay:latest
    container_name: shairport-sync
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
    logging:
      options:
        max-size: "500k"
        max-file: "5"

  webui:
    image: docker.io/hanfu1997/airplay-panel:latest
    container_name: shairport-sync-webui
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
      SHAIRPORT_CONTAINER: shairport-sync
      PANEL_HOST: 0.0.0.0
      PANEL_PORT: "8099"
      # PANEL_USER: admin
      # PANEL_PASSWORD: "change-me"
    depends_on:
      - shairport-sync
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
http://NAS_IP:8099
```

第一次进入面板后建议按顺序设置：

1. 选择 AirPlay 图标。
2. 选择 ALSA 输出设备，例如 `hw:1,0 · US05 · USB Audio`。
3. 如果要播放端控制音量，保持 `忽略 iOS 音量` 关闭。
4. `音量范围` 建议先用 `60 dB`；如果播放端音量归零后仍能听到，可以改成 `90 dB`。
5. 点击保存并重启。

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

## 更新镜像

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
容器名：shairport-sync
镜像：hanfu1997/airplay:latest
网络：host
重启策略：unless-stopped / 总是重启
平台：linux/amd64
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
容器名：shairport-sync-webui
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
SHAIRPORT_CONTAINER=shairport-sync
PANEL_HOST=0.0.0.0
PANEL_PORT=8099
```

可选认证：

```text
PANEL_USER=admin
PANEL_PASSWORD=换成强密码
```

启动两个容器后访问：

```text
http://NAS_IP:8099
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
docker logs -f --tail=120 shairport-sync
```

查看 Web 面板日志：

```bash
docker logs -f --tail=120 shairport-sync-webui
```

检查是否生成配置：

```bash
ls -l ./config
cat ./config/shairport-sync.conf
cat ./config/model.env
```

检查图标标识：

```bash
docker logs --tail=80 shairport-sync | grep 'model identifier'
```

检查 avahi 模式：

```bash
docker logs --tail=80 shairport-sync | grep 'avahi mode'
```
