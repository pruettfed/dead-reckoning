FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY requirements-ml.txt .
# headless opencv: ultralytics pulls opencv-python, whose GUI deps (libxcb etc.)
# don't exist in slim images
RUN pip install --no-cache-dir -r requirements-ml.txt \
    && pip uninstall -y opencv-python \
    && pip install --no-cache-dir opencv-python-headless

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
