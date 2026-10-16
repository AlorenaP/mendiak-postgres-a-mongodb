"""
Parte C.2 · Migración de PostgreSQL a MongoDB.

Una consulta SQL por colección construye cada documento completo en JSON
(Extended JSON: $numberDecimal para importes, $date para fechas,
$numberLong para claves grandes). Python solo lee las filas en bloques
y las inserta: los validadores de 06_mongo_esquema.py comprueban cada
documento al cargarlo.

Uso (dentro del contenedor del banco, que ya tiene los dos drivers):
  docker compose run --rm --entrypoint python banco 07_migrar.py
  docker compose run --rm --entrypoint python banco 07_migrar.py pedidos   (solo una)
  docker compose run --rm --entrypoint python banco 07_migrar.py final     (solo carrusel,
                                                                            contadores e índices)
"""
import os
import sys
import time

import psycopg
from bson import json_util
from pymongo import MongoClient
from pymongo.errors import BulkWriteError

import importlib
esquema = importlib.import_module("06_mongo_esquema")

LOTE = 5000

# ---------------------------------------------------------------------
# Funciones auxiliares de conversión a Extended JSON (temporales)
# ---------------------------------------------------------------------
AUXILIARES = """
CREATE FUNCTION pg_temp.fecha(x timestamptz) RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN x IS NULL THEN 'null'::jsonb ELSE
         jsonb_build_object('$date', jsonb_build_object('$numberLong',
             floor(extract(epoch FROM x) * 1000)::bigint::text)) END $$;
CREATE FUNCTION pg_temp.fecha(x date) RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
  SELECT pg_temp.fecha((x::timestamp AT TIME ZONE 'UTC')) $$;
CREATE FUNCTION pg_temp.dec(x numeric) RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN x IS NULL THEN 'null'::jsonb
              ELSE jsonb_build_object('$numberDecimal', x::text) END $$;
CREATE FUNCTION pg_temp.lng(x bigint) RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_build_object('$numberLong', x::text) $$;
"""

