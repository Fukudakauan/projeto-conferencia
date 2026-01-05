FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Dependências de sistema (pandas/openpyxl/reportlab gostam disso)
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copia requirements e instala
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o restante do projeto
COPY . .

EXPOSE 8080
ENV PORT=8080

# app:app  →  arquivo app.py com variável app = Flask(...)
CMD ["gunicorn", "-b", "0.0.0.0:8080", "app:app"]
