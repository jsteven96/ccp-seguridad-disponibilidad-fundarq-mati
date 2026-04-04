import os

NATS_URL = os.getenv("NATS_URL", "nats://nats.messaging.svc.cluster.local:4222")
LOG_AUDITORIA_URL = os.getenv("LOG_AUDITORIA_URL", "http://log-auditoria.ccp.svc.cluster.local:8096")
BLOCK_DURATION_HOURS = int(os.getenv("BLOCK_DURATION_HOURS", "24"))
SERVICE_NAME = "modulo_seguridad"
PORT = int(os.getenv("PORT", "8093"))
