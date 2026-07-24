# ExpPilot container image — serves the FastAPI copilot API by default.
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy source and install the package itself.
COPY . .
RUN pip install .

# Seed the local demo DB at build time so the image is demo-ready.
RUN python -m data.seed || true

EXPOSE 8000 8501

# Default: FastAPI. Override CMD to run the Streamlit UI instead:
#   docker run -p 8501:8501 <image> streamlit run ui/app.py --server.address 0.0.0.0
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
