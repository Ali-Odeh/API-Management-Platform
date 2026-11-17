FROM python:3.12-slim

# Don't write .pyc files; print logs immediately instead of buffering.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /code

# Install dependencies first, in their own layer, so Docker can cache them
# and skip reinstalling when only our code changes.
# Dev dependencies (pytest) are included so tests run inside the container.
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .

EXPOSE 8000
# Apply database migrations first, then start the app.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 3"]
