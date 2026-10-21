FROM python:3.13-slim

WORKDIR /app
COPY tinyparse /app/tinyparse
COPY tests /app/tests
RUN pip install --no-cache-dir pytest==8.4.2
