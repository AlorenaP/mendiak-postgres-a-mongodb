-- =====================================================================
--  MENDIAK OUTDOOR — carga de datos sintéticos (v3, reproducible)
--
--  Unos 16,8 millones de filas y unos 3 GB en disco. Tarda entre 5 y 15
--  minutos según la máquina. NO reduzcáis los volúmenes.
--
--  Cambios respecto de la v2 (para que los datos se parezcan a los de
--  una tienda real y la comparación con MongoDB sea justa):
--    * Los pedidos se numeran por orden de llegada (PED-00000001 es el
--      más antiguo). Antes la fecha era aleatoria respecto del número.
--    * Cada línea apunta a la versión del producto vigente EL DÍA DE LA
--      VENTA. Antes todas apuntaban a la versión actual y el SCD2 no
--      servía para nada.
--    * El historial de estados y las reseñas se insertan por orden de
--      fecha, mezclados entre pedidos y productos, como llegan en la
--      realidad. Antes quedaban agrupados y las lecturas salían
--      artificialmente baratas.
--    * El historial de estados es coherente con estado_actual.
--    * El segmento del cliente cumple la regla de negocio de E1.
--    * Nueva tabla stock.
--
--  Reproducible: semilla fija y sin paralelismo. Dos cargas con la
--  misma versión de PostgreSQL generan exactamente los mismos datos.
-- =====================================================================
SET search_path TO mendiak, public;
SET max_parallel_workers_per_gather = 0;
SELECT setseed(0.42);

\timing on

CREATE TEMP TABLE params AS
SELECT 480000  AS n_productos,
       200000  AS n_clientes,
       3200    AS n_vendedores,
       600000  AS n_pedidos,
       2060000 AS n_resenas,      -- + 40.000 forzadas sobre MEND-000001
       600000  AS n_deseos;

-- "Ahora" en el conjunto de datos: el último pedido es del 31-8-2026.
-- Los pedidos de las últimas horas todavía no se han entregado.

-- ---------------------------------------------------------------------
-- Jerarquía de catálogo
-- ---------------------------------------------------------------------
INSERT INTO dim_departamento VALUES
 (1,'Montaña'),(2,'Escalada'),(3,'Agua'),(4,'Ciclismo'),(5,'Trail'),(6,'Camping');

INSERT INTO dim_categoria
SELECT i, 1 + (i % 6), 'Categoria ' || i
FROM generate_series(1,40) i;

-- ---------------------------------------------------------------------
-- Catálogo de 60 atributos
-- ---------------------------------------------------------------------
INSERT INTO dim_atributo (atributo_id, nombre, tipo, unidad) VALUES
 (1,'peso','numero','g'),                  (2,'talla','texto',NULL),
 (3,'color','texto',NULL),                 (4,'impermeable','booleano',NULL),
 (5,'material','texto',NULL),              (6,'plazas','numero','ud'),
 (7,'estaciones','numero','ud'),           (8,'membrana','texto',NULL),
 (9,'volumen','numero','l'),               (10,'temp_confort','numero','C'),
 (11,'horma','texto',NULL),                (12,'cana_alta','booleano',NULL),
 (13,'suela','texto',NULL),                (14,'cierre','texto',NULL),
 (15,'compresion','booleano',NULL),        (16,'longitud','numero','cm'),
 (17,'diametro','numero','mm'),            (18,'carga_max','numero','kg'),
 (19,'plegable','booleano',NULL),          (20,'autonomia','numero','h'),
 (21,'genero','texto',NULL),               (22,'temporada','texto',NULL),
 (23,'certificacion','texto',NULL),        (24,'reciclado','booleano',NULL),
 (25,'columna_agua','numero','mm'),        (26,'transpirabilidad','numero','g/m2'),
 (27,'relleno','texto',NULL),              (28,'poder_calorifico','numero','cuin'),
 (29,'capucha','booleano',NULL),           (30,'bolsillos','numero','ud'),
 (31,'ajuste','texto',NULL),               (32,'varillas','texto',NULL),
 (33,'montaje','texto',NULL),              (34,'peso_empaquetado','numero','g'),
 (35,'diametro_cuerda','numero','mm'),     (36,'caidas_norma','numero','ud'),
 (37,'factor_impacto','numero','kN'),      (38,'tratamiento','texto',NULL),
 (39,'alma','texto',NULL),                 (40,'camara','texto',NULL),
 (41,'talla_bici','texto',NULL),           (42,'grupo_cambio','texto',NULL),
 (43,'recorrido','numero','mm'),           (44,'eje','texto',NULL),
 (45,'rodamiento','texto',NULL),           (46,'potencia_lumenes','numero','lm'),
 (47,'alcance','numero','m'),              (48,'tipo_bateria','texto',NULL),
 (49,'recargable','booleano',NULL),        (50,'resistencia_agua','texto',NULL),
 (51,'pantalla','texto',NULL),             (52,'gps','booleano',NULL),
 (53,'altimetro','booleano',NULL),         (54,'conectividad','texto',NULL),
 (55,'aislamiento','texto',NULL),          (56,'forro','texto',NULL),
 (57,'costuras','texto',NULL),             (58,'packaging','texto',NULL),
 (59,'origen_fabricacion','texto',NULL),   (60,'garantia_meses','numero','meses');

