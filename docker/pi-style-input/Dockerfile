ARG BASE=ppt-rl-pi-sandbox:20261104
FROM ${BASE}
# PPTX references are ZIP archives. The original sandbox already contains the
# pinned Python tools and Node binary; this layer adds the common archive CLI.
RUN apt-get update && apt-get install -y --no-install-recommends unzip zip \
    && rm -rf /var/lib/apt/lists/*
