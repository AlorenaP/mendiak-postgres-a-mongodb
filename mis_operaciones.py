"""
VUESTRAS operaciones: PostgreSQL mejorado (parte R.2) y MongoDB (parte D).

El banco de pruebas (banco_pruebas.py) llama a estas funciones. No
cambiéis los nombres ni lo que devuelven: el modo "comprobar" compara
la salida de cada motor y tiene que coincidir.

Firmas
------
PostgreSQL:  f(cur, p)      cur = cursor de psycopg que devuelve diccionarios
MongoDB:     f(db, p)       lecturas;  db = base de datos "mendiak" de pymongo
             f(db, p, s)    escrituras; s = sesión con la transacción YA abierta.
                            Pasad session=s en CADA operación de escritura y de
                            lectura que haga la función. No hagáis commit: lo hace
                            el banco (o la deshace, si no se usa --confirmar).
`p` es un diccionario con los parámetros (ver Parametros.para en banco_pruebas.py).

Qué tiene que devolver cada operación (lo que el banco compara)
---------------------------------------------------------------
L1 {sku, precio, n_resenas, nota_media, histograma {1..5: n}, ficha_tecnica {nombre: valor},
    resenas_recientes [{titulo, ...}] (3, de la más reciente a la más antigua),
    relacionados [{sku, ...}] (6)}
L2 {total, pagina [{sku, ...}] (24, por precio y sku), facetas [{atributo, valor, n}]}
L3 {pedidos, facturado, top_skus [{sku, ...}] (10), ultimos_pedidos [{pedido_id, ...}] (50),
    salud_catalogo {skus_vivos, nota_media_catalogo, resenas_totales}}
L4 [ {pedido_id, importe_total, lineas [...], historial [...]} ]   (10, del más reciente)
L5 {pedido_id, estado_actual, importe_total,
    lineas [{sku, nombre, cantidad, precio_unit}], historial [{estado, ...}]}
L6 [ {departamento, mes, pedidos, ventas} ]  (con las filas de subtotal: departamento o mes = None)
E1 {importe_total, segmento}      E2 {skus_reversionados}
E3 {estado_nuevo}                 E4 {atributos}   (atributos guardados del SKU)

Mirad operaciones_originales.py: es la versión original y sirve de ejemplo.
"""
import json

import operaciones_originales as orig


def _json(x):
    return json.dumps(x)


# =====================================================================
# PostgreSQL mejorado (parte R.2)
# Si en una operación no hay nada que mejorar, dejad la original y
# comprobad con su EXPLAIN por qué no hace falta tocarla.
# =====================================================================
def pg_L1(cur, p):
    """Ficha de producto.

    Cambios respecto del original (ver 05_pg_mejorado.sql, bloque L1):
      * contador, nota y histograma salen de resena_resumen (1 fila)
        en vez de recorrer todas las reseñas del producto;
      * el carrusel sale de mv_top_categoria (7 filas por categoría)
        en vez de recorrer la tabla resena entera;
      * la ficha técnica va en una subconsulta: ya no hace falta el
        GROUP BY de toda la fila.
    """
    cur.execute("""
    SELECT p.sku, p.nombre, p.marca, p.precio_base AS precio, p.peso_g, p.activo,
           c.nombre AS categoria, d.nombre AS departamento,
           v.vendedor_id, v.nombre AS vendedor, v.valoracion, v.provincia,
           (SELECT coalesce(jsonb_object_agg(a.nombre,
                        coalesce(pa.valor_texto, pa.valor_num::text, pa.valor_bool::text)),
                        '{}'::jsonb)
              FROM producto_atributo pa
              JOIN dim_atributo a ON a.atributo_id = pa.atributo_id
             WHERE pa.sku = p.sku)                                     AS ficha_tecnica,
           coalesce(rr.n, 0)                                           AS n_resenas,
           CASE WHEN rr.n > 0 THEN round(rr.suma::numeric / rr.n, 2) END AS nota_media,
           CASE WHEN rr.n > 0 THEN jsonb_build_object(
                    '1', rr.n1, '2', rr.n2, '3', rr.n3, '4', rr.n4, '5', rr.n5)
           END                                                         AS histograma,
           (SELECT jsonb_agg(to_jsonb(u))
              FROM (SELECT r.puntuacion, r.titulo, r.texto, r.votos_utiles, r.ts,
                           cl.nombre AS cliente
                      FROM resena r
                      JOIN cliente cl ON cl.cliente_id = r.cliente_id
                     WHERE r.sku = p.sku
                     ORDER BY r.ts DESC, r.resena_id DESC
                     LIMIT 3) u)                                       AS resenas_recientes,
           (SELECT jsonb_agg(jsonb_build_object(
                       'sku', m.sku, 'nombre', m.nombre, 'precio_base', m.precio_base,
                       'n_resenas', m.n_resenas, 'nota', m.nota) ORDER BY m.puesto)
              FROM (SELECT * FROM mv_top_categoria m
                     WHERE m.categoria_id = p.categoria_id AND m.sku <> p.sku
                     ORDER BY m.puesto
                     LIMIT 6) m)                                       AS relacionados
    FROM dim_producto p
    JOIN dim_categoria    c ON c.categoria_id    = p.categoria_id
    JOIN dim_departamento d ON d.departamento_id = c.departamento_id
    JOIN dim_vendedor     v ON v.vendedor_id     = p.vendedor_id
    LEFT JOIN resena_resumen rr ON rr.sku = p.sku
    WHERE p.sku = %(sku)s AND p.es_actual
    """, p)
    r = cur.fetchone()
    r["resenas_recientes"] = r["resenas_recientes"] or []
    r["relacionados"] = r["relacionados"] or []
    return r