-- ---------------------------------------------------------------------
-- Vendedores
-- ---------------------------------------------------------------------
INSERT INTO dim_vendedor
SELECT i,
       'Vendedor ' || i,
       (ARRAY['Bizkaia','Gipuzkoa','Araba','Navarra','Cantabria','La Rioja',
              'Huesca','Girona','Madrid','Asturias','León','Granada'])[1 + (i % 12)],
       DATE '2019-01-01' + (random()*2200)::int,
       round((3 + random()*2)::numeric, 2)
FROM generate_series(1,(SELECT n_vendedores FROM params)) i;

-- ---------------------------------------------------------------------
-- Productos (SCD2: 1 de cada 5 SKUs tiene una segunda versión, con un
-- precio un 8 % más alto desde el 1-7-2025)
-- ---------------------------------------------------------------------
INSERT INTO dim_producto
 (sku, nombre, marca, categoria_id, vendedor_id, precio_base, peso_g,
  activo, valido_desde, valido_hasta, es_actual)
SELECT 'MEND-' || lpad(i::text,6,'0'),
       'Producto ' || i,
       'Marca ' || (1 + (abs(hashtext('m' || i)) % 180)),
       1 + (abs(hashtext('c' || i)) % 40),
       1 + floor(power(random(),3) * (SELECT n_vendedores FROM params))::int,
       round((9.95 + random()*490)::numeric, 2),
       (100 + random()*4000)::int,
       random() < 0.94,
       DATE '2024-01-01',
       NULL,
       TRUE
FROM generate_series(1,(SELECT n_productos FROM params)) i;

UPDATE dim_producto
   SET valido_hasta = DATE '2025-06-30', es_actual = FALSE
 WHERE right(sku,1) IN ('0','5');                      -- 20 % de los SKUs

INSERT INTO dim_producto
 (sku, nombre, marca, categoria_id, vendedor_id, precio_base, peso_g,
  activo, valido_desde, valido_hasta, es_actual)
SELECT sku, nombre, marca, categoria_id, vendedor_id,
       round(precio_base * 1.08, 2), peso_g, activo,
       DATE '2025-07-01', NULL, TRUE
FROM dim_producto
WHERE es_actual = FALSE
ORDER BY producto_sk;

