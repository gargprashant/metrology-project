FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY reporting/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared module and service code
COPY shared/ ./shared/
COPY reporting/ .

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
