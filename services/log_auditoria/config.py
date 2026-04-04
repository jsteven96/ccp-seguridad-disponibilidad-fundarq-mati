import os

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://mongodb.data.svc.cluster.local:27017/ccp")
SERVICE_NAME = "log_auditoria"
PORT = int(os.getenv("PORT", "8096"))
