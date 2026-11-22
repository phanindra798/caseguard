# Build context: the repository root (the agent needs ../policies).
FROM python:3.12-slim
WORKDIR /app
RUN useradd --system --uid 10001 caseguard
COPY agent/pyproject.toml ./
COPY agent/caseguard_agent/ caseguard_agent/
RUN pip install --no-cache-dir ".[tracing]"
COPY policies/ /app/policies/
ENV CASEGUARD_POLICIES_DIR=/app/policies
USER caseguard
EXPOSE 8090
CMD ["uvicorn", "caseguard_agent.app:app", "--host", "0.0.0.0", "--port", "8090"]
