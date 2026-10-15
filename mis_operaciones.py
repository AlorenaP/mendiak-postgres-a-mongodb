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


def pg_L3(cur, p):
    raise NotImplementedError


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
    "L2": orig.L2,
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
# =====================================================================
def mongo_L1(db, p):
    raise NotImplementedError


def mongo_L2(db, p):
    raise NotImplementedError


def mongo_L3(db, p):
    raise NotImplementedError


def mongo_L4(db, p):
    raise NotImplementedError


def mongo_L5(db, p):
    raise NotImplementedError


def mongo_L6(db, p):
    raise NotImplementedError


def mongo_E1(db, p, s):
    raise NotImplementedError


def mongo_E2(db, p, s):
    raise NotImplementedError


def mongo_E3(db, p, s):
    raise NotImplementedError


def mongo_E4(db, p, s):
    raise NotImplementedError


MONGO = {
    "L1": mongo_L1, "L2": mongo_L2, "L3": mongo_L3, "L4": mongo_L4, "L5": mongo_L5,
    "L6": mongo_L6, "E1": mongo_E1, "E2": mongo_E2, "E3": mongo_E3, "E4": mongo_E4,
}
