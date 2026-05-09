FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data/sessions data/output data/users

EXPOSE 8000

CMD ["python3", "-m", "uvicorn", "web.server:app", "--host", "0.0.0.0", "--port", "8000"]
