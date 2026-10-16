"""
Parte C · Validación de la migración.

  C.1  Los validadores rechazan documentos que incumplen cada decisión.
  C.2  La migración no ha perdido ni inventado nada.
  C.3  Tamaño de los documentos y de los arrays.
  C.4  Trazabilidad: origen de cada documento y vuelta a PostgreSQL.

Uso:
  docker compose run --rm --entrypoint python banco 08_validar.py
"""
import copy
import os
from decimal import Decimal

import psycopg
from bson import Decimal128, Int64
from pymongo import MongoClient
from pymongo.errors import WriteError

pg = psycopg.connect(os.environ["PG_DSN"], autocommit=True)
pg.execute("SET search_path TO mendiak, public")
db = MongoClient(os.environ["MONGO_URI"])[os.environ.get("MONGO_DB", "mendiak")]
fallos = 0


def pg1(sql, *args):
    return pg.execute(sql, args).fetchone()[0]


def ok(cond, texto, detalle=""):
    global fallos
    fallos += not cond
    print("  %-4s %-62s %s" % ("OK" if cond else "MAL", texto, detalle))


def dec(x):
    return x.to_decimal() if isinstance(x, Decimal128) else Decimal(str(x or 0))


# =====================================================================
print("\nC.1 · Los validadores rechazan lo que no cumple el modelo")
# =====================================================================
base = {c: db[c].find_one() for c in ["stock", "productos", "pedidos", "clientes",
                                       "resenas", "productos_versiones"]}
ID_PRUEBA = {"stock": "MEND-999999", "productos": "MEND-999999", "pedidos": "PED-99999999",
             "clientes": 999999, "resenas": Int64(99999999999),
             "productos_versiones": Int64(99999999999)}


def caso(coleccion, descripcion, cambio):
    doc = copy.deepcopy(base[coleccion])
    doc["_id"] = ID_PRUEBA[coleccion]
    cambio(doc)
    try:
        db[coleccion].insert_one(doc)
        db[coleccion].delete_one({"_id": doc["_id"]})
        ok(False, "%s: %s" % (coleccion, descripcion), "-> ACEPTADO (no debería)")
    except WriteError as e:
        ok(e.code == 121, "%s: %s" % (coleccion, descripcion), "-> rechazado")


caso("stock", "unidades negativas", lambda d: d.update(unidades=-1))
caso("productos", "precio como double, no Decimal128", lambda d: d.update(precio=19.9))
caso("productos", "18 atributos (máximo 17, A.2)", lambda d: d.update(
    atributos=[{"k": "a%d" % i, "v": i} for i in range(18)]))
caso("productos", "atributo sin la forma {k, v}", lambda d: d.update(
    atributos=[{"nombre": "peso", "valor": 1200}]))
caso("productos", "4 reseñas recientes (subset de 3)", lambda d: d.update(
    resenas_recientes=(d["resenas_recientes"] * 4)[:4] if d["resenas_recientes"] else
    [{"resena_id": 1, "ts": None, "puntuacion": 5, "titulo": "x"}] * 4))
caso("productos", "sin resumen de reseñas", lambda d: d.pop("resenas_resumen"))
caso("productos", "SKU con formato incorrecto", lambda d: d.update(_id="SKU-1"))
caso("productos_versiones", "versión sin precio", lambda d: d.pop("precio"))
caso("resenas", "puntuación 6", lambda d: d.update(puntuacion=6))
caso("clientes", "segmento inexistente", lambda d: d.update(segmento="vip"))
caso("clientes", "15 deseos (máximo 14, A.2)", lambda d: d.update(
    deseos=[{"sku": "MEND-%06d" % i, "anadido": d["alta"]} for i in range(1, 16)]))
caso("clientes", "idioma no permitido", lambda d: d["preferencias"].update(idioma="de"))
caso("pedidos", "estado inexistente", lambda d: d.update(estado_actual="perdido"))
caso("pedidos", "41 líneas (máximo 40, A.2)", lambda d: d.update(
    lineas=(d["lineas"] * 41)[:41]))
caso("pedidos", "6 estados en el historial (máximo 5)", lambda d: d.update(
    historial=(d["historial"] * 6)[:6]))
caso("pedidos", "línea con cantidad 0", lambda d: d["lineas"][0].update(cantidad=0))
caso("pedidos", "importe como texto", lambda d: d.update(importe_total="12.50"))
caso("pedidos", "pedido sin líneas", lambda d: d.update(lineas=[]))
caso("pedidos", "identificador con formato incorrecto", lambda d: d.update(_id="PEDIDO-1"))
caso("pedidos", "sin origen (_origen)", lambda d: d.pop("_origen"))

