FROM python:3.12-slim
ENV TZ=Asia/Kolkata PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends tzdata git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
ARG BROKER_EXTRA=zerodha
RUN pip install --no-cache-dir ".[${BROKER_EXTRA}]" \
 && (pip uninstall -y asyncio uuid 2>/dev/null || true)
COPY config ./config
VOLUME ["/app/data", "/app/runs", "/app/recordings", "/app/.cache"]
ENTRYPOINT ["nsealgo", "-c", "config/system.yaml"]
CMD ["forwardtest", "--daemon"]
