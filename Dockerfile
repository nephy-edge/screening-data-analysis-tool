# Container image for the ECS Express deployment.
#
# ALWAYS build with --platform linux/amd64: ECS Express Mode task definitions
# are X86_64, and an arm64 image will pull and then fail to start.
#
# Runbook (in the terraform repo): ecs-express/RUNBOOK-screening-tool.md
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# No apt-get step on purpose. Nothing here needs a system package -- the
# HEALTHCHECK below uses Python's stdlib instead of curl -- so the image stays
# smaller and the build has one less network dependency and one less layer.

# Dependencies first so the layer caches across code changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py theme.py ./
# app.py does sys.path.insert(0, <dir>/scripts), so this layout is load-bearing:
# rental_analysis and template_analysis must stay under ./scripts.
COPY scripts/ ./scripts/
COPY .streamlit/config.toml ./.streamlit/config.toml
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# certs/ is deliberately NOT copied. It holds a corporate root CA for laptops
# behind TLS interception (Zscaler and similar) and is gitignored, so it is
# absent from a clean checkout anyway. _ca_bundle_path() in app.py falls back
# to certifi.where() when it is missing, which is correct inside a container.
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
 && useradd --create-home --uid 10001 app \
 && chown -R app:app /app
USER app

EXPOSE 8501

# ECS does NOT use this: the Express-managed target group health-checks
# /_stcore/health over the network. Kept so a local `docker run` reports
# honestly. Uses urllib rather than curl so the image needs no apt packages.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request as u; exit(0 if u.urlopen('http://localhost:8501/_stcore/health', timeout=3).status == 200 else 1)"]

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]
