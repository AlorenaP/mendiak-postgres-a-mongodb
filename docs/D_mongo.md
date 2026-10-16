# Parte D · MongoDB frente a PostgreSQL

- Operaciones en MongoDB: funciones `mongo_*` de [`mis_operaciones.py`](../mis_operaciones.py), sobre el modelo de la [parte B](B_modelo.md)
- Migración y validación: [`07_migrar.py`](../07_migrar.py), [`08_validar.py`](../08_validar.py) → [`resultados/c_validacion.txt`](../resultados/c_validacion.txt) (**todo correcto**)
- Transacciones: [`09_d2_transacciones.py`](../09_d2_transacciones.py) → [`resultados/d2_transacciones.txt`](../resultados/d2_transacciones.txt)
- Mediciones: [`resultados/medidas.csv`](../resultados/medidas.csv), etiquetas `final` y `final-poca-memoria`

---

## D.1 · Las operaciones en MongoDB

`banco comprobar --motores pg_mejorado,mongo`: **39 de 39 casos coinciden**, incluidos el producto con 43.020 reseñas (L1), el vendedor con 32.459 productos (L3) y el cliente con 4.452 pedidos (L4).

| Op | Cómo se resuelve en MongoDB |
|---|---|
| L1 | 1 documento de `productos` (ficha, atributos, resumen y 3 últimas reseñas) + 1 de `top_categoria` |
| L2 | Una agregación: `$match` con el índice de atributos y `$facet` (total, página y facetas) |
| L3 | Agregación sobre `pedidos` (índice `lineas.vendedor_id`) + resumen de reseñas de sus productos |
| L4 | 10 documentos de `pedidos`, ya ordenados por el índice |
| L5 | **1** documento |
| L6 | Agregación sobre los pedidos de un año: departamento × mes, subtotales y total |
| E1 | Transacción: 1 pedido + *k* stock + 1 cliente |
| E2 | Transacción: por cada producto, 1 `productos` + 2 `productos_versiones` |
| E3 | **1** actualización atómica (`$set` del estado y añadir al historial en el mismo documento) |
| E4 | **1** actualización atómica (`$inc` solo del atributo editado, con `arrayFilters`) |

---

## D.2 · Transacciones en MongoDB

### 1. ¿Necesita E3 transacción?

| | PostgreSQL | MongoDB |
|---|---|---|
| Qué escribe | 1 fila en `pedido` + 1 en `pedido_estado_hist` | 1 documento |
| ¿Transacción? | **Sí**: dos tablas | **No**: una escritura sobre un documento es atómica |

La diferencia viene del **modelo**: el historial vive dentro del pedido, así que el estado y su transición se escriben de una vez.

### 2. E1 y la última unidad (ejercicio 4 de la R.1, en MongoDB)

E1 toca **1 pedido + *k* stock + 1 cliente** (*k* = productos distintos), así que necesita transacción.

Dos transacciones intentan comprar la última unidad de `MEND-000777` con la resta condicional `updateOne({_id, unidades: {$gte: 1}}, {$inc: {unidades: -1}})`:

```
T1 resta 1: modificados = 1
T2 resta 1 -> ERROR inmediato: OperationFailure ['TransientTransactionError']
   Write conflict during plan execution …
```

- **No se vende dos veces.** La condición `unidades >= 1` y la resta van en la misma operación, igual que la solución 4b de PostgreSQL.
- **La diferencia:** en PostgreSQL, T2 **esperaba** a que T1 terminara y luego devolvía `UPDATE 0`. En MongoDB, T2 **falla al instante** con un conflicto de escritura etiquetado como `TransientTransactionError`. Se parece al `REPEATABLE READ` del ejercicio 4d: la aplicación tiene que **reintentar la transacción entera** (el banco lo hace, como `with_transaction` del driver). En el reintento verá 0 unidades y no venderá.

### 3. E2 con el vendedor 1 (32.459 productos)

