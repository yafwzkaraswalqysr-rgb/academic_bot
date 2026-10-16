FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./
RUN useradd --create-home --uid 10001 botuser \
    && mkdir -p /app/uploads /app/temp \
    && chown -R botuser:botuser /app

USER botuser
CMD ["python", "bot.py"]
