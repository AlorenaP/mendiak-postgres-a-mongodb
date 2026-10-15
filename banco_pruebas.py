"""
Banco de pruebas de Mendiak (v3)
================================
Mide las diez operaciones en PostgreSQL y en MongoDB con las mismas reglas:

  * el mismo cliente (Python, dentro de Docker) y los mismos parámetros;
  * la misma salida (el modo "comprobar" lo verifica);
  * la misma durabilidad (COMMIT normal / writeConcern majority + journal);
  * la misma memoria (la fija el docker-compose).

Uso (desde la carpeta de la práctica):

  docker compose run --rm banco comprobar
  docker compose run --rm banco medir
  docker compose run --rm banco medir --ops L1,L4 --motores pg_mejorado,mongo --rep 50
  docker compose run --rm banco dia --n 1000 --motores pg_mejorado,mongo

Motores: pg (consultas originales), pg_mejorado y mongo (las vuestras).

Las escrituras (E1-E4) se ejecutan dentro de una transacción que el banco
DESHACE al final (ROLLBACK / abortTransaction). Así se pueden repetir sin
estropear los datos. Con --confirmar se hace COMMIT de verdad (solo para
pedidos de prueba: luego tendréis que borrarlos).

Vuestras funciones van en mis_operaciones.py (o en el módulo que digáis
con --modulo). Las originales están en operaciones_originales.py.
"""
import argparse
import csv
import datetime as dt
import importlib
import os
import random
import statistics
import sys
import time
from decimal import Decimal
from zoneinfo import ZoneInfo

import psycopg
from bson.decimal128 import Decimal128
from psycopg.rows import dict_row
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from pymongo.write_concern import WriteConcern

import operaciones_originales

# Veces al día que la aplicación lanza cada operación (dato de negocio).
FRECUENCIA = {"L1": 2_900_000, "L2": 780_000, "L3": 12_000, "L4": 350_000,
              "L5": 900_000, "L6": 50,
              "E1": 6_250, "E2": 40, "E3": 25_000, "E4": 3_000}
REPETICIONES = {"L1": 30, "L2": 20, "L3": 10, "L4": 30, "L5": 50, "L6": 3,
                "E1": 30, "E2": 3, "E3": 50, "E4": 50}
ESCRITURAS = {"E1", "E2", "E3", "E4"}
MADRID = ZoneInfo("Europe/Madrid")

FILTROS_L2 = [
    {"departamento_id": 1, "color": "negro",    "genero": "unisex",   "peso_min": 300,  "peso_max": 2500},
    {"departamento_id": 2, "color": "rojo",     "genero": "mujer",    "peso_min": 100,  "peso_max": 1500},
    {"departamento_id": 3, "color": "azul",     "genero": "hombre",   "peso_min": 500,  "peso_max": 4000},
    {"departamento_id": 4, "color": "verde",    "genero": "infantil", "peso_min": 50,   "peso_max": 1000},
    {"departamento_id": 5, "color": "gris",     "genero": "unisex",   "peso_min": 200,  "peso_max": 3000},
    {"departamento_id": 6, "color": "amarillo", "genero": "mujer",    "peso_min": 1000, "peso_max": 5000},
]
VENDEDORES_L3 = [1, 3, 10, 40, 150, 600, 1500, 3000]
VENDEDORES_E2 = [1500, 600, 150]  # vendedores pequeños: el 1 no cabe en una transacción de Mongo (parte D)


