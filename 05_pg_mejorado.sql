-- =====================================================================
--  MENDIAK · PARTE R.2 · PostgreSQL mejorado
--
--  Cambios sobre el esquema original, ordenados por ahorro de coste
--  diario (ver docs/R2_pg_mejorado.md). Las consultas que los usan
--  están en las funciones pg_* de mis_operaciones.py.
--
--  El script se puede ejecutar varias veces: cada bloque borra lo que
--  crea antes de volver a crearlo.
--
--  Uso:
--    docker exec mendiak-pg psql -U mendiak -d mendiak -f /scripts/05_pg_mejorado.sql
-- =====================================================================
SET search_path TO mendiak, public;
\timing on


-- =====================================================================
--  L1 · FICHA DE PRODUCTO  (894 h/día en el original)
--
--  Problema: cada visita recalcula, a partir de la tabla resena entera,
--    1) el contador, la nota media y el histograma del producto, y
--    2) el carrusel con los 6 mejor valorados de su categoría, que
--       hace un Seq Scan de los 2,1 M de reseñas.
--
--  Solución:
--    1) resena_resumen: un CONTADOR por SKU que se mantiene al escribir
--       (trigger). Tolerancia: ninguna, siempre exacto.
--    2) mv_top_categoria: el carrusel PRECALCULADO por categoría en una
--       vista materializada. Tolerancia: 1 hora (se refresca cada hora).
-- =====================================================================

-- ---------------------------------------------------------------------
-- L1.1 · Resumen de reseñas por SKU
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS resena_resumen CASCADE;   -- CASCADE: arrastra la vista materializada

CREATE TABLE resena_resumen (
    sku   TEXT    PRIMARY KEY,
    n     INTEGER NOT NULL CHECK (n >= 0),          -- número de reseñas
    suma  BIGINT  NOT NULL CHECK (suma >= 0),       -- suma de puntuaciones (nota = suma / n)
    n1    INTEGER NOT NULL DEFAULT 0,               -- histograma
    n2    INTEGER NOT NULL DEFAULT 0,
    n3    INTEGER NOT NULL DEFAULT 0,
    n4    INTEGER NOT NULL DEFAULT 0,
    n5    INTEGER NOT NULL DEFAULT 0
);

INSERT INTO resena_resumen (sku, n, suma, n1, n2, n3, n4, n5)
SELECT sku,
       count(*),
       sum(puntuacion),
       count(*) FILTER (WHERE puntuacion = 1),
       count(*) FILTER (WHERE puntuacion = 2),
       count(*) FILTER (WHERE puntuacion = 3),
       count(*) FILTER (WHERE puntuacion = 4),
       count(*) FILTER (WHERE puntuacion = 5)
FROM resena
GROUP BY sku;

-- El contador se mantiene en la misma transacción que la reseña:
-- nunca puede quedar desfasado.
CREATE OR REPLACE FUNCTION trg_resena_resumen() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('DELETE', 'UPDATE') THEN
        UPDATE resena_resumen
           SET n    = n    - 1,
               suma = suma - OLD.puntuacion,
               n1   = n1   - (OLD.puntuacion = 1)::int,
               n2   = n2   - (OLD.puntuacion = 2)::int,
               n3   = n3   - (OLD.puntuacion = 3)::int,
               n4   = n4   - (OLD.puntuacion = 4)::int,
               n5   = n5   - (OLD.puntuacion = 5)::int
         WHERE sku = OLD.sku;
    END IF;

    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        INSERT INTO resena_resumen AS r (sku, n, suma, n1, n2, n3, n4, n5)
        VALUES (NEW.sku, 1, NEW.puntuacion,
                (NEW.puntuacion = 1)::int, (NEW.puntuacion = 2)::int,
                (NEW.puntuacion = 3)::int, (NEW.puntuacion = 4)::int,
                (NEW.puntuacion = 5)::int)
        ON CONFLICT (sku) DO UPDATE
           SET n    = r.n    + 1,
               suma = r.suma + EXCLUDED.suma,
               n1   = r.n1   + EXCLUDED.n1,
               n2   = r.n2   + EXCLUDED.n2,
               n3   = r.n3   + EXCLUDED.n3,
               n4   = r.n4   + EXCLUDED.n4,
               n5   = r.n5   + EXCLUDED.n5;
    END IF;

    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS resena_resumen_sync ON resena;
CREATE TRIGGER resena_resumen_sync
AFTER INSERT OR DELETE OR UPDATE OF sku, puntuacion ON resena
FOR EACH ROW EXECUTE FUNCTION trg_resena_resumen();

ANALYZE resena_resumen;

