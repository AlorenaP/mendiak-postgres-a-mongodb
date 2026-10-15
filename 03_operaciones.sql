-- =====================================================================
--  MENDIAK OUTDOOR — LAS DIEZ OPERACIONES  (v3)
--
--  Seis lecturas (L1-L6) y cuatro escrituras (E1-E4), tal y como las
--  ha escrito el equipo actual. Son la versión ORIGINAL: no las
--  arregléis aquí. En la parte R escribiréis la versión mejorada.
--
--  Ejecutad cada una con EXPLAIN (ANALYZE, BUFFERS) y anotad:
--    tiempo, filas devueltas, filas examinadas, buffers leídos (read)
--    y encontrados en caché (hit), y el nodo del plan donde se va el
--    tiempo.
--
--  Las escrituras terminan en ROLLBACK: se pueden repetir tal cual.
-- =====================================================================
SET search_path TO mendiak, public;
\timing on


-- =====================================================================
--  L1 · FICHA DE PRODUCTO — 2.900.000 veces al día
--  Probadla con MEND-000123 (producto normal) y con MEND-000001
--  (producto atípico, con más de 40.000 reseñas).
-- =====================================================================
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
       -- Carrusel "lo mejor valorado de esta categoría"
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
WHERE p.sku = 'MEND-000123' AND p.es_actual
GROUP BY p.producto_sk, p.sku, p.nombre, p.marca, p.precio_base, p.peso_g,
         p.activo, c.nombre, d.nombre, v.vendedor_id, v.nombre,
         v.valoracion, v.provincia;


-- =====================================================================
--  L2 · LISTADO DE CATEGORÍA CON FILTROS Y FACETAS — 780.000 veces al día
--  Tres filtros = tres self-joins contra el EAV. Las facetas (contadores
--  que se pintan junto a cada filtro) obligan a volver al EAV con TODO
--  el conjunto filtrado. Probad a quitar o añadir un filtro.
-- =====================================================================
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
WITH filtrados AS MATERIALIZED (
    SELECT p.producto_sk, p.sku, p.nombre, p.marca, p.precio_base
    FROM dim_producto p
    JOIN dim_categoria c ON c.categoria_id = p.categoria_id
    JOIN producto_atributo f1 ON f1.sku = p.sku AND f1.atributo_id = 3
                             AND f1.valor_texto = 'negro'
    JOIN producto_atributo f2 ON f2.sku = p.sku AND f2.atributo_id = 21
                             AND f2.valor_texto = 'unisex'
    JOIN producto_atributo f3 ON f3.sku = p.sku AND f3.atributo_id = 1
                             AND f3.valor_num BETWEEN 300 AND 2500
    WHERE p.es_actual AND p.activo
      AND c.departamento_id = 1
)
SELECT (SELECT count(*) FROM filtrados) AS total_resultados,
       (SELECT jsonb_agg(to_jsonb(t))
          FROM (SELECT sku, nombre, marca, precio_base
                  FROM filtrados
                 ORDER BY precio_base, sku
                 LIMIT 24) t)                              AS pagina,
       (SELECT jsonb_agg(to_jsonb(f))
          FROM (SELECT a.nombre AS atributo,
                       coalesce(pa.valor_texto, pa.valor_num::text,
                                pa.valor_bool::text) AS valor,
                       count(*) AS n
                  FROM filtrados fl
                  JOIN producto_atributo pa ON pa.sku = fl.sku
                  JOIN dim_atributo      a  ON a.atributo_id = pa.atributo_id
                 GROUP BY 1, 2
                 ORDER BY 1, 3 DESC, 2) f)                 AS facetas;


