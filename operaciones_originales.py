"""
Las diez operaciones ORIGINALES de Mendiak, en PostgreSQL.

Es el mismo SQL de 03_operaciones.sql, con parámetros. No lo cambiéis:
es la columna "original" de la comparación. Os sirve además de ejemplo
de lo que tiene que devolver cada función (ver mis_operaciones.py).

Firma de las funciones:
  lecturas:   f(cur, p)  -> resultado
  escrituras: f(cur, p)  -> resumen   (el banco abre y cierra la transacción)
`cur` es un cursor de psycopg que devuelve diccionarios; `p` es un
diccionario con los parámetros de la operación.
"""


def L1(cur, p):
    cur.execute("""
    SELECT p.sku, p.nombre, p.marca, p.precio_base AS precio, p.peso_g, p.activo,
           c.nombre AS categoria, d.nombre AS departamento,
           v.vendedor_id, v.nombre AS vendedor, v.valoracion, v.provincia,
           jsonb_object_agg(a.nombre,
                coalesce(pa.valor_texto, pa.valor_num::text, pa.valor_bool::text)
           ) AS ficha_tecnica,
           (SELECT count(*)                   FROM resena r WHERE r.sku = p.sku) AS n_resenas,
           (SELECT round(avg(r.puntuacion),2) FROM resena r WHERE r.sku = p.sku) AS nota_media,
           (SELECT jsonb_object_agg(h.puntuacion, h.n)
              FROM (SELECT r.puntuacion, count(*) AS n
                      FROM resena r WHERE r.sku = p.sku
                     GROUP BY r.puntuacion) h)                         AS histograma,
           (SELECT jsonb_agg(to_jsonb(u))
              FROM (SELECT r.puntuacion, r.titulo, r.texto, r.votos_utiles, r.ts,
                           cl.nombre AS cliente
                      FROM resena r
                      JOIN cliente cl ON cl.cliente_id = r.cliente_id
                     WHERE r.sku = p.sku
                     ORDER BY r.ts DESC, r.resena_id DESC
                     LIMIT 3) u)                                       AS resenas_recientes,
           (SELECT jsonb_agg(to_jsonb(rel))
              FROM (SELECT p2.sku, p2.nombre, p2.precio_base,
                           count(r2.resena_id)         AS n_resenas,
                           round(avg(r2.puntuacion),2) AS nota
                      FROM dim_producto p2
                      LEFT JOIN resena r2 ON r2.sku = p2.sku
                     WHERE p2.categoria_id = p.categoria_id
                       AND p2.es_actual AND p2.activo AND p2.sku <> p.sku
                     GROUP BY p2.sku, p2.nombre, p2.precio_base
                    HAVING count(r2.resena_id) > 0
                     ORDER BY avg(r2.puntuacion) DESC, count(r2.resena_id) DESC, p2.sku
                     LIMIT 6) rel)                                     AS relacionados
    FROM dim_producto p
    JOIN dim_categoria    c ON c.categoria_id    = p.categoria_id
    JOIN dim_departamento d ON d.departamento_id = c.departamento_id
    JOIN dim_vendedor     v ON v.vendedor_id     = p.vendedor_id
    LEFT JOIN producto_atributo pa ON pa.sku = p.sku
    LEFT JOIN dim_atributo      a  ON a.atributo_id = pa.atributo_id
    WHERE p.sku = %(sku)s AND p.es_actual
    GROUP BY p.producto_sk, p.sku, p.nombre, p.marca, p.precio_base, p.peso_g,
             p.activo, c.nombre, d.nombre, v.vendedor_id, v.nombre,
             v.valoracion, v.provincia
    """, p)
    r = cur.fetchone()
    r["resenas_recientes"] = r["resenas_recientes"] or []
    r["relacionados"] = r["relacionados"] or []
    return r


