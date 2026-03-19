FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends docker.io build-essential libffi-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

EXPOSE 3000

CMD ["python", "-m", "app.main"]