def pg_L2(cur, p):
    """Buscador con filtros y facetas.

    Cambio respecto del original (ver 05_pg_mejorado.sql, bloque L2):
    los productos se buscan en producto_busqueda, con un índice
    compuesto que resuelve los cuatro filtros a la vez, en lugar de
    tres JOIN contra el EAV. Las facetas salen del jsonb de atributos
    de las filas ya encontradas, sin volver al EAV.
    """
    cur.execute("""
    WITH filtrados AS MATERIALIZED (
        SELECT sku, nombre, marca, precio_base, atributos
          FROM producto_busqueda
         WHERE departamento_id = %(departamento_id)s
           AND color  = %(color)s
           AND genero = %(genero)s
           AND peso BETWEEN %(peso_min)s AND %(peso_max)s
    )
    SELECT (SELECT count(*) FROM filtrados)                  AS total,
           (SELECT jsonb_agg(to_jsonb(t))
              FROM (SELECT sku, nombre, marca, precio_base
                      FROM filtrados
                     ORDER BY precio_base, sku
                     LIMIT 24) t)                            AS pagina,
           (SELECT jsonb_agg(to_jsonb(f))
              FROM (SELECT e.key AS atributo, e.value AS valor, count(*) AS n
                      FROM filtrados fl, jsonb_each_text(fl.atributos) e
                     GROUP BY 1, 2
                     ORDER BY 1, 3 DESC, 2) f)               AS facetas
    """, p)
    r = cur.fetchone()
    r["pagina"] = r["pagina"] or []
    r["facetas"] = r["facetas"] or []
    return r


