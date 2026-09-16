# Official Python 3.12.14, immutable multi-platform index; build for linux/amd64.
FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src
WORKDIR /app
COPY deploy/requirements-hosted.txt /app/requirements-hosted.txt
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements-hosted.txt \
    && groupadd --gid 10001 msf && useradd --uid 10001 --gid 10001 --no-create-home msf
COPY src /app/src
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()"
ENTRYPOINT ["python", "-m", "msf_assistant", "hosted"]
CMD ["serve", "--host", "0.0.0.0"]
