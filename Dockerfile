FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py database.py runtime_state.py database.json ./
COPY handlers/ ./handlers/
COPY middlewares/ ./middlewares/

EXPOSE 3000
CMD ["python", "-u", "bot.py"]
