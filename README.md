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

_Se completará al terminar la parte D._

| Operación | Veces/día | PG original (ms) | PG mejorado (ms) | MongoDB (ms) |
|---|---|---|---|---|
| L1 Ficha de producto | 2.900.000 | | | |
| L2 Listado con facetas | 780.000 | | | |
| L3 Panel del vendedor | 12.000 | | | |
| L4 Mis pedidos | 350.000 | | | |
| L5 Seguimiento de pedido | 900.000 | | | |
| L6 Informe de ventas | 50 | | | |
| E1 Crear pedido | 6.250 | | | |
| E2 Publicar catálogo | 40 | | | |
| E3 Avanzar estado | 25.000 | | | |
| E4 Editar ficha técnica | 3.000 | | | |

## Decisiones de diseño

_Se completará en las partes A–C: qué se incrusta, qué se referencia y por qué._

## Cómo reproducirlo

Requisitos: Docker Desktop, 8 GB de RAM y 15 GB libres.

```bash
docker compose up -d
docker exec -it mendiak-pg psql -U mendiak -d mendiak -f /scripts/01_esquema.sql
docker exec -it mendiak-pg psql -U mendiak -d mendiak -f /scripts/02_carga_datos.sql
docker exec -it mendiak-pg psql -U mendiak -d mendiak -c "CREATE EXTENSION pg_stat_statements;"

docker compose run --rm banco medir --motores pg
```

## Estructura

| Archivo | Contenido |
|---|---|
| `01_esquema.sql`, `02_carga_datos.sql` | Esquema relacional y datos sintéticos reproducibles |
| `03_operaciones.sql` | Las diez operaciones originales |
| `04_transacciones.sql` | Ejercicios de transacciones (parte R.1) |
| `05_pg_mejorado.sql` | Mejoras en PostgreSQL (parte R.2) |
| `mis_operaciones.py` | Operaciones mejoradas y en MongoDB |
| `banco_pruebas.py` | Banco de pruebas común a los dos motores |
| `resultados/medidas.csv` | Mis mediciones (las genera el banco) |
| `resultados/referencia_profesor.csv` | Mediciones de referencia del curso, para contrastar |
| `docs/` | Análisis y respuestas de cada parte (0, R, A, B, C, D) |

## Autora

Lore · [github.com/AlorenaP](https://github.com/AlorenaP)