# ---------------------------------------------------------------------
# Una consulta por colección
# ---------------------------------------------------------------------
CONSULTAS = {
    # -------------------------------------------------------------
    "productos": """
SELECT jsonb_build_object(
    '_id',          p.sku,
    'version_sk',   pg_temp.lng(p.producto_sk),
    'nombre',       p.nombre,
    'marca',        p.marca,
    'precio',       pg_temp.dec(p.precio_base),
    'peso_g',       p.peso_g,
    'activo',       p.activo,
    'valido_desde', pg_temp.fecha(p.valido_desde),
    'categoria',    jsonb_build_object('id', c.categoria_id, 'nombre', c.nombre),
    'departamento', jsonb_build_object('id', d.departamento_id, 'nombre', d.nombre),
    'vendedor',     jsonb_build_object('id', v.vendedor_id, 'nombre', v.nombre,
                                       'valoracion', pg_temp.dec(v.valoracion),
                                       'provincia', v.provincia),
    'atributos',    coalesce(a.atributos, '[]'::jsonb),
    'resenas_resumen', jsonb_build_object(
                        'n', coalesce(rr.n, 0), 'suma', coalesce(rr.suma, 0),
                        'hist', jsonb_build_object('1', coalesce(rr.n1, 0), '2', coalesce(rr.n2, 0),
                                                   '3', coalesce(rr.n3, 0), '4', coalesce(rr.n4, 0),
                                                   '5', coalesce(rr.n5, 0))),
    'resenas_recientes', coalesce(ult.lista, '[]'::jsonb),
    '_origen',      jsonb_build_object('tabla', 'dim_producto', 'clave', p.producto_sk),
    '_esquema',     1)::text
FROM dim_producto p
JOIN dim_categoria    c ON c.categoria_id    = p.categoria_id
JOIN dim_departamento d ON d.departamento_id = c.departamento_id
JOIN dim_vendedor     v ON v.vendedor_id     = p.vendedor_id
LEFT JOIN resena_resumen rr ON rr.sku = p.sku
CROSS JOIN LATERAL (
    SELECT jsonb_agg(jsonb_build_object('k', da.nombre, 'v',
               CASE da.tipo WHEN 'texto'  THEN to_jsonb(pa.valor_texto)
                            WHEN 'numero' THEN to_jsonb(pa.valor_num::float8)
                            ELSE to_jsonb(pa.valor_bool) END)
               ORDER BY pa.atributo_id) AS atributos
      FROM producto_atributo pa
      JOIN dim_atributo da ON da.atributo_id = pa.atributo_id
     WHERE pa.sku = p.sku) a
CROSS JOIN LATERAL (
    SELECT jsonb_agg(jsonb_build_object(
               'resena_id', pg_temp.lng(r.resena_id), 'ts', pg_temp.fecha(r.ts),
               'puntuacion', r.puntuacion, 'titulo', r.titulo, 'texto', r.texto,
               'votos_utiles', r.votos_utiles, 'cliente', cl.nombre)
               ORDER BY r.ts DESC, r.resena_id DESC) AS lista
      FROM (SELECT * FROM resena r WHERE r.sku = p.sku
             ORDER BY r.ts DESC, r.resena_id DESC LIMIT 3) r
      JOIN cliente cl ON cl.cliente_id = r.cliente_id) ult
WHERE p.es_actual
""",
    # -------------------------------------------------------------
    "productos_versiones": """
SELECT jsonb_build_object(
    '_id',          pg_temp.lng(p.producto_sk),
    'sku',          p.sku,
    'nombre',       p.nombre,
    'marca',        p.marca,
    'precio',       pg_temp.dec(p.precio_base),
    'categoria_id', p.categoria_id,
    'vendedor_id',  p.vendedor_id,
    'valido_desde', pg_temp.fecha(p.valido_desde),
    'valido_hasta', pg_temp.fecha(p.valido_hasta),
    'es_actual',    p.es_actual,
    '_origen',      jsonb_build_object('tabla', 'dim_producto', 'clave', p.producto_sk),
    '_esquema',     1)::text
FROM dim_producto p
""",
    # -------------------------------------------------------------
    "stock": """
SELECT jsonb_build_object(
    '_id', s.sku, 'unidades', s.unidades, 'actualizado', pg_temp.fecha(s.actualizado),
    '_origen', jsonb_build_object('tabla', 'stock', 'clave', s.sku),
    '_esquema', 1)::text
FROM stock s
""",
    # -------------------------------------------------------------
    "resenas": """
SELECT jsonb_build_object(
    '_id',          pg_temp.lng(r.resena_id),
    'sku',          r.sku,
    'cliente',      jsonb_build_object('id', cl.cliente_id, 'nombre', cl.nombre),
    'ts',           pg_temp.fecha(r.ts),
    'puntuacion',   r.puntuacion,
    'titulo',       r.titulo,
    'texto',        r.texto,
    'votos_utiles', r.votos_utiles,
    '_origen',      jsonb_build_object('tabla', 'resena', 'clave', r.resena_id),
    '_esquema',     1)::text
FROM resena r
JOIN cliente cl ON cl.cliente_id = r.cliente_id
""",
    # -------------------------------------------------------------
    "clientes": """
SELECT jsonb_build_object(
    '_id',       c.cliente_id,
    'email',     c.email,
    'nombre',    c.nombre,
    'provincia', c.provincia,
    'segmento',  c.segmento,
    'alta',      pg_temp.fecha(c.alta),
    'preferencias', jsonb_build_object(
                     'email_ofertas', cp.email_ofertas, 'push_pedidos', cp.push_pedidos,
                     'idioma', cp.idioma, 'actualizado', pg_temp.fecha(cp.actualizado)),
    'deseos',    coalesce(de.lista, '[]'::jsonb),
    'resumen_pedidos', jsonb_build_object('n', coalesce(rp.n, 0),
                                          'gasto', pg_temp.dec(coalesce(rp.gasto, 0))),
    '_origen',   jsonb_build_object('tabla', 'cliente', 'clave', c.cliente_id),
    '_esquema',  1)::text
FROM cliente c
JOIN cliente_preferencia cp ON cp.cliente_id = c.cliente_id
LEFT JOIN (SELECT cliente_id,
                  jsonb_agg(jsonb_build_object('sku', sku, 'anadido', pg_temp.fecha(anadido))
                            ORDER BY anadido, sku) AS lista
             FROM lista_deseos GROUP BY cliente_id) de ON de.cliente_id = c.cliente_id
LEFT JOIN (SELECT cliente_id, count(*)::int AS n, sum(importe_total) AS gasto
             FROM pedido WHERE estado_actual <> 'cancelado'
            GROUP BY cliente_id) rp ON rp.cliente_id = c.cliente_id
""",
    # -------------------------------------------------------------
    "pedidos": """
SELECT jsonb_build_object(
    '_id',           p.pedido_id,
    'cliente_id',    p.cliente_id,
    'canal',         p.canal,
    'ts_creacion',   pg_temp.fecha(p.ts_creacion),
    'estado_actual', p.estado_actual,
    'metodo_pago',   p.metodo_pago,
    'importe_total', pg_temp.dec(p.importe_total),
    'envio',         jsonb_build_object('calle', p.env_calle, 'cp', p.env_cp,
                                        'provincia', p.env_provincia,
                                        'telefono', p.env_telefono),
    'lineas',        li.lineas,
    'historial',     hi.historial,
    '_origen',       jsonb_build_object('tabla', 'pedido', 'clave', p.pedido_id),
    '_esquema',      1)::text
FROM pedido p
CROSS JOIN LATERAL (
    SELECT jsonb_agg(jsonb_build_object(
               'n', l.linea_num, 'sku', pr.sku, 'producto_sk', pg_temp.lng(l.producto_sk),
               'nombre', pr.nombre, 'vendedor_id', pr.vendedor_id, 'departamento', d.nombre,
               'cantidad', l.cantidad, 'precio_unit', pg_temp.dec(l.precio_unit),
               'descuento', pg_temp.dec(l.descuento), 'importe_neto', pg_temp.dec(l.importe_neto),
               'linea_id', pg_temp.lng(l.linea_id))
               ORDER BY l.linea_num) AS lineas
      FROM pedido_linea l
      JOIN dim_producto     pr ON pr.producto_sk    = l.producto_sk
      JOIN dim_categoria    c  ON c.categoria_id    = pr.categoria_id
      JOIN dim_departamento d  ON d.departamento_id = c.departamento_id
     WHERE l.pedido_id = p.pedido_id) li
CROSS JOIN LATERAL (
    SELECT jsonb_agg(jsonb_build_object('estado', h.estado, 'ts', pg_temp.fecha(h.ts),
                                        'actor', h.actor)
                     ORDER BY h.ts, h.hist_id) AS historial
      FROM pedido_estado_hist h
     WHERE h.pedido_id = p.pedido_id) hi
""",
}

