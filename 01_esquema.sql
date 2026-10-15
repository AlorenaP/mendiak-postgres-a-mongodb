-- =====================================================================
--  MENDIAK OUTDOOR MARKETPLACE — esquema relacional  (v3)
--  Copo de nieve sobre PostgreSQL 17
--
--  14 tablas. Tres puntos de dolor deliberados:
--    1. dim_producto es SCD tipo 2  -> varias filas por SKU.
--    2. producto_atributo es un EAV -> un atributo, una fila.
--    3. pedido / pedido_linea / pedido_estado_hist son estado
--       operacional viviendo dentro de un almacén analítico.
--
--  Novedad v3: tabla stock (para las transacciones de la parte R) y
--  restricciones CHECK en los dominios cerrados.
--
--  Uso:
--    psql -d mendiak -f 01_esquema.sql
--    psql -d mendiak -f 02_carga_datos.sql
-- =====================================================================

DROP SCHEMA IF EXISTS mendiak CASCADE;
CREATE SCHEMA mendiak;
ALTER DATABASE mendiak SET search_path TO mendiak, public;
SET search_path TO mendiak, public;

-- ---------------------------------------------------------------------
-- 1. JERARQUÍA DE CATÁLOGO
-- ---------------------------------------------------------------------
CREATE TABLE dim_departamento (
    departamento_id SMALLINT PRIMARY KEY,
    nombre          TEXT NOT NULL
);

