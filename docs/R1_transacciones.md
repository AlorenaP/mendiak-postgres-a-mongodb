# R.1 · Transacciones en PostgreSQL

Guion: [`04_transacciones.sql`](../04_transacciones.sql). Ejercicios 1, 4, 5 y 6 con dos terminales `psql` abiertas a la vez (T1 y T2).

Productos preparados en la carga: `MEND-000666` (0 unidades), `MEND-000777` (1 unidad) y `MEND-000001`, `MEND-120200`, `MEND-300500` (500 unidades).

> Pista útil de `psql`: el indicador muestra el estado de la sesión. `mendiak=#` fuera de transacción, `mendiak=*#` dentro de una transacción y `mendiak=!#` en una transacción abortada.

---

## Ejercicio 1 · Lo que hace una transacción no lo ve nadie hasta el COMMIT

| Paso | T1 | T2 |
|---|---|---|
| T1-1 | `BEGIN` + `INSERT` de `PED-90000001` → el `SELECT` devuelve la fila | |
| T2-1 | | El mismo `SELECT` → **0 filas** |
| T1-2 | `ROLLBACK` → `count = 0` | |

**¿Por qué T2 no ve el pedido?** Porque T1 no lo ha confirmado. PostgreSQL usa **MVCC** (control de concurrencia multiversión): cada transacción ve una foto de los datos confirmados; las filas nuevas de T1 existen, pero solo son visibles para T1 hasta el `COMMIT`. T2 no espera ni se bloquea: simplemente no la ve.

**Propiedad ACID:** **aislamiento** (*Isolation*). Tras el `ROLLBACK`, la fila desaparece para todos: **atomicidad**.

---

## Ejercicio 2 · Todo o nada: un pedido con un producto agotado

Pedido de `MEND-000001` (500 unidades) y `MEND-000666` (0 unidades).

### 2a · Sin transacción (autocommit)

| Sentencia | Resultado |
|---|---|
| `INSERT pedido` | `INSERT 0 1` ✅ confirmado |
| `INSERT pedido_linea` | `INSERT 0 2` ✅ confirmado |
| `UPDATE stock` | ❌ `violates check constraint "stock_unidades_check"` (`MEND-000666` quedaría en −1) |
| `UPDATE pedido` (total) | `UPDATE 1` ✅ confirmado |
| `INSERT pedido_estado_hist` | `INSERT 0 1` ✅ confirmado |

Estado final:

| pedido_id | importe_total | lineas | stock MEND-000001 | stock MEND-000666 |
|---|---:|---:|---:|---:|
| PED-90000002 | 821,16 | 2 | **500** | 0 |

**Queda un pedido de 821,16 € con sus líneas y su historial, pero sin haber reservado ninguna unidad**, una de ellas de un producto agotado. La base de datos queda incoherente y hubo que limpiarla a mano.

**Detalle importante:** el stock de `MEND-000001` **no bajó a 499**. El `UPDATE` de stock es una sola sentencia que toca dos filas; al fallar una, PostgreSQL deshace la sentencia completa. **Cada sentencia es atómica por sí misma**; lo que no es atómico sin `BEGIN` es el conjunto de las cinco.

### 2b · Con transacción

| Sentencia | Resultado |
|---|---|
| `BEGIN` | `BEGIN` |
| `INSERT pedido`, `INSERT pedido_linea` | `INSERT 0 1`, `INSERT 0 2` (provisionales) |
| `UPDATE stock` | ❌ `stock_unidades_check` |
| `UPDATE pedido`, `INSERT hist` | ❌ `current transaction is aborted, commands ignored until end of transaction block` |
| `COMMIT` | **`ROLLBACK`** |

Comprobación: `count(*) = 0`. **No queda nada del pedido.**

**¿Qué significa "current transaction is aborted"?** Que tras el primer error la transacción ya no puede terminar bien: PostgreSQL rechaza cualquier orden hasta que se cierre el bloque, para que no se pueda confirmar a medias.

