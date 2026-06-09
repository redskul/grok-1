# AI Token (AIT) platform node + dashboard.
# Build:  docker build -t aitoken .
# Run:    docker run -d -p 8545:8545 -v aitoken-data:/data --name aitoken aitoken
FROM python:3.11-slim

WORKDIR /app

COPY requirements-aitoken.txt .
RUN pip install --no-cache-dir -r requirements-aitoken.txt

COPY aitoken/ aitoken/

# Chain database and the auto-generated owner wallet live on the volume so
# they survive container upgrades. Back up /data/owner_wallet.json — it is
# the key that collects all exchange fees.
ENV AITOKEN_DB_PATH=/data/aitoken.db \
    AITOKEN_OWNER_WALLET_PATH=/data/owner_wallet.json \
    AITOKEN_HOST=0.0.0.0 \
    AITOKEN_PORT=8545
VOLUME /data
EXPOSE 8545

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8545/api/status', timeout=3)"

CMD ["python", "-m", "aitoken", "node"]