-- =====================================================================
--  L3 · PANEL DEL VENDEDOR — 12.000 veces al día
--  vendedor_id vive en dim_producto, la fecha en pedido y el importe en
--  pedido_linea. Probadla con el vendedor 1 (el más grande) y con el
--  vendedor 1500 (unas decenas de SKUs).
-- =====================================================================
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
WITH lineas_vendedor AS MATERIALIZED (
    SELECT li.pedido_id, li.linea_num, li.cantidad, li.precio_unit,
           li.importe_neto,
           pr.sku, pr.nombre,
           ped.ts_creacion, ped.estado_actual, ped.canal, ped.cliente_id
    FROM dim_producto pr
    JOIN pedido_linea li  ON li.producto_sk = pr.producto_sk
    JOIN pedido       ped ON ped.pedido_id  = li.pedido_id
    WHERE pr.vendedor_id = 1
      AND ped.ts_creacion >= TIMESTAMPTZ '2025-09-02 00:00:00+02'   -- últimos 365 días
      AND ped.estado_actual <> 'cancelado'
)
SELECT (SELECT count(DISTINCT pedido_id)     FROM lineas_vendedor) AS pedidos,
       (SELECT round(sum(importe_neto),2)    FROM lineas_vendedor) AS facturado,
       (SELECT jsonb_agg(to_jsonb(t))
          FROM (SELECT sku, nombre,
                       sum(cantidad)                AS unidades,
                       round(sum(importe_neto),2)   AS importe
                  FROM lineas_vendedor
                 GROUP BY sku, nombre
                 ORDER BY 4 DESC, sku
                 LIMIT 10) t)                                      AS top_skus,
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
                 LIMIT 50) u)                                      AS ultimos_pedidos,
       -- salud del catálogo: la otra mitad del panel
       (SELECT to_jsonb(s)
          FROM (SELECT count(*)             AS skus_vivos,
                       round(avg(t.nota),2) AS nota_media_catalogo,
                       sum(t.n)             AS resenas_totales
                  FROM (SELECT p.sku,
                               count(r.resena_id) AS n,
                               avg(r.puntuacion)  AS nota
                          FROM dim_producto p
                          LEFT JOIN resena r ON r.sku = p.sku
                         WHERE p.vendedor_id = 1 AND p.es_actual
                         GROUP BY p.sku) t) s)                     AS salud_catalogo;


-- =====================================================================
--  L4 · MIS PEDIDOS — 350.000 veces al día
--  Los 10 últimos pedidos de un cliente, completos: líneas, estados y
--  dirección. Probadla con el cliente 1 (miles de pedidos) y con el
--  cliente 4242 (unos pocos).
-- =====================================================================
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


-- =====================================================================
--  L5 · SEGUIMIENTO DE UN PEDIDO ("¿dónde está mi pedido?")
--  900.000 veces al día. Un pedido completo: cabecera, dirección,
--  líneas con el nombre del producto vendido e historial de estados.
-- =====================================================================
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT p.pedido_id, p.cliente_id, p.ts_creacion, p.estado_actual, p.canal,
       p.metodo_pago, p.importe_total,
       jsonb_build_object('calle', p.env_calle, 'cp', p.env_cp,
                          'provincia', p.env_provincia,
                          'telefono', p.env_telefono)                 AS envio,
       (SELECT jsonb_agg(jsonb_build_object(
                   'n', l.linea_num, 'sku', pr.sku, 'nombre', pr.nombre,
                   'cantidad', l.cantidad, 'precio_unit', l.precio_unit,
                   'importe_neto', l.importe_neto) ORDER BY l.linea_num)
          FROM pedido_linea l
          JOIN dim_producto pr ON pr.producto_sk = l.producto_sk
         WHERE l.pedido_id = p.pedido_id)                             AS lineas,
       (SELECT jsonb_agg(jsonb_build_object('estado', h.estado, 'ts', h.ts,
                                            'actor', h.actor) ORDER BY h.ts)
          FROM pedido_estado_hist h
         WHERE h.pedido_id = p.pedido_id)                             AS historial
FROM pedido p
WHERE p.pedido_id = 'PED-00412345';


-- =====================================================================
--  L6 · INFORME DE VENTAS POR DEPARTAMENTO Y MES — 50 veces al día
--  Lo pide dirección. Subtotales por departamento y total general con
--  ROLLUP. Mañana pedirán otro corte (por marca, por provincia...).
-- =====================================================================
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT d.nombre                                          AS departamento,
       date_trunc('month', p.ts_creacion, 'Europe/Madrid') AS mes,
       count(DISTINCT p.pedido_id)                       AS pedidos,
       sum(l.importe_neto)                               AS ventas
