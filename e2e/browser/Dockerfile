# The browser end-to-end runner (FE): the e2e helpers plus Playwright and
# a Chromium that matches it (the image ships the browsers of one Playwright
# version, so the Python package is pinned to the same one). Built from
# ./e2e; the tests are mounted from there, like the API stories.
FROM mcr.microsoft.com/playwright/python:v1.56.0-noble

WORKDIR /e2e
COPY requirements.txt ./requirements.txt
COPY browser/requirements.txt ./browser-requirements.txt
# The image's Python is the system one: pip needs leave to install into it.
RUN pip install --no-cache-dir --break-system-packages --root-user-action=ignore \
    -r requirements.txt -r browser-requirements.txt

CMD ["pytest", "-v", "-p", "no:cacheprovider", "-o", "addopts=-ra", "browser"]
