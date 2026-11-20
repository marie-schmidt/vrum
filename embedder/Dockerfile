# The embedding service Vrum runs by default (see server.py). The model is fetched here, once, at
# build time and checked against fixed checksums, so the running container needs no internet.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /service
COPY requirements.txt .
RUN pip install --require-hashes --no-deps -r requirements.txt
COPY fetch_model.py .
RUN python fetch_model.py /model
COPY server.py .
RUN useradd --system --no-create-home --uid 10002 embedder
USER embedder
CMD ["python", "server.py"]
