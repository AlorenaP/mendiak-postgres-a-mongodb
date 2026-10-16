# R.2 · Un PostgreSQL mejor

Objetivo: mejorar cada operación **sin salir de PostgreSQL**, para saber después qué parte de la mejora de MongoDB se debe al modelo documental y qué parte se podía conseguir igual con el motor relacional.

- Cambios en la base de datos: [`05_pg_mejorado.sql`](../05_pg_mejorado.sql) (se puede ejecutar varias veces)
- Consultas nuevas: funciones `pg_*` de [`mis_operaciones.py`](../mis_operaciones.py)
- Mediciones: [`resultados/medidas.csv`](../resultados/medidas.csv), etiquetas `r2-*`

## Criterio

Se mejora por **coste diario** (veces al día × tiempo), no por tiempo de una ejecución. Cada mejora se valida en dos pasos:

1. `banco comprobar`: la versión mejorada devuelve lo mismo que la original.
2. `banco medir`: tiempo y KB leídos.

Para cada dato se decide cuánto retraso admite, porque eso determina la técnica:

| Técnica | Cuándo | Ejemplo |
|---|---|---|
| **Contador mantenido al escribir** (trigger) | El dato tiene que ser exacto siempre | Nº de reseñas y nota de un producto |
| **Vista materializada** (refresco periódico) | Admite retraso | Carrusel de L1 (1 hora) |
| **Índice cubriente** (`INCLUDE`) | La consulta lee pocas columnas de muchas filas dispersas | Segmento del cliente en E1 |
| **Dejar la original** | La operación es barata o mejorarla encarece otra más crítica | L6, parte de ventas de L3 |

---

## L1 · Ficha de producto

**Problema** ([Parte 0](0_punto_de_partida.md)): en cada visita se recorría la tabla `resena` entera (2,1 M filas, ~490 MB) para el carrusel, y todas las reseñas del producto para el contador, la nota y el histograma.

**Cambios:**

| Dato | Antes | Ahora | Tolerancia |
|---|---|---|---|
| Nº de reseñas, nota media, histograma | Recorrer todas las reseñas del SKU | Tabla `resena_resumen` (1 fila por SKU) mantenida por un trigger en la misma transacción que la reseña | Ninguna: siempre exacta |
| Carrusel "mejor valorados de la categoría" | `Seq Scan` de la tabla `resena` | Vista materializada `mv_top_categoria`: 7 productos × 40 categorías = 280 filas | 1 hora: `REFRESH ... CONCURRENTLY` cada hora |

Detalles para que el resultado sea idéntico al original:

- La vista guarda **7** productos por categoría, no 6: si el producto que se visita está entre ellos, se excluye y siguen quedando 6.
- Se ordena por la nota **exacta** (`suma::numeric / n`), que es el mismo cálculo que hace `avg()`, para que los empates se resuelvan en el mismo orden. El redondeo a 2 decimales solo se aplica al mostrarla.
- El trigger cubre `INSERT`, `DELETE` y `UPDATE` de `sku` o `puntuacion`. Verificado: tras insertar, modificar y borrar reseñas, el resumen coincide con un recálculo completo.

**Resultado:**

| | Original | Mejorado | Factor |
|---|---:|---:|---:|
| Tiempo medio | 1.110 ms | **0,95 ms** | ×1.170 |
| p95 | 1.215 ms | 1,94 ms | |
| KB leídos por ejecución | 498.582 | **59** | ×8.450 |
| **Horas de servidor al día** | **894** | **0,76** | |

`banco comprobar`: 5/5 OK, incluido `MEND-000001` (43.020 reseñas).

**Coste de la mejora:**

- Cada reseña nueva hace una escritura más (el `UPSERT` en `resena_resumen`). Las reseñas son mucho menos frecuentes que las visitas a la ficha (L1 se lanza 2,9 M veces al día).
- El producto con 43.020 reseñas concentra muchas escrituras en **una sola fila** del resumen (un punto caliente). Con el ritmo de reseñas de una tienda no es un problema; si lo fuera, se repartiría el contador en varias filas.
- La vista materializada hay que refrescarla con un proceso programado (`pg_cron` o un cron externo). Refrescarla cuesta menos de 1 s, porque se calcula a partir de `resena_resumen` y no de las reseñas.

---

## E1 · Crear pedido

### El error del paso 6