FROM pedido p
JOIN pedido_linea     l  ON l.pedido_id = p.pedido_id
JOIN dim_producto     pr ON pr.producto_sk = l.producto_sk
JOIN dim_categoria    c  ON c.categoria_id = pr.categoria_id
JOIN dim_departamento d  ON d.departamento_id = c.departamento_id
WHERE p.estado_actual <> 'cancelado'
  AND p.ts_creacion >= TIMESTAMPTZ '2025-01-01 00:00:00+01'
  AND p.ts_creacion <  TIMESTAMPTZ '2026-01-01 00:00:00+01'
GROUP BY ROLLUP (d.nombre, date_trunc('month', p.ts_creacion, 'Europe/Madrid'))
ORDER BY 1 NULLS LAST, 2 NULLS LAST;


-- =====================================================================
--  E1 · CREAR PEDIDO — 6.250 veces al día
--  Una acción del usuario = siete sentencias sobre cinco tablas dentro
--  de una transacción. Fijaos en qué se congela (precio_unit) y qué no
--  (el nombre del producto: se recupera con el producto_sk vendido).
--  Termina en ROLLBACK: se puede repetir tal cual.
-- =====================================================================
\set pedido_nuevo 'PED-90000001'
\set cliente_nuevo 1

BEGIN;

-- 1) cabecera
INSERT INTO pedido (pedido_id, cliente_id, canal, ts_creacion, estado_actual,
                    env_calle, env_cp, env_provincia, env_telefono,
                    metodo_pago, importe_total)
VALUES (:'pedido_nuevo', :cliente_nuevo, 'app_ios', now(), 'creado',
        'Calle Autonomía 21', '48010', 'Bizkaia', '600111222', 'tarjeta', 0);

-- 2) líneas: resolver la versión vigente de cada SKU y congelar su precio
INSERT INTO pedido_linea (pedido_id, linea_num, producto_sk, cantidad,
                          precio_unit, descuento, importe_neto)
SELECT :'pedido_nuevo',
       (row_number() OVER (ORDER BY c.sku))::smallint,
       p.producto_sk,
       c.cantidad,
       p.precio_base,
       0,
       round(p.precio_base * c.cantidad, 2)
FROM (VALUES ('MEND-000001', 1::smallint),
             ('MEND-120200', 2::smallint),
             ('MEND-300500', 1::smallint)) AS c(sku, cantidad)
JOIN dim_producto p ON p.sku = c.sku AND p.es_actual;

-- 3) descontar el stock. Si de algún SKU no hay unidades suficientes,
--    el CHECK (unidades >= 0) falla y la transacción entera queda abortada.
UPDATE stock s
   SET unidades = s.unidades - c.cantidad, actualizado = now()
  FROM (VALUES ('MEND-000001', 1), ('MEND-120200', 2), ('MEND-300500', 1)) AS c(sku, cantidad)
 WHERE s.sku = c.sku;

-- 4) total de la cabecera
UPDATE pedido
   SET importe_total = (SELECT round(sum(importe_neto),2)
                          FROM pedido_linea WHERE pedido_id = :'pedido_nuevo')
 WHERE pedido_id = :'pedido_nuevo';

-- 5) primera transición de estado
INSERT INTO pedido_estado_hist (pedido_id, estado, ts, actor)
VALUES (:'pedido_nuevo', 'creado', now(), 'sistema');

-- 6) recalcular el segmento del cliente con todo su historial
--    (premium si ha gastado más de 3.000 €, recurrente si tiene más de 3 pedidos)
UPDATE cliente c
   SET segmento = h.segmento
  FROM (SELECT CASE WHEN sum(p.importe_total) > 3000 THEN 'premium'
                    WHEN count(*) > 3                THEN 'recurrente'
                    ELSE 'nuevo' END AS segmento
          FROM pedido p
          JOIN pedido_linea l ON l.pedido_id = p.pedido_id
         WHERE p.cliente_id = :cliente_nuevo
           AND p.estado_actual <> 'cancelado') h
 WHERE c.cliente_id = :cliente_nuevo;

