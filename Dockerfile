FROM python:3.12-slim AS base
# libgomp is the OpenMP runtime LightGBM needs on Linux.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH \
    CARBON_CHARGE_DATA_DIR=/data \
    CARBON_CHARGE_REPORTS_DIR=/reports

FROM base AS app
RUN uv sync --frozen --no-dev
ENTRYPOINT ["carbon-charge"]
CMD ["--help"]

FROM base AS test
RUN uv sync --frozen
COPY tests ./tests
ENTRYPOINT ["pytest"]
