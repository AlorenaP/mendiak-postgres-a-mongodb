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

Tiempo medio por operación (ms), medido con el banco de pruebas. Se completará al terminar la parte D.

| Operación | Veces/día | PG original | PG mejorado | MongoDB |
|---|---:|---:|---:|---:|
| L1 Ficha de producto | 2.900.000 | 1.110 | 1,18 | |
| L2 Listado con facetas | 780.000 | 808 | 24,7 | |
| L3 Panel del vendedor | 12.000 | 117 | 37,8 | |
| L4 Mis pedidos | 350.000 | 3,5 | 1,85 | |
| L5 Seguimiento de pedido | 900.000 | 1,5 | 0,76 | |
| L6 Informe de ventas | 50 | 5.480 | 3.241 | |
| E1 Crear pedido | 6.250 | 8,9 | 3,8 | |
| E2 Publicar catálogo | 40 | 88 | 80 | |
| E3 Avanzar estado | 25.000 | 1,8 | 0,72 | |
| E4 Editar ficha técnica | 3.000 | 1,3 | 1,96 | |
| **Horas de servidor al día** | | **≈ 1.070** | **≈ 6,9** | |

- **Punto de partida:** el 99,9 % del coste diario estaba en L1 (ficha de producto) y L2 (buscador). Análisis en [docs/0_punto_de_partida.md](docs/0_punto_de_partida.md).
- **PostgreSQL mejorado:** de ≈ 1.070 a ≈ 6,9 horas de servidor al día (**unas 155 veces menos**) con un contador de reseñas, una vista materializada, una tabla de búsqueda y un índice cubriente, sin salir de PostgreSQL. Detalle y decisiones en [docs/R2_pg_mejorado.md](docs/R2_pg_mejorado.md).

> Cada columna se midió en una ejecución distinta del banco. L4, L5 y L6 no se modificaron y aun así salen más rápidas en la columna "PG mejorado" porque esa medición se hizo después de `VACUUM ANALYZE` y con la caché más caliente. Para las operaciones mejoradas, la comparación justa es la A/B (los dos motores en la misma ejecución) que recoge cada documento.

## Decisiones de diseño

_Se completará en las partes A–C: qué se incrusta, qué se referencia y por qué._

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
docker compose run --rm banco medir --motores pg_mejorado
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
| `mis_operaciones.py` | Operaciones mejoradas y en MongoDB |
| `banco_pruebas.py` | Banco de pruebas común a los dos motores |
| `resultados/medidas.csv` | Mis mediciones (las genera el banco) |
| `resultados/referencia_profesor.csv` | Mediciones de referencia del curso, para contrastar |
| `docs/` | Análisis y respuestas de cada parte (0, R, A, B, C, D) |

## Autora

Lore · [github.com/AlorenaP](https://github.com/AlorenaP)