CREATE TABLE dim_categoria (
    categoria_id    SMALLINT PRIMARY KEY,
    departamento_id SMALLINT NOT NULL REFERENCES dim_departamento(departamento_id),
    nombre          TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- 2. CATÁLOGO DE ATRIBUTOS + EAV
--    60 atributos posibles; cada producto usa 17 como máximo.
-- ---------------------------------------------------------------------
CREATE TABLE dim_atributo (
    atributo_id SMALLINT PRIMARY KEY,
    nombre      TEXT NOT NULL,
    tipo        TEXT NOT NULL CHECK (tipo IN ('texto','numero','booleano')),
    unidad      TEXT
);

CREATE TABLE producto_atributo (
    sku         TEXT     NOT NULL,          -- clave de NEGOCIO, no de versión
    atributo_id SMALLINT NOT NULL REFERENCES dim_atributo(atributo_id),
    valor_texto TEXT,
    valor_num   NUMERIC(12,3),
    valor_bool  BOOLEAN,
    PRIMARY KEY (sku, atributo_id)
);
CREATE INDEX ix_pa_texto ON producto_atributo (atributo_id, valor_texto) WHERE valor_texto IS NOT NULL;
CREATE INDEX ix_pa_num   ON producto_atributo (atributo_id, valor_num)   WHERE valor_num   IS NOT NULL;
CREATE INDEX ix_pa_bool  ON producto_atributo (atributo_id, valor_bool)  WHERE valor_bool  IS NOT NULL;

-- ---------------------------------------------------------------------
-- 3. VENDEDOR (cola larga: unos pocos concentran el catálogo)
-- ---------------------------------------------------------------------
CREATE TABLE dim_vendedor (
    vendedor_id INTEGER PRIMARY KEY,
    nombre      TEXT NOT NULL,
    provincia   TEXT NOT NULL,
    alta        DATE NOT NULL,
    valoracion  NUMERIC(3,2)
);

-- ---------------------------------------------------------------------
-- 4. PRODUCTO — SCD TIPO 2
--    Una fila por VERSIÓN. Solo una tiene es_actual.
--    pedido_linea apunta a la versión vigente el día de la venta.
-- ---------------------------------------------------------------------
CREATE TABLE dim_producto (
    producto_sk  BIGSERIAL PRIMARY KEY,     -- clave subrogada = versión
    sku          TEXT      NOT NULL,        -- clave de negocio, NO única
    nombre       TEXT      NOT NULL,
    marca        TEXT      NOT NULL,
    categoria_id SMALLINT  NOT NULL REFERENCES dim_categoria(categoria_id),
    vendedor_id  INTEGER   NOT NULL REFERENCES dim_vendedor(vendedor_id),
    precio_base  NUMERIC(10,2) NOT NULL CHECK (precio_base >= 0),
    peso_g       INTEGER,
    activo       BOOLEAN   NOT NULL DEFAULT TRUE,
    valido_desde DATE      NOT NULL,
    valido_hasta DATE,
    es_actual    BOOLEAN   NOT NULL DEFAULT TRUE,
    CHECK (valido_hasta IS NULL OR valido_hasta >= valido_desde)
);
CREATE UNIQUE INDEX ux_producto_sku_actual ON dim_producto (sku) WHERE es_actual;
CREATE INDEX ix_producto_categoria ON dim_producto (categoria_id) WHERE es_actual;
CREATE INDEX ix_producto_vendedor  ON dim_producto (vendedor_id);

-- ---------------------------------------------------------------------
-- 5. STOCK (nuevo en v3)
--    Va por SKU, no por versión: subir el precio no cambia las unidades
--    que hay en el almacén. El CHECK impide vender lo que no hay.
-- ---------------------------------------------------------------------
CREATE TABLE stock (
    sku         TEXT PRIMARY KEY,
    unidades    INTEGER NOT NULL CHECK (unidades >= 0),
    actualizado TIMESTAMPTZ NOT NULL
);

-- ---------------------------------------------------------------------
-- 6. CLIENTE + PREFERENCIAS (1-1)
-- ---------------------------------------------------------------------
CREATE TABLE cliente (
    cliente_id INTEGER PRIMARY KEY,
    email      TEXT NOT NULL UNIQUE,
    nombre     TEXT NOT NULL,
    provincia  TEXT NOT NULL,
    segmento   TEXT NOT NULL CHECK (segmento IN ('nuevo','recurrente','premium')),
    alta       DATE NOT NULL
);

CREATE TABLE cliente_preferencia (
    cliente_id    INTEGER PRIMARY KEY REFERENCES cliente(cliente_id),
    email_ofertas BOOLEAN NOT NULL,
    push_pedidos  BOOLEAN NOT NULL,
    idioma        TEXT    NOT NULL CHECK (idioma IN ('es','eu','en','fr')),
    actualizado   TIMESTAMPTZ NOT NULL
);

-- ---------------------------------------------------------------------
-- 7. LISTA DE DESEOS — N-M cliente <-> producto
-- ---------------------------------------------------------------------
CREATE TABLE lista_deseos (
    cliente_id INTEGER NOT NULL REFERENCES cliente(cliente_id),
    sku        TEXT    NOT NULL,
    anadido    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (cliente_id, sku)
);
CREATE INDEX ix_deseos_sku ON lista_deseos (sku);

-- ---------------------------------------------------------------------
-- 8. PEDIDO, LÍNEAS E HISTORIAL DE ESTADOS
-- ---------------------------------------------------------------------
CREATE TABLE pedido (
    pedido_id     TEXT PRIMARY KEY,
    cliente_id    INTEGER NOT NULL REFERENCES cliente(cliente_id),
    canal         TEXT NOT NULL CHECK (canal IN ('web','app_ios','app_android')),
    ts_creacion   TIMESTAMPTZ NOT NULL,
    estado_actual TEXT NOT NULL CHECK (estado_actual IN
                  ('creado','pagado','preparacion','enviado','entregado','cancelado')),
    env_calle     TEXT NOT NULL,
    env_cp        CHAR(5) NOT NULL,
    env_provincia TEXT NOT NULL,
    env_telefono  TEXT,
    metodo_pago   TEXT NOT NULL CHECK (metodo_pago IN ('tarjeta','bizum','paypal','transferencia')),
    importe_total NUMERIC(12,2) NOT NULL DEFAULT 0
);
CREATE INDEX ix_pedido_cliente ON pedido (cliente_id, ts_creacion DESC);
CREATE INDEX ix_pedido_ts      ON pedido (ts_creacion);

CREATE TABLE pedido_linea (
    linea_id     BIGSERIAL PRIMARY KEY,
    pedido_id    TEXT     NOT NULL REFERENCES pedido(pedido_id),
    linea_num    SMALLINT NOT NULL,
    producto_sk  BIGINT   NOT NULL REFERENCES dim_producto(producto_sk),  -- VERSIÓN vendida
    cantidad     SMALLINT NOT NULL CHECK (cantidad > 0),
    precio_unit  NUMERIC(10,2) NOT NULL,    -- congelado en el momento de la compra
    descuento    NUMERIC(10,2) NOT NULL DEFAULT 0,
    importe_neto NUMERIC(12,2) NOT NULL,
    UNIQUE (pedido_id, linea_num)
);
CREATE INDEX ix_linea_producto ON pedido_linea (producto_sk);

CREATE TABLE pedido_estado_hist (
    hist_id   BIGSERIAL PRIMARY KEY,
    pedido_id TEXT NOT NULL REFERENCES pedido(pedido_id),
    estado    TEXT NOT NULL CHECK (estado IN
              ('creado','pagado','preparacion','enviado','entregado','cancelado')),
    ts        TIMESTAMPTZ NOT NULL,
    actor     TEXT NOT NULL CHECK (actor IN ('sistema','vendedor','soporte'))
);
CREATE INDEX ix_hist_pedido ON pedido_estado_hist (pedido_id, ts);

-- ---------------------------------------------------------------------
-- 9. RESEÑAS
--    Apuntan al SKU (clave de negocio), no a la versión.
-- ---------------------------------------------------------------------
CREATE TABLE resena (
    resena_id    BIGSERIAL PRIMARY KEY,
    sku          TEXT    NOT NULL,
    cliente_id   INTEGER NOT NULL REFERENCES cliente(cliente_id),
    ts           TIMESTAMPTZ NOT NULL,
    puntuacion   SMALLINT NOT NULL CHECK (puntuacion BETWEEN 1 AND 5),
    titulo       TEXT,
    texto        TEXT,
    votos_utiles INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ix_resena_sku ON resena (sku, ts DESC, resena_id DESC);

COMMENT ON SCHEMA mendiak IS
  'Copo de nieve de Mendiak Outdoor (v3). Punto de partida del laboratorio relacional -> documental.';
