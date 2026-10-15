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
