FROM node:22.19.0-alpine AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN corepack enable && corepack prepare pnpm@11.19.0 --activate \
    && pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.13.7-slim AS wheels
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

FROM python:3.13.7-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd -g 10001 studio && useradd -r -u 10001 -g studio studio
WORKDIR /app
COPY --from=wheels /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt && rm -rf /wheels
COPY . .
COPY --from=frontend /src/frontend/dist /app/static/editor
RUN mkdir -p /app/media /app/staticfiles \
    && python manage.py collectstatic --clear --noinput \
    && chown -R studio:studio /app
USER 10001:10001
EXPOSE 8000
CMD ["gunicorn","config.asgi:application","-k","uvicorn.workers.UvicornWorker","--bind","0.0.0.0:8000","--workers","2","--access-logfile","-"]