-- ---------------------------------------------------------------------
-- EAV: 5 atributos universales + 12 candidatos de la categoría.
-- UNION quita repetidos: máximo 17 filas por SKU (~7,9 M en total).
-- ---------------------------------------------------------------------
INSERT INTO producto_atributo (sku, atributo_id, valor_texto, valor_num, valor_bool)
SELECT p.sku,
       a.atributo_id,
       CASE WHEN a.tipo = 'texto' THEN
         CASE a.atributo_id
           WHEN 3  THEN (ARRAY['negro','rojo','azul','verde','gris','amarillo'])
                        [1 + abs(hashtext('col'||p.sku)) % 6]
           WHEN 21 THEN (ARRAY['hombre','mujer','unisex','infantil'])
                        [1 + abs(hashtext('gen'||p.sku)) % 4]
           WHEN 22 THEN (ARRAY['verano','invierno','entretiempo','todo el año'])
                        [1 + abs(hashtext('tmp'||p.sku)) % 4]
           WHEN 5  THEN (ARRAY['nylon','poliester','algodon','aluminio','acero','lana merino'])
                        [1 + abs(hashtext('mat'||p.sku)) % 6]
           WHEN 2  THEN (ARRAY['XS','S','M','L','XL','XXL'])
                        [1 + abs(hashtext('tal'||p.sku)) % 6]
           ELSE (ARRAY['estandar','premium','ligero','reforzado','tecnico'])
                [1 + abs(hashtext(p.sku||':'||a.atributo_id)) % 5]
         END
       END,
       CASE WHEN a.tipo = 'numero' THEN
         CASE a.atributo_id
           WHEN 1  THEN round((50 + (abs(hashtext('p'||p.sku)) % 4950))::numeric, 2)   -- peso 50..5000 g
           WHEN 10 THEN round((-20 + (abs(hashtext('t'||p.sku)) % 36))::numeric, 2)    -- temp -20..15 C
           WHEN 60 THEN (ARRAY[12,24,36,60])[1 + abs(hashtext('g'||p.sku)) % 4]
           ELSE round((1 + (abs(hashtext(p.sku||'#'||a.atributo_id)) % 300))::numeric, 2)
         END
       END,
       CASE WHEN a.tipo = 'booleano'
            THEN (abs(hashtext(p.sku||'!'||a.atributo_id)) % 2) = 0 END
FROM dim_producto p
CROSS JOIN LATERAL (
     SELECT unnest(ARRAY[1,3,5,21,22]) AS atributo_id          -- universales
     UNION
     SELECT 6 + ((p.categoria_id * 7 + k) % 55)                -- propios de la categoría
     FROM generate_series(0,11) k
) sel
JOIN dim_atributo a ON a.atributo_id = sel.atributo_id
WHERE p.es_actual;

-- ---------------------------------------------------------------------
-- Stock por SKU. Tres SKUs preparados para los ejercicios de la parte R:
--   MEND-000666 -> 0 unidades  (pedido que debe fallar entero)
--   MEND-000777 -> 1 unidad    (dos clientes compran la última)
--   los tres SKUs del pedido de ejemplo de E1 -> 500 unidades
-- ---------------------------------------------------------------------
INSERT INTO stock (sku, unidades, actualizado)
SELECT sku, 5 + abs(hashtext('st'||sku)) % 56, TIMESTAMPTZ '2026-08-31 08:00:00+02'
FROM dim_producto WHERE es_actual
ORDER BY sku;

UPDATE stock SET unidades = 0   WHERE sku = 'MEND-000666';
UPDATE stock SET unidades = 1   WHERE sku = 'MEND-000777';
UPDATE stock SET unidades = 500 WHERE sku IN ('MEND-000001','MEND-120200','MEND-300500');

-- ---------------------------------------------------------------------
-- Clientes y sus preferencias (1-1). El segmento se calcula al final.
-- ---------------------------------------------------------------------
INSERT INTO cliente (cliente_id, email, nombre, provincia, segmento, alta)
SELECT i,
       'cliente' || i || '@example.eus',
       'Cliente ' || i,
       (ARRAY['Bizkaia','Gipuzkoa','Araba','Navarra','Cantabria','La Rioja',
              'Huesca','Girona','Madrid','Asturias','León','Granada'])[1 + (i % 12)],
       'nuevo',
       DATE '2022-01-01' + (random()*1600)::int
FROM generate_series(1,(SELECT n_clientes FROM params)) i;

INSERT INTO cliente_preferencia (cliente_id, email_ofertas, push_pedidos, idioma, actualizado)
SELECT cliente_id,
       (abs(hashtext('eo'||cliente_id)) % 2) = 0,
       (abs(hashtext('pp'||cliente_id)) % 4) > 0,
       (ARRAY['es','eu','en','fr'])[1 + abs(hashtext('id'||cliente_id)) % 4],
       TIMESTAMPTZ '2026-01-01 00:00:00+01' + (random()*240) * INTERVAL '1 day'
