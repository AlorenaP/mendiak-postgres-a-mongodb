"""
Parte D.2 · Transacciones en MongoDB. Tres experimentos; ninguno cambia los
datos (todas las transacciones terminan en abortTransaction).

  1. Dos clientes compran a la vez la última unidad de MEND-000777.
  2. E2 con el vendedor 1 (32.459 productos) en UNA sola transacción.
  3. E2 con el vendedor 1 por lotes, una transacción por lote.

Uso:
  docker compose run --rm --entrypoint python banco 09_d2_transacciones.py
"""
import os
import time

from pymongo import MongoClient
from pymongo.errors import PyMongoError
from pymongo.write_concern import WriteConcern

import mis_operaciones as ops

cli = MongoClient(os.environ["MONGO_URI"])
db = cli[os.environ.get("MONGO_DB", "mendiak")]
WC = WriteConcern("majority", j=True)

# ---------------------------------------------------------------------
print("\n1. Dos clientes compran la última unidad de MEND-000777")
# ---------------------------------------------------------------------
print("   stock inicial:", db.stock.find_one({"_id": "MEND-000777"})["unidades"])
s1, s2 = cli.start_session(), cli.start_session()
s1.start_transaction(write_concern=WC)
s2.start_transaction(write_concern=WC)
filtro = {"_id": "MEND-000777", "unidades": {"$gte": 1}}
cambio = {"$inc": {"unidades": -1}}
r1 = db.stock.update_one(filtro, cambio, session=s1)
print("   T1 resta 1 (comprobar y restar en una operación): modificados =", r1.modified_count)
try:
    r2 = db.stock.update_one(filtro, cambio, session=s2)
    print("   T2 resta 1: modificados =", r2.modified_count)
except PyMongoError as e:
    etiquetas = [x for x in ("TransientTransactionError",) if e.has_error_label(x)]
    print("   T2 resta 1 -> ERROR inmediato: %s %s" % (type(e).__name__, etiquetas))
    print("      %s" % str(e).split(",")[0][:120])
s1.abort_transaction()
try:
    s2.abort_transaction()
except PyMongoError:
    pass
print("   (las dos transacciones se deshacen) stock final:",
      db.stock.find_one({"_id": "MEND-000777"})["unidades"])

# ---------------------------------------------------------------------
print("\n2. E2 con el vendedor 1 en UNA sola transacción")
# ---------------------------------------------------------------------
n = db.productos.count_documents({"vendedor.id": 1})
print("   productos del vendedor 1:", n, "-> documentos a escribir:", 3 * n)
with cli.start_session() as s:
    t0 = time.perf_counter()
    s.start_transaction(write_concern=WC)
    try:
        r = ops.mongo_E2(db, {"vendedor_id": 1}, s)
        print("   escrito en %.1f s: %s (sin confirmar)" % (time.perf_counter() - t0, r))
        s.abort_transaction()
    except PyMongoError as e:
        print("   FALLA tras %.1f s: %s" % (time.perf_counter() - t0, type(e).__name__))
        print("      %s" % str(e)[:300])
        try:
            s.abort_transaction()
        except PyMongoError:
            pass

# ---------------------------------------------------------------------
print("\n3. E2 con el vendedor 1 por lotes (una transacción por lote)")
# ---------------------------------------------------------------------
LOTE = 1000
skus = [d["_id"] for d in db.productos.find({"vendedor.id": 1}, {"_id": 1}).sort("_id", 1)]
t0, lotes, peor, fallos = time.perf_counter(), 0, 0.0, 0
for i in range(0, len(skus), LOTE):
    with cli.start_session() as s:
        t = time.perf_counter()
        s.start_transaction(write_concern=WC)
        try:
            ops.mongo_E2(db, {"vendedor_id": 1, "skus": skus[i:i + LOTE]}, s)
        except PyMongoError as e:
            fallos += 1
            print("   lote %d falla: %s" % (lotes + 1, type(e).__name__))
        finally:
            try:
                s.abort_transaction()      # en producción: commit_transaction()
            except PyMongoError:
                pass
        lotes += 1
        peor = max(peor, time.perf_counter() - t)
print("   %d lotes de %d productos, %d fallidos: %.1f s en total, el más lento %.2f s"
      % (lotes, LOTE, fallos, time.perf_counter() - t0, peor))
