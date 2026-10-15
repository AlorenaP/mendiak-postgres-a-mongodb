-- =====================================================================
--  MENDIAK — PARTE R.1: TRANSACCIONES EN POSTGRESQL
--
--  Guion de ejercicios. NO lo ejecutéis de golpe: id bloque a bloque,
--  copiando y pegando en psql, y apuntad lo que veis.
--
--  Para los ejercicios con DOS terminales abrid dos terminales:
--    docker exec -it mendiak-pg psql -U mendiak -d mendiak     (terminal 1)
--    docker exec -it mendiak-pg psql -U mendiak -d mendiak     (terminal 2)
--  y ejecutad los pasos en el orden numerado (T1-1, T2-1, T1-2...).
--
--  Productos preparados en la carga:
--    MEND-000666 -> 0 unidades     MEND-000777 -> 1 unidad
--    MEND-000001, MEND-120200, MEND-300500 -> 500 unidades
-- =====================================================================
SET search_path TO mendiak, public;


-- =====================================================================
--  EJERCICIO 1 · Lo que hace una transacción no lo ve nadie más
--  hasta el COMMIT                                       (2 terminales)
-- =====================================================================
-- T1-1
BEGIN;
INSERT INTO pedido (pedido_id, cliente_id, canal, ts_creacion, estado_actual,
                    env_calle, env_cp, env_provincia, env_telefono, metodo_pago, importe_total)
VALUES ('PED-90000001', 4242, 'web', now(), 'creado',
        'Calle Mayor 1', '48001', 'Bizkaia', '600000000', 'tarjeta', 0);
SELECT pedido_id, estado_actual FROM pedido WHERE pedido_id = 'PED-90000001';

-- T2-1   (en la otra terminal)
--   SELECT pedido_id, estado_actual FROM pedido WHERE pedido_id = 'PED-90000001';

-- T1-2
ROLLBACK;
SELECT count(*) FROM pedido WHERE pedido_id = 'PED-90000001';

-- Preguntas: ¿qué ve cada terminal en cada paso? ¿Por qué T2 no ve el
-- pedido de T1? ¿Qué propiedad ACID es esa?


-- =====================================================================
--  EJERCICIO 2 · Todo o nada: un pedido con un producto agotado
--  (1 terminal)
-- =====================================================================
-- 2a) SIN transacción. psql confirma cada sentencia por separado
--     (autocommit). Ejecutad las cinco sentencias seguidas:
INSERT INTO pedido (pedido_id, cliente_id, canal, ts_creacion, estado_actual,
                    env_calle, env_cp, env_provincia, env_telefono, metodo_pago, importe_total)
VALUES ('PED-90000002', 4242, 'web', now(), 'creado',
        'Calle Mayor 1', '48001', 'Bizkaia', '600000000', 'tarjeta', 0);

INSERT INTO pedido_linea (pedido_id, linea_num, producto_sk, cantidad,
                          precio_unit, descuento, importe_neto)
SELECT 'PED-90000002', (row_number() OVER (ORDER BY c.sku))::smallint,
       p.producto_sk, c.cantidad, p.precio_base, 0, round(p.precio_base * c.cantidad, 2)
FROM (VALUES ('MEND-000001', 1::smallint), ('MEND-000666', 1::smallint)) AS c(sku, cantidad)
JOIN dim_producto p ON p.sku = c.sku AND p.es_actual;

UPDATE stock s SET unidades = s.unidades - c.cantidad, actualizado = now()
  FROM (VALUES ('MEND-000001', 1), ('MEND-000666', 1)) AS c(sku, cantidad)
 WHERE s.sku = c.sku;                                   -- <- aquí falla

UPDATE pedido SET importe_total = (SELECT sum(importe_neto) FROM pedido_linea
                                    WHERE pedido_id = 'PED-90000002')
 WHERE pedido_id = 'PED-90000002';

INSERT INTO pedido_estado_hist (pedido_id, estado, ts, actor)
VALUES ('PED-90000002', 'creado', now(), 'sistema');