-- 7) lo comprado sale de la lista de deseos
DELETE FROM lista_deseos
 WHERE cliente_id = :cliente_nuevo
   AND sku IN ('MEND-000001','MEND-120200','MEND-300500');

ROLLBACK;   -- cambiad a COMMIT si queréis dejar el pedido en la base


-- =====================================================================
--  E2 · EL VENDEDOR PUBLICA SU CATÁLOGO ACTUALIZADO — 40 veces al día
--  Nueva versión SCD2 de cada SKU (precio +6 %) y ficha técnica
--  reescrita entera (el vendedor sube su CSV completo; solo cambia la
--  garantía, pero el sistema borra y vuelve a insertar los atributos).
--  Probadlo con el vendedor 1 y con el 1500. Termina en ROLLBACK.
-- =====================================================================
\set vendedor 1

BEGIN;

-- 1) cerrar la versión vigente de cada SKU y abrir la nueva con el
--    precio actualizado. Las líneas de pedido ya vendidas siguen
--    apuntando a la versión antigua: su precio no cambia.
WITH cerradas AS (
    UPDATE dim_producto
       SET es_actual = FALSE, valido_hasta = CURRENT_DATE - 1
     WHERE vendedor_id = :vendedor AND es_actual
    RETURNING sku, nombre, marca, categoria_id, vendedor_id, precio_base, peso_g, activo
)
INSERT INTO dim_producto (sku, nombre, marca, categoria_id, vendedor_id,
                          precio_base, peso_g, activo,
                          valido_desde, valido_hasta, es_actual)
SELECT sku, nombre, marca, categoria_id, vendedor_id,
       round(precio_base * 1.06, 2), peso_g, activo,
       CURRENT_DATE, NULL, TRUE
FROM cerradas;

-- 2) reescribir la ficha técnica entera de esos SKUs
WITH borrados AS (
    DELETE FROM producto_atributo pa
     USING dim_producto p
     WHERE p.sku = pa.sku AND p.vendedor_id = :vendedor AND p.es_actual
    RETURNING pa.sku, pa.atributo_id, pa.valor_texto, pa.valor_num, pa.valor_bool
)
INSERT INTO producto_atributo (sku, atributo_id, valor_texto, valor_num, valor_bool)
SELECT sku, atributo_id, valor_texto,
       CASE WHEN atributo_id = 60 THEN valor_num + 12 ELSE valor_num END,   -- garantía +12 meses
       valor_bool
FROM borrados;

-- 3) qué queda tocado
SELECT (SELECT count(*) FROM dim_producto
         WHERE vendedor_id = :vendedor AND valido_desde = CURRENT_DATE)    AS skus_reversionados,
       (SELECT count(*) FROM pedido_linea l
          JOIN dim_producto p ON p.producto_sk = l.producto_sk
         WHERE p.vendedor_id = :vendedor AND NOT p.es_actual)              AS lineas_en_versiones_cerradas;

ROLLBACK;


-- =====================================================================
--  E3 · AVANZAR EL ESTADO DE UN PEDIDO — 25.000 veces al día
--  Cada pedido pasa por cuatro transiciones (6.250 x 4). Se cambia el
--  estado actual y se apunta la transición en el historial.
--  Probadlo con un pedido de las últimas horas (todavía en curso).
-- =====================================================================
\set pedido 'PED-00599999'

BEGIN;

UPDATE pedido
   SET estado_actual = CASE estado_actual
                         WHEN 'creado'      THEN 'pagado'
                         WHEN 'pagado'      THEN 'preparacion'
                         WHEN 'preparacion' THEN 'enviado'
                         WHEN 'enviado'     THEN 'entregado' END
 WHERE pedido_id = :'pedido'
   AND estado_actual IN ('creado','pagado','preparacion','enviado');

