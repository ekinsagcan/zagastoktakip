# 1. Python 3.9 Slim imajını kullan
FROM python:3.9-slim

# 2. Logların anlık görünmesini sağla
ENV PYTHONUNBUFFERED=1

# 3. Gerekli sistem araçlarını kur
RUN apt-get update && apt-get install -y \
    wget \
    unzip \
    ca-certificates \
    gnupg \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# 4. Google Chrome'u en sağlam yöntemle (.deb) kur
RUN wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends ./google-chrome-stable_current_amd64.deb \
    && rm google-chrome-stable_current_amd64.deb \
    && rm -rf /var/lib/apt/lists/*

# 5. Çalışma klasörünü ayarla
WORKDIR /app

# 6. Gereksinimleri yükle
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 7. Kodları kopyala
COPY bot.py .

# 8. Botu başlat
CMD ["python", "bot.py"]
