-- =====================================================================
--  R.2 · Diagnóstico del error de E1 (paso 6: segmento del cliente)
--
--  Regla de negocio (pedidos no cancelados):
--    gasto > 3.000 € -> premium; más de 3 pedidos -> recurrente; si no, nuevo.
--
--  El paso 6 original hace JOIN con pedido_linea, así que cada pedido
--  aparece una vez POR LÍNEA: count(*) cuenta líneas y sum(importe_total)
--  suma el total del pedido tantas veces como líneas tiene.
--
--  Uso:
--    docker exec mendiak-pg psql -U mendiak -d mendiak -f /scripts/05_e1_diagnostico.sql
-- =====================================================================
SET search_path TO mendiak, public;

\echo '=== 1. Cliente 1: lo que hay frente a lo que cuenta el paso 6 ==='
SELECT
    (SELECT count(*)
       FROM pedido
      WHERE cliente_id = 1 AND estado_actual <> 'cancelado')            AS pedidos_reales,
    (SELECT count(*)
       FROM pedido p JOIN pedido_linea l ON l.pedido_id = p.pedido_id
      WHERE p.cliente_id = 1 AND p.estado_actual <> 'cancelado')        AS pedidos_segun_e1,
    (SELECT sum(importe_total)
       FROM pedido
      WHERE cliente_id = 1 AND estado_actual <> 'cancelado')            AS gasto_real,
    (SELECT sum(p.importe_total)
       FROM pedido p JOIN pedido_linea l ON l.pedido_id = p.pedido_id
      WHERE p.cliente_id = 1 AND p.estado_actual <> 'cancelado')        AS gasto_segun_e1;

\echo '=== 2. Clientes a los que el paso 6 asignaría un segmento incorrecto ==='
WITH bien AS (
    SELECT cliente_id,
           CASE WHEN sum(importe_total) > 3000 THEN 'premium'
                WHEN count(*) > 3              THEN 'recurrente'
                ELSE 'nuevo' END AS segmento
      FROM pedido
     WHERE estado_actual <> 'cancelado'
     GROUP BY cliente_id
), mal AS (
    SELECT p.cliente_id,
           CASE WHEN sum(p.importe_total) > 3000 THEN 'premium'
                WHEN count(*) > 3                THEN 'recurrente'
                ELSE 'nuevo' END AS segmento
      FROM pedido p
      JOIN pedido_linea l ON l.pedido_id = p.pedido_id
     WHERE p.estado_actual <> 'cancelado'
     GROUP BY p.cliente_id
)
SELECT b.segmento AS segmento_correcto,
       m.segmento AS segmento_con_error,
       count(*)   AS clientes
  FROM bien b
  JOIN mal  m USING (cliente_id)
 WHERE b.segmento <> m.segmento
 GROUP BY 1, 2
 ORDER BY 3 DESC;

\echo '=== 3. Total de clientes afectados y porcentaje sobre los que tienen pedidos ==='
WITH bien AS (
    SELECT cliente_id,
           CASE WHEN sum(importe_total) > 3000 THEN 'premium'
                WHEN count(*) > 3              THEN 'recurrente'
                ELSE 'nuevo' END AS segmento
      FROM pedido WHERE estado_actual <> 'cancelado' GROUP BY cliente_id
), mal AS (
    SELECT p.cliente_id,
           CASE WHEN sum(p.importe_total) > 3000 THEN 'premium'
                WHEN count(*) > 3                THEN 'recurrente'
                ELSE 'nuevo' END AS segmento
      FROM pedido p JOIN pedido_linea l ON l.pedido_id = p.pedido_id
     WHERE p.estado_actual <> 'cancelado' GROUP BY p.cliente_id
)
SELECT count(*) FILTER (WHERE b.segmento <> m.segmento)                      AS clientes_mal,
       count(*)                                                              AS clientes_con_pedidos,
       round(100.0 * count(*) FILTER (WHERE b.segmento <> m.segmento) / count(*), 1) AS porcentaje
  FROM bien b JOIN mal m USING (cliente_id);
