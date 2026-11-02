FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gsbot ./gsbot
RUN useradd -m -u 1000 bot && mkdir -p /app/data && chown bot /app/data
USER bot
CMD ["python", "-m", "gsbot"]
