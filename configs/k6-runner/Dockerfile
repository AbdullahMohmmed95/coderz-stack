FROM python:3.11-slim

RUN apt-get update && apt-get install -y curl gnupg ca-certificates && \
    curl -fsSL https://dl.k6.io/key.gpg | gpg --dearmor -o /usr/share/keyrings/k6-archive-keyring.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" \
         | tee /etc/apt/sources.list.d/k6.list && \
    apt-get update && apt-get install -y k6 && \
    pip install --no-cache-dir flask && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app.py .

EXPOSE 9000
CMD ["python", "app.py"]