| Prueba | Resultado |
|---|---|
| **Una sola transacción** (97.377 documentos) | **Falla** tras 27,2 s: MongoDB interrumpe la transacción (código 365) en el documento 15.600. Una transacción no puede crecer sin límite: su tamaño está acotado por la caché y por el tiempo de vida de la transacción |
| **Por lotes de 1.000 productos**, una transacción por lote | **33 lotes, 0 fallos, 3,4 s en total**; el lote más lento, 0,28 s |

En PostgreSQL, el mismo E2 (1,1 M de filas) es **una sola transacción** sin problema ([R.3](R2_pg_mejorado.md#r3--lo-que-ya-hacía-bien-postgresql)).

**Lo que cambia al publicar por lotes:**

- **¿Qué vería un cliente mientras se publica?** Una mezcla: los productos de los lotes ya confirmados con el precio nuevo y el resto con el antiguo. Se pierde el "todo o nada" que daba PostgreSQL. Es aceptable si el negocio lo tolera unos segundos; si no, habría que publicar la versión nueva sin activarla y cambiar después un único indicador de "versión del catálogo".
- **¿Y si el proceso se corta a mitad?** `mongo_E2` es **idempotente**: no vuelve a versionar un producto cuya versión vigente empieza hoy. Relanzar el proceso completo solo procesa los lotes pendientes **y no crea versiones duplicadas**. Es la lección del [ejercicio 6 de la R.1](R1_transacciones.md#ejercicio-6--publicar-un-catálogo-es-todo-o-nada), resuelta en el código.

### Documentos frente a filas en cada escritura

| Escritura | PostgreSQL: filas (tablas) | ¿Transacción? | MongoDB: documentos | ¿Transacción? |
|---|---|---|---|---|
| E1 (3 productos) | 10 filas (6 tablas) | Sí | 5 documentos (3 colecciones) | Sí |
| E2 (vendedor 1500, 87 productos) | ~3.050 filas (2 tablas) | Sí | 261 documentos (2 colecciones) | Sí (por lotes si es grande) |
| E3 | 2 filas (2 tablas) | Sí | **1 documento** | **No** |
| E4 | 34 filas (17 borradas + 17 insertadas) | Una sentencia | **1 documento** | **No** |

---

## D.3 · Mediciones

Las tres versiones en la **misma ejecución** del banco, después de `VACUUM ANALYZE`. Tiempo medio en ms; entre paréntesis, KB leídos por operación.

### Modo normal (`final`)

| Op | Veces/día | PG original | PG mejorado | MongoDB | Mejor |
|---|---:|---:|---:|---:|---|
| L1 | 2.900.000 | 738 (498.544) | **0,53** (2) | **0,52** (80) | Empate |
| L2 | 780.000 | 527 (422.096) | **27,3** (219) | 220 (465.392) | **PG mejorado** ×8 |
| L3 | 12.000 | 59 (89.482) | **39** (134) | 132 (303.244) | **PG mejorado** ×3 |
| L4 | 350.000 | 1,29 (203) | 0,53 (0) | **0,46** (197) | Empate |
| L5 | 900.000 | 0,54 (71) | **0,22** (0) | 0,38 (90) | PG mejorado |
| L6 | 50 | 2.839 (72.096) | **2.799** (68.053) | 6.275 (628.975) | **PostgreSQL** ×2 |
| E1 | 6.250 | 3,41 | **1,92** | 3,61 | PG mejorado |
| E2 | 40 | 105 | 82 | **37** | **MongoDB** ×2 |
| E3 | 25.000 | 0,59 | **0,49** | 0,75 | PG mejorado |
| E4 | 3.000 | 2,26 | 1,22 | **1,07** | Empate |
| **Horas/día** (`medir`) | | **709** | **6,6** | **48,7** | |
| **Horas/día** (`dia`, 1.000 operaciones) | | — | **8,2** | **70,6** | |

> El "PG original" de esta tabla es más rápido que en la [Parte 0](0_punto_de_partida.md) (709 frente a 1.070 h/día) porque se mide con la caché caliente y después de `VACUUM`. Además, E2 y E4 originales ya disparan los triggers de la tabla de búsqueda de la R.2.

### Con poca memoria (1 GB por motor; `final-poca-memoria`)

| Op | PG mejorado normal | **PG mejorado poca memoria** | MongoDB normal | **MongoDB poca memoria** | Mejor con poca memoria |
|---|---:|---:|---:|---:|---|
| L1 | 0,53 | **7,90** (×15) | 0,52 | **2,44** (×4,7) | **MongoDB ×3,2** |
| L2 | 27,3 | **37,9** | 220 | **367** | PG mejorado ×9,7 |
| L3 | 39 | 55 | 132 | 200 | PG mejorado ×3,7 |
| L4 | 0,53 | **7,31** (×14) | 0,46 | **2,55** (×5,5) | **MongoDB ×2,9** |
| L5 | 0,22 | **3,92** (×18) | 0,38 | **1,21** (×3,2) | **MongoDB ×3,2** |
| L6 | 2.799 | 4.326 | 6.275 | 8.808 | PG mejorado ×2 |
| E1 | 1,92 | 5,97 | 3,61 | 6,47 | Empate |
| E2 | 82 | 299 | 37 | 70 | MongoDB ×4,3 |
| E3 | 0,49 | 1,63 | 0,75 | 1,55 | Empate |
| E4 | 1,22 | 3,89 | 1,07 | 1,29 | MongoDB ×3 |
| **Horas/día** (`dia`) | 8,2 | **10,8** | 70,6 | **67,1** | |

---

## D.4 · Qué hemos aprendido

### ¿En qué operaciones es más rápido el modelo documental? ¿Por qué?

En las que **montan una entidad a partir de muchas filas repartidas por el disco**: L1 (ficha de producto), L4 (mis pedidos) y L5 (seguimiento de un pedido). **Con poca memoria, MongoDB es unas 3 veces más rápido** en las tres.

La razón es cuántos sitios distintos del disco hay que leer:

| Operación | PostgreSQL mejorado | MongoDB |
|---|---|---|
| L5 (un pedido) | Cabecera + líneas + versión de producto de cada línea + historial: **~18 bloques** repartidos (Parte 0) | **1 documento** |
| L1 (una ficha) | Producto, categoría, departamento, vendedor, 17 filas del EAV, resumen, 3 reseñas con su cliente y el carrusel | **1 documento** + 1 del carrusel |

Con la caché caliente, todo está en memoria y **no hay diferencia** (L1: 0,53 frente a 0,52 ms). Cuando los datos no caben en memoria, que es lo normal en producción, cada bloque disperso es una lectura real: PostgreSQL mejorado se multiplica por 14-18 y MongoDB solo por 3-5.

### ¿En cuáles quedan igual y en cuáles es mejor PostgreSQL? ¿Por qué?

| Op | Resultado | Por qué |
|---|---|---|
| **L2** | **PG mejorado, 8 veces más rápido** | La tabla de búsqueda de PostgreSQL tiene un índice compuesto que resuelve **los cuatro filtros a la vez** y entrega exactamente las 1.233 filas. En MongoDB, el índice *multikey* del patrón *attribute* solo resuelve **una** de las tres condiciones `$elemMatch`; el resto se comprueba abriendo documentos: **465 MB leídos por búsqueda**. Es justo lo que anticipaba la [parte B.5](B_modelo.md#b5--relaciones-muchos-a-muchos) |
| **L3** | PG mejorado, 3 veces más rápido | MongoDB tiene que cargar los pedidos **enteros** del vendedor para quedarse con sus líneas (303 MB con el vendedor 1); PostgreSQL lee solo las líneas |
| **L6** | **PostgreSQL, 2 veces más rápido** | Un informe recorre muchas filas y pocas columnas. MongoDB carga documentos completos (629 MB); PostgreSQL, filas estrechas. Y es una consulta que nadie preparó: en PostgreSQL se escribe un `GROUP BY ROLLUP`; en MongoDB, una agregación con `$facet` a medida |
| **E1** | PG mejorado (1,9 frente a 3,6 ms) | La transacción multidocumento de MongoDB con `writeConcern: majority` tiene más coste fijo |
| **E2** | MongoDB más rápido con vendedores pequeños | 3 documentos por producto frente a borrar y reinsertar 17 filas de atributos; pero **no puede publicar el catálogo grande en una transacción** (D.2) |
| E3, E4, L4 | Empate con caché caliente | Operaciones de una o pocas filas por índice en los dos motores |

### ¿Qué cambia al pasar al modo de poca memoria?

- **PostgreSQL mejorado** sufre en las operaciones con datos dispersos: L1 ×15, L4 ×14, L5 ×18. Sigue ganando en L2 porque su tabla de búsqueda está ordenada físicamente (`CLUSTER`) y lee pocas páginas.
- **MongoDB** empeora mucho menos en L1, L4 y L5 (×3-5): la **localidad de los datos** del documento es su gran ventaja.
- Si se quita L2, que domina en los dos motores, el coste de un día con poca memoria es **3,0 h en PostgreSQL mejorado frente a 1,6 h en MongoDB**.

### De toda la mejora, ¿cuánto se debe al modelo documental y cuánto a precalcular?

| Paso | Horas de servidor al día | Debido a |
|---|---:|---|
| PostgreSQL original (Parte 0) | ≈ 1.070 | |
| PostgreSQL mejorado | ≈ 6,6–8,2 | **Precalcular y copiar datos donde se leen** (contadores, vista materializada, tabla de búsqueda, índice cubriente) |
| Lo que aporta el modelo documental | ≈ 1,4 h/día con poca memoria, en L1, L4 y L5 | **Localidad de los datos**: un pedido o una ficha en un solo sitio |

**Más del 99 % de la mejora sale de precalcular, y se consiguió sin salir de PostgreSQL.** El modelo documental aporta una mejora real pero pequeña en términos absolutos: lee hasta 3 veces más rápido las entidades que la aplicación consulta enteras, sobre todo cuando no caben en memoria.

### L2 se queda con casi todo el coste en los dos motores. ¿Lo debe resolver la base de datos?

En el simulador de un día, L2 es el **84 %** del tiempo en PostgreSQL mejorado y el **98 %** en MongoDB. Un buscador con filtros combinables sobre decenas de atributos y **facetas** (contar cuántos productos hay de cada valor de cada atributo) es el problema para el que existen los **motores de búsqueda** (Elasticsearch u OpenSearch, Atlas Search en MongoDB): índices invertidos y agregaciones de facetas sobre todo el catálogo.

**Recomendación:** sacar L2 a un motor de búsqueda alimentado desde la base de datos principal (*change data capture*), con la tolerancia de segundos que admite un listado. Las bases de datos transaccionales seguirían sirviendo la ficha, los pedidos y las escrituras.

> Si L2 se quedara en MongoDB, la mejora inmediata sería la misma que en PostgreSQL: promover los atributos universales de filtro (color, género, peso) a campos propios con un índice compuesto. No se aplicó para mantener el patrón *attribute* tal como lo plantea el enunciado.

---

## Conclusión: ¿migrar Mendiak a MongoDB?

**Con estos datos, no compensa migrar todo.**

| A favor de MongoDB | A favor de quedarse en PostgreSQL mejorado |
|---|---|
| ~3 veces más rápido leyendo pedidos y fichas con poca memoria | **6-8 h/día frente a 49-71 h/día** en el total, por L2 |
| E3 y E4 sin transacción; E2 más rápido con vendedores pequeños | L2, L3 y L6 entre 2 y 8 veces más rápidos |
| El documento refleja cómo lee la aplicación | Claves ajenas, `CHECK`, SCD2 y transacciones de cualquier tamaño sin programar nada |
| | E2 del vendedor grande, atómico en una sola transacción |

**Propuesta de arquitectura:**

1. **PostgreSQL mejorado** como base de datos principal: resuelve el 99 % del problema original.
2. **Un motor de búsqueda** para L2, el único coste relevante que queda en cualquiera de los dos motores.
3. **Reconsiderar MongoDB para los pedidos** (L4, L5 y E3) solo si la presión de memoria en producción hace que su lectura empiece a pesar. Es donde el modelo documental demostró una ventaja clara.

La lección de la práctica: **antes de cambiar de base de datos, hay que medir y usar bien la que se tiene**. El cuello de botella no era el motor relacional, sino recalcular en cada lectura y un modelo de atributos poco apto para filtrar.
