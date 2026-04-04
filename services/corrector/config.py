import os

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://mongodb.data.svc.cluster.local:27017/ccp")
NATS_URL = os.getenv("NATS_URL", "nats://nats.messaging.svc.cluster.local:4222")
INVENTARIO_URL = os.getenv("INVENTARIO_URL", "http://modulo-inventarios.ccp.svc.cluster.local:8090")
INV_STANDBY_URL = os.getenv("INV_STANDBY_URL", "http://inv-standby.ccp.svc.cluster.local:8095")
SERVICE_NAME = "corrector"
NODE_ID = os.getenv("NODE_ID", "corrector-0")
PORT = int(os.getenv("PORT", "8092"))
