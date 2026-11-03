FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash ripgrep curl ca-certificates fontconfig fonts-dejavu-core fonts-liberation2 \
    libgomp1 libcairo2 graphviz && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt
# Supply an existing Linux Node >=22.19 binary in the build context.
COPY node /usr/local/bin/node
RUN node --version && python -c "import pptx,fitz,matplotlib,pandas,shapely"
ENV PYTHONDONTWRITEBYTECODE=1 \
    TMPDIR=/workspace/work/scratch \
    XDG_CACHE_HOME=/workspace/work/scratch/cache \
    XDG_CONFIG_HOME=/workspace/work/scratch/config \
    XDG_DATA_HOME=/workspace/work/scratch/data \
    XDG_STATE_HOME=/workspace/work/scratch/state \
    MPLCONFIGDIR=/workspace/work/scratch/cache/matplotlib \
    PIP_CACHE_DIR=/workspace/work/scratch/cache/pip \
    PIP_TARGET=/workspace/work/scratch/python-packages \
    PYTHONPATH=/workspace/work/scratch/python-packages \
    npm_config_cache=/workspace/work/scratch/cache/npm \
    HF_HOME=/workspace/work/scratch/cache/huggingface \
    HF_HUB_CACHE=/workspace/work/scratch/cache/huggingface/hub \
    SHELL=/bin/bash
WORKDIR /workspace/work