def pg_L3(cur, p):
    """Panel del vendedor.

    Cambio respecto del original: la "salud del catálogo" (nota media y
    reseñas de todos sus productos) sale de resena_resumen, 1 fila por
    SKU, en vez de recorrer todas las reseñas de cada producto. Con el
    vendedor 1 eran 32.459 productos x sus reseñas.

    La parte de ventas (líneas del último año) se deja igual: L3 cuesta
    0,4 h/día y acelerarla exigiría índices nuevos sobre pedido y
    pedido_linea, que encarecen cada E1 (ver docs/R2_pg_mejorado.md).
    """
    cur.execute("""
    WITH lineas_vendedor AS MATERIALIZED (
        SELECT li.pedido_id, li.linea_num, li.cantidad, li.precio_unit,
               li.importe_neto,
               pr.sku, pr.nombre,
               ped.ts_creacion, ped.estado_actual, ped.canal, ped.cliente_id
        FROM dim_producto pr
        JOIN pedido_linea li  ON li.producto_sk = pr.producto_sk
        JOIN pedido       ped ON ped.pedido_id  = li.pedido_id
        WHERE pr.vendedor_id = %(vendedor_id)s
          AND ped.ts_creacion >= TIMESTAMPTZ '2025-09-02 00:00:00+02'
          AND ped.estado_actual <> 'cancelado'
    )
    SELECT (SELECT count(DISTINCT pedido_id)  FROM lineas_vendedor) AS pedidos,
           (SELECT round(sum(importe_neto),2) FROM lineas_vendedor) AS facturado,
           (SELECT jsonb_agg(to_jsonb(t))
              FROM (SELECT sku, nombre,
                           sum(cantidad)              AS unidades,
                           round(sum(importe_neto),2) AS importe
                      FROM lineas_vendedor
                     GROUP BY sku, nombre
                     ORDER BY 4 DESC, sku
                     LIMIT 10) t)                                   AS top_skus,
           (SELECT jsonb_agg(to_jsonb(u))
              FROM (SELECT lv.pedido_id, lv.ts_creacion, lv.estado_actual, lv.canal,
                           cl.nombre AS cliente, cl.email,
                           count(*)                      AS lineas,
                           round(sum(lv.importe_neto),2) AS importe
                      FROM lineas_vendedor lv
                      JOIN cliente cl ON cl.cliente_id = lv.cliente_id
                     GROUP BY lv.pedido_id, lv.ts_creacion, lv.estado_actual,
                              lv.canal, cl.nombre, cl.email
                     ORDER BY lv.ts_creacion DESC, lv.pedido_id DESC
                     LIMIT 50) u)                                   AS ultimos_pedidos,
           (SELECT to_jsonb(s)
              FROM (SELECT count(*)                                     AS skus_vivos,
                           round(avg(CASE WHEN rr.n > 0
                                          THEN rr.suma::numeric / rr.n END), 2)
                                                                        AS nota_media_catalogo,
                           coalesce(sum(rr.n), 0)                       AS resenas_totales
                      FROM dim_producto p
                      LEFT JOIN resena_resumen rr ON rr.sku = p.sku
                     WHERE p.vendedor_id = %(vendedor_id)s AND p.es_actual) s)
                                                                    AS salud_catalogo
    """, p)
    r = cur.fetchone()
    r["top_skus"] = r["top_skus"] or []
    r["ultimos_pedidos"] = r["ultimos_pedidos"] or []
    return r


