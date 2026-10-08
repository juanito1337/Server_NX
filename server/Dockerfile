FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY requirements-runtime.txt ./requirements-runtime.txt
RUN python -m pip install --no-cache-dir --only-binary=:all: -r requirements-runtime.txt \
    && groupadd --gid 10001 nxapp \
    && useradd --uid 10001 --gid nxapp --no-create-home --shell /usr/sbin/nologin nxapp \
    && mkdir -p /data/r \
    && chown -R nxapp:nxapp /data

COPY nxserver ./nxserver
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import json,urllib.request; r=urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:8765/health',timeout=3); assert json.load(r)['status']=='ok'"
ENTRYPOINT ["python", "-m", "nxserver"]
CMD ["--settings", "/app/config/settings.json", "--host", "0.0.0.0", "--port", "8765"]
