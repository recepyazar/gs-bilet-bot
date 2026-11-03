FROM python:3.12-slim AS base
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Test aşaması: docker build --target test .
# Testlerden biri kalırsa build başarısız olur (CD bu yüzden bozuk kodu yayına almaz).
FROM base AS test
COPY requirements-dev.txt pytest.ini ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY gsbot ./gsbot
COPY tests ./tests
RUN python -m pytest -p no:cacheprovider

# Çalışan bot
FROM base AS app
COPY gsbot ./gsbot
RUN useradd -m -u 1000 bot && mkdir -p /app/data && chown bot /app/data
USER bot
CMD ["python", "-m", "gsbot"]