FROM cliente;

-- ---------------------------------------------------------------------
-- Índice auxiliar: catálogo vigente con su versión anterior (si la hay)
-- ---------------------------------------------------------------------
CREATE TEMP TABLE idx_producto AS
SELECT row_number() OVER (ORDER BY a.sku) AS rn,
       a.sku, a.vendedor_id,
       a.producto_sk AS sk_actual,   a.precio_base AS precio_actual,
       v.producto_sk AS sk_anterior, v.precio_base AS precio_anterior
FROM dim_producto a
LEFT JOIN dim_producto v ON v.sku = a.sku AND NOT v.es_actual
WHERE a.es_actual;
CREATE UNIQUE INDEX ON idx_producto (rn);
ANALYZE idx_producto;

-- ---------------------------------------------------------------------
-- Lista de deseos (N-M)
-- ---------------------------------------------------------------------
INSERT INTO lista_deseos (cliente_id, sku, anadido)
SELECT q.cl, pr.sku,
       TIMESTAMPTZ '2025-01-01 00:00:00+01' + (random()*600) * INTERVAL '1 day'
FROM generate_series(1,(SELECT n_deseos FROM params)) i
CROSS JOIN LATERAL (
     SELECT i AS ancla,
            (1 + floor(random()*(SELECT n_clientes FROM params)))::int AS cl,
            (1 + floor(power(random(),2) * (SELECT count(*) FROM idx_producto)))::bigint AS prn
) q
JOIN idx_producto pr ON pr.rn = q.prn
ON CONFLICT (cliente_id, sku) DO NOTHING;

-- ---------------------------------------------------------------------
-- Pedidos, numerados por orden de llegada.
-- Clientes muy sesgados (el cliente 1 tiene miles de pedidos) y pico
-- horario entre las 20:00 y las 22:00.
-- ---------------------------------------------------------------------
CREATE TEMP TABLE pedido_gen AS
SELECT i,
       1 + floor(power(random(),2.5) * (SELECT n_clientes FROM params))::int AS cliente_id,
       TIMESTAMPTZ '2024-01-01 00:00:00+01'
         + floor(random() * 974) * INTERVAL '1 day'
         + (CASE WHEN random() < 0.50 THEN 20 + random()*2 ELSE random()*24 END) * INTERVAL '1 hour'
         AS ts
FROM generate_series(1,(SELECT n_pedidos FROM params)) i;

INSERT INTO pedido
 (pedido_id, cliente_id, canal, ts_creacion, estado_actual,
  env_calle, env_cp, env_provincia, env_telefono, metodo_pago)
SELECT 'PED-' || lpad(n::text,8,'0'),
       cliente_id,
       (ARRAY['web','app_ios','app_android'])[1 + abs(hashtext('ca' || n)) % 3],
       ts,
       CASE WHEN abs(hashtext('es' || n)) % 7 = 0 AND edad_h >= 12 THEN 'cancelado'
            WHEN edad_h <  9 THEN 'creado'
            WHEN edad_h < 18 THEN 'pagado'
            WHEN edad_h < 27 THEN 'preparacion'
            WHEN edad_h < 72 THEN 'enviado'
            ELSE 'entregado' END,
       'Calle ' || (1 + abs(hashtext('cl' || n)) % 400) || ', ' || (1 + n % 90),
       lpad((48000 + (abs(hashtext('cp' || n)) % 900))::text,5,'0'),
       (ARRAY['Bizkaia','Gipuzkoa','Araba','Navarra','Cantabria','La Rioja',
              'Huesca','Girona','Madrid','Asturias','León','Granada'])[1 + abs(hashtext('pv'||n)) % 12],
       '6' || lpad((n % 100000000)::text,8,'0'),
       (ARRAY['tarjeta','bizum','paypal','transferencia'])[1 + abs(hashtext('pg' || n)) % 4]
FROM (SELECT g.*,
             row_number() OVER (ORDER BY g.ts, g.i) AS n,
             extract(epoch FROM TIMESTAMPTZ '2026-09-01 02:00:00+02' - g.ts) / 3600 AS edad_h
        FROM pedido_gen g) x
