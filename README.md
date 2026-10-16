# Mendiak Outdoor · Evaluación de migración PostgreSQL → MongoDB

Evaluación de la migración de un marketplace (16,8 M de filas, ~3 GB) desde un modelo
en copo de nieve sobre **PostgreSQL 17** a un modelo documental en **MongoDB 8**,
comparando tres alternativas con el mismo banco de pruebas:

1. **PostgreSQL original**: el sistema tal como está.
2. **PostgreSQL mejorado**: índices, vistas materializadas y corrección de lógica.
3. **MongoDB**: modelo documental diseñado a partir de la carga de trabajo.

> Proyecto del módulo de Arquitectura de Datos. El enunciado y los scripts de partida
> son material del curso; el análisis, el diseño y las mejoras son trabajo propio.

## Stack

PostgreSQL 17 · MongoDB 8 (replica set) · Docker Compose · Python 3.12 (psycopg 3, pymongo)

## Resultados

Las tres versiones medidas en la misma ejecución del banco de pruebas (tiempo medio en ms).

| Operación | Veces/día | PG original | PG mejorado | MongoDB |
|---|---:|---:|---:|---:|
| L1 Ficha de producto | 2.900.000 | 738 | **0,53** | **0,52** |
| L2 Buscador con facetas | 780.000 | 527 | **27,3** | 220 |
| L3 Panel del vendedor | 12.000 | 59 | **39** | 132 |
| L4 Mis pedidos | 350.000 | 1,29 | 0,53 | **0,46** |
| L5 Seguimiento de pedido | 900.000 | 0,54 | **0,22** | 0,38 |
| L6 Informe de ventas | 50 | 2.839 | **2.799** | 6.275 |
| E1 Crear pedido | 6.250 | 3,41 | **1,92** | 3,61 |
| E2 Publicar catálogo | 40 | 105 | 82 | **37** |
| E3 Avanzar estado | 25.000 | 0,59 | **0,49** | 0,75 |
| E4 Editar ficha técnica | 3.000 | 2,26 | 1,22 | **1,07** |
| **Horas de servidor al día** | | **709** (1.070 en frío) | **6,6** | **48,7** |

**Con poca memoria** (1 GB por motor, los datos no caben), MongoDB lee **unas 3 veces más rápido** las entidades que se consultan enteras: L1 (2,4 frente a 7,9 ms), L4 (2,6 frente a 7,3) y L5 (1,2 frente a 3,9).

### Conclusiones

1. **Más del 99 % de la mejora se consiguió sin salir de PostgreSQL**: de ≈ 1.070 a ≈ 7 horas de servidor al día con un contador de reseñas, una vista materializada, una tabla de búsqueda con índice compuesto y un índice cubriente. El cuello de botella no era el motor, sino recalcular en cada lectura y un modelo EAV poco apto para filtrar.
2. **El modelo documental gana donde la aplicación lee entidades completas** (un pedido, una ficha): un documento en un solo sitio frente a filas repartidas por el disco. La ventaja aparece cuando los datos no caben en memoria.
3. **PostgreSQL gana en búsqueda con filtros (L2), agregaciones (L3, L6) y transacciones grandes** (E2 del vendedor con 32.459 productos, imposible en una sola transacción de MongoDB).
4. **L2 domina el coste en los dos motores**: es un problema para un motor de búsqueda, no para la base de datos transaccional.
5. **Recomendación:** PostgreSQL mejorado como base principal, motor de búsqueda para L2, y MongoDB solo para pedidos si la presión de memoria lo justifica.

| Fase | Documento |
|---|---|
| 0 · Punto de partida | [docs/0_punto_de_partida.md](docs/0_punto_de_partida.md) |
| R.1 · Transacciones | [docs/R1_transacciones.md](docs/R1_transacciones.md) |
| R.2 · PostgreSQL mejorado | [docs/R2_pg_mejorado.md](docs/R2_pg_mejorado.md) |
| A · Carga de trabajo | [docs/A_carga.md](docs/A_carga.md) |
| B · Modelo documental | [docs/B_modelo.md](docs/B_modelo.md) |
| C · Migración y validación | [resultados/c_validacion.txt](resultados/c_validacion.txt) |
| D · MongoDB frente a PostgreSQL | [docs/D_mongo.md](docs/D_mongo.md) |