```sql
FROM pedido p JOIN pedido_linea l ON l.pedido_id = p.pedido_id
```

Al unir con las líneas, **cada pedido aparece una vez por línea**: `count(*)` cuenta líneas y `sum(importe_total)` suma el total del pedido tantas veces como líneas tiene. Diagnóstico: [`05_e1_diagnostico.sql`](../05_e1_diagnostico.sql) → [`resultados/e1_diagnostico.txt`](../resultados/e1_diagnostico.txt).

**El cliente 1:**

| | Real | Según el paso 6 | Error |
|---|---:|---:|---:|
| Pedidos no cancelados | **3.834** | 11.120 | ×2,9 |
| Gasto | **5.412.703 €** | 37.413.847 € | ×6,9 |

El error es mayor en el importe que en el número de pedidos porque los pedidos con muchas líneas (el 2 % tiene entre 8 y 40) suelen ser también los más caros, así que son los que más se repiten en la suma.

**Clientes a los que se asignaría mal el segmento:**

| Correcto | Con el error | Clientes |
|---|---|---:|
| nuevo | premium | 52.176 |
| nuevo | recurrente | 3.018 |
| recurrente | premium | 1.289 |
| **Total** | | **56.483 (35,0 % de los 161.593 clientes con pedidos)** |

El error siempre **sube** de categoría, nunca baja: contar de más solo puede hacer que se superen los umbrales. En la práctica, uno de cada tres clientes que hiciera un pedido pasaría a un segmento que no le corresponde, por ejemplo recibiendo ventajas de cliente premium.

### Cambios

