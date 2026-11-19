# The end-to-end test runner: a small Python image with pytest and httpx.
# The tests themselves are mounted from ./e2e (docker-compose.yml), so editing
# them needs no rebuild.
FROM python:3.12-slim

WORKDIR /e2e
COPY requirements.txt .
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

CMD ["pytest", "-v", "-p", "no:cacheprovider"]
