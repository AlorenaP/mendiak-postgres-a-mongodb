"""
Parte C.1 · Colecciones de MongoDB con validador $jsonSchema e índices.

Un validador por colección (validationLevel "strict", validationAction
"error"): campos obligatorios y tipos, valores permitidos (los CHECK de
PostgreSQL), patrones de las claves, tamaño máximo de cada array
(sacado de la parte A.2), forma de los atributos y stock no negativo.

Lo usa 07_migrar.py. Se puede importar sin efectos.
"""
from pymongo import ASCENDING as ASC, DESCENDING as DESC

ESQUEMA = 1          # versión del modelo, se guarda en cada documento (_esquema)

ID_NUM = ["int", "long"]
SKU = {"bsonType": "string", "pattern": "^MEND-[0-9]{6}$"}
PEDIDO_ID = {"bsonType": "string", "pattern": "^PED-[0-9]{8}$"}
DINERO = {"bsonType": "decimal"}                      # importes: siempre Decimal128
ESTADOS = ["creado", "pagado", "preparacion", "enviado", "entregado", "cancelado"]


def origen(tabla):
    return {"bsonType": "object", "required": ["tabla", "clave"],
            "properties": {"tabla": {"enum": [tabla]}, "clave": {}}}


COMUNES = ["_origen", "_esquema"]
ESQ = {"bsonType": "int", "minimum": 1}