## Decisiones de diseño

Siete colecciones (detalle en [docs/B_modelo.md](docs/B_modelo.md)):

- **pedidos**: dirección, líneas e historial **incrustados** (máx. 40 y 5). La línea **copia** el nombre y el precio pagado y referencia la versión vendida.
- **productos**: versión vigente con los atributos (patrón *attribute*), un **resumen** de reseñas y las **3 últimas** (patrones *computed* y *subset*): el producto con 43.020 reseñas ocupa 2 KB.
- **resenas**, **productos_versiones** (historial SCD2) y **stock**, en colecciones propias: crecen sin límite o se escriben a otro ritmo.
- **clientes** con preferencias, deseos y un resumen de pedidos; **top_categoria** con el carrusel precalculado.
- Validadores `$jsonSchema` en todas las colecciones (tipos, `enum`, patrones, tamaño máximo de los arrays, stock no negativo) y trazabilidad (`_origen`, `_esquema`) en cada documento.

## Cómo reproducirlo

Requisitos: Docker Desktop, 8 GB de RAM y 15 GB libres.

```bash
docker compose up -d
docker exec -it mendiak-pg psql -U mendiak -d mendiak -f /scripts/01_esquema.sql
docker exec -it mendiak-pg psql -U mendiak -d mendiak -f /scripts/02_carga_datos.sql
docker exec -it mendiak-pg psql -U mendiak -d mendiak -c "CREATE EXTENSION pg_stat_statements;"

# Punto de partida
docker compose run --rm banco medir --motores pg

# PostgreSQL mejorado
docker exec -it mendiak-pg psql -U mendiak -d mendiak -f /scripts/05_pg_mejorado.sql
docker compose run --rm banco comprobar --motores pg,pg_mejorado

# MongoDB: migración, validación y comparación
docker compose run --rm --entrypoint python banco 07_migrar.py
docker compose run --rm --entrypoint python banco 08_validar.py
docker compose run --rm --entrypoint python banco 09_d2_transacciones.py
docker compose run --rm banco comprobar --motores pg_mejorado,mongo
docker compose run --rm banco medir --motores pg,pg_mejorado,mongo
```

## Estructura

| Archivo | Contenido |
|---|---|
| `01_esquema.sql`, `02_carga_datos.sql` | Esquema relacional y datos sintéticos reproducibles |
| `03_operaciones.sql` | Las diez operaciones originales |
| `04_transacciones.sql` | Ejercicios de transacciones (parte R.1) |
| `00_parte0_pruebas.sql` | Pruebas extra del punto de partida (caché fría y caliente, producto atípico) |
| `05_pg_mejorado.sql` | Mejoras en PostgreSQL (parte R.2); se puede ejecutar varias veces |
| `05_e1_diagnostico.sql`, `05_l3_diagnostico.sql` | Diagnósticos de la R.2: el error de E1 y la medición de L3 |
| `06_mongo_esquema.py` | Colecciones de MongoDB: validadores `$jsonSchema` e índices |
| `07_migrar.py` | Migración: una consulta SQL por colección que construye cada documento |
| `08_validar.py` | Validación de la migración (C.1 a C.4) |
| `09_d2_transacciones.py` | Experimentos de transacciones en MongoDB (D.2) |
| `mis_operaciones.py` | Las diez operaciones en PostgreSQL mejorado y en MongoDB |
| `banco_pruebas.py` | Banco de pruebas común a los dos motores |
| `resultados/medidas.csv` | Mis mediciones (las genera el banco) |
| `resultados/referencia_profesor.csv` | Mediciones de referencia del curso, para contrastar |
| `docs/` | Análisis y respuestas de cada parte (0, R, A, B, C, D) |

## Autora

Lore · [github.com/AlorenaP](https://github.com/AlorenaP)