def pg_E1(cur, p):
    """Crear pedido.

    Cambios respecto del original:
      * paso 0 (nuevo): bloquea las filas de stock SIEMPRE en el mismo
        orden (por SKU) para que dos pedidos con los mismos productos no
        se interbloqueen (R.1, ejercicio 5);
      * paso 6 (CORREGIDO): el segmento se calcula sobre pedido, sin
        JOIN a pedido_linea. El original contaba líneas en vez de
        pedidos y sumaba cada importe una vez por línea.
    El resto de pasos es igual: el stock se descuenta con un UPDATE
    que falla por el CHECK si no hay unidades (R.1, ejercicio 4b).
    """
    skus = sorted(s for s, _ in p["lineas"])
    lineas = _json([{"sku": s, "cantidad": c} for s, c in p["lineas"]])

    # 0) bloquear el stock en orden fijo
    cur.execute("""
    SELECT sku FROM stock WHERE sku = ANY(%(skus)s) ORDER BY sku FOR UPDATE
    """, {"skus": skus})

    # 1) cabecera
    cur.execute("""
    INSERT INTO pedido (pedido_id, cliente_id, canal, ts_creacion, estado_actual,
                        env_calle, env_cp, env_provincia, env_telefono,
                        metodo_pago, importe_total)
    VALUES (%(pedido_id)s, %(cliente_id)s, %(canal)s, now(), 'creado',
            %(calle)s, %(cp)s, %(provincia)s, %(telefono)s, %(metodo_pago)s, 0)
    """, p | p["envio"])

    # 2) líneas: versión vigente de cada SKU y precio congelado
    cur.execute("""
    INSERT INTO pedido_linea (pedido_id, linea_num, producto_sk, cantidad,
                              precio_unit, descuento, importe_neto)
    SELECT %(pedido_id)s, (row_number() OVER (ORDER BY c.sku))::smallint,
           p.producto_sk, c.cantidad, p.precio_base, 0,
           round(p.precio_base * c.cantidad, 2)
    FROM jsonb_to_recordset(%(lineas)s::jsonb) AS c(sku text, cantidad smallint)
    JOIN dim_producto p ON p.sku = c.sku AND p.es_actual
    """, {"pedido_id": p["pedido_id"], "lineas": lineas})

    # 3) descontar stock (el CHECK unidades >= 0 aborta si no hay)
    cur.execute("""
    UPDATE stock s
       SET unidades = s.unidades - c.cantidad, actualizado = now()
      FROM jsonb_to_recordset(%(lineas)s::jsonb) AS c(sku text, cantidad int)
     WHERE s.sku = c.sku
    """, {"lineas": lineas})

    # 4) total de la cabecera
    cur.execute("""
    UPDATE pedido
       SET importe_total = (SELECT round(sum(importe_neto),2)
                              FROM pedido_linea WHERE pedido_id = %(pedido_id)s)
     WHERE pedido_id = %(pedido_id)s
    RETURNING importe_total
    """, p)
    total = cur.fetchone()["importe_total"]

    # 5) primera transición de estado
    cur.execute("""
    INSERT INTO pedido_estado_hist (pedido_id, estado, ts, actor)
    VALUES (%(pedido_id)s, 'creado', now(), 'sistema')
    """, p)

    # 6) segmento CORREGIDO: un pedido cuenta una vez (Index Only Scan)
    cur.execute("""
    UPDATE cliente c
       SET segmento = h.segmento
      FROM (SELECT CASE WHEN sum(importe_total) > 3000 THEN 'premium'
                        WHEN count(*) > 3              THEN 'recurrente'
                        ELSE 'nuevo' END AS segmento
              FROM pedido
             WHERE cliente_id = %(cliente_id)s
               AND estado_actual <> 'cancelado') h
     WHERE c.cliente_id = %(cliente_id)s
    RETURNING c.segmento
    """, p)
    segmento = cur.fetchone()["segmento"]

    # 7) lo comprado sale de la lista de deseos
    cur.execute("""
    DELETE FROM lista_deseos
     WHERE cliente_id = %(cliente_id)s AND sku = ANY(%(skus)s)
    """, {"cliente_id": p["cliente_id"], "skus": skus})

    return {"importe_total": total, "segmento": segmento}


PG_MEJORADO = {
    "L1": pg_L1,
    "L2": pg_L2,
    "L3": pg_L3,
    "L4": orig.L4,
    "L5": orig.L5,
    "L6": orig.L6,
    "E1": pg_E1,
    "E2": orig.E2,
    "E3": orig.E3,
    "E4": orig.E4,
}


# =====================================================================
# MongoDB (parte D)
# Modelo: docs/B_modelo.md. Importes en Decimal128; los cálculos con
# dinero se hacen con Decimal y redondeo "half up", como round() de
# PostgreSQL.
# =====================================================================
import datetime as dt
from decimal import Decimal, ROUND_HALF_UP

from bson import Decimal128, Int64
from pymongo import InsertOne, ReturnDocument, UpdateOne

UTC = dt.timezone.utc
SIGUIENTE = {"creado": "pagado", "pagado": "preparacion",
             "preparacion": "enviado", "enviado": "entregado"}


def _dec(x):
    return x.to_decimal() if isinstance(x, Decimal128) else Decimal(str(x))


def _r2(x):
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _segmento(n, gasto):
    if gasto > 3000:
        return "premium"
    if n > 3:
        return "recurrente"
    return "nuevo"


