FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGENT_HTTP_SERVER_INSTANCES=1 \
    AGENT_REQUEST_LOGGING_INPUT_SLICE=0

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 10001 colleague

COPY --chown=colleague:colleague . .

RUN mkdir -p /data && chown colleague:colleague /data

USER colleague

EXPOSE 8080

CMD ["python", "-m", "scripts.start_server"]
