FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-impress fonts-dejavu fonts-liberation fonts-noto-cjk && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["libreoffice"]
