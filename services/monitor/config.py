import os

NATS_URL = os.getenv("NATS_URL", "nats://nats.messaging.svc.cluster.local:4222")
CORRECTOR_URL = os.getenv("CORRECTOR_URL", "http://corrector.ccp.svc.cluster.local:8092")
SERVICE_NAME = "monitor"
NODE_ID = os.getenv("NODE_ID", "monitor-0")
PORT = int(os.getenv("PORT", "8091"))
