FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    PLANNING_BASE_URL=http://planning:8767 \
    FLIGHT_BASE_URL=http://flight:8772 \
    LOWAIR_BASE_URL=http://lowair:8771
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv python3-pip ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /tests
COPY requirements.txt ./
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
COPY tests /tests
CMD ["pytest", "-q", "/tests"]