def mongo_L1(db, p):
    """1 documento de productos + 1 de top_categoria."""
    d = db.productos.find_one({"_id": p["sku"]})
    rr = d["resenas_resumen"]
    top = db.top_categoria.find_one({"_id": d["categoria"]["id"]}) or {"top": []}
    return {
        "sku": d["_id"], "nombre": d["nombre"], "marca": d["marca"],
        "precio": _dec(d["precio"]), "peso_g": d.get("peso_g"), "activo": d["activo"],
        "categoria": d["categoria"]["nombre"], "departamento": d["departamento"]["nombre"],
        "vendedor_id": d["vendedor"]["id"], "vendedor": d["vendedor"]["nombre"],
        "valoracion": d["vendedor"].get("valoracion"), "provincia": d["vendedor"].get("provincia"),
        "ficha_tecnica": {a["k"]: a["v"] for a in d["atributos"]},
        "n_resenas": rr["n"],
        "nota_media": _r2(Decimal(rr["suma"]) / Decimal(rr["n"])) if rr["n"] else None,
        "histograma": rr["hist"] if rr["n"] else None,
        "resenas_recientes": d["resenas_recientes"],
        "relacionados": [x for x in top["top"] if x["sku"] != d["_id"]][:6],
    }


def mongo_L2(db, p):
    """Una agregación: filtro por el índice de atributos y $facet con total, página y facetas."""
    filtro = {"departamento.id": p["departamento_id"], "activo": True,
              "atributos": {"$all": [
                  {"$elemMatch": {"k": "color", "v": p["color"]}},
                  {"$elemMatch": {"k": "genero", "v": p["genero"]}},
                  {"$elemMatch": {"k": "peso", "v": {"$gte": p["peso_min"],
                                                     "$lte": p["peso_max"]}}}]}}
    r = next(db.productos.aggregate([
        {"$match": filtro},
        {"$facet": {
            "total": [{"$count": "n"}],
            "pagina": [{"$sort": {"precio": 1, "_id": 1}}, {"$limit": 24},
                       {"$project": {"_id": 0, "sku": "$_id", "nombre": 1, "marca": 1,
                                     "precio_base": "$precio"}}],
            "facetas": [{"$unwind": "$atributos"},
                        {"$group": {"_id": {"a": "$atributos.k", "v": "$atributos.v"},
                                    "n": {"$sum": 1}}},
                        {"$project": {"_id": 0, "atributo": "$_id.a", "valor": "$_id.v", "n": 1}},
                        {"$sort": {"atributo": 1, "n": -1, "valor": 1}}]}}]))
    return {"total": r["total"][0]["n"] if r["total"] else 0,
            "pagina": r["pagina"], "facetas": r["facetas"]}


