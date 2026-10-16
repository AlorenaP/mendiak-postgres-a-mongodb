# Parte A · La carga de trabajo

Antes de diseñar documentos hay que saber **qué se lee, qué se escribe, cuántas veces y con qué tolerancia**. En un modelo documental, el diseño sale de la carga de trabajo, no de las entidades.

Datos de partida: [Parte 0](0_punto_de_partida.md) (tamaños y cardinalidades) y [R.2](R2_pg_mejorado.md) (costes y decisiones).

---

## A.1 · Qué se queda en PostgreSQL, qué pasa a MongoDB y cómo viajan los datos

La raíz del problema de Mendiak es que un **almacén analítico** acabó sirviendo **tráfico de la aplicación**. La propuesta separa los dos usos según su carga de trabajo:

| Datos | Dónde | Por qué |
|---|---|---|
| **Catálogo vivo** (producto vigente, ficha técnica, stock, resumen de reseñas) | **MongoDB** | Se lee millones de veces al día y siempre junto (L1, L2). Un documento por producto con la ficha técnica dentro evita el EAV |
| **Reseñas** | **MongoDB** (su propia colección) | Las lee L1 (las 3 últimas) y crecen sin límite por producto |
| **Pedidos** (cabecera, líneas, historial, dirección) | **MongoDB** | Se leen siempre completos (L4, L5) y hoy están repartidos por el disco. Un pedido = un documento |
| **Clientes** (preferencias, lista de deseos) | **MongoDB** | Datos de la aplicación, leídos por cliente |
| **Historial de versiones de producto (SCD2)** | **MongoDB** (colección aparte) **y PostgreSQL** | La aplicación solo necesita saber qué versión compró cada línea; el historial completo sirve para análisis |
| **Informes** (L6 y los cortes que vengan) | **PostgreSQL** | Consultas libres, agregaciones y `ROLLUP` sobre todo el histórico, que es lo que el modelo relacional hace bien sin preparar nada ([R.3](R2_pg_mejorado.md#r3--lo-que-ya-hacía-bien-postgresql)) |

**Cómo viajan los datos:**

1. **Migración inicial (parte C):** PostgreSQL → JSON → `mongoimport`. Cada colección sale de una consulta SQL que construye el documento completo.
2. **En funcionamiento:** MongoDB pasa a ser la **fuente de verdad** de los datos operativos. PostgreSQL se queda como **almacén analítico**, alimentado desde MongoDB con *change streams* o con una carga periódica. Los informes toleran un día de retraso, así que bastaría con una carga nocturna.

> Para la comparación de la práctica, las **diez** operaciones se implementan en los dos motores (parte D), incluida L6. La tabla anterior es la recomendación de arquitectura; las mediciones dirán si se sostiene.

---

## A.2 · Volumen y cardinalidades

### Tamaño de cada entidad

De [`resultados/explain_original.txt`](../resultados/explain_original.txt) (tamaño total con índices):

| Tabla | Filas | Tamaño | Bytes/fila |
|---|---:|---:|---:|
| producto_atributo | 7.944.263 | 901 MB | 118 |
| resena | 2.099.473 | 668 MB | 333 |
| pedido_estado_hist | 2.825.031 | 435 MB | 161 |
| pedido_linea | 1.773.080 | 265 MB | 156 |
| pedido | 600.000 | 249 MB | 434 |
| dim_producto | 576.000 | 122 MB | 222 |
| lista_deseos | 599.994 | 68 MB | 119 |
| cliente | 200.000 | 58 MB | 301 |
| stock | 480.000 | 38 MB | 83 |
| cliente_preferencia | 200.000 | 14 MB | 74 |

### Hijos por padre: el máximo es el que decide

| Relación | Media | p95 | **Máximo** | ¿Qué impide el máximo? |
|---|---:|---:|---:|---|
| Reseñas por producto | 4,6 | 11 | **43.020** | **Incrustar todas las reseñas en el producto.** 43.020 × ~300 B ≈ 13 MB: rozaría el límite de 16 MB por documento, y L1 cargaría 13 MB en cada visita para mostrar 3. Crece sin límite |
| Productos por vendedor | 150 | 370 | **32.459** | **Incrustar los productos en el vendedor**, e incluso hacer E2 en una sola transacción de MongoDB (1,1 M de filas tocadas en PostgreSQL) |
| Pedidos por cliente | 3,5 | 9 | **4.452** | **Incrustar los pedidos en el cliente.** 4.452 × ~1,7 KB ≈ 7,5 MB y creciendo; y L4 solo quiere los 10 últimos |
| Líneas por pedido | 3,0 | 4 | 40 | Nada: 40 × ~150 B ≈ 6 KB. **Se pueden incrustar** |
| Estados por pedido | 4,7 | 5 | 5 | Nada: como mucho 5 y no crece. **Se pueden incrustar** |
| Atributos por producto | 16,6 | 17 | 17 | Nada: acotado por diseño. **Se pueden incrustar** |
| Deseos por cliente | 3,2 | 6 | 14 | Nada en el volumen actual, pero no tiene un límite natural |
| Versiones por producto | 1,2 | 2 | 2 | Hoy nada, pero **cada E2 crea una versión más**: crece con el tiempo sin límite |

> La media dice cómo es el caso normal; **el modelo tiene que aguantar el máximo** sin perjudicar a los demás. El producto con 43.020 reseñas no puede obligar a que los otros 479.999 paguen su tamaño.

---

## A.3 · Qué necesita cada operación

| Op | Lee / escribe | Quién la lanza | Entidades | Campos que necesita de verdad |
|---|---|---|---|---|
| **L1** | Lee | Cliente | Producto, categoría, vendedor, atributos, resumen y 3 últimas reseñas, carrusel | Producto completo + ficha técnica; nombre de categoría, departamento y vendedor; nº de reseñas, nota, histograma; 3 reseñas recientes (con el nombre del cliente); 6 productos relacionados (sku, nombre, precio, nota) |
| **L2** | Lee | Cliente | Producto, atributos | Para filtrar: departamento, color, género, peso. Para la página: **solo sku, nombre, marca y precio** (no los 17 atributos). Para las facetas: todos los atributos de los productos encontrados |
| **L3** | Lee | Vendedor | Productos del vendedor, líneas y pedidos del último año, cliente, resumen de reseñas | Ventas: sku, cantidad, importe, fecha y estado del pedido, nombre y email del cliente. Catálogo: nº de productos, nota y nº de reseñas |
| **L4** | Lee | Cliente | Pedido, líneas (con nombre del producto vendido), historial | Los 10 últimos pedidos completos del cliente |
| **L5** | Lee | Cliente | Pedido, líneas, historial | Un pedido completo, con dirección y teléfono |
| **L6** | Lee | Dirección | Pedido, líneas, producto → categoría → departamento | Fecha, estado, importe neto y departamento de cada línea vendida |
| **E1** | Escribe | Cliente | Pedido, líneas, historial, stock, cliente (segmento), lista de deseos | Precio vigente de cada SKU (se congela en la línea), stock disponible |
| **E2** | Escribe | Vendedor | Producto (nueva versión), atributos | Todo el catálogo del vendedor |
| **E3** | Escribe | Sistema | Pedido (estado), historial | Estado actual del pedido |
| **E4** | Escribe | Vendedor | Atributos de un producto | La ficha técnica del SKU |

**Observaciones que guiarán el diseño:**

- **L4 y L5 leen siempre el pedido entero**: cabecera, líneas e historial juntos. Es el caso de libro para incrustar.
- **L1 lee del producto mucho más que el producto**: categoría, vendedor y reseñas. Lo que cambia poco (nombres de categoría y vendedor) se puede copiar en el producto; lo que crece (reseñas) no.
- **El nombre del producto en las líneas de pedido es el de la versión vendida** (L4, L5). Si se copia en la línea al crear el pedido, el pedido ya no necesita consultar las versiones.
- **Las escrituras son pocas y casi todas sobre un solo padre**: un pedido (E1, E3), un producto (E4). La excepción es E2, que toca todo el catálogo de un vendedor.

---

## A.4 · Coste diario, tolerancias y escrituras que no se pueden perder

### El orden de prioridades cambia al multiplicar por la frecuencia

Ordenadas por su coste en PostgreSQL mejorado:

| Op | Veces/día | Original (ms) | Horas/día original | Horas/día PG mejorado |
|---|---:|---:|---:|---:|
| L2 | 780.000 | 808 | **175** | **5,36** |
| L1 | 2.900.000 | 1.110 | **894** | 0,95 |
| L5 | 900.000 | 1,5 | 0,38 | 0,19 |
| L4 | 350.000 | 3,5 | 0,34 | 0,18 |
| L3 | 12.000 | 117 | 0,39 | 0,13 |
| L6 | 50 | 5.480 | 0,08 | 0,05 |
| E1, E2, E3, E4 | 34.290 en total | | 0,03 | 0,02 |
| **Total** | | | **≈ 1.070** | **≈ 6,9** |

- **Por tiempo de una ejecución**, la peor era L6 (5,5 s). **Por coste diario**, es la penúltima.
- Tras la R.2, **L2 es el 78 % del coste que queda** (5,4 de 6,9 h). Será la operación que más pese también en MongoDB.
- L5 tarda poco más de 1 ms, pero se lanza 900.000 veces al día: tras la R.2 es la **tercera en coste**, por delante de L3, que tarda 50 veces más. Por eso merece la pena que leer un pedido sea lo más barato posible.

### ¿Qué pasaría si cada dato se mostrara con retraso?

La tolerancia de cada dato decide **qué se puede precalcular** y cada cuánto:

| Dato | Lo usa | Tolerancia | Consecuencia para el diseño |
|---|---|---|---|
| Importe, líneas y precio de un pedido | E1, L4, L5 | **Ninguna** | Se escribe una vez y se congela. El precio de la línea se copia al crear el pedido |
| Stock | E1 | **Ninguna** | Se descuenta con una actualización condicional ([R.1, ej. 4b](R1_transacciones.md#4b--comprobar-y-restar-en-la-misma-sentencia)) |
| Estado del pedido | E3, L5 | **Ninguna** | El cliente pregunta "¿dónde está mi pedido?" y espera la verdad |
| Precio en ficha y listado | L1, L2 | **Ninguna** | Tienen que coincidir entre sí en cuanto el vendedor publica (E2) |
| Nº de reseñas, nota e histograma | L1 | Minutos | Se podría precalcular, pero mantenerlo exacto al escribir es barato (las reseñas son pocas) |
| **Carrusel "relacionados"** | L1 | **1 hora** (enunciado) | Precalculado por categoría y refrescado cada hora |
| Facetas del buscador | L2 | Minutos | Se calculan al buscar; no hace falta precalcularlas |
| Ventas del panel del vendedor | L3 | Minutos | Podría leerse de una réplica secundaria |
| Salud del catálogo del vendedor | L3 | 1 hora | Se puede precalcular |
| Segmento del cliente | E1 | **1 día** | No hace falta recalcularlo dentro de cada pedido: un proceso nocturno quitaría trabajo a E1. Se mantiene en E1 para respetar la operación original |
| Informe de ventas | L6 | **1 día** | Se podría servir desde el almacén analítico o una réplica |

### Escrituras que no se pueden perder

En MongoDB, la durabilidad la decide el `writeConcern`. Con `w: "majority", j: true`, la escritura está en el diario (*journal*) de la mayoría de los nodos antes de confirmarse, y es el equivalente al `COMMIT` normal de PostgreSQL. Es lo que exige la comparación justa del enunciado y lo que usa el banco.

| Escritura | ¿Se puede perder? | writeConcern |
|---|---|---|
| E1 · Pedido y stock | **No**: es dinero y mercancía | `majority`, `j: true` |
| E3 · Cambio de estado | **No**: el cliente y el almacén actúan según el estado | `majority`, `j: true` |
| E2 · Nuevos precios | **No**: los pedidos posteriores se cobran con ellos | `majority`, `j: true` |
| E4 · Ficha técnica | **No**: la edita a mano un vendedor | `majority`, `j: true` |
| Datos precalculados (carrusel, resúmenes) | **Sí**: se pueden reconstruir desde el origen | Podría bastar `w: 1` |

### Proporción entre lecturas y escrituras

| | Operaciones al día |
|---|---:|
| Lecturas (L1–L6) | 4.942.050 |
| Escrituras (E1–E4) | 34.290 |
| **Proporción** | **≈ 144 lecturas por cada escritura** |

**Qué permite:** con 144 lecturas por escritura, compensa **hacer más trabajo al escribir para que leer sea barato**: copiar datos donde se leen (nombre de categoría y vendedor en el producto, nombre del producto en la línea de pedido), mantener contadores y precalcular. Es exactamente lo que se hizo en la R.2 con PostgreSQL y lo que hará el modelo documental.

**El límite:** las copias hay que mantenerlas. Copiar un dato que cambia a menudo y en muchos sitios (por ejemplo, el nombre de un vendedor con 32.459 productos) convierte una escritura pequeña en una muy grande. La parte B decidirá caso a caso.
