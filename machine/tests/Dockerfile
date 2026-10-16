# A clean Ubuntu 24.04 plus what every real data machine already has from its
# provisioning: sudo and a non-root user who may use it. Without the image's
# docker-clean hook apt keeps the packages it downloads, as it does on a real
# machine, so run.sh can mount a package cache that outlives the container.
FROM ubuntu:24.04
RUN apt-get update \
    && apt-get install -y --no-install-recommends sudo \
    && rm -rf /var/lib/apt/lists/* /etc/apt/apt.conf.d/docker-clean \
    && useradd -m -s /bin/bash agent \
    && echo 'agent ALL=(ALL) NOPASSWD:ALL' >/etc/sudoers.d/agent
USER agent
WORKDIR /home/agent
