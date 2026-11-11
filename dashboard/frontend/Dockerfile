FROM python:3.12-slim

# tzdata so session times are shown in local time (TZ is set in docker-compose.yml)
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 5000
CMD ["python", "app.py", "--port", "5000"]
