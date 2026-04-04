import os

MONGODB_URL = os.getenv(
    "MONGODB_URL",
    "mongodb://mongodb-0.mongodb-headless.data.svc.cluster.local:27017/ccp?replicaSet=rs0&directConnection=true",
)
NATS_URL = os.getenv("NATS_URL", "nats://nats.messaging.svc.cluster.local:4222")
HEARTBEAT_INTERVAL_S = float(os.getenv("HEARTBEAT_INTERVAL_S", "5"))
STANDBY_MODE = os.getenv("STANDBY_MODE", "false").lower() == "true"
SERVICE_NAME = os.getenv("SERVICE_NAME", "modulo-inventarios")
NODE_ID = os.getenv("NODE_ID", "inv-primary")
