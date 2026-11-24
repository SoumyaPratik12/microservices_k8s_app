FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt requirements-dev.txt .
ARG REQUIREMENTS_FILE=requirements.txt
RUN apt-get update && \
    apt-get upgrade -y --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir --upgrade pip setuptools && \
    pip install --no-cache-dir -r "$REQUIREMENTS_FILE"

COPY . .

ARG APP_MODULE
ENV APP_MODULE=${APP_MODULE}
ENV PORT=8000

EXPOSE 8000

CMD ["sh", "-c", "uvicorn ${APP_MODULE} --host 0.0.0.0 --port ${PORT}"]
