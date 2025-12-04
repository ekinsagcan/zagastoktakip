# Microsoft'un hazır Playwright imajını kullanıyoruz (Python ve Tarayıcılar yüklü)
FROM mcr.microsoft.com/playwright/python:v1.41.0-jammy

WORKDIR /app

# Gerekli kütüphaneleri yükle
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright tarayıcılarını kur
RUN playwright install chromium
RUN playwright install-deps

COPY bot.py .

# Botu başlat
CMD ["python", "-u", "bot.py"]
