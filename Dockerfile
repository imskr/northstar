FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app
COPY requirements.txt requirements-turso.txt ./
RUN pip install --no-cache-dir -r requirements-turso.txt
COPY . .
RUN mkdir -p /app/data
EXPOSE 8000
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --workers 2 --threads 4 --timeout 90 'northstar:create_app()'"]