-- ¿Qué ha quedado en la base? Comprobadlo:
SELECT p.pedido_id, p.importe_total,
       (SELECT count(*) FROM pedido_linea l WHERE l.pedido_id = p.pedido_id) AS lineas,
       (SELECT unidades FROM stock WHERE sku = 'MEND-000001') AS stock_000001,
       (SELECT unidades FROM stock WHERE sku = 'MEND-000666') AS stock_000666
FROM pedido p WHERE p.pedido_id = 'PED-90000002';

-- Limpieza (a mano, porque nada lo ha deshecho por vosotros):
DELETE FROM pedido_estado_hist WHERE pedido_id = 'PED-90000002';
DELETE FROM pedido_linea       WHERE pedido_id = 'PED-90000002';
DELETE FROM pedido             WHERE pedido_id = 'PED-90000002';

-- 2b) CON transacción. Lo mismo, entre BEGIN y COMMIT:
BEGIN;
-- ... pegad aquí las cinco sentencias de 2a ...
COMMIT;
-- Leed los mensajes. ¿Qué significa "current transaction is aborted,
-- commands ignored until end of transaction block"? ¿Qué ha hecho el
-- COMMIT final? ¿Queda algo del pedido?
SELECT count(*) FROM pedido WHERE pedido_id = 'PED-90000002';

-- 2c) Pensad: si el servidor se apaga justo después del segundo INSERT,
--     ¿qué queda del pedido en 2a? ¿Y en 2b?


-- =====================================================================
--  EJERCICIO 3 · SAVEPOINT: deshacer solo una parte              (1 terminal)
--  Quitar lo comprado de la lista de deseos (paso 7 de E1) no es
--  imprescindible: si falla, el pedido tiene que salir igualmente.
-- =====================================================================
BEGIN;
INSERT INTO pedido (pedido_id, cliente_id, canal, ts_creacion, estado_actual,
                    env_calle, env_cp, env_provincia, env_telefono, metodo_pago, importe_total)
VALUES ('PED-90000003', 4242, 'web', now(), 'creado',
        'Calle Mayor 1', '48001', 'Bizkaia', '600000000', 'tarjeta', 0);
SAVEPOINT antes_de_deseos;
DELETE FROM lista_deseos WHERE cliente_id = 'cuatro mil';   -- simulamos un fallo
ROLLBACK TO SAVEPOINT antes_de_deseos;
SELECT count(*) FROM pedido WHERE pedido_id = 'PED-90000003';  -- ¿sigue ahí?
ROLLBACK;     -- (en la vida real sería COMMIT; aquí lo deshacemos para no ensuciar)


-- =====================================================================
--  EJERCICIO 4 · Dos clientes compran la última unidad   (2 terminales)
--  Antes de cada apartado, dejad el stock en 1:
--    UPDATE stock SET unidades = 1 WHERE sku = 'MEND-000777';
-- =====================================================================
-- 4a) "Leer y luego escribir" (lo que hace mucha gente en la aplicación)
--   T1-1  BEGIN;
--         SELECT unidades FROM stock WHERE sku = 'MEND-000777';     -- 1: hay stock
--   T2-1  BEGIN;
--         SELECT unidades FROM stock WHERE sku = 'MEND-000777';     -- 1: hay stock
--   T1-2  UPDATE stock SET unidades = 0 WHERE sku = 'MEND-000777';  -- la app calcula 1-1
--         COMMIT;
--   T2-2  UPDATE stock SET unidades = 0 WHERE sku = 'MEND-000777';
--         COMMIT;
--   ¿Cuántas unidades se han vendido? ¿Cuántas había? Esto se llama
--   "actualización perdida".

-- 4b) Actualización condicional: comprobar y restar en la misma sentencia
--   T1-1  BEGIN;
--         UPDATE stock SET unidades = unidades - 1
--          WHERE sku = 'MEND-000777' AND unidades >= 1;              -- UPDATE 1
--   T2-1  BEGIN;
--         UPDATE stock SET unidades = unidades - 1
--          WHERE sku = 'MEND-000777' AND unidades >= 1;              -- ¿qué pasa?
--   T1-2  COMMIT;
--   T2-2  (mirad el resultado de su UPDATE)  ROLLBACK;
--   ¿Por qué se ha quedado esperando T2? ¿Qué devuelve al final y cómo
--   sabría la aplicación que no queda stock?

