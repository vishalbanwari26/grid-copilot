# Cortex (grid-copilot's zero-dependency orchestration core) is an unpublished
# sibling repo, not a PyPI package. Rather than using the parent directory as
# the whole build context (which would tar up every unrelated sibling repo
# next to grid-copilot), Cortex is pulled in as a separate named build
# context, so the primary context stays just grid-copilot/ itself:
#
#   docker build -f Dockerfile --build-context cortexsrc=../cortex -t grid-copilot:latest .
#
# run from inside grid-copilot/. docker-compose.yml already sets this up, so
# `docker compose up` handles it for you.

FROM python:3.11-slim AS builder
WORKDIR /build
ARG EXTRAS=web,observability,groq

# Cortex: package source plus its own pyproject.toml, installed non-editable
# ("a copied install is clean" - it is zero-dependency itself, so this is a
# plain, reproducible install, not a live symlink into the sibling checkout).
COPY --from=cortexsrc cortex /build/cortex/cortex
COPY --from=cortexsrc pyproject.toml /build/cortex/pyproject.toml
COPY --from=cortexsrc README.md /build/cortex/README.md
RUN pip install --no-cache-dir --prefix=/install /build/cortex

COPY grid_copilot /build/grid-copilot/grid_copilot
COPY eval /build/grid-copilot/eval
COPY pyproject.toml /build/grid-copilot/pyproject.toml
COPY README.md /build/grid-copilot/README.md
COPY LICENSE /build/grid-copilot/LICENSE
RUN pip install --no-cache-dir --prefix=/install "/build/grid-copilot[${EXTRAS}]"

FROM python:3.11-slim AS runtime
RUN useradd --create-home --uid 1000 grid
COPY --from=builder /install /usr/local
COPY grid_copilot /app/grid_copilot
COPY eval /app/eval
WORKDIR /app
USER grid
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s CMD python -c \
    "import urllib.request as u; u.urlopen('http://localhost:8000/health', timeout=2)" || exit 1
CMD ["uvicorn", "grid_copilot.server:app", "--host", "0.0.0.0", "--port", "8000"]
