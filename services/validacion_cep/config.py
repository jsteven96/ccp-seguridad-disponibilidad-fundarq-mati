import os

NATS_URL = os.getenv("NATS_URL", "nats://nats.messaging.svc.cluster.local:4222")
MODULO_SEGURIDAD_URL = os.getenv("MODULO_SEGURIDAD_URL", "http://modulo-seguridad.ccp.svc.cluster.local:8093")
WINDOW_SECONDS = int(os.getenv("WINDOW_SECONDS", "60"))
SIGNAL_THRESHOLD = int(os.getenv("SIGNAL_THRESHOLD", "2"))  # >=2 señales = ataque
RATE_THRESHOLD = int(os.getenv("RATE_THRESHOLD", "10"))     # requests per window
SKU_CONCENTRATION_THRESHOLD = float(os.getenv("SKU_CONCENTRATION_THRESHOLD", "0.8"))  # 80% same SKU
CANCEL_RATE_THRESHOLD = float(os.getenv("CANCEL_RATE_THRESHOLD", "0.5"))  # 50% cancellations
SERVICE_NAME = "validacion_cep"
PORT = int(os.getenv("PORT", "8094"))