# ---------------------------------------------------------------------
# Parámetros: se sacan una vez de PostgreSQL y valen para los dos motores
# ---------------------------------------------------------------------
class Parametros:
    def __init__(self, pg, semilla=42):
        self.rnd = random.Random(semilla)
        self.n_pedido = 0
        with pg.cursor() as cur:
            cur.execute("SELECT sku FROM dim_producto WHERE es_actual AND activo "
                        "ORDER BY md5(sku) LIMIT 300")
            self.skus = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT s.sku FROM stock s JOIN dim_producto p "
                        "ON p.sku = s.sku AND p.es_actual AND p.activo "
                        "WHERE s.unidades >= 5 ORDER BY md5(s.sku) LIMIT 300")
            self.skus_stock = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT cliente_id FROM (SELECT DISTINCT cliente_id FROM pedido) t "
                        "ORDER BY md5(cliente_id::text) LIMIT 300")
            self.clientes = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT pedido_id FROM pedido WHERE estado_actual IN "
                        "('creado','pagado','preparacion','enviado') ORDER BY pedido_id")
            self.en_curso = [r[0] for r in cur.fetchall()]

    def para(self, op):
        r = self.rnd
        if op == "L1":
            return {"sku": r.choice(self.skus)}
        if op == "L2":
            return dict(r.choice(FILTROS_L2))
        if op == "L3":
            return {"vendedor_id": r.choice(VENDEDORES_L3)}
        if op == "L4":
            return {"cliente_id": r.choice(self.clientes)}
        if op == "L5":
            return {"pedido_id": "PED-%08d" % r.randint(1, 600_000)}
        if op == "L6":
            return {"anio": 2025}
        if op == "E1":
            self.n_pedido += 1
            skus = r.sample(self.skus_stock, r.randint(1, 4))
            return {"pedido_id": "PED-9%07d" % (r.randint(0, 9_000_000) + self.n_pedido),
                    "cliente_id": r.choice(self.clientes),
                    "lineas": [(s, r.randint(1, 2)) for s in sorted(skus)],
                    "canal": r.choice(["web", "app_ios", "app_android"]),
                    "metodo_pago": r.choice(["tarjeta", "bizum", "paypal"]),
                    "envio": {"calle": "Calle Autonomía 21", "cp": "48010",
                              "provincia": "Bizkaia", "telefono": "600111222"}}
        if op == "E2":
            return {"vendedor_id": r.choice(VENDEDORES_E2)}
        if op == "E3":
            return {"pedido_id": r.choice(self.en_curso)}
        if op == "E4":
            return {"sku": r.choice(self.skus), "delta_peso": 10}
        raise ValueError(op)

    def casos_comprobacion(self, op):
        """Parámetros fijos para comprobar que los motores devuelven lo mismo."""
        fijos = {
            "L1": [{"sku": "MEND-000123"}, {"sku": "MEND-000001"}],
            "L2": [FILTROS_L2[0], FILTROS_L2[3]],
            "L3": [{"vendedor_id": 1500}, {"vendedor_id": 1}],
            "L4": [{"cliente_id": 1}, {"cliente_id": 4242}],
            "L5": [{"pedido_id": "PED-00412345"}, {"pedido_id": "PED-00000001"}],
            "L6": [{"anio": 2025}],
            "E1": [],
            "E2": [{"vendedor_id": 1500}],
            "E3": [{"pedido_id": self.en_curso[-1]}, {"pedido_id": self.en_curso[0]}],
            "E4": [{"sku": "MEND-000123", "delta_peso": 10}],
        }[op]
        aleatorios = [self.para(op) for _ in range(3 if op not in ("L6", "E2") else 0)]
        return fijos + aleatorios


# ---------------------------------------------------------------------
# Huella: la parte del resultado que tiene que coincidir entre motores
# ---------------------------------------------------------------------
def d2(x):
    if x is None:
        return None
    if isinstance(x, Decimal128):
        x = x.to_decimal()
    return Decimal(str(x)).quantize(Decimal("0.01"))


def mes(x):
    if x is None:
        return None
    if isinstance(x, str):
        return x[:7]
    if isinstance(x, dt.datetime):
        if x.tzinfo is None:
            x = x.replace(tzinfo=dt.timezone.utc)
        return x.astimezone(MADRID).strftime("%Y-%m")
    return x.strftime("%Y-%m")


