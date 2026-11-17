FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv python3-pip ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/planning
COPY requirements.txt ./planning-requirements.txt
COPY ubuntu/flight/requirements.txt ./requirements.txt
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt -r planning-requirements.txt
COPY app ./app
COPY static ./static
COPY ubuntu/flight/app ./ubuntu/flight/app
COPY ubuntu/flight/tests ./ubuntu/flight/tests
WORKDIR /opt/planning/ubuntu/flight
EXPOSE 8772
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8772"]