# =====================================================================
print("\nC.2 · No se ha perdido ni inventado nada")
# =====================================================================
print("  -- documentos por colección")
for col, sql in [("productos", "SELECT count(*) FROM dim_producto WHERE es_actual"),
                 ("productos_versiones", "SELECT count(*) FROM dim_producto"),
                 ("stock", "SELECT count(*) FROM stock"),
                 ("resenas", "SELECT count(*) FROM resena"),
                 ("clientes", "SELECT count(*) FROM cliente"),
                 ("pedidos", "SELECT count(*) FROM pedido")]:
    m, p = db[col].count_documents({}), pg1(sql)
    ok(m == p, "%s" % col, "mongo %d = pg %d" % (m, p))


def suma_array(col, campo):
    r = list(db[col].aggregate([{"$group": {"_id": None, "n": {"$sum": {"$size": "$" + campo}}}}]))
    return r[0]["n"] if r else 0


print("  -- elementos de los arrays incrustados = filas de origen")
for col, campo, sql in [("pedidos", "lineas", "SELECT count(*) FROM pedido_linea"),
                        ("pedidos", "historial", "SELECT count(*) FROM pedido_estado_hist"),
                        ("clientes", "deseos", "SELECT count(*) FROM lista_deseos"),
                        ("productos", "atributos", "SELECT count(*) FROM producto_atributo")]:
    m, p = suma_array(col, campo), pg1(sql)
    ok(m == p, "%s.%s" % (col, campo), "mongo %d = pg %d" % (m, p))

print("  -- importes")
r = list(db.pedidos.aggregate([{"$group": {"_id": None, "t": {"$sum": "$importe_total"}}}]))[0]
m, p = dec(r["t"]), pg1("SELECT sum(importe_total) FROM pedido")
ok(m == p, "suma de importe_total de todos los pedidos", "mongo %s = pg %s" % (m, p))
r = list(db.pedidos.aggregate([{"$unwind": "$lineas"},
                               {"$group": {"_id": None, "t": {"$sum": "$lineas.importe_neto"}}}]))[0]
m, p = dec(r["t"]), pg1("SELECT sum(importe_neto) FROM pedido_linea")
ok(m == p, "suma de importe_neto de todas las líneas", "mongo %s = pg %s" % (m, p))
descuadrados = db.pedidos.count_documents({"$expr": {"$ne": [
    {"$round": [{"$sum": "$lineas.importe_neto"}, 2]}, "$importe_total"]}})
ok(descuadrados == 0, "pedidos cuya cabecera no es la suma de sus líneas", "%d" % descuadrados)


def claves(col, campo, unwind=None):
    pipe = ([{"$unwind": "$" + unwind}] if unwind else []) + [{"$group": {"_id": "$" + campo}}]
    return {d["_id"] for d in db[col].aggregate(pipe, allowDiskUse=True)}


print("  -- referencias rotas (en Mongo no hay claves ajenas)")
skus = {d["_id"] for d in db.productos.find({}, {"_id": 1})}
versiones = {d["_id"] for d in db.productos_versiones.find({}, {"_id": 1})}
clientes = {d["_id"] for d in db.clientes.find({}, {"_id": 1})}
for texto, conjunto, destino in [
        ("reseñas de productos que no existen", claves("resenas", "sku"), skus),
        ("deseos de productos que no existen", claves("clientes", "deseos.sku", "deseos"), skus),
        ("stock de productos que no existen", {d["_id"] for d in db.stock.find({}, {"_id": 1})}, skus),
        ("pedidos de clientes que no existen", claves("pedidos", "cliente_id"), clientes),
        ("líneas que apuntan a versiones inexistentes",
         claves("pedidos", "lineas.producto_sk", "lineas"), versiones)]:
    rotas = conjunto - destino
    ok(not rotas, texto, "%d" % len(rotas))

print("  -- datos precalculados = su origen")
reales = {d["_id"]: (d["n"], d["s"]) for d in db.resenas.aggregate(
    [{"$group": {"_id": "$sku", "n": {"$sum": 1}, "s": {"$sum": "$puntuacion"}}}], allowDiskUse=True)}
mal = sum(1 for d in db.productos.find({}, {"resenas_resumen": 1})
          if reales.get(d["_id"], (0, 0)) != (d["resenas_resumen"]["n"], d["resenas_resumen"]["suma"]))
ok(mal == 0, "productos.resenas_resumen = recuento de resenas", "%d distintos" % mal)
reales = {d["_id"]: (d["n"], dec(d["g"])) for d in db.pedidos.aggregate(
    [{"$match": {"estado_actual": {"$ne": "cancelado"}}},
     {"$group": {"_id": "$cliente_id", "n": {"$sum": 1}, "g": {"$sum": "$importe_total"}}}])}
