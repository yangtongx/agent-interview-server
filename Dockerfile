FROM node:22-alpine AS dump
WORKDIR /app
COPY package.json ./
RUN npm install
COPY tsconfig.json ./
COPY scripts ./scripts
COPY src ./src
COPY data ./data
RUN npx tsx scripts/dump-public-bank.ts

FROM python:3.12-slim AS runtime
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=43180
ENV WEB_ROOT=/app/out

COPY server/requirements.txt /app/server/requirements.txt
RUN pip install --no-cache-dir -r /app/server/requirements.txt

COPY server /app/server
COPY --from=dump /app/data/public-bank.json /app/data/public-bank.json

EXPOSE 43180
CMD ["python", "-m", "server"]