-- 4c) Bloqueo explícito con SELECT ... FOR UPDATE
--   T1-1  BEGIN;
--         SELECT unidades FROM stock WHERE sku = 'MEND-000777' FOR UPDATE;
--   T2-1  BEGIN;
--         SELECT unidades FROM stock WHERE sku = 'MEND-000777' FOR UPDATE;
--   T1-2  UPDATE stock SET unidades = 0 WHERE sku = 'MEND-000777';
--         COMMIT;
--   T2-2  (¿qué valor lee por fin?)  ROLLBACK;

-- 4d) Repetid 4a con BEGIN ISOLATION LEVEL REPEATABLE READ; en las dos
--     terminales. ¿Qué error da T2? ¿Qué tendría que hacer la aplicación?


-- =====================================================================
--  EJERCICIO 5 (opcional) · Interbloqueo                 (2 terminales)
--  Dos pedidos con los mismos dos productos, en distinto orden.
-- =====================================================================
--   T1-1  BEGIN; UPDATE stock SET unidades = unidades - 1 WHERE sku = 'MEND-000001';
--   T2-1  BEGIN; UPDATE stock SET unidades = unidades - 1 WHERE sku = 'MEND-120200';
--   T1-2  UPDATE stock SET unidades = unidades - 1 WHERE sku = 'MEND-120200';
--   T2-2  UPDATE stock SET unidades = unidades - 1 WHERE sku = 'MEND-000001';
--   T1-3 y T2-3  ROLLBACK;
--   ¿Quién pierde? ¿Cómo lo evitaríais en E1? (pista: el orden)


-- =====================================================================
--  EJERCICIO 6 · Publicar un catálogo es todo o nada     (2 terminales)
--  E2 para el vendedor 1500 (87 productos), sin confirmar.
-- =====================================================================
-- T1-1
BEGIN;
WITH cerradas AS (
    UPDATE dim_producto SET es_actual = FALSE, valido_hasta = CURRENT_DATE - 1
     WHERE vendedor_id = 1500 AND es_actual
    RETURNING sku, nombre, marca, categoria_id, vendedor_id, precio_base, peso_g, activo)
INSERT INTO dim_producto (sku, nombre, marca, categoria_id, vendedor_id, precio_base,
                          peso_g, activo, valido_desde, valido_hasta, es_actual)
SELECT sku, nombre, marca, categoria_id, vendedor_id, round(precio_base * 1.06, 2),
       peso_g, activo, CURRENT_DATE, NULL, TRUE
FROM cerradas;
SELECT sku, precio_base FROM dim_producto
 WHERE vendedor_id = 1500 AND es_actual ORDER BY sku LIMIT 3;

-- T2-1   (en la otra terminal: ¿qué precio ve un cliente ahora mismo?)
--   SELECT sku, precio_base FROM dim_producto
--    WHERE vendedor_id = 1500 AND es_actual ORDER BY sku LIMIT 3;

-- T1-2   El vendedor pulsa "publicar" otra vez el mismo día (repetid el
--        WITH ... INSERT de arriba). Leed el error: ¿qué regla ha saltado?
--        ¿Qué queda de la primera publicación después del ROLLBACK?
ROLLBACK;


-- =====================================================================
--  Comprobación final: todo tiene que estar como al principio
-- =====================================================================
UPDATE stock SET unidades = 1 WHERE sku = 'MEND-000777';
SELECT sku, unidades FROM stock
 WHERE sku IN ('MEND-000001','MEND-120200','MEND-300500','MEND-000666','MEND-000777')
 ORDER BY sku;                     -- 500, 500, 500, 0, 1
SELECT count(*) AS pedidos_de_prueba FROM pedido WHERE pedido_id LIKE 'PED-9%';   -- 0
