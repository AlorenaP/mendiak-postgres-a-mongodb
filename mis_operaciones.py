"""
VUESTRAS operaciones: PostgreSQL mejorado (parte R.2) y MongoDB (parte D).

El banco de pruebas (banco_pruebas.py) llama a estas funciones. No
cambiéis los nombres ni lo que devuelven: el modo "comprobar" compara
la salida de cada motor y tiene que coincidir.

Firmas
------
PostgreSQL:  f(cur, p)      cur = cursor de psycopg que devuelve diccionarios
MongoDB:     f(db, p)       lecturas;  db = base de datos "mendiak" de pymongo
             f(db, p, s)    escrituras; s = sesión con la transacción YA abierta.
                            Pasad session=s en CADA operación de escritura y de
                            lectura que haga la función. No hagáis commit: lo hace
                            el banco (o la deshace, si no se usa --confirmar).
`p` es un diccionario con los parámetros (ver Parametros.para en banco_pruebas.py).

Qué tiene que devolver cada operación (lo que el banco compara)
---------------------------------------------------------------
L1 {sku, precio, n_resenas, nota_media, histograma {1..5: n}, ficha_tecnica {nombre: valor},
    resenas_recientes [{titulo, ...}] (3, de la más reciente a la más antigua),
    relacionados [{sku, ...}] (6)}
L2 {total, pagina [{sku, ...}] (24, por precio y sku), facetas [{atributo, valor, n}]}
L3 {pedidos, facturado, top_skus [{sku, ...}] (10), ultimos_pedidos [{pedido_id, ...}] (50),
    salud_catalogo {skus_vivos, nota_media_catalogo, resenas_totales}}
L4 [ {pedido_id, importe_total, lineas [...], historial [...]} ]   (10, del más reciente)
L5 {pedido_id, estado_actual, importe_total,
    lineas [{sku, nombre, cantidad, precio_unit}], historial [{estado, ...}]}
L6 [ {departamento, mes, pedidos, ventas} ]  (con las filas de subtotal: departamento o mes = None)
E1 {importe_total, segmento}      E2 {skus_reversionados}
E3 {estado_nuevo}                 E4 {atributos}   (atributos guardados del SKU)

Mirad operaciones_originales.py: es la versión original y sirve de ejemplo.
"""
import operaciones_originales as orig


# =====================================================================
# PostgreSQL mejorado (parte R.2)
# Si en una operación no hay nada que mejorar, dejad la original y
# comprobad con su EXPLAIN por qué no hace falta tocarla.
# =====================================================================
def pg_L1(cur, p):
    raise NotImplementedError


def pg_L3(cur, p):
    raise NotImplementedError


def pg_E1(cur, p):
    raise NotImplementedError


PG_MEJORADO = {
    "L1": pg_L1,
    "L2": orig.L2,
    "L3": pg_L3,
    "L4": orig.L4,
    "L5": orig.L5,
    "L6": orig.L6,
    "E1": pg_E1,
    "E2": orig.E2,
    "E3": orig.E3,
    "E4": orig.E4,
}


# =====================================================================
# MongoDB (parte D)
# =====================================================================
def mongo_L1(db, p):
    raise NotImplementedError


def mongo_L2(db, p):
    raise NotImplementedError


def mongo_L3(db, p):
    raise NotImplementedError


def mongo_L4(db, p):
    raise NotImplementedError


def mongo_L5(db, p):
    raise NotImplementedError


def mongo_L6(db, p):
    raise NotImplementedError


def mongo_E1(db, p, s):
    raise NotImplementedError


def mongo_E2(db, p, s):
    raise NotImplementedError


def mongo_E3(db, p, s):
    raise NotImplementedError


def mongo_E4(db, p, s):
    raise NotImplementedError


MONGO = {
    "L1": mongo_L1, "L2": mongo_L2, "L3": mongo_L3, "L4": mongo_L4, "L5": mongo_L5,
    "L6": mongo_L6, "E1": mongo_E1, "E2": mongo_E2, "E3": mongo_E3, "E4": mongo_E4,
}