-- ---------------------------------------------------------------------
-- L1.2 · Carrusel "lo mejor valorado de la categoría", precalculado
--
-- Se guardan los 7 primeros de cada categoría (no 6): si el producto que
-- se está viendo está entre ellos, se quita y siguen quedando 6.
-- La nota exacta (sin redondear) se usa para ordenar, igual que el
-- avg() de la consulta original, para que el orden coincida al empatar.
-- ---------------------------------------------------------------------
CREATE MATERIALIZED VIEW mv_top_categoria AS
SELECT categoria_id, puesto, sku, nombre, precio_base, n_resenas, nota
FROM (
    SELECT p.categoria_id,
           row_number() OVER (PARTITION BY p.categoria_id
                              ORDER BY r.suma::numeric / r.n DESC, r.n DESC, p.sku) AS puesto,
           p.sku,
           p.nombre,
           p.precio_base,
           r.n                              AS n_resenas,
           round(r.suma::numeric / r.n, 2)  AS nota
    FROM dim_producto p
    JOIN resena_resumen r ON r.sku = p.sku
    WHERE p.es_actual AND p.activo AND r.n > 0
) t
WHERE puesto <= 7;

-- Necesario para REFRESH ... CONCURRENTLY (refrescar sin bloquear lecturas)
CREATE UNIQUE INDEX ux_mv_top_categoria ON mv_top_categoria (categoria_id, puesto);

-- Refresco cada hora (con pg_cron o un cron externo):
--   REFRESH MATERIALIZED VIEW CONCURRENTLY mendiak.mv_top_categoria;


-- =====================================================================
--  E1 · CREAR PEDIDO
--
--  Error: el paso 6 calcula el segmento con JOIN a pedido_linea, así que
--  cuenta LÍNEAS en vez de pedidos y suma el total de cada pedido una
--  vez por línea (diagnóstico en 05_e1_diagnostico.sql).
--
--  Corrección (en pg_E1 de mis_operaciones.py): contar y sumar solo
--  sobre pedido. Para que esa consulta no tenga que visitar la tabla
--  (los pedidos de un cliente están repartidos por el disco), el índice
--  por cliente pasa a CUBRIR también estado e importe: se resuelve con
--  un Index Only Scan. Se amplía el índice existente en lugar de crear
--  otro, para no añadir coste a cada INSERT de pedido. L4 lo sigue
--  usando igual (mismas columnas de búsqueda y de orden).
-- =====================================================================
DROP INDEX IF EXISTS ix_pedido_cliente;
CREATE INDEX ix_pedido_cliente ON pedido (cliente_id, ts_creacion DESC)
    INCLUDE (estado_actual, importe_total);
VACUUM ANALYZE pedido;   -- mapa de visibilidad al día para el Index Only Scan


-- =====================================================================
--  L2 · BUSCADOR CON FILTROS Y FACETAS  (175 h/día en el original)
--
--  Problema: con el EAV, cada filtro es un JOIN más contra 7,9 M de filas
--  y solo el primero usa bien un índice. Para devolver 1.233 productos
--  se examinan unos 80.000 (65 por cada uno que se devuelve), y luego
--  hay que volver al EAV para las facetas.
--
--  Solución: tabla de búsqueda producto_busqueda, 1 fila por producto
--  vendible (vigente y activo), con:
--    * departamento_id copiado (ahorra el JOIN con dim_categoria);
--    * los atributos UNIVERSALES por los que filtra el buscador (color,
--      género y peso) como columnas normales, con un índice compuesto
--      que resuelve los tres filtros a la vez;
--    * la ficha técnica entera en un jsonb para calcular las facetas
--      sin volver al EAV.
--  El EAV se mantiene como fuente de verdad: producto_busqueda es una
--  copia derivada.
--
--  Tolerancia: NINGUNA. El listado muestra el precio, que tiene que
--  coincidir con la ficha en cuanto el vendedor publica (E2). Por eso
--  no es una vista materializada con refresco periódico, sino una copia
--  que mantienen triggers por SENTENCIA en la misma transacción que la
--  escritura. Coste: E2 y E4 hacen más trabajo (ver docs).
-- =====================================================================

-- ---------------------------------------------------------------------
-- L2.1 · Cómo se calcula una fila (una sola definición para la carga
-- inicial y para los triggers)
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS producto_busqueda CASCADE;
DROP VIEW  IF EXISTS v_producto_busqueda CASCADE;

CREATE VIEW v_producto_busqueda AS
SELECT p.sku,
       c.departamento_id,
       p.nombre,
       p.marca,
       p.precio_base,
       a.color,
       a.genero,
       a.peso,
       a.atributos
FROM dim_producto p
JOIN dim_categoria c ON c.categoria_id = p.categoria_id
-- LATERAL: los atributos se buscan producto a producto por la clave
-- primaria del EAV. Con un GROUP BY de todo el EAV, el filtro por SKU
-- de los triggers no llegaba hasta producto_atributo y cada recálculo
-- recorría los 7,9 M de filas.
CROSS JOIN LATERAL (
      SELECT max(pa.valor_texto) FILTER (WHERE pa.atributo_id = 3)  AS color,
             max(pa.valor_texto) FILTER (WHERE pa.atributo_id = 21) AS genero,
             max(pa.valor_num)   FILTER (WHERE pa.atributo_id = 1)  AS peso,
             coalesce(jsonb_object_agg(da.nombre,
                  coalesce(pa.valor_texto, pa.valor_num::text, pa.valor_bool::text)),
                  '{}'::jsonb) AS atributos
        FROM producto_atributo pa
        JOIN dim_atributo da ON da.atributo_id = pa.atributo_id
       WHERE pa.sku = p.sku) a