VALIDADORES = {
    # -----------------------------------------------------------------
    "productos": {
        "bsonType": "object",
        "required": ["_id", "version_sk", "nombre", "marca", "precio", "activo",
                     "valido_desde", "categoria", "departamento", "vendedor",
                     "atributos", "resenas_resumen", "resenas_recientes"] + COMUNES,
        "properties": {
            "_id": SKU,
            "version_sk": {"bsonType": ID_NUM},
            "nombre": {"bsonType": "string"},
            "marca": {"bsonType": "string"},
            "precio": DINERO,
            "peso_g": {"bsonType": ["int", "null"]},
            "activo": {"bsonType": "bool"},
            "valido_desde": {"bsonType": "date"},
            "categoria": {"bsonType": "object", "required": ["id", "nombre"]},
            "departamento": {"bsonType": "object", "required": ["id", "nombre"]},
            "vendedor": {"bsonType": "object", "required": ["id", "nombre"],
                         "properties": {"id": {"bsonType": "int"},
                                        "valoracion": {"bsonType": ["decimal", "null"]}}},
            # patrón attribute: pares {k, v}, como máximo 17 (A.2)
            "atributos": {
                "bsonType": "array", "maxItems": 17,
                "items": {"bsonType": "object", "required": ["k", "v"],
                          "additionalProperties": False,
                          "properties": {"k": {"bsonType": "string"},
                                         "v": {"bsonType": ["string", "int", "long",
                                                            "double", "bool"]}}}},
            # patrón computed
            "resenas_resumen": {
                "bsonType": "object", "required": ["n", "suma", "hist"],
                "properties": {"n": {"bsonType": ID_NUM, "minimum": 0},
                               "suma": {"bsonType": ID_NUM, "minimum": 0},
                               "hist": {"bsonType": "object",
                                        "required": ["1", "2", "3", "4", "5"]}}},
            # patrón subset: las 3 últimas
            "resenas_recientes": {
                "bsonType": "array", "maxItems": 3,
                "items": {"bsonType": "object",
                          "required": ["resena_id", "ts", "puntuacion", "titulo"],
                          "properties": {"puntuacion": {"bsonType": "int",
                                                        "minimum": 1, "maximum": 5}}}},
            "_origen": origen("dim_producto"),
            "_esquema": ESQ,
        },
    },
    # -----------------------------------------------------------------
    "productos_versiones": {
        "bsonType": "object",
        "required": ["_id", "sku", "nombre", "precio", "categoria_id", "vendedor_id",
                     "valido_desde", "es_actual"] + COMUNES,
        "properties": {
            "_id": {"bsonType": ID_NUM},
            "sku": SKU,
            "precio": DINERO,
            "valido_desde": {"bsonType": "date"},
            "valido_hasta": {"bsonType": ["date", "null"]},
            "es_actual": {"bsonType": "bool"},
            "_origen": origen("dim_producto"),
            "_esquema": ESQ,
        },
    },
    # -----------------------------------------------------------------
    "stock": {
        "bsonType": "object",
        "required": ["_id", "unidades", "actualizado"] + COMUNES,
        "properties": {
            "_id": SKU,
            "unidades": {"bsonType": "int", "minimum": 0},     # nunca negativo
            "actualizado": {"bsonType": "date"},
            "_origen": origen("stock"),
            "_esquema": ESQ,
        },
    },
    # -----------------------------------------------------------------
    "resenas": {
        "bsonType": "object",
        "required": ["_id", "sku", "cliente", "ts", "puntuacion"] + COMUNES,
        "properties": {
            "_id": {"bsonType": ID_NUM},
            "sku": SKU,
            "cliente": {"bsonType": "object", "required": ["id", "nombre"]},
            "ts": {"bsonType": "date"},
            "puntuacion": {"bsonType": "int", "minimum": 1, "maximum": 5},
            "votos_utiles": {"bsonType": "int", "minimum": 0},
            "_origen": origen("resena"),
            "_esquema": ESQ,
        },
    },
    # -----------------------------------------------------------------
    "top_categoria": {
        "bsonType": "object",
        "required": ["_id", "top", "calculado", "_esquema"],
        "properties": {
            "_id": {"bsonType": "int"},
            "top": {"bsonType": "array", "maxItems": 7,
                    "items": {"bsonType": "object", "required": ["sku", "n_resenas"]}},
            "calculado": {"bsonType": "date"},
            "_esquema": ESQ,
        },
    },
    # -----------------------------------------------------------------
    "clientes": {
        "bsonType": "object",
        "required": ["_id", "email", "nombre", "provincia", "segmento", "alta",
                     "preferencias", "deseos", "resumen_pedidos"] + COMUNES,
        "properties": {
            "_id": {"bsonType": "int"},
            "email": {"bsonType": "string", "pattern": "^[^@]+@[^@]+$"},
            "segmento": {"enum": ["nuevo", "recurrente", "premium"]},
            "alta": {"bsonType": "date"},
            "preferencias": {
                "bsonType": "object",
                "required": ["email_ofertas", "push_pedidos", "idioma"],
                "properties": {"idioma": {"enum": ["es", "eu", "en", "fr"]}}},
            "deseos": {"bsonType": "array", "maxItems": 14,
                       "items": {"bsonType": "object", "required": ["sku", "anadido"],
                                 "properties": {"sku": SKU}}},
            "resumen_pedidos": {"bsonType": "object", "required": ["n", "gasto"],
                                "properties": {"n": {"bsonType": "int", "minimum": 0},
                                               "gasto": DINERO}},
            "_origen": origen("cliente"),
            "_esquema": ESQ,
        },
    },
    # -----------------------------------------------------------------
    "pedidos": {
        "bsonType": "object",
        "required": ["_id", "cliente_id", "canal", "ts_creacion", "estado_actual",
                     "metodo_pago", "importe_total", "envio", "lineas",
                     "historial"] + COMUNES,
        "properties": {
            "_id": PEDIDO_ID,
            "cliente_id": {"bsonType": "int"},
            "canal": {"enum": ["web", "app_ios", "app_android"]},
            "ts_creacion": {"bsonType": "date"},
            "estado_actual": {"enum": ESTADOS},
            "metodo_pago": {"enum": ["tarjeta", "bizum", "paypal", "transferencia"]},
            "importe_total": DINERO,
            "envio": {"bsonType": "object", "required": ["calle", "cp", "provincia"],
                      "properties": {"cp": {"bsonType": "string",
                                            "pattern": "^[0-9]{5}$"}}},
            "lineas": {
                "bsonType": "array", "minItems": 1, "maxItems": 40,
                "items": {"bsonType": "object",
                          "required": ["n", "sku", "producto_sk", "nombre", "cantidad",
                                       "precio_unit", "importe_neto"],
                          "properties": {"sku": SKU,
                                         "producto_sk": {"bsonType": ID_NUM},
                                         "cantidad": {"bsonType": "int", "minimum": 1},
                                         "precio_unit": DINERO,
                                         "descuento": DINERO,
                                         "importe_neto": DINERO}}},
            "historial": {
                "bsonType": "array", "minItems": 1, "maxItems": 5,
                "items": {"bsonType": "object", "required": ["estado", "ts", "actor"],
                          "properties": {"estado": {"enum": ESTADOS},
                                         "ts": {"bsonType": "date"},
                                         "actor": {"enum": ["sistema", "vendedor",
                                                            "soporte"]}}}},
            "_origen": origen("pedido"),
            "_esquema": ESQ,
        },
    },
}

# Cada índice, con la operación que lo necesita (parte B.6)
INDICES = {
    "productos": [
        ([("departamento.id", ASC), ("atributos.k", ASC), ("atributos.v", ASC)], "L2"),
        ([("vendedor.id", ASC)], "L3 salud del catálogo, E2"),
    ],
    "resenas": [
        ([("sku", ASC), ("ts", DESC), ("_id", DESC)], "mantenimiento de resenas_recientes"),
    ],
    "pedidos": [
        ([("cliente_id", ASC), ("ts_creacion", DESC), ("_id", DESC)], "L4"),
        ([("lineas.vendedor_id", ASC), ("ts_creacion", DESC)], "L3 ventas"),
    ],
}


def crear_colecciones(db):
    """Borra y crea las colecciones con su validador (sin índices secundarios)."""
    for nombre, esquema in VALIDADORES.items():
        db.drop_collection(nombre)
        db.create_collection(nombre, validator={"$jsonSchema": esquema},
                             validationLevel="strict", validationAction="error")
    db.drop_collection("contadores")
    db.create_collection("contadores")


def crear_indices(db):
    for coleccion, indices in INDICES.items():
        for claves, uso in indices:
            nombre = db[coleccion].create_index(claves)
            print("   índice %-20s %-45s -> %s" % (coleccion, nombre, uso))