def mongo_L3(db, p):
    """Ventas: pedidos del vendedor (índice lineas.vendedor_id + fecha).
    Catálogo: resumen de reseñas de sus productos (índice vendedor.id)."""
    v = p["vendedor_id"]
    desde = dt.datetime(2025, 9, 1, 22, 0, tzinfo=UTC)          # 2025-09-02 00:00 Madrid
    r = next(db.pedidos.aggregate([
        {"$match": {"lineas.vendedor_id": v, "ts_creacion": {"$gte": desde},
                    "estado_actual": {"$ne": "cancelado"}}},
        {"$unwind": "$lineas"},
        {"$match": {"lineas.vendedor_id": v}},
        {"$facet": {
            "total": [{"$group": {"_id": "$_id", "imp": {"$sum": "$lineas.importe_neto"}}},
                      {"$group": {"_id": None, "pedidos": {"$sum": 1},
                                  "facturado": {"$sum": "$imp"}}}],
            "top": [{"$group": {"_id": {"sku": "$lineas.sku", "nombre": "$lineas.nombre"},
                                "unidades": {"$sum": "$lineas.cantidad"},
                                "importe": {"$sum": "$lineas.importe_neto"}}},
                    {"$sort": {"importe": -1, "_id.sku": 1}}, {"$limit": 10},
                    {"$project": {"_id": 0, "sku": "$_id.sku", "nombre": "$_id.nombre",
                                  "unidades": 1, "importe": 1}}],
            "ultimos": [{"$group": {"_id": "$_id", "ts_creacion": {"$first": "$ts_creacion"},
                                    "estado_actual": {"$first": "$estado_actual"},
                                    "canal": {"$first": "$canal"},
                                    "cliente_id": {"$first": "$cliente_id"},
                                    "lineas": {"$sum": 1},
                                    "importe": {"$sum": "$lineas.importe_neto"}}},
                        {"$sort": {"ts_creacion": -1, "_id": -1}}, {"$limit": 50}]}}],
        allowDiskUse=True))
    tot = r["total"][0] if r["total"] else {"pedidos": 0, "facturado": Decimal128("0")}
    clientes = {c["_id"]: c for c in db.clientes.find(
        {"_id": {"$in": [u["cliente_id"] for u in r["ultimos"]]}}, {"nombre": 1, "email": 1})}
    ultimos = [{"pedido_id": u["_id"], "ts_creacion": u["ts_creacion"],
                "estado_actual": u["estado_actual"], "canal": u["canal"],
                "cliente": clientes[u["cliente_id"]]["nombre"],
                "email": clientes[u["cliente_id"]]["email"],
                "lineas": u["lineas"], "importe": _r2(_dec(u["importe"]))} for u in r["ultimos"]]
    skus = n_total = 0
    notas = []
    for d in db.productos.find({"vendedor.id": v}, {"resenas_resumen.n": 1, "resenas_resumen.suma": 1}):
        skus += 1
        n = d["resenas_resumen"]["n"]
        n_total += n
        if n:
            notas.append(Decimal(d["resenas_resumen"]["suma"]) / Decimal(n))
    return {"pedidos": tot["pedidos"], "facturado": _r2(_dec(tot["facturado"])),
            "top_skus": r["top"], "ultimos_pedidos": ultimos,
            "salud_catalogo": {"skus_vivos": skus, "resenas_totales": n_total,
                               "nota_media_catalogo": _r2(sum(notas) / len(notas)) if notas else None}}


_CAMPOS_PEDIDO = {"cliente_id": 1, "ts_creacion": 1, "estado_actual": 1, "canal": 1,
                  "metodo_pago": 1, "importe_total": 1, "envio": 1, "lineas": 1, "historial": 1}


def _pedido(d):
    d["pedido_id"] = d.pop("_id")
    return d


def mongo_L4(db, p):
    """10 documentos por el índice (cliente_id, ts_creacion desc, _id desc)."""
    cur = (db.pedidos.find({"cliente_id": p["cliente_id"]}, _CAMPOS_PEDIDO)
           .sort([("ts_creacion", -1), ("_id", -1)]).limit(10))
    return [_pedido(d) for d in cur]


def mongo_L5(db, p):
    """1 documento por _id: el pedido completo."""
    return _pedido(db.pedidos.find_one({"_id": p["pedido_id"]}, _CAMPOS_PEDIDO))


def mongo_L6(db, p):
    """Agregación sobre los pedidos del año: departamento x mes, subtotales y total."""
    anio = p["anio"]
    desde = dt.datetime(anio - 1, 12, 31, 23, 0, tzinfo=UTC)   # 1 de enero, 00:00 Madrid
    hasta = dt.datetime(anio, 12, 31, 23, 0, tzinfo=UTC)
    r = next(db.pedidos.aggregate([
        {"$match": {"ts_creacion": {"$gte": desde, "$lt": hasta},
                    "estado_actual": {"$ne": "cancelado"}}},
        {"$unwind": "$lineas"},
        {"$group": {"_id": {"d": "$lineas.departamento", "p": "$_id",
                            "m": {"$dateToString": {"format": "%Y-%m", "date": "$ts_creacion",
                                                    "timezone": "Europe/Madrid"}}},
                    "v": {"$sum": "$lineas.importe_neto"}}},
        {"$facet": {
            "dm": [{"$group": {"_id": {"d": "$_id.d", "m": "$_id.m"},
                               "pedidos": {"$sum": 1}, "ventas": {"$sum": "$v"}}}],
            "d": [{"$group": {"_id": {"d": "$_id.d", "p": "$_id.p"}, "v": {"$sum": "$v"}}},
                  {"$group": {"_id": {"d": "$_id.d"}, "pedidos": {"$sum": 1},
                              "ventas": {"$sum": "$v"}}}],
            "t": [{"$group": {"_id": "$_id.p", "v": {"$sum": "$v"}}},
                  {"$group": {"_id": {}, "pedidos": {"$sum": 1}, "ventas": {"$sum": "$v"}}}]}}],
        allowDiskUse=True))
    filas = []
    for grupo in ("dm", "d", "t"):
        for f in r[grupo]:
            filas.append({"departamento": f["_id"].get("d"), "mes": f["_id"].get("m"),
                          "pedidos": f["pedidos"], "ventas": _dec(f["ventas"])})
    return filas


