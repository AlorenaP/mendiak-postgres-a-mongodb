-- =====================================================================
--  PARTE 0 · Pruebas extra del punto de partida
--
--  1) L4 dos veces seguidas con el cliente 1 (caché fría y caliente).
--     Ejecutar justo después de reiniciar PostgreSQL y vaciar la caché.
--  2) L1 con el producto atípico MEND-000001 (43.020 reseñas).
--
--  Mismo SQL que 03_operaciones.sql; solo cambian los parámetros.
-- =====================================================================
SET search_path TO mendiak, public;
\timing on

\echo '=== L4 · cliente 1 · PRIMERA ejecución (caché fría) ==='
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT p.pedido_id, p.ts_creacion, p.estado_actual, p.canal,
       p.metodo_pago, p.importe_total,
       jsonb_build_object('calle', p.env_calle, 'cp', p.env_cp,
                          'provincia', p.env_provincia)               AS envio,
       (SELECT jsonb_agg(jsonb_build_object(
                   'n', l.linea_num, 'sku', pr.sku, 'nombre', pr.nombre,
                   'cantidad', l.cantidad, 'precio_unit', l.precio_unit,
                   'importe_neto', l.importe_neto) ORDER BY l.linea_num)
          FROM pedido_linea l
          JOIN dim_producto pr ON pr.producto_sk = l.producto_sk
         WHERE l.pedido_id = p.pedido_id)                             AS lineas,
       (SELECT jsonb_agg(jsonb_build_object('estado', h.estado, 'ts', h.ts)
                         ORDER BY h.ts)
          FROM pedido_estado_hist h
         WHERE h.pedido_id = p.pedido_id)                             AS historial
FROM pedido p
WHERE p.cliente_id = 1
ORDER BY p.ts_creacion DESC, p.pedido_id DESC
LIMIT 10;

\echo '=== L4 · cliente 1 · SEGUNDA ejecución (caché caliente) ==='
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT p.pedido_id, p.ts_creacion, p.estado_actual, p.canal,
       p.metodo_pago, p.importe_total,
       jsonb_build_object('calle', p.env_calle, 'cp', p.env_cp,
                          'provincia', p.env_provincia)               AS envio,
       (SELECT jsonb_agg(jsonb_build_object(
                   'n', l.linea_num, 'sku', pr.sku, 'nombre', pr.nombre,
                   'cantidad', l.cantidad, 'precio_unit', l.precio_unit,
                   'importe_neto', l.importe_neto) ORDER BY l.linea_num)
          FROM pedido_linea l
          JOIN dim_producto pr ON pr.producto_sk = l.producto_sk
         WHERE l.pedido_id = p.pedido_id)                             AS lineas,
       (SELECT jsonb_agg(jsonb_build_object('estado', h.estado, 'ts', h.ts)
                         ORDER BY h.ts)
          FROM pedido_estado_hist h
         WHERE h.pedido_id = p.pedido_id)                             AS historial
FROM pedido p
WHERE p.cliente_id = 1
ORDER BY p.ts_creacion DESC, p.pedido_id DESC
LIMIT 10;

\echo '=== L1 · producto atípico MEND-000001 ==='
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT p.sku, p.nombre, p.marca, p.precio_base, p.peso_g, p.activo,
       c.nombre AS categoria,
       d.nombre AS departamento,
       v.vendedor_id, v.nombre AS vendedor, v.valoracion, v.provincia,
       jsonb_object_agg(a.nombre,
            coalesce(pa.valor_texto, pa.valor_num::text, pa.valor_bool::text)
       ) AS ficha_tecnica,
       (SELECT count(*)                    FROM resena r WHERE r.sku = p.sku) AS n_resenas,
       (SELECT round(avg(r.puntuacion),2)  FROM resena r WHERE r.sku = p.sku) AS nota_media,
       (SELECT jsonb_object_agg(h.puntuacion, h.n)
          FROM (SELECT r.puntuacion, count(*) AS n
                  FROM resena r WHERE r.sku = p.sku
                 GROUP BY r.puntuacion) h)                                    AS histograma,
       (SELECT jsonb_agg(to_jsonb(u))
          FROM (SELECT r.puntuacion, r.titulo, r.texto, r.votos_utiles, r.ts,
                       cl.nombre AS cliente
                  FROM resena r
                  JOIN cliente cl ON cl.cliente_id = r.cliente_id
                 WHERE r.sku = p.sku
                 ORDER BY r.ts DESC, r.resena_id DESC
                 LIMIT 3) u)                                                  AS resenas_recientes,
       (SELECT jsonb_agg(to_jsonb(rel))
          FROM (SELECT p2.sku, p2.nombre, p2.precio_base,
                       count(r2.resena_id)          AS n_resenas,
                       round(avg(r2.puntuacion),2)  AS nota
                  FROM dim_producto p2
                  LEFT JOIN resena r2 ON r2.sku = p2.sku
                 WHERE p2.categoria_id = p.categoria_id
                   AND p2.es_actual AND p2.activo AND p2.sku <> p.sku
                 GROUP BY p2.sku, p2.nombre, p2.precio_base
                HAVING count(r2.resena_id) > 0
                 ORDER BY avg(r2.puntuacion) DESC, count(r2.resena_id) DESC, p2.sku
                 LIMIT 6) rel)                                                AS relacionados
FROM dim_producto p
JOIN dim_categoria    c ON c.categoria_id    = p.categoria_id
JOIN dim_departamento d ON d.departamento_id = c.departamento_id
JOIN dim_vendedor     v ON v.vendedor_id     = p.vendedor_id
LEFT JOIN producto_atributo pa ON pa.sku = p.sku
LEFT JOIN dim_atributo      a  ON a.atributo_id = pa.atributo_id
WHERE p.sku = 'MEND-000001' AND p.es_actual
GROUP BY p.producto_sk, p.sku, p.nombre, p.marca, p.precio_base, p.peso_g,
         p.activo, c.nombre, d.nombre, v.vendedor_id, v.nombre,
         v.valoracion, v.provincia;