ORDER BY n;

-- Líneas: media ~3 líneas; el 2 % de los pedidos tiene entre 8 y 40.
-- Cada línea apunta a la versión vigente el día del pedido y copia su
-- precio: los pedidos anteriores al 1-7-2025 usan la versión antigua.
INSERT INTO pedido_linea
 (pedido_id, linea_num, producto_sk, cantidad, precio_unit, descuento, importe_neto)
SELECT p.pedido_id,
       l.n::smallint,
       v.sk,
       q.cantidad,
       v.precio,
       round(v.precio * q.cantidad * q.desc_pc, 2),
       round(v.precio * q.cantidad * (1 - q.desc_pc), 2)
FROM pedido p
CROSS JOIN LATERAL generate_series(1,
     CASE WHEN abs(hashtext(p.pedido_id)) % 100 < 2
          THEN 8 + (abs(hashtext(p.pedido_id)) % 33)
          ELSE 1 + (abs(hashtext(p.pedido_id)) % 4) END) AS l(n)
CROSS JOIN LATERAL (
     SELECT l.n AS ancla,
            (1 + floor(power(random(),2) * (SELECT count(*) FROM idx_producto)))::bigint AS rn,
            (1 + floor(random()*3))::smallint AS cantidad,
            CASE WHEN random() < 0.3 THEN 0.15 ELSE 0 END::numeric AS desc_pc
) q
JOIN idx_producto pr ON pr.rn = q.rn
CROSS JOIN LATERAL (
     SELECT CASE WHEN p.ts_creacion < TIMESTAMPTZ '2025-07-01 00:00:00+02'
                  AND pr.sk_anterior IS NOT NULL
                 THEN pr.sk_anterior ELSE pr.sk_actual END AS sk,
            CASE WHEN p.ts_creacion < TIMESTAMPTZ '2025-07-01 00:00:00+02'
                  AND pr.sk_anterior IS NOT NULL
                 THEN pr.precio_anterior ELSE pr.precio_actual END AS precio
) v
ORDER BY p.pedido_id, l.n;

UPDATE pedido p
   SET importe_total = t.total
  FROM (SELECT pedido_id, round(sum(importe_neto),2) AS total
          FROM pedido_linea GROUP BY pedido_id) t
 WHERE t.pedido_id = p.pedido_id;

-- Historial de estados, coherente con estado_actual e insertado por
-- orden de fecha (las transiciones de un pedido quedan repartidas).
--   normal:    creado +0 h, pagado +9 h, preparacion +18 h, enviado +27 h, entregado +72 h
--   cancelado: creado +0 h, pagado +9 h, cancelado +12 h
INSERT INTO pedido_estado_hist (pedido_id, estado, ts, actor)
SELECT pedido_id, estado, ts, actor
FROM (
    SELECT p.pedido_id, e.estado, e.orden,
           p.ts_creacion + e.desfase AS ts,
           CASE e.estado WHEN 'preparacion' THEN 'vendedor'
                         WHEN 'cancelado'   THEN 'soporte'
                         ELSE 'sistema' END AS actor
    FROM pedido p
    CROSS JOIN LATERAL (VALUES
        ('creado',      0, INTERVAL '0 hours'),
        ('pagado',      1, INTERVAL '9 hours'),
        ('preparacion', 2, INTERVAL '18 hours'),
        ('enviado',     3, INTERVAL '27 hours'),
        ('entregado',   4, INTERVAL '72 hours'),
        ('cancelado',   9, INTERVAL '12 hours')) AS e(estado, orden, desfase)
    WHERE (p.estado_actual = 'cancelado' AND e.estado IN ('creado','pagado','cancelado'))
       OR (p.estado_actual <> 'cancelado' AND e.estado <> 'cancelado'
           AND e.orden <= array_position(ARRAY['creado','pagado','preparacion',
                                               'enviado','entregado'], p.estado_actual) - 1)
) t
ORDER BY ts, pedido_id, orden;