mal = sum(1 for d in db.clientes.find({}, {"resumen_pedidos": 1})
          if reales.get(d["_id"], (0, Decimal(0))) !=
          (d["resumen_pedidos"]["n"], dec(d["resumen_pedidos"]["gasto"])))
ok(mal == 0, "clientes.resumen_pedidos = pedidos no cancelados", "%d distintos" % mal)

top = db.top_categoria.count_documents({})
ok(top == pg1("SELECT count(DISTINCT categoria_id) FROM mv_top_categoria"),
   "top_categoria: una entrada por categoría", "%d" % top)

# =====================================================================
print("\nC.3 · Tamaño de los documentos ($bsonSize) y de los arrays")
# =====================================================================
print("  %-20s %10s %12s %12s" % ("colección", "docs", "media (B)", "máximo (B)"))
for col in ["productos", "productos_versiones", "stock", "resenas", "top_categoria",
            "clientes", "pedidos"]:
    r = list(db[col].aggregate([{"$group": {"_id": None, "n": {"$sum": 1},
                                            "media": {"$avg": {"$bsonSize": "$$ROOT"}},
                                            "max": {"$max": {"$bsonSize": "$$ROOT"}}}}]))
    if not r:
        ok(False, "%s está vacía" % col)
        continue
    r = r[0]
    print("  %-20s %10d %12.0f %12d" % (col, r["n"], r["media"], r["max"]))
print("  %-30s %8s" % ("array", "máximo"))
for col, campo in [("pedidos", "lineas"), ("pedidos", "historial"), ("productos", "atributos"),
                   ("productos", "resenas_recientes"), ("clientes", "deseos")]:
    r = list(db[col].aggregate([{"$group": {"_id": None, "m": {"$max": {"$size": "$" + campo}}}}]))[0]
    print("  %-30s %8d" % (col + "." + campo, r["m"]))
r = list(db.productos.aggregate([{"$match": {"_id": "MEND-000001"}},
                                 {"$project": {"b": {"$bsonSize": "$$ROOT"},
                                               "n": "$resenas_resumen.n"}}]))[0]
print("  Producto con más reseñas (MEND-000001): %d reseñas, documento de %d bytes" % (r["n"], r["b"]))

# =====================================================================
print("\nC.4 · Trazabilidad")
# =====================================================================
for col in ["productos", "productos_versiones", "stock", "resenas", "clientes", "pedidos"]:
    sin = db[col].count_documents({"$or": [{"_origen": {"$exists": False}},
                                           {"_esquema": {"$exists": False}}]})
    ok(sin == 0, "%s: documentos sin _origen o _esquema" % col, "%d" % sin)

SQL_PEDIDO = """
SELECT 'pedido' AS tabla, count(*) FROM pedido WHERE pedido_id = %(id)s
UNION ALL SELECT 'pedido_linea', count(*) FROM pedido_linea WHERE pedido_id = %(id)s
UNION ALL SELECT 'pedido_estado_hist', count(*) FROM pedido_estado_hist WHERE pedido_id = %(id)s"""
ped = db.pedidos.find_one({"_id": "PED-00412345"})
filas = dict(pg.execute(SQL_PEDIDO, {"id": ped["_origen"]["clave"]}).fetchall())
ok(filas == {"pedido": 1, "pedido_linea": len(ped["lineas"]),
             "pedido_estado_hist": len(ped["historial"])},
   "pedido de Mongo -> sus filas en PostgreSQL", str(filas))

sk = ped["lineas"][0]["producto_sk"]
SQL_SK = "SELECT DISTINCT pedido_id FROM pedido_linea WHERE producto_sk = %s ORDER BY 1"
en_pg = [r[0] for r in pg.execute(SQL_SK, (int(sk),)).fetchall()]
en_mongo = sorted(d["_id"] for d in db.pedidos.find({"lineas.producto_sk": sk}, {"_id": 1}))
ok(en_pg == en_mongo, "producto_sk %d -> pedidos que lo contienen" % sk, "%d pedidos" % len(en_pg))

antiguas = [d["_id"] for d in db.productos_versiones.find({"es_actual": False}, {"_id": 1})]
r = list(db.pedidos.aggregate([{"$unwind": "$lineas"},
                               {"$match": {"lineas.producto_sk": {"$in": antiguas}}},
                               {"$count": "n"}]))
m = r[0]["n"] if r else 0
p = pg1("SELECT count(*) FROM pedido_linea l JOIN dim_producto p "
        "ON p.producto_sk = l.producto_sk WHERE NOT p.es_actual")
ok(m == p and m > 0, "líneas vendidas con una versión que ya no es la vigente",
   "mongo %d = pg %d" % (m, p))

print("\n%s" % ("TODO CORRECTO." if not fallos else "%d COMPROBACIONES FALLIDAS." % fallos))