1. **Paso 6 corregido:** cuenta y suma solo sobre `pedido`, sin `JOIN`.
2. **Índice cubriente:** `ix_pedido_cliente (cliente_id, ts_creacion DESC) INCLUDE (estado_actual, importe_total)`. El paso 6 corregido se resuelve con un *Index Only Scan*, sin visitar los pedidos del cliente repartidos por el disco (3.834 en el caso del cliente 1). Se **amplía el índice que ya existía** en lugar de crear otro, para no añadir coste a cada `INSERT` de pedido; L4 lo sigue usando igual, porque busca y ordena por las mismas columnas.
3. **Paso 0 nuevo:** `SELECT ... ORDER BY sku FOR UPDATE` sobre el stock antes de descontarlo. Bloquea siempre en el mismo orden y evita el interbloqueo del [ejercicio 5 de la R.1](R1_transacciones.md#ejercicio-5--interbloqueo).

El descuento de stock se mantiene como en el original: un `UPDATE` que falla por el `CHECK (unidades >= 0)` si no hay unidades, y la transacción entera se deshace ([R.1, ejercicio 4b](R1_transacciones.md#4b--comprobar-y-restar-en-la-misma-sentencia)).

### Resultado

| | Original | Mejorado |
|---|---:|---:|
| Tiempo medio | 8,9 ms | **3,8 ms** |
| p95 | 11,3 ms | 5,3 ms |
| KB leídos | 146 | 120 |
| Horas de servidor al día | 0,015 | 0,007 |

E1 pesa poco en el coste total. Lo importante de esta mejora es que **ahora el resultado es correcto**.

`banco comprobar`: en uno de los tres casos aleatorios los motores **no coinciden**, y es lo esperado:

```
E1  pedido_id=PED-91037875, cliente_id=16639  DISTINTO
      pg           (699.50, 'premium')   ← original: segmento incorrecto
      pg_mejorado  (699.50, 'nuevo')     ← corregido
```

El importe es el mismo y solo cambia el segmento: el original clasifica como premium a un cliente que ha gastado mucho menos de 3.000 €. Es un ejemplo de lo que advierte el enunciado: cuando dos versiones no coinciden, **no siempre está mal la nueva**.

---

## L3 · Panel del vendedor

**Cambio:** la "salud del catálogo" (nº de productos vivos, nota media del catálogo y total de reseñas) sale de `resena_resumen`, la misma tabla de L1, en vez de recorrer todas las reseñas de cada producto del vendedor.

**Decisión de no tocar la parte de ventas.** Las líneas del último año se siguen calculando como en el original: con el vendedor 1 son 119.235 líneas y otras tantas búsquedas de su pedido. Acelerarlo exigiría índices nuevos sobre `pedido_linea` y `pedido` (o copiar `vendedor_id` y la fecha en cada línea), lo que encarece **cada alta de pedido (E1)**. L3 cuesta 0,4–0,8 h al día: no compensa encarecer una escritura crítica para ahorrar en una lectura barata.

### Una medición que no cuadraba

La primera medición (`r2-L3`, 10 repeticiones) dio **peor** tiempo medio que el original (179 ms frente a 117 ms) a pesar de leer 46 KB en vez de 89.386. Antes de aceptarla o descartarla:

1. **Planes** ([`05_l3_diagnostico.sql`](../05_l3_diagnostico.sql) → [`resultados/l3_diagnostico.txt`](../resultados/l3_diagnostico.txt)) de la salud del catálogo, original frente a mejorada:

   | Vendedor | Original | Mejorada |
   |---|---:|---:|
   | 1500 (87 productos) | 3,3 ms · 754 bloques | **1,4 ms** · 471 bloques |
   | 1 (32.459 productos) | 172,7 ms · 245.620 bloques | **42,6 ms** · 13.894 bloques |

   Con el vendedor 1, PostgreSQL elige recorrer `resena_resumen` entera (*Parallel Seq Scan* + *Hash Join*) en lugar de 32.459 búsquedas por índice, y es la decisión correcta: leer una tabla compacta de una vez es más barato que 32.000 accesos sueltos.

2. **Medición A/B** (`r2-L3-ab`): los dos motores en la misma ejecución, con los mismos vendedores y 30 repeticiones:

   | | Original | Mejorado |
   |---|---:|---:|
   | p50 | 54,8 ms | **37,2 ms** |
   | Media | 236,6 ms | **161,8 ms** (−32 %) |
   | p95 | 1.396 ms | 1.011 ms |
   | KB leídos | 201.070 | **68** |
   | Horas/día | 0,79 | **0,54** |

**Conclusión:** la primera medición era **ruido**. Con 10 repeticiones y 8 vendedores de tamaños muy distintos, la media depende de cuántas veces sale el vendedor 1, y el original y el mejorado se habían medido en momentos distintos. **Lección: comparar siempre en la misma ejecución y con suficientes repeticiones.** El tiempo que queda (p95 ≈ 1 s) es la parte de ventas del vendedor 1, que se deja a propósito.

### Observación: filas muertas por los `ROLLBACK`

En los planes, el índice `ix_producto_vendedor` devuelve **103.923** entradas para el vendedor 1, cuando en la Parte 0 eran 39.005 (y 545 para el 1500, que tiene 107 filas). Son las versiones que crean las ejecuciones de E2 del banco y que después se deshacen con `ROLLBACK`: quedan como filas muertas en la tabla y en sus índices hasta que pasa `VACUUM`. No afectan al resultado, pero sí al trabajo de las consultas. **Antes de las mediciones finales (D.3) se ejecutará `VACUUM ANALYZE`** para que todos los motores partan de la misma situación.

---

## L2 · Buscador con filtros y facetas

**Problema** ([Parte 0](0_punto_de_partida.md)): con el EAV, cada filtro es un `JOIN` más contra 7,9 M de filas y solo el primero aprovecha bien un índice. Para devolver 1.233 productos se examinaban unos 80.000, y luego había que volver al EAV para las facetas.

**Cambio: una tabla de búsqueda** (`producto_busqueda`), con una fila por producto vendible (vigente y activo), es decir, *copiar los datos en la tabla donde se consultan*:

| Qué guarda | Para qué |
|---|---|
| `departamento_id` copiado de la categoría | Evita el `JOIN` con `dim_categoria` |
| **Color, género y peso como columnas** | Son atributos **universales** (los tiene todo producto) y son los que usa el buscador. Un índice compuesto `(departamento_id, color, genero, peso)` resuelve los cuatro filtros a la vez y entrega exactamente las filas que los cumplen |
| La ficha técnica completa en un `jsonb` | Las facetas se calculan con `jsonb_each_text` sobre las filas ya encontradas, sin volver al EAV |

Además, la tabla se ordena físicamente según el índice (`CLUSTER`), así que las filas de una misma búsqueda quedan juntas en el disco. Las altas posteriores no mantienen ese orden; se repetiría en el mantenimiento periódico.

El **EAV sigue siendo la fuente de verdad**: `producto_busqueda` es una copia derivada, definida en una sola vista (`v_producto_busqueda`) que sirve tanto para la carga inicial como para mantenerla.

### ¿Cómo se mantiene al día? Tolerancia cero

El listado muestra el **precio**, y tiene que coincidir con la ficha de producto en cuanto el vendedor publica (E2). Por eso **no** se usa una vista materializada con refresco periódico (como el carrusel de L1), sino **triggers por sentencia** sobre `dim_producto` y `producto_atributo`: cuando una sentencia toca productos o atributos, se recalculan las filas de esos SKU **en la misma transacción**.

- **Por sentencia y no por fila:** E2 con el vendedor 1 toca 1,1 M de filas en dos sentencias. Con triggers por fila se recalcularía cada SKU unas 17 veces; por sentencia, una vez por sentencia, usando las tablas de transición (`REFERENCING NEW TABLE / OLD TABLE`).
- **Verificado** en una copia de pruebas: después de E2 (vendedores 1 y 1500) y de E4, la tabla coincide exactamente con un recálculo completo (0 diferencias), y el precio nuevo aparece en el buscador en la misma transacción que la publicación.

**Un fallo encontrado al probar.** La primera versión de la vista agrupaba todo el EAV con `GROUP BY sku`. Con esa forma, el filtro por SKU de los triggers no llegaba hasta `producto_atributo` y **cada recálculo de un solo producto recorría los 7,9 M de filas**. Se cambió por un `LATERAL`, que busca los atributos producto a producto por la clave primaria del EAV. Lo delató el plan de ejecución, no el tiempo: en la copia de pruebas, pequeña, solo eran 12 ms.

### Resultado

`banco comprobar`: 10/10 OK (L2, E2 y E4, estas dos con los triggers activos).

| L2 · medición A/B, 30 repeticiones | Original | Mejorado | Factor |
|---|---:|---:|---:|
| p50 | 538,6 ms | **30,4 ms** | |
| Media | 522,3 ms | **27,9 ms** | ×19 |
| p95 | 882,3 ms | **41,5 ms** | |
| KB leídos | 453.182 | **104** | ×4.360 |
| **Horas de servidor al día** | **113,2** | **6,05** | |

> La media del original es más baja que en la Parte 0 (808 ms) porque esta medición se hizo después de `VACUUM ANALYZE`. Por eso se compara en A/B, en la misma ejecución.

### El precio: las escrituras hacen más trabajo

| Escritura | Parte 0 (sin triggers) | Con triggers |
|---|---:|---:|
| E2 (vendedores 1500, 600, 150) | 87,6 ms · 3.165 KB | 81,7 ms · 6.997 KB |
| E4 | 1,32 ms · 43 KB | 1,46 ms · 78 KB |

Con vendedores de tamaño normal el tiempo apenas cambia, aunque se lee más (los KB se duplican). En la copia de pruebas, E2 con el **vendedor 1** pasa de 536 a 759 ms (+40 %). Es el mismo dilema que aparecerá en MongoDB al incrustar la ficha técnica en el producto: **leer barato a cambio de escribir más caro**. Aquí compensa con mucho: L2 se lanza 780.000 veces al día; E2, 40 veces, y E4, 3.000.

### Por qué solo color, género y peso como columnas

Son los tres atributos por los que filtra el buscador y están entre los cinco universales. Los demás atributos dependen de la categoría (una tienda de campaña no tiene talla), así que siguen en el EAV y en el `jsonb`. Si mañana se quisiera filtrar por cualquier atributo, la alternativa en PostgreSQL sería un índice GIN sobre el `jsonb`. Es la misma idea que el **patrón *attribute*** que se usará en MongoDB (parte B.5).

---

## Operaciones que se dejan como estaban

El enunciado pide no tocar lo que no hace falta mejorar y justificarlo con el `EXPLAIN`:

| Op | Coste/día | Plan (Parte 0) | Decisión |
|---|---:|---|---|
| **L4** Mis pedidos | 0,34 h | Todo por índice; 2,7 ms y 214 bloques para 10 pedidos completos | **No se toca.** Lo que cuesta no es el plan, sino que las filas de un pedido (líneas e historial) están **repartidas por el disco**; por eso es la operación más sensible a la falta de memoria (×2,7). En PostgreSQL solo se arreglaría reordenando las tablas (`CLUSTER`), y el orden se pierde con cada inserción. **Es justo lo que resuelve el modelo documental**: el pedido completo en un solo sitio |
| **L5** Seguimiento de pedido | 0,38 h | Igual que L4 para un pedido: 2,2 ms y 18 bloques | Igual que L4 |
| **L6** Informe de ventas | 0,08 h | Lectura secuencial de tres tablas, 4,6 s | **No se toca.** Se lanza 50 veces al día. Precalcularla (una vista materializada por departamento y mes) ahorraría segundos al día y quitaría flexibilidad: mañana pedirán otro corte |
| **E2** Publicar catálogo | 0,001 h | Una sentencia por paso | Se deja. Su problema (no es idempotente: ver [R.1, ejercicio 6](R1_transacciones.md#ejercicio-6--publicar-un-catálogo-es-todo-o-nada)) es de diseño de la aplicación, no de rendimiento |
| **E3** Avanzar estado | 0,01 h | 1,3 ms | No hay nada que mejorar |
| **E4** Editar ficha | 0,001 h | 1 ms | No hay nada que mejorar |

---

## R.3 · Lo que ya hacía bien PostgreSQL

Antes de pasar a MongoDB conviene anotar lo que el modelo relacional daba **sin programar nada**, porque en un modelo documental habrá que hacerlo a mano o se perderá:

1. **Integridad referencial.** Las claves ajenas impiden reseñas de productos inexistentes, líneas que apuntan a versiones que no existen o pedidos de clientes borrados. En MongoDB no hay claves ajenas: habrá que comprobarlo con consultas (parte C.2).
2. **Reglas de dominio.** Los `CHECK` (stock nunca negativo, estados y canales válidos, una versión que no termina antes de empezar) se cumplieron en todos los ejercicios de la R.1. En MongoDB, el equivalente es el validador `$jsonSchema` (parte C.1).
3. **El historial de precios (SCD2).** Cada línea apunta a la versión del producto que se vendió: 198.542 líneas conservan el precio antiguo. El modelo documental tendrá que guardar ese dato en la línea o mantener las versiones.
4. **Transacciones de cualquier tamaño.** E2 con el vendedor 1 toca 1,1 M de filas en una sola transacción, todo o nada, y los clientes nunca ven el catálogo a medias (MVCC, [R.1, ejercicio 6](R1_transacciones.md#ejercicio-6--publicar-un-catálogo-es-todo-o-nada)). En MongoDB, una transacción tan grande no es viable (parte D.2).
5. **Consultas que nadie preparó.** L6 cruza cinco tablas, agrupa por departamento y mes y saca subtotales con `ROLLUP` sin ninguna estructura hecha para ello. Mañana podrá ser por marca o por provincia sin cambiar el modelo.
6. **Bloqueos y concurrencia.** Bloqueo de fila, detección de interbloqueos y niveles de aislamiento ([R.1, ejercicios 4 y 5](R1_transacciones.md#ejercicio-4--dos-clientes-compran-la-última-unidad)).

Y la conclusión más importante de la R.2: **el 99 % del coste original no venía del motor relacional, sino de recalcular en cada lectura datos que cambian poco** (L1) y **de un modelo de atributos poco apto para filtrar** (L2). Ambas cosas se han resuelto sin salir de PostgreSQL.

---

## Resumen de la R.2

| Op | Veces/día | Original (ms) | Mejorado (ms) | Horas/día original | Horas/día mejorado | Técnica |
|---|---:|---:|---:|---:|---:|---|
| **L1** | 2.900.000 | 1.110 | **0,95** | 894 | **0,76** | Contador + vista materializada |
| **L2** | 780.000 | 522 | **27,9** | 113 | **6,05** | Tabla de búsqueda + índice compuesto |
| **L3** | 12.000 | 237 | **162** | 0,79 | 0,54 | Contador de L1 |
| **E1** | 6.250 | 8,9 | **3,8** | 0,015 | 0,007 | Corrección + índice cubriente |
| L4, L5, L6, E2, E3, E4 | | | | ≈ 0,8 | ≈ 0,8 | Sin cambios |
| **Total** | | | | **≈ 1.070** (Parte 0) | **≈ 8** | **≈ 130 veces menos** |

> Los valores "original" de L2 y L3 son los de las mediciones A/B. La tabla definitiva, con todas las operaciones medidas en una misma ejecución, está en el [README](../README.md).
