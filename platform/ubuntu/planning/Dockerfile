FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/planning \
    SENSE_DATA_DIR=/tmp/sense-data \
    PATH=/opt/venv/bin:$PATH
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv python3-pip ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/planning
COPY requirements-dev.txt requirements.txt ./
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r requirements-dev.txt
COPY app ./app
COPY static ./static
COPY tests ./tests
EXPOSE 8767
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8767"]
