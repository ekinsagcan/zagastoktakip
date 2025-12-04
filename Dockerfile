# 1. Python 3.9 Slim imajını baz al (Hafif ve hızlı)
FROM python:3.9-slim

# 2. Logların anlık görünmesi için Python ayarı
ENV PYTHONUNBUFFERED=1

# 3. Gerekli sistem araçlarını ve Chrome'u kur
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list' \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 4. Çalışma klasörünü oluştur
WORKDIR /app

# 5. Gereksinimleri kopyala ve kur
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 6. Bot kodunu kopyala
COPY bot.py .

# 7. Botu başlat
CMD ["python", "bot.py"]
