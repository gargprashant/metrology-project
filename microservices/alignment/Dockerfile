# Use Python 3.11 (Open3D compatible)
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for Open3D
RUN apt-get update && apt-get install -y \
    libgl1 \
    libgomp1 \
    libx11-6 \
    libxi6 \
    libxxf86vm1 \
    && rm -rf /var/lib/apt/lists/*


# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy code
COPY . .

# Run FastAPI service
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]