def huella(op, r):
    if op == "L1":
        hist = {int(k): int(v) for k, v in (r.get("histograma") or {}).items()}
        return (r["sku"], d2(r["precio"]), int(r["n_resenas"]), d2(r["nota_media"]),
                tuple(hist.get(i, 0) for i in range(1, 6)),
                tuple(x["titulo"] for x in r["resenas_recientes"]),
                tuple(x["sku"] for x in r["relacionados"]),
                len(r["ficha_tecnica"]))
    if op == "L2":
        return (int(r["total"]), tuple(x["sku"] for x in r["pagina"]),
                len(r["facetas"]), sum(int(f["n"]) for f in r["facetas"]))
    if op == "L3":
        s = r["salud_catalogo"]
        return (int(r["pedidos"]), d2(r["facturado"]),
                tuple(x["sku"] for x in r["top_skus"]),
                tuple(x["pedido_id"] for x in r["ultimos_pedidos"]),
                int(s["skus_vivos"]), d2(s["nota_media_catalogo"]), int(s["resenas_totales"]))
    if op == "L4":
        return tuple((p["pedido_id"], d2(p["importe_total"]), len(p["lineas"]),
                      len(p["historial"])) for p in r)
    if op == "L5":
        return (r["pedido_id"], r["estado_actual"], d2(r["importe_total"]),
                tuple((l["sku"], l["nombre"], int(l["cantidad"]), d2(l["precio_unit"]))
                      for l in r["lineas"]),
                tuple(h["estado"] for h in r["historial"]))
    if op == "L6":
        filas = [(f["departamento"], mes(f["mes"]), int(f["pedidos"]), d2(f["ventas"])) for f in r]
        return tuple(sorted(filas, key=lambda f: (f[0] is None, f[0] or "", f[1] is None, f[1] or "")))
    if op == "E1":
        return (d2(r["importe_total"]), r["segmento"])
    if op == "E2":
        return (int(r["skus_reversionados"]),)
    if op == "E3":
        return (r["estado_nuevo"],)
    if op == "E4":
        return (int(r["atributos"]),)
    raise ValueError(op)


