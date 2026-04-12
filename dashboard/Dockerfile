FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY dashboard/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy dashboard and simulation code
COPY dashboard/ ./
COPY simulation/ ./simulation/

CMD ["streamlit", "run", "simulation/live_grid.py", "--server.port=8501", "--server.address=0.0.0.0"]
