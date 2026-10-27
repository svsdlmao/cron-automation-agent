# Real-cron integration test: Ubuntu + the cron daemon + this project.
#   docker build -f docker/Dockerfile -t cron-agent .
#   docker run --rm cron-agent
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends cron python3 python3-pip procps ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home demo

WORKDIR /app
COPY pyproject.toml README.md ./
COPY cron_agent ./cron_agent
RUN pip3 install --no-cache-dir ".[dev]"
COPY tests ./tests
COPY eval ./eval
COPY docker/integration.sh docker/live_cron_check.py ./docker/
RUN chown -R demo /app

CMD ["bash", "docker/integration.sh"]