def L2(cur, p):
    cur.execute("""
    WITH filtrados AS MATERIALIZED (
        SELECT p.producto_sk, p.sku, p.nombre, p.marca, p.precio_base
        FROM dim_producto p
        JOIN dim_categoria c ON c.categoria_id = p.categoria_id
        JOIN producto_atributo f1 ON f1.sku = p.sku AND f1.atributo_id = 3
                                 AND f1.valor_texto = %(color)s
        JOIN producto_atributo f2 ON f2.sku = p.sku AND f2.atributo_id = 21
                                 AND f2.valor_texto = %(genero)s
        JOIN producto_atributo f3 ON f3.sku = p.sku AND f3.atributo_id = 1
                                 AND f3.valor_num BETWEEN %(peso_min)s AND %(peso_max)s
        WHERE p.es_actual AND p.activo
          AND c.departamento_id = %(departamento_id)s
    )
    SELECT (SELECT count(*) FROM filtrados) AS total,
           (SELECT jsonb_agg(to_jsonb(t))
              FROM (SELECT sku, nombre, marca, precio_base
                      FROM filtrados
                     ORDER BY precio_base, sku
                     LIMIT 24) t)                          AS pagina,
           (SELECT jsonb_agg(to_jsonb(f))
              FROM (SELECT a.nombre AS atributo,
                           coalesce(pa.valor_texto, pa.valor_num::text,
                                    pa.valor_bool::text) AS valor,
                           count(*) AS n
                      FROM filtrados fl
                      JOIN producto_atributo pa ON pa.sku = fl.sku
                      JOIN dim_atributo      a  ON a.atributo_id = pa.atributo_id
                     GROUP BY 1, 2
                     ORDER BY 1, 3 DESC, 2) f)             AS facetas
    """, p)
    r = cur.fetchone()
    r["pagina"] = r["pagina"] or []
    r["facetas"] = r["facetas"] or []
    return r


def L3(cur, p):
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
              FROM (SELECT count(*)             AS skus_vivos,
                           round(avg(t.nota),2) AS nota_media_catalogo,
                           sum(t.n)             AS resenas_totales
                      FROM (SELECT p.sku,
                                   count(r.resena_id) AS n,
                                   avg(r.puntuacion)  AS nota
                              FROM dim_producto p
                              LEFT JOIN resena r ON r.sku = p.sku
                             WHERE p.vendedor_id = %(vendedor_id)s AND p.es_actual
                             GROUP BY p.sku) t) s)                  AS salud_catalogo
    """, p)
    r = cur.fetchone()
    r["top_skus"] = r["top_skus"] or []
    r["ultimos_pedidos"] = r["ultimos_pedidos"] or []
    return r


SQL_PEDIDO_COMPLETO = """
    SELECT p.pedido_id, p.cliente_id, p.ts_creacion, p.estado_actual, p.canal,
           p.metodo_pago, p.importe_total,
           jsonb_build_object('calle', p.env_calle, 'cp', p.env_cp,
                              'provincia', p.env_provincia,
                              'telefono', p.env_telefono)              AS envio,
           (SELECT jsonb_agg(jsonb_build_object(
                       'n', l.linea_num, 'sku', pr.sku, 'nombre', pr.nombre,
                       'cantidad', l.cantidad, 'precio_unit', l.precio_unit,
                       'importe_neto', l.importe_neto) ORDER BY l.linea_num)
              FROM pedido_linea l
              JOIN dim_producto pr ON pr.producto_sk = l.producto_sk
             WHERE l.pedido_id = p.pedido_id)                          AS lineas,
           (SELECT jsonb_agg(jsonb_build_object('estado', h.estado, 'ts', h.ts,
                                                'actor', h.actor) ORDER BY h.ts)
              FROM pedido_estado_hist h
             WHERE h.pedido_id = p.pedido_id)                          AS historial
    FROM pedido p
