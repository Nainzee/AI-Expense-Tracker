# Multi-Stage Production Dockerfile for Receipt Scanner & Expense Intelligence
FROM python:3.11-slim as builder

# Install system dependencies & Tesseract OCR
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    libgl1-mesa-glx \
    libglib2.0-0 \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Generate sample receipts if not already present
RUN python sample_generator.py

# Expose Web UI (7860) and API (8000)
EXPOSE 7860 8000

ENV PORT=7860
ENV PYTHONUNBUFFERED=1

# Default command launches the Gradio web interface
CMD ["python", "app.py"]
