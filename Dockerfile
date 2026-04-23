FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for better layer caching
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copy application code
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY pyproject.toml .

WORKDIR /app/backend

# Create non-root user
RUN useradd --create-home appuser
USER appuser

EXPOSE 5000

ENV FLASK_APP=app.py \
    FLASK_ENV=production

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "120", "app:app"]