**¿Qué hizo el `COMMIT`?** Como la transacción estaba abortada, no podía confirmar nada: la deshizo entera y respondió `ROLLBACK`.

### 2c · Si el servidor se apaga justo después del segundo INSERT

| | 2a (sin transacción) | 2b (con transacción) |
|---|---|---|
| Qué queda | La cabecera **y** las 2 líneas, con `importe_total = 0`, sin historial y sin reservar stock | **Nada** |
| Por qué | Cada sentencia se confirmó al terminar y está en el WAL | Nada se confirmó; al arrancar, PostgreSQL descarta lo no confirmado |

Esto es **atomicidad** y **durabilidad**: lo confirmado sobrevive a un apagón y lo no confirmado no deja rastro.

---

## Ejercicio 3 · SAVEPOINT: deshacer solo una parte

| Orden | Resultado | Indicador |
|---|---|---|
| `BEGIN` | `BEGIN` | `=*#` |
| `INSERT pedido` (`PED-90000003`) | `INSERT 0 1` | `=*#` |
| `SAVEPOINT antes_de_deseos` | `SAVEPOINT` | `=*#` |
| `DELETE FROM lista_deseos WHERE cliente_id = 'cuatro mil'` | ❌ `invalid input syntax for type integer` | **`=!#`** |
| `ROLLBACK TO SAVEPOINT antes_de_deseos` | `ROLLBACK` | **`=*#`** |
| `SELECT count(*)` del pedido | **1** | `=*#` |

El `SAVEPOINT` marca un punto dentro de la transacción. Al volver a él se deshace **solo** lo posterior (el `DELETE` fallido) y la transacción deja de estar abortada: **el pedido sigue en pie** y se podría confirmar.

Aplicado a E1: quitar lo comprado de la lista de deseos (paso 7) no es imprescindible. Envolviéndolo en un `SAVEPOINT`, un fallo ahí no tumba el pedido.

---

## Ejercicio 4 · Dos clientes compran la última unidad

_Pendiente._

## Ejercicio 5 · Interbloqueo

_Pendiente._

## Ejercicio 6 · Publicar un catálogo es todo o nada

_Pendiente._

---

## Repaso de las escrituras E1–E4

Filas y tablas que toca cada escritura (cifras de [`explain_original.txt`](../resultados/explain_original.txt)) y qué quedaría si el servidor se apagara a mitad **sin transacción**:

| Op | Tablas | Filas (caso medido) | Si se corta a mitad sin transacción |
|---|---|---|---|
| **E1** Crear pedido | 6: `pedido`, `pedido_linea`, `stock`, `pedido_estado_hist`, `cliente`, `lista_deseos` | 10 (cliente 1, 3 productos) en 7 sentencias | Pedido con o sin líneas, con total 0, stock sin reservar, sin historial o con el segmento sin recalcular: cualquier combinación incoherente |
| **E2** Publicar catálogo | 2: `dim_producto`, `producto_atributo` | ≈ 1,14 M con el vendedor 1: 32.459 versiones cerradas + 32.459 nuevas + 537.183 atributos borrados + 537.183 insertados | Catálogo con el precio nuevo y la ficha técnica antigua (entre la sentencia 1 y la 2) |
| **E3** Avanzar estado | 2: `pedido`, `pedido_estado_hist` | 2 | Estado cambiado sin su línea en el historial |
| **E4** Editar ficha | 1: `producto_atributo` | 34 (17 borrados + 17 insertados) | **Nada a medias**: es una sola sentencia (`WITH ... DELETE ... INSERT`), atómica por sí misma |

> Nota: el comentario de `03_operaciones.sql` dice "siete sentencias sobre cinco tablas" para E1, pero desde la v3 también toca `stock`: son **seis tablas**.

**Conclusión:** E1, E2 y E3 necesitan transacción porque escriben en varias sentencias. E4 no lo necesitaría, porque ya es una sola sentencia; aun así, la transacción no cuesta nada y protege si mañana se añade un paso.
