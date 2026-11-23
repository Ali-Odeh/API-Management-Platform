# The React app's dev server (Vite), for the local stack and CI.
#
# The dependencies are installed in /node_modules, one folder above the app:
# Node and Vite find them by walking up from /app. The code is bind-mounted
# on /app (docker-compose.yml), and an empty volume hides the host's own
# node_modules (built for Windows), so only these Linux ones are used.
FROM node:22.19.0-alpine

WORKDIR /
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

WORKDIR /app
COPY . .

EXPOSE 5173
CMD ["/node_modules/.bin/vite"]
