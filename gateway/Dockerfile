FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY gateway/requirements.txt /app/gateway/requirements.txt
RUN pip install --no-cache-dir -r /app/gateway/requirements.txt
COPY gateway /app/gateway
COPY index.html /app/index.html
COPY config /app/config
COPY data /app/data
EXPOSE 8787
CMD ["uvicorn", "gateway.app:app", "--host", "0.0.0.0", "--port", "8787"]