def mongo_E1(db, p, s):
    """Transacción: lee k productos; escribe 1 pedido, k stock y 1 cliente."""
    ahora = dt.datetime.now(UTC)
    cantidades = dict(p["lineas"])
    skus = sorted(cantidades)
    productos = {d["_id"]: d for d in db.productos.find(
        {"_id": {"$in": skus}},
        {"precio": 1, "nombre": 1, "version_sk": 1, "vendedor.id": 1, "departamento.nombre": 1},
        session=s)}
    lineas, total = [], Decimal(0)
    for n, sku in enumerate(skus, 1):
        pr = productos[sku]
        precio, cant = _dec(pr["precio"]), cantidades[sku]
        importe = _r2(precio * cant)
        total += importe
        lineas.append({"n": n, "sku": sku, "producto_sk": Int64(pr["version_sk"]),
                       "nombre": pr["nombre"], "vendedor_id": pr["vendedor"]["id"],
                       "departamento": pr["departamento"]["nombre"], "cantidad": cant,
                       "precio_unit": Decimal128(precio), "descuento": Decimal128("0.00"),
                       "importe_neto": Decimal128(importe)})
    total = _r2(total)
    # stock: comprobar y restar en la misma operación, siempre en orden de SKU (R.1)
    for sku in skus:
        r = db.stock.update_one({"_id": sku, "unidades": {"$gte": cantidades[sku]}},
                                {"$inc": {"unidades": -cantidades[sku]},
                                 "$set": {"actualizado": ahora}}, session=s)
        if r.modified_count != 1:
            raise RuntimeError("Sin stock suficiente de %s" % sku)
    db.pedidos.insert_one({
        "_id": p["pedido_id"], "cliente_id": p["cliente_id"], "canal": p["canal"],
        "ts_creacion": ahora, "estado_actual": "creado", "metodo_pago": p["metodo_pago"],
        "importe_total": Decimal128(total), "envio": dict(p["envio"]), "lineas": lineas,
        "historial": [{"estado": "creado", "ts": ahora, "actor": "sistema"}],
        "_origen": {"tabla": "pedido", "clave": p["pedido_id"]}, "_esquema": 1}, session=s)
    # resumen del cliente (patrón computed) y lista de deseos, en una sola actualización
    c = db.clientes.find_one_and_update(
        {"_id": p["cliente_id"]},
        {"$inc": {"resumen_pedidos.n": 1, "resumen_pedidos.gasto": Decimal128(total)},
         "$pull": {"deseos": {"sku": {"$in": skus}}}},
        projection={"resumen_pedidos": 1}, return_document=ReturnDocument.AFTER, session=s)
    segmento = _segmento(c["resumen_pedidos"]["n"], _dec(c["resumen_pedidos"]["gasto"]))
    db.clientes.update_one({"_id": p["cliente_id"]}, {"$set": {"segmento": segmento}}, session=s)
    return {"importe_total": total, "segmento": segmento}