-- ---------------------------------------------------------------------
-- Reseñas, por orden de fecha. Cola larga + un producto atípico:
-- MEND-000001 recibe 40.000 reseñas más que cualquier otro SKU.
-- ---------------------------------------------------------------------
INSERT INTO resena (sku, cliente_id, ts, puntuacion, titulo, texto, votos_utiles)
SELECT sku, cid, ts, punt, titulo, texto, votos
FROM (
    SELECT pr.sku, q.cid,
           TIMESTAMPTZ '2024-01-01 00:00:00+01' + (random()*974) * INTERVAL '1 day' AS ts,
           (1 + floor(random()*5))::smallint AS punt,
           'Opinión ' || i AS titulo,
           repeat('Texto de la reseña con detalle suficiente. ', 2 + (i % 4)) AS texto,
           (random()*50)::int AS votos
    FROM generate_series(1,(SELECT n_resenas FROM params)) i
    CROSS JOIN LATERAL (
         SELECT i AS ancla,
                (1 + floor(power(random(),2) * (SELECT count(*) FROM idx_producto)))::bigint AS prn,
                (1 + floor(random()*(SELECT n_clientes FROM params)))::int AS cid
    ) q
    JOIN idx_producto pr ON pr.rn = q.prn
    UNION ALL
    SELECT 'MEND-000001',
           1 + floor(random()*(SELECT n_clientes FROM params))::int,
           TIMESTAMPTZ '2024-01-01 00:00:00+01' + (random()*974) * INTERVAL '1 day',
           (1 + floor(random()*5))::smallint,
           'Opinión destacada ' || i,
           repeat('Texto de la reseña con detalle suficiente. ', 2 + (i % 4)),
           (random()*50)::int
    FROM generate_series(1,40000) i
) r
ORDER BY ts, titulo;

-- ---------------------------------------------------------------------
-- Segmento del cliente, con la misma regla que aplica E1:
--   gasto > 3.000 € -> premium; más de 3 pedidos -> recurrente; si no, nuevo.
--   (pedidos no cancelados)
-- ---------------------------------------------------------------------
UPDATE cliente c
   SET segmento = CASE WHEN t.gasto > 3000 THEN 'premium'
                       WHEN t.n > 3        THEN 'recurrente'
                       ELSE 'nuevo' END
  FROM (SELECT cliente_id, count(*) AS n, sum(importe_total) AS gasto
          FROM pedido WHERE estado_actual <> 'cancelado'
         GROUP BY cliente_id) t
 WHERE t.cliente_id = c.cliente_id;

-- ---------------------------------------------------------------------
VACUUM ANALYZE;

-- ---------------------------------------------------------------------
-- Recuento final: punto de partida del apartado A.2
-- ---------------------------------------------------------------------
SELECT 'dim_departamento'   AS tabla, count(*) FROM dim_departamento
UNION ALL SELECT 'dim_categoria',       count(*) FROM dim_categoria
UNION ALL SELECT 'dim_atributo',        count(*) FROM dim_atributo
UNION ALL SELECT 'dim_vendedor',        count(*) FROM dim_vendedor
UNION ALL SELECT 'dim_producto',        count(*) FROM dim_producto
UNION ALL SELECT 'dim_producto (vivos)',count(*) FROM dim_producto WHERE es_actual
UNION ALL SELECT 'producto_atributo',   count(*) FROM producto_atributo
UNION ALL SELECT 'stock',               count(*) FROM stock
UNION ALL SELECT 'cliente',             count(*) FROM cliente
UNION ALL SELECT 'cliente_preferencia', count(*) FROM cliente_preferencia
UNION ALL SELECT 'lista_deseos',        count(*) FROM lista_deseos
UNION ALL SELECT 'pedido',              count(*) FROM pedido
UNION ALL SELECT 'pedido_linea',        count(*) FROM pedido_linea
UNION ALL SELECT 'pedido_estado_hist',  count(*) FROM pedido_estado_hist
UNION ALL SELECT 'resena',              count(*) FROM resena
ORDER BY 1;

-- Comprobación del SCD2: líneas vendidas con una versión ya cerrada.
-- Tiene que ser mayor que cero.
SELECT count(*) AS lineas_con_version_antigua
FROM pedido_linea l JOIN dim_producto p ON p.producto_sk = l.producto_sk
WHERE NOT p.es_actual;
