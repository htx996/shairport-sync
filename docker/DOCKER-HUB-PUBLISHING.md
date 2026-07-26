# Publishing the Docker Images to Docker Hub

This fork has a GitHub Actions workflow —
[`.github/workflows/docker-hub-publish.yaml`](../.github/workflows/docker-hub-publish.yaml) —
that builds the two docker images in this folder and pushes them to Docker Hub.

| Image | Dockerfile | What it is |
| --- | --- | --- |
| `main` | [`docker/Dockerfile`](Dockerfile) | AirPlay 2 build, with NQPTP included |
| `classic` | [`docker/classic/Dockerfile`](classic/Dockerfile) | Classic (AirPlay 1) build |

## One-off setup

### 1. Create a Docker Hub access token

On Docker Hub, go to **Account Settings → Personal access tokens → Generate new
token**, give it **Read & Write** scope, and copy the token. It is shown only once.

### 2. Add the credentials to GitHub

In the GitHub repository, go to **Settings → Secrets and variables → Actions** and add:

| Kind | Name | Value |
| --- | --- | --- |
| Secret | `DOCKERHUB_USERNAME` | your Docker Hub account name, e.g. `htx996` |
| Secret | `DOCKERHUB_TOKEN` | the access token created above |
| Variable (optional) | `DOCKERHUB_IMAGE` | image name to push to; defaults to `htx996/shairport-sync` |

If the two secrets are missing, the workflow still runs and builds the images, but
it skips the push and leaves a warning in the run summary — so a misconfiguration
shows up as a warning rather than as a silent success.

### 3. Create the Docker Hub repository

Docker Hub creates the repository automatically on the first push of a private
image, but for a public image create it first at
<https://hub.docker.com/repositories> with the same name as `DOCKERHUB_IMAGE`.

## When it runs

* **Manually** — **Actions → Publish docker images to Docker Hub → Run workflow**.
  The manual run takes four inputs:
  * `variant` — build `both` images, or just `main` or `classic`
  * `platforms` — defaults to `linux/amd64,linux/arm64`; the full upstream set
    (`linux/386,linux/amd64,linux/arm/v7,linux/arm64`) is also offered, but it is
    emulated with QEMU and takes considerably longer
  * `image_tag` — override the tag that would otherwise be derived from the branch
  * `push` — untick it for a build-only test that never touches Docker Hub
* **Automatically** on a push to `master`, `development`, or
  `claude/github-docker-hub-integration-pfpf3p`, and on any git tag.

To build automatically from another branch as well, add its name to the `push:`
`branches:` list at the top of the workflow.

## Tags produced

The `classic` image gets the same tag as the `main` image with a `-classic` suffix,
except for `latest`, whose classic counterpart is `classic` — the same convention
the upstream project uses.

| Trigger | `main` tag | `classic` tag |
| --- | --- | --- |
| push to `master` | `rolling` | `rolling-classic` |
| push to `development` | `development` | `development-classic` |
| push to another listed branch | branch name, `/` replaced by `-` | same, `-classic` |
| push of git tag `X` | `X`, `latest` | `X-classic`, `classic` |
| manual run with `image_tag` | that tag | that tag, `-classic` |

## Running the published image

```bash
docker run -d --restart unless-stopped --net host --device /dev/snd \
  -v /etc/shairport-sync.conf:/etc/shairport-sync.conf \
  htx996/shairport-sync:rolling
```

See [`docker/README.md`](README.md) for the full set of run options and for the
`docker-compose` example.

## Note on the upstream docker workflow

The repository also carries the upstream workflow
[`docker-on-push-tag-or-pr.yaml`](../.github/workflows/docker-on-push-tag-or-pr.yaml).
It pushes to a registry configured through a different set of secrets
(`DOCKER_REGISTRY`, `DOCKER_REGISTRY_USER`, `DOCKER_REGISTRY_TOKEN`,
`DOCKER_IMAGE_NAME`) and it also triggers on pushes to `master` and on tags. In a
fork where those secrets are not set, its login step fails and the run is reported
as failed. Either leave it alone and ignore those runs, set that second set of
secrets too, or disable it in **Actions → Build and conditionally push docker image
→ ⋯ → Disable workflow**. It has been left untouched here so that merges from
upstream stay clean.

## Building locally instead

```bash
# AirPlay 2 image, current architecture only
docker build -f docker/Dockerfile --build-arg SHAIRPORT_SYNC_BRANCH=. \
  --build-arg NQPTP_BRANCH=main -t shairport-sync:local .

# classic image
docker build -f docker/classic/Dockerfile --build-arg SHAIRPORT_SYNC_BRANCH=. \
  -t shairport-sync:local-classic .
```

Both Dockerfiles build from the working tree (`SHAIRPORT_SYNC_BRANCH=.` with
`COPY . .`), so local, uncommitted changes are included.