def mongo_E2(db, p, s, tamano_lote=None):
    """Nueva versión de cada producto del vendedor: cierra la versión vigente,
    crea la nueva (productos_versiones) y actualiza el producto (precio +6 %,
    garantía +12 meses). Idempotente: no vuelve a versionar lo publicado hoy."""
    hoy = dt.datetime.combine(dt.date.today(), dt.time(), tzinfo=UTC)
    ayer = hoy - dt.timedelta(days=1)
    pendientes = [d for d in db.productos.find(
        {"vendedor.id": p["vendedor_id"]},
        {"version_sk": 1, "nombre": 1, "marca": 1, "precio": 1, "categoria.id": 1,
         "valido_desde": 1}, session=s)
        if d["valido_desde"].replace(tzinfo=UTC) < hoy]
    if not pendientes:
        return {"skus_reversionados": 0}
    seq = db.contadores.find_one_and_update(
        {"_id": "producto_sk"}, {"$inc": {"seq": len(pendientes)}},
        return_document=ReturnDocument.AFTER, session=s)["seq"]
    primero = seq - len(pendientes) + 1
    versiones, cambios = [], []
    for i, d in enumerate(pendientes):
        sk = Int64(primero + i)
        precio = Decimal128(_r2(_dec(d["precio"]) * Decimal("1.06")))
        versiones.append(UpdateOne({"_id": d["version_sk"]},
                                   {"$set": {"es_actual": False, "valido_hasta": ayer}}))
        versiones.append(InsertOne({
            "_id": sk, "sku": d["_id"], "nombre": d["nombre"], "marca": d["marca"],
            "precio": precio, "categoria_id": d["categoria"]["id"], "vendedor_id": p["vendedor_id"],
            "valido_desde": hoy, "valido_hasta": None, "es_actual": True,
            "_origen": {"tabla": "dim_producto", "clave": int(sk)}, "_esquema": 1}))
        cambios.append(UpdateOne({"_id": d["_id"]},
                                 {"$set": {"precio": precio, "version_sk": sk, "valido_desde": hoy},
                                  "$inc": {"atributos.$[g].v": 12}},
                                 array_filters=[{"g.k": "garantia_meses"}]))
    lote = tamano_lote or len(cambios)
    for i in range(0, len(cambios), lote):
        db.productos_versiones.bulk_write(versiones[2 * i:2 * (i + lote)], ordered=False, session=s)
        db.productos.bulk_write(cambios[i:i + lote], ordered=False, session=s)
    return {"skus_reversionados": len(pendientes)}


def mongo_E3(db, p, s):
    """Una sola actualización atómica de un documento: estado + historial."""
    ramas = [{"case": {"$eq": ["$estado_actual", a]}, "then": b} for a, b in SIGUIENTE.items()]
    d = db.pedidos.find_one_and_update(
        {"_id": p["pedido_id"], "estado_actual": {"$in": list(SIGUIENTE)}},
        [{"$set": {"estado_actual": {"$switch": {"branches": ramas,
                                                 "default": "$estado_actual"}}}},
         {"$set": {"historial": {"$concatArrays": [
             "$historial", [{"estado": "$estado_actual", "ts": "$$NOW", "actor": "sistema"}]]}}}],
        projection={"estado_actual": 1}, return_document=ReturnDocument.AFTER, session=s)
    if d is None:      # pedido ya terminado: como el original, se devuelve su estado
        d = db.pedidos.find_one({"_id": p["pedido_id"]}, {"estado_actual": 1}, session=s)
    return {"estado_nuevo": d["estado_actual"]}


def mongo_E4(db, p, s):
    """Una sola actualización de un documento: solo cambia el atributo editado."""
    d = db.productos.find_one_and_update(
        {"_id": p["sku"]}, {"$inc": {"atributos.$[a].v": p["delta_peso"]}},
        array_filters=[{"a.k": "peso"}], projection={"atributos": 1},
        return_document=ReturnDocument.AFTER, session=s)
    return {"atributos": len(d["atributos"])}


MONGO = {
    "L1": mongo_L1, "L2": mongo_L2, "L3": mongo_L3, "L4": mongo_L4, "L5": mongo_L5,
    "L6": mongo_L6, "E1": mongo_E1, "E2": mongo_E2, "E3": mongo_E3, "E4": mongo_E4,
}