WHERE p.es_actual AND p.activo;

-- ---------------------------------------------------------------------
-- L2.2 · La tabla y su índice
-- ---------------------------------------------------------------------
CREATE TABLE producto_busqueda (
    sku             TEXT PRIMARY KEY,
    departamento_id SMALLINT      NOT NULL,
    nombre          TEXT          NOT NULL,
    marca           TEXT          NOT NULL,
    precio_base     NUMERIC(10,2) NOT NULL,
    color           TEXT,
    genero          TEXT,
    peso            NUMERIC(12,3),
    atributos       JSONB         NOT NULL
);

INSERT INTO producto_busqueda
SELECT * FROM v_producto_busqueda;

-- Los tres filtros de igualdad primero y el rango (peso) al final:
-- el índice entrega exactamente las filas que cumplen los cuatro.
CREATE INDEX ix_busqueda_filtros
    ON producto_busqueda (departamento_id, color, genero, peso);

-- Ordena físicamente la tabla según el índice: las filas de una misma
-- búsqueda quedan juntas en el disco (pocas páginas que leer). Las
-- altas posteriores no mantienen este orden; se repetiría de vez en
-- cuando en mantenimiento.
CLUSTER producto_busqueda USING ix_busqueda_filtros;
ANALYZE producto_busqueda;

-- ---------------------------------------------------------------------
-- L2.3 · Mantenimiento: triggers por sentencia
--
-- Cada vez que una sentencia toca dim_producto o producto_atributo, se
-- recalculan las filas de los SKU afectados. Por sentencia y no por
-- fila: E2 con el vendedor 1 toca 1,1 M de filas en dos sentencias, y
-- así se recalcula cada SKU una vez por sentencia, no una vez por fila.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION refrescar_busqueda(p_skus TEXT[]) RETURNS void
LANGUAGE sql AS $$
    DELETE FROM producto_busqueda WHERE sku = ANY(p_skus);
    INSERT INTO producto_busqueda
    SELECT * FROM v_producto_busqueda WHERE sku = ANY(p_skus);
$$;

CREATE OR REPLACE FUNCTION trg_busqueda() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        PERFORM refrescar_busqueda(ARRAY(SELECT DISTINCT sku FROM nuevas));
    ELSIF TG_OP = 'UPDATE' THEN
        PERFORM refrescar_busqueda(ARRAY(SELECT sku FROM nuevas
                                         UNION SELECT sku FROM viejas));
    ELSE
        PERFORM refrescar_busqueda(ARRAY(SELECT DISTINCT sku FROM viejas));
    END IF;
    RETURN NULL;
END $$;

-- PostgreSQL no permite tablas de transición en un trigger con varios
-- eventos: un trigger por evento y tabla.
DROP TRIGGER IF EXISTS busqueda_producto_ins ON dim_producto;
DROP TRIGGER IF EXISTS busqueda_producto_upd ON dim_producto;
DROP TRIGGER IF EXISTS busqueda_producto_del ON dim_producto;
DROP TRIGGER IF EXISTS busqueda_atributo_ins ON producto_atributo;
DROP TRIGGER IF EXISTS busqueda_atributo_upd ON producto_atributo;
DROP TRIGGER IF EXISTS busqueda_atributo_del ON producto_atributo;

CREATE TRIGGER busqueda_producto_ins AFTER INSERT ON dim_producto
    REFERENCING NEW TABLE AS nuevas FOR EACH STATEMENT EXECUTE FUNCTION trg_busqueda();
CREATE TRIGGER busqueda_producto_upd AFTER UPDATE ON dim_producto
    REFERENCING OLD TABLE AS viejas NEW TABLE AS nuevas FOR EACH STATEMENT EXECUTE FUNCTION trg_busqueda();
CREATE TRIGGER busqueda_producto_del AFTER DELETE ON dim_producto
    REFERENCING OLD TABLE AS viejas FOR EACH STATEMENT EXECUTE FUNCTION trg_busqueda();

CREATE TRIGGER busqueda_atributo_ins AFTER INSERT ON producto_atributo
    REFERENCING NEW TABLE AS nuevas FOR EACH STATEMENT EXECUTE FUNCTION trg_busqueda();
CREATE TRIGGER busqueda_atributo_upd AFTER UPDATE ON producto_atributo
    REFERENCING OLD TABLE AS viejas NEW TABLE AS nuevas FOR EACH STATEMENT EXECUTE FUNCTION trg_busqueda();
CREATE TRIGGER busqueda_atributo_del AFTER DELETE ON producto_atributo
    REFERENCING OLD TABLE AS viejas FOR EACH STATEMENT EXECUTE FUNCTION trg_busqueda();