"""


def L4(cur, p):
    cur.execute(SQL_PEDIDO_COMPLETO + """
    WHERE p.cliente_id = %(cliente_id)s
    ORDER BY p.ts_creacion DESC, p.pedido_id DESC
    LIMIT 10
    """, p)
    return cur.fetchall()


def L5(cur, p):
    cur.execute(SQL_PEDIDO_COMPLETO + "WHERE p.pedido_id = %(pedido_id)s", p)
    return cur.fetchone()


def L6(cur, p):
    cur.execute("""
    SELECT d.nombre                                            AS departamento,
           date_trunc('month', p.ts_creacion, 'Europe/Madrid') AS mes,
           count(DISTINCT p.pedido_id)                         AS pedidos,
           sum(l.importe_neto)                                 AS ventas
    FROM pedido p
    JOIN pedido_linea     l  ON l.pedido_id = p.pedido_id
    JOIN dim_producto     pr ON pr.producto_sk = l.producto_sk
    JOIN dim_categoria    c  ON c.categoria_id = pr.categoria_id
    JOIN dim_departamento d  ON d.departamento_id = c.departamento_id
    WHERE p.estado_actual <> 'cancelado'
      AND p.ts_creacion >= make_timestamptz(%(anio)s, 1, 1, 0, 0, 0, 'Europe/Madrid')
      AND p.ts_creacion <  make_timestamptz(%(anio)s + 1, 1, 1, 0, 0, 0, 'Europe/Madrid')
    GROUP BY ROLLUP (d.nombre, date_trunc('month', p.ts_creacion, 'Europe/Madrid'))
    ORDER BY 1 NULLS LAST, 2 NULLS LAST
    """, p)
    return cur.fetchall()


def E1(cur, p):
    skus = [s for s, _ in p["lineas"]]
    lineas = [{"sku": s, "cantidad": c} for s, c in p["lineas"]]
    cur.execute("""
    INSERT INTO pedido (pedido_id, cliente_id, canal, ts_creacion, estado_actual,
                        env_calle, env_cp, env_provincia, env_telefono,
                        metodo_pago, importe_total)
    VALUES (%(pedido_id)s, %(cliente_id)s, %(canal)s, now(), 'creado',
            %(calle)s, %(cp)s, %(provincia)s, %(telefono)s, %(metodo_pago)s, 0)
    """, p | p["envio"])
    cur.execute("""
    INSERT INTO pedido_linea (pedido_id, linea_num, producto_sk, cantidad,
                              precio_unit, descuento, importe_neto)
    SELECT %(pedido_id)s, (row_number() OVER (ORDER BY c.sku))::smallint,
           p.producto_sk, c.cantidad, p.precio_base, 0,
           round(p.precio_base * c.cantidad, 2)
    FROM jsonb_to_recordset(%(lineas)s::jsonb) AS c(sku text, cantidad smallint)
    JOIN dim_producto p ON p.sku = c.sku AND p.es_actual
    """, {"pedido_id": p["pedido_id"], "lineas": _json(lineas)})
    cur.execute("""
    UPDATE stock s
       SET unidades = s.unidades - c.cantidad, actualizado = now()
      FROM jsonb_to_recordset(%(lineas)s::jsonb) AS c(sku text, cantidad int)
     WHERE s.sku = c.sku
    """, {"lineas": _json(lineas)})
    cur.execute("""
    UPDATE pedido
       SET importe_total = (SELECT round(sum(importe_neto),2)
                              FROM pedido_linea WHERE pedido_id = %(pedido_id)s)
     WHERE pedido_id = %(pedido_id)s
    RETURNING importe_total
    """, p)
    total = cur.fetchone()["importe_total"]
    cur.execute("""
    INSERT INTO pedido_estado_hist (pedido_id, estado, ts, actor)
    VALUES (%(pedido_id)s, 'creado', now(), 'sistema')
    """, p)
    cur.execute("""
    UPDATE cliente c
       SET segmento = h.segmento
      FROM (SELECT CASE WHEN sum(p.importe_total) > 3000 THEN 'premium'
                        WHEN count(*) > 3                THEN 'recurrente'
                        ELSE 'nuevo' END AS segmento
              FROM pedido p
              JOIN pedido_linea l ON l.pedido_id = p.pedido_id
             WHERE p.cliente_id = %(cliente_id)s
               AND p.estado_actual <> 'cancelado') h
     WHERE c.cliente_id = %(cliente_id)s
    RETURNING c.segmento
    """, p)
    segmento = cur.fetchone()["segmento"]
    cur.execute("""
    DELETE FROM lista_deseos
     WHERE cliente_id = %(cliente_id)s AND sku = ANY(%(skus)s)
    """, {"cliente_id": p["cliente_id"], "skus": skus})
    return {"importe_total": total, "segmento": segmento}


def E2(cur, p):
    cur.execute("""
    WITH cerradas AS (
        UPDATE dim_producto
           SET es_actual = FALSE, valido_hasta = CURRENT_DATE - 1
         WHERE vendedor_id = %(vendedor_id)s AND es_actual
        RETURNING sku, nombre, marca, categoria_id, vendedor_id, precio_base, peso_g, activo
    )
    INSERT INTO dim_producto (sku, nombre, marca, categoria_id, vendedor_id,
                              precio_base, peso_g, activo,
                              valido_desde, valido_hasta, es_actual)
    SELECT sku, nombre, marca, categoria_id, vendedor_id,
           round(precio_base * 1.06, 2), peso_g, activo,
           CURRENT_DATE, NULL, TRUE
    FROM cerradas
    """, p)
    reversionados = cur.rowcount
    cur.execute("""
    WITH borrados AS (
        DELETE FROM producto_atributo pa
         USING dim_producto p
         WHERE p.sku = pa.sku AND p.vendedor_id = %(vendedor_id)s AND p.es_actual
        RETURNING pa.sku, pa.atributo_id, pa.valor_texto, pa.valor_num, pa.valor_bool
    )
    INSERT INTO producto_atributo (sku, atributo_id, valor_texto, valor_num, valor_bool)
    SELECT sku, atributo_id, valor_texto,
           CASE WHEN atributo_id = 60 THEN valor_num + 12 ELSE valor_num END,
           valor_bool
    FROM borrados
    """, p)
    return {"skus_reversionados": reversionados}


def E3(cur, p):
    cur.execute("""
    UPDATE pedido
       SET estado_actual = CASE estado_actual
                             WHEN 'creado'      THEN 'pagado'
                             WHEN 'pagado'      THEN 'preparacion'
                             WHEN 'preparacion' THEN 'enviado'
                             WHEN 'enviado'     THEN 'entregado' END
     WHERE pedido_id = %(pedido_id)s
       AND estado_actual IN ('creado','pagado','preparacion','enviado')
    """, p)
    cur.execute("""
    INSERT INTO pedido_estado_hist (pedido_id, estado, ts, actor)
    SELECT pedido_id, estado_actual, now(), 'sistema'
      FROM pedido WHERE pedido_id = %(pedido_id)s
    RETURNING estado
    """, p)
    return {"estado_nuevo": cur.fetchone()["estado"]}


def E4(cur, p):
    cur.execute("""
    WITH borrados AS (
        DELETE FROM producto_atributo WHERE sku = %(sku)s
        RETURNING sku, atributo_id, valor_texto, valor_num, valor_bool
    )
    INSERT INTO producto_atributo (sku, atributo_id, valor_texto, valor_num, valor_bool)
    SELECT sku, atributo_id, valor_texto,
           CASE WHEN atributo_id = 1 THEN valor_num + %(delta_peso)s ELSE valor_num END,
           valor_bool
    FROM borrados
    """, p)
    return {"atributos": cur.rowcount}


def _json(x):
    import json
    return json.dumps(x)


OPERACIONES = {"L1": L1, "L2": L2, "L3": L3, "L4": L4, "L5": L5, "L6": L6,
               "E1": E1, "E2": E2, "E3": E3, "E4": E4}