# Orden de carga
ORDEN = ["stock", "productos_versiones", "productos", "resenas", "clientes", "pedidos"]


def cargar(pg, db, nombre):
    t0 = time.perf_counter()
    coleccion = db[nombre]
    n = 0
    with pg.cursor(name="c_" + nombre) as cur:          # cursor de servidor: lee por bloques
        cur.itersize = LOTE
        cur.execute(CONSULTAS[nombre])
        lote = []
        for (texto,) in cur:
            lote.append(json_util.loads(texto))
            if len(lote) == LOTE:
                n += _insertar(coleccion, lote)
                lote = []
        if lote:
            n += _insertar(coleccion, lote)
    print("   %-20s %10d documentos  %6.1f s" % (nombre, n, time.perf_counter() - t0))
    return n


def _insertar(coleccion, lote):
    try:
        coleccion.insert_many(lote, ordered=False)
    except BulkWriteError as e:
        err = e.details["writeErrors"][0]
        print("\nERROR de validación en %s (_id=%s):\n%s" %
              (coleccion.name, err.get("op", {}).get("_id"),
               json_util.dumps(err.get("errInfo", err.get("errmsg")), indent=1)[:3000]))
        raise SystemExit(1)
    return len(lote)


def refrescar_top_categoria(db):
    """Carrusel de L1: los 7 mejor valorados de cada categoría.
    Mismo criterio que mv_top_categoria en PostgreSQL. Se ejecutaría cada hora."""
    db.productos.aggregate([
        {"$match": {"activo": True, "resenas_resumen.n": {"$gt": 0}}},
        {"$project": {"categoria_id": "$categoria.id", "nombre": 1, "precio": 1,
                      "n": "$resenas_resumen.n",
                      "nota_exacta": {"$divide": ["$resenas_resumen.suma",
                                                  "$resenas_resumen.n"]}}},
        # $topN: los 7 primeros de cada categoría, con el mismo orden que la
        # vista materializada de PostgreSQL (nota, nº de reseñas, sku)
        {"$group": {"_id": "$categoria_id",
                    "top": {"$topN": {
                        "n": 7,
                        "sortBy": {"nota_exacta": -1, "n": -1, "_id": 1},
                        "output": {"sku": "$_id", "nombre": "$nombre", "precio": "$precio",
                                   "n_resenas": "$n",
                                   "nota": {"$round": ["$nota_exacta", 2]}}}}}},
        {"$set": {"calculado": "$$NOW", "_esquema": 1}},
        {"$merge": {"into": "top_categoria", "whenMatched": "replace",
                    "whenNotMatched": "insert"}},
    ])
    print("   %-20s %10d documentos" % ("top_categoria", db.top_categoria.count_documents({})))


def main():
    solo = sys.argv[1:] or None
    if solo == ["final"]:            # solo los pasos posteriores a la carga
        solo = []
    pg = psycopg.connect(os.environ["PG_DSN"])
    pg.execute("SET search_path TO mendiak, public")
    pg.execute(AUXILIARES)
    db = MongoClient(os.environ["MONGO_URI"])[os.environ.get("MONGO_DB", "mendiak")]

    t0 = time.perf_counter()
    if solo is None:
        print("1. Colecciones con validador")
        esquema.crear_colecciones(db)
        print("2. Carga")
    for nombre in ORDEN:
        if solo is not None and nombre not in solo:     # "final": no carga nada
            continue
        if solo:
            db[nombre].delete_many({})
        cargar(pg, db, nombre)

    if solo is None or solo == [] or "productos" in solo:
        refrescar_top_categoria(db)
    if solo is None or solo == []:
        maximo = pg.execute("SELECT max(producto_sk) FROM dim_producto").fetchone()[0]
        db.contadores.replace_one({"_id": "producto_sk"},
                                  {"_id": "producto_sk", "seq": maximo}, upsert=True)
        print("3. Índices")
        esquema.crear_indices(db)
    print("\nMigración terminada en %.1f min" % ((time.perf_counter() - t0) / 60))


if __name__ == "__main__":
    main()
