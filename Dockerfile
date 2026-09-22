# Container image for the ECS Express deployment.
#
# ALWAYS build with --platform linux/amd64: ECS Express Mode task definitions
# are X86_64, and an arm64 image will pull fine and then fail to start.
#
# Runbook (lendableio/terraform): ecs-express/RUNBOOK-screening-tool.md
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# No apt-get step on purpose: nothing here needs a system package -- the
# HEALTHCHECK below uses Python's stdlib instead of curl -- so the image
# stays smaller and the build has one less network dependency and one less
# layer.

# Dependencies first so the layer caches across code changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py theme.py config.py config.toml ./
# app.py does sys.path.insert(0, <dir>/scripts) and .../rental_and_subscription,
# so both layouts are load-bearing.
COPY scripts/ ./scripts/
COPY rental_and_subscription/ ./rental_and_subscription/
COPY .streamlit/config.toml ./.streamlit/config.toml
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# certs/ and docs/ are deliberately NOT copied. certs/corporate_root.pem
# holds a corporate root CA for laptops behind TLS interception (Zscaler and
# similar) - _ca_bundle_path() in scripts/ca_bundle.py falls back to
# certifi.where() when it's missing, which is correct inside a container.
# docs/ holds raw client workbook data and must never reach an image.

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
