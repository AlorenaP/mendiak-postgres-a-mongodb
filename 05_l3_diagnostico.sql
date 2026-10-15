-- =====================================================================
--  R.2 · Diagnóstico de L3: "salud del catálogo", original frente a
--  mejorada, con un vendedor pequeño (1500) y con el más grande (1).
--
--  Uso:
--    docker exec mendiak-pg psql -U mendiak -d mendiak -f /scripts/05_l3_diagnostico.sql
-- =====================================================================
SET search_path TO mendiak, public;

\echo '=== Salud del catálogo · ORIGINAL · vendedor 1500 ==='
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT count(*), round(avg(t.nota),2), sum(t.n)
  FROM (SELECT p.sku, count(r.resena_id) AS n, avg(r.puntuacion) AS nota
          FROM dim_producto p
          LEFT JOIN resena r ON r.sku = p.sku
         WHERE p.vendedor_id = 1500 AND p.es_actual
         GROUP BY p.sku) t;

\echo '=== Salud del catálogo · MEJORADA · vendedor 1500 ==='
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT count(*),
       round(avg(CASE WHEN rr.n > 0 THEN rr.suma::numeric / rr.n END), 2),
       coalesce(sum(rr.n), 0)
  FROM dim_producto p
  LEFT JOIN resena_resumen rr ON rr.sku = p.sku
 WHERE p.vendedor_id = 1500 AND p.es_actual;

\echo '=== Salud del catálogo · ORIGINAL · vendedor 1 ==='
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT count(*), round(avg(t.nota),2), sum(t.n)
  FROM (SELECT p.sku, count(r.resena_id) AS n, avg(r.puntuacion) AS nota
          FROM dim_producto p
          LEFT JOIN resena r ON r.sku = p.sku
         WHERE p.vendedor_id = 1 AND p.es_actual
         GROUP BY p.sku) t;

\echo '=== Salud del catálogo · MEJORADA · vendedor 1 ==='
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT count(*),
       round(avg(CASE WHEN rr.n > 0 THEN rr.suma::numeric / rr.n END), 2),
       coalesce(sum(rr.n), 0)
  FROM dim_producto p
  LEFT JOIN resena_resumen rr ON rr.sku = p.sku
 WHERE p.vendedor_id = 1 AND p.es_actual;