# ---------------------------------------------------------------------
# Ejecución
# ---------------------------------------------------------------------
class Banco:
    def __init__(self, args):
        self.args = args
        self.pg = psycopg.connect(os.environ["PG_DSN"], autocommit=True)
        self.pg.execute("SET search_path TO mendiak, public")
        self.mcli = MongoClient(os.environ["MONGO_URI"])
        self.db = self.mcli[os.environ.get("MONGO_DB", "mendiak")]
        sys.path.insert(0, os.getcwd())
        alumno = importlib.import_module(args.modulo)
        self.motores = {
            "pg": ("pg", operaciones_originales.OPERACIONES),
            "pg_mejorado": ("pg", getattr(alumno, "PG_MEJORADO", {})),
            "mongo": ("mongo", getattr(alumno, "MONGO", {})),
        }
        try:
            self.pg.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
            self.hay_pgss = True
        except psycopg.Error:
            self.hay_pgss = False

    def funcion(self, motor, op):
        return self.motores[motor][1].get(op)

    def ejecutar(self, motor, op, p):
        tipo, ops = self.motores[motor]
        fn = ops[op]
        confirmar = self.args.confirmar
        t0 = time.perf_counter()
        if tipo == "pg":
            if op in ESCRITURAS:
                with self.pg.transaction(force_rollback=not confirmar):
                    with self.pg.cursor(row_factory=dict_row) as cur:
                        r = fn(cur, p)
            else:
                with self.pg.cursor(row_factory=dict_row) as cur:
                    r = fn(cur, p)
        else:
            if op in ESCRITURAS:
                for intento in range(5):
                    try:
                        r = self._transaccion_mongo(fn, p, confirmar)
                        break
                    except PyMongoError as e:
                        # Lo mismo que hace una aplicación real (with_transaction):
                        # si el error es transitorio, se repite la transacción.
                        if not e.has_error_label("TransientTransactionError") or intento == 4:
                            raise
            else:
                r = fn(self.db, p)
        return r, (time.perf_counter() - t0) * 1000

    def _transaccion_mongo(self, fn, p, confirmar):
        with self.mcli.start_session() as s:
            s.start_transaction(write_concern=WriteConcern("majority", j=True))
            try:
                r = fn(self.db, p, s)
            except Exception:
                s.abort_transaction()
                raise
            if confirmar:
                s.commit_transaction()
            else:
                s.abort_transaction()
            return r

    def leido(self, tipo):
        """Bytes que el motor ha tenido que traer a su caché (no estaban en ella)."""
        if tipo == "pg":
            if not self.hay_pgss:
                return None
            fila = self.pg.execute("SELECT coalesce(sum(shared_blks_read),0) "
                                   "FROM pg_stat_statements").fetchone()
            return int(fila[0]) * 8192
        st = self.mcli.admin.command("serverStatus")
        return int(st["wiredTiger"]["cache"]["bytes read into cache"])

    # --- modos -------------------------------------------------------
    def comprobar(self, ops, motores):
        par = Parametros(self.pg, semilla=7)
        fallos = 0
        for op in ops:
            for p in par.casos_comprobacion(op):
                huellas = {}
                for m in motores:
                    if not self.funcion(m, op):
                        continue
                    try:
                        r, _ = self.ejecutar(m, op, p)
                        huellas[m] = huella(op, r)
                    except NotImplementedError:
                        continue
                    except Exception as e:
                        huellas[m] = "ERROR: %s" % e
                if len(huellas) < 2:
                    print("  %-3s %-40s (falta implementar)" % (op, _corto(p)))
                    continue
                referencia = list(huellas.values())[0]
                iguales = all(h == referencia for h in huellas.values())
                fallos += not iguales
                print("  %-3s %-40s %s" % (op, _corto(p), "OK" if iguales else "DISTINTO"))
                if not iguales:
                    for m, h in huellas.items():
                        print("        %-12s %s" % (m, str(h)[:300]))
        print("\n%s" % ("Todo coincide." if not fallos else "%d casos no coinciden." % fallos))

    def medir(self, ops, motores):
        par = Parametros(self.pg, semilla=self.args.semilla)
        filas = []
        print("%-3s %-12s %5s %10s %10s %10s %12s %12s" %
              ("op", "motor", "n", "p50 ms", "p95 ms", "media ms", "KB leídos", "h/día"))
        for op in ops:
            n = self.args.rep or REPETICIONES[op]
            parametros = [par.para(op) for _ in range(n + 2)]
            for m in motores:
                if not self.funcion(m, op):
                    continue
                tipo = self.motores[m][0]
                try:
                    for p in parametros[:2]:                      # calentamiento
                        self.ejecutar(m, op, p)
                    antes = self.leido(tipo)
                    tiempos = [self.ejecutar(m, op, p)[1] for p in parametros[2:]]
                    despues = self.leido(tipo)
                except NotImplementedError:
                    continue
                tiempos.sort()
                media = statistics.mean(tiempos)
                p95 = tiempos[min(len(tiempos) - 1, int(round(0.95 * (len(tiempos) - 1))))]
                kb = (despues - antes) / 1024 / n if antes is not None else None
                coste = FRECUENCIA[op] * media / 3_600_000
                print("%-3s %-12s %5d %10.2f %10.2f %10.2f %12s %12.2f" %
                      (op, m, n, statistics.median(tiempos), p95, media,
                       "%.0f" % kb if kb is not None else "-", coste))
                filas.append({"op": op, "motor": m, "n": n,
                              "p50_ms": round(statistics.median(tiempos), 3),
                              "p95_ms": round(p95, 3), "media_ms": round(media, 3),
                              "kb_leidos_op": round(kb, 1) if kb is not None else "",
                              "horas_dia": round(coste, 3)})
        self.guardar("medir", filas)

    def dia(self, motores):
        """Simulación de un día: operaciones al azar con su frecuencia real."""
        par = Parametros(self.pg, semilla=self.args.semilla)
        ops = list(FRECUENCIA)
        secuencia = par.rnd.choices(ops, weights=[FRECUENCIA[o] for o in ops], k=self.args.n)
        parametros = [(op, par.para(op)) for op in secuencia]
        total_dia = sum(FRECUENCIA.values())
        filas = []
        for m in motores:
            if any(not self.funcion(m, op) for op in set(secuencia)):
                print("%s: faltan operaciones, se omite" % m)
                continue
            por_op = {}
            t0 = time.perf_counter()
            try:
                for op, p in parametros:
                    _, t = self.ejecutar(m, op, p)
                    por_op.setdefault(op, []).append(t)
            except NotImplementedError:
                print("%s: falta implementar alguna operación, se omite" % m)
                continue
            total = (time.perf_counter() - t0) * 1000
            horas = total / len(parametros) * total_dia / 3_600_000
            print("\n%s: %d operaciones en %.1f s -> %.1f horas de servidor al día" %
                  (m, len(parametros), total / 1000, horas))
            for op in ops:
                if op in por_op:
                    t = por_op[op]
                    print("   %-3s %6d veces  media %9.2f ms  (%4.1f %% del tiempo)" %
                          (op, len(t), statistics.mean(t), 100 * sum(t) / total))
            filas.append({"op": "TODAS", "motor": m, "n": len(parametros),
                          "media_ms": round(total / len(parametros), 3), "horas_dia": round(horas, 2)})
        self.guardar("dia", filas)

    def guardar(self, modo, filas):
        if not filas:
            return
        os.makedirs("resultados", exist_ok=True)
        ruta = os.path.join("resultados", "medidas.csv")
        nuevo = not os.path.exists(ruta)
        campos = ["fecha", "etiqueta", "modo", "op", "motor", "n", "p50_ms", "p95_ms",
                  "media_ms", "kb_leidos_op", "horas_dia"]
        with open(ruta, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=campos)
            if nuevo:
                w.writeheader()
            for fila in filas:
                w.writerow({"fecha": dt.datetime.now().isoformat(timespec="seconds"),
                            "etiqueta": self.args.etiqueta, "modo": modo, **fila})
        print("\nGuardado en %s" % ruta)


