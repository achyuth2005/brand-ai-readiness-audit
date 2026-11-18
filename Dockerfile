# Reproducible environment for the engine and its test suite.
#   docker build -t brand-ai-readiness-audit .
#   docker run --rm --network none brand-ai-readiness-audit                  # 115 offline tests
#   docker run --rm --network none brand-ai-readiness-audit \
#       python /app/examples/sample_audit.py                                  # offline sample audit
#
# Python 3.11 is the version the evaluation in evaluation/ ran on. The digest pins the exact
# multi-arch image (linux/amd64 and linux/arm64).
FROM python:3.11.18-slim-trixie@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# Code is owned by root and read-only to the runtime user; outputs go to the user's home.
COPY . .
RUN useradd --create-home --uid 10001 auditor
USER auditor
WORKDIR /home/auditor

CMD ["bash", "/app/tests/run_tests.sh"]
