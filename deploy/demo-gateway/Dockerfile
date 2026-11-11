FROM python:3.12-slim
WORKDIR /app
RUN mkdir /data && chown 10001:10001 /data
COPY gateway.py /app/gateway.py
USER 10001:10001
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
CMD ["python", "gateway.py"]
