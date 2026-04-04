"""
Script para inicializar el inventario en MongoDB.
Uso: python scripts/init_inventory.py [MONGODB_URL]
"""
import asyncio
import os
import sys

from motor.motor_asyncio import AsyncIOMotorClient

MONGODB_URL = sys.argv[1] if len(sys.argv) > 1 else os.getenv(
    "MONGODB_URL",
    "mongodb://localhost:27017/ccp",
)

INITIAL_INVENTORY = [
    {
        "SKU": "COCA-COLA-350",
        "stock": 9,
        "stock_inicial": 9,
        "reservas_activas": [],
        "version": 0,
    },
    {
        "SKU": "AGUA-500",
        "stock": 100,
        "stock_inicial": 100,
        "reservas_activas": [],
        "version": 0,
    },
    {
        "SKU": "ARROZ-1KG",
        "stock": 50,
        "stock_inicial": 50,
        "reservas_activas": [],
        "version": 0,
    },
]


async def main():
    client = AsyncIOMotorClient(MONGODB_URL)
    db = client["ccp"]

    # Limpiar y reinsertar
    await db.inventario.drop()
    result = await db.inventario.insert_many(INITIAL_INVENTORY)
    print(f"Inventario inicializado: {len(result.inserted_ids)} documentos insertados.")
    for item in INITIAL_INVENTORY:
        print(f"  SKU={item['SKU']}  stock={item['stock']}")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