def _corto(p):
    s = ", ".join("%s=%s" % (k, v) for k, v in p.items() if k not in ("envio",))
    return s[:40]


def main():
    ap = argparse.ArgumentParser(description="Banco de pruebas de Mendiak")
    ap.add_argument("modo", choices=["comprobar", "medir", "dia"])
    ap.add_argument("--ops", default=",".join(FRECUENCIA))
    ap.add_argument("--motores", default="pg,pg_mejorado,mongo")
    ap.add_argument("--rep", type=int, default=0, help="repeticiones por operación")
    ap.add_argument("--n", type=int, default=1000, help="operaciones en el modo dia")
    ap.add_argument("--modulo", default="mis_operaciones")
    ap.add_argument("--semilla", type=int, default=42)
    ap.add_argument("--etiqueta", default="", help="texto libre para el CSV (p. ej. poca-memoria)")
    ap.add_argument("--confirmar", action="store_true", help="las escrituras hacen COMMIT")
    args = ap.parse_args()
    ops = [o.strip() for o in args.ops.split(",") if o.strip()]
    motores = [m.strip() for m in args.motores.split(",") if m.strip()]
    banco = Banco(args)
    if args.modo == "comprobar":
        banco.comprobar(ops, motores)
    elif args.modo == "medir":
        banco.medir(ops, motores)
    else:
        banco.dia(motores)


if __name__ == "__main__":
    main()
