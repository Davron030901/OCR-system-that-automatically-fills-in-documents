FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements/ml.txt ./requirements.txt
# onnxruntime, not onnxruntime-gpu: this runs on CPU instances.
RUN pip install -r requirements.txt

COPY packages ./packages
COPY apps/ml-service ./apps/ml-service
# The classifier, if one has been trained. models/ always exists (it holds a
# README), so this COPY succeeds whether or not the .onnx file is present —
# and CLASSIFIER_MODEL_PATH pointing at a missing file simply disables the
# classifier rather than failing the boot.
COPY models ./models

RUN useradd -m -u 10001 appuser && chown -R appuser /srv
USER appuser

ENV PYTHONPATH=/srv:/srv/apps/ml-service
EXPOSE 8100
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100", "--app-dir", "apps/ml-service"]
