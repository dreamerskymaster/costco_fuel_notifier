# Cloud Run job gas-compare (see README.md next to this file).
FROM python:3.12-slim
RUN pip install --no-cache-dir tzdata==2025.2
WORKDIR /app
COPY compare.py .
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "compare.py"]