INSERT INTO pedido_estado_hist (pedido_id, estado, ts, actor)
SELECT pedido_id, estado_actual, now(), 'sistema'
  FROM pedido WHERE pedido_id = :'pedido';

ROLLBACK;


-- =====================================================================
--  E4 · EDITAR LA FICHA TÉCNICA DE UN PRODUCTO — 3.000 veces al día
--  El vendedor corrige el peso en el formulario y la aplicación guarda
--  el formulario entero: borra los atributos del SKU y los vuelve a
--  insertar. Termina en ROLLBACK.
-- =====================================================================
\set sku 'MEND-000123'

BEGIN;

WITH borrados AS (
    DELETE FROM producto_atributo WHERE sku = :'sku'
    RETURNING sku, atributo_id, valor_texto, valor_num, valor_bool
)
INSERT INTO producto_atributo (sku, atributo_id, valor_texto, valor_num, valor_bool)
SELECT sku, atributo_id, valor_texto,
       CASE WHEN atributo_id = 1 THEN valor_num + 10 ELSE valor_num END,    -- peso +10 g
       valor_bool
FROM borrados;

ROLLBACK;


-- =====================================================================
--  MEDIDAS DE APOYO PARA EL APARTADO A.2
-- =====================================================================

-- Tamaño en disco y bytes por fila
SELECT relname AS tabla,
       n_live_tup AS filas,
       pg_size_pretty(pg_total_relation_size(relid)) AS tamano_total,
       CASE WHEN n_live_tup > 0
            THEN pg_total_relation_size(relid) / n_live_tup END AS bytes_por_fila
FROM pg_stat_user_tables
WHERE schemaname = 'mendiak'
ORDER BY pg_total_relation_size(relid) DESC;

-- Cardinalidades: media, p95 y MÁXIMO. La última columna es la que
-- decide si una relación se puede incrustar o no.
SELECT 'resenas_por_sku' AS relacion,
       round(avg(n),2) AS media,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY n)::numeric(10,1) AS p95,
       max(n) AS maximo
FROM (SELECT count(*) n FROM resena GROUP BY sku) t
UNION ALL
SELECT 'lineas_por_pedido', round(avg(n),2),
       percentile_cont(0.95) WITHIN GROUP (ORDER BY n)::numeric(10,1), max(n)
FROM (SELECT count(*) n FROM pedido_linea GROUP BY pedido_id) t
UNION ALL
SELECT 'pedidos_por_cliente', round(avg(n),2),
       percentile_cont(0.95) WITHIN GROUP (ORDER BY n)::numeric(10,1), max(n)
FROM (SELECT count(*) n FROM pedido GROUP BY cliente_id) t
UNION ALL
SELECT 'estados_por_pedido', round(avg(n),2),
       percentile_cont(0.95) WITHIN GROUP (ORDER BY n)::numeric(10,1), max(n)
FROM (SELECT count(*) n FROM pedido_estado_hist GROUP BY pedido_id) t
UNION ALL
SELECT 'atributos_por_sku', round(avg(n),2),
       percentile_cont(0.95) WITHIN GROUP (ORDER BY n)::numeric(10,1), max(n)
FROM (SELECT count(*) n FROM producto_atributo GROUP BY sku) t
UNION ALL
SELECT 'skus_por_vendedor', round(avg(n),2),
       percentile_cont(0.95) WITHIN GROUP (ORDER BY n)::numeric(10,1), max(n)
FROM (SELECT count(*) n FROM dim_producto WHERE es_actual GROUP BY vendedor_id) t
UNION ALL
SELECT 'deseos_por_cliente', round(avg(n),2),
       percentile_cont(0.95) WITHIN GROUP (ORDER BY n)::numeric(10,1), max(n)
FROM (SELECT count(*) n FROM lista_deseos GROUP BY cliente_id) t
UNION ALL
SELECT 'versiones_por_sku', round(avg(n),2),
       percentile_cont(0.95) WITHIN GROUP (ORDER BY n)::numeric(10,1), max(n)
FROM (SELECT count(*) n FROM dim_producto GROUP BY sku) t;
