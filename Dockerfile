FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 VOXRN_DATA_DIR=/var/lib/voxrn
WORKDIR /app
COPY requirements*.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN mkdir -p /var/lib/voxrn && chown 10001:10001 /var/lib/voxrn
COPY contracts ./contracts
COPY service ./service
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "service.server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
