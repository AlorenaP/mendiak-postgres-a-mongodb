# Parte 0 · Punto de partida

Medición del sistema **tal como está**, antes de crear ningún índice ni tabla.

- Planes completos: [`resultados/explain_original.txt`](../resultados/explain_original.txt) y [`resultados/explain_pruebas_extra.txt`](../resultados/explain_pruebas_extra.txt)
- Mediciones del banco: [`resultados/medidas.csv`](../resultados/medidas.csv), etiquetas `linea-base` y `linea-base-poca-memoria`
- Carga verificada: los recuentos de las 15 tablas coinciden con el enunciado y hay 198.542 líneas vendidas con una versión antigua del producto (el SCD2 funciona).

---

## 1. Resultado del banco de pruebas

| Op | Qué hace | Veces/día | Media (ms) | p95 (ms) | KB leídos/op | **Horas/día** |
|---|---|---:|---:|---:|---:|---:|
| L1 | Ficha de producto | 2.900.000 | 1.110 | 1.215 | 498.582 | **893,9** |
| L2 | Buscador con filtros | 780.000 | 808 | 1.708 | 516.192 | **175,0** |
| L3 | Panel del vendedor | 12.000 | 117 | 345 | 89.386 | 0,39 |
| L4 | Mis pedidos | 350.000 | 3,5 | 6,6 | 230 | 0,34 |
| L5 | Seguimiento de pedido | 900.000 | 1,5 | 2,5 | 80 | 0,38 |
| L6 | Informe de ventas | 50 | 5.480 | 5.609 | 149.387 | 0,08 |
| E1 | Crear pedido | 6.250 | 8,9 | 11,3 | 146 | 0,02 |
| E2 | Publicar catálogo | 40 | 88 | 161 | 3.165 | 0,00 |
| E3 | Avanzar estado | 25.000 | 1,8 | 2,8 | 14 | 0,01 |
| E4 | Editar ficha técnica | 3.000 | 1,3 | 2,1 | 43 | 0,00 |
| | **Total** | | | | | **≈ 1.070 h** |

**Conclusión principal:** 1.070 horas de servidor al día equivalen a unos **45 núcleos ocupados las 24 horas**. El **99,9 %** de ese coste está en **L1 (83,5 %) y L2 (16,4 %)**. El resto de operaciones suman menos de 1,5 horas.

El orden de prioridades cambia por completo al multiplicar por la frecuencia:

| Por tiempo de una ejecución | Por coste diario |
|---|---|
| 1. L6 · 5,5 s | 1. **L1** · 894 h |
| 2. L1 · 1,1 s | 2. **L2** · 175 h |
| 3. L2 · 0,8 s | 3. L3 · 0,4 h |
| … | … |
| | 10. L6 · 0,08 h |

L6 es la consulta más lenta y la que menos importa.

### Contraste con la referencia del curso

Mis tiempos son entre 1,4 y 1,8 veces más altos que los de [`referencia_profesor.csv`](../resultados/referencia_profesor.csv) (L1: 1.110 frente a 720–770 ms; L6: 5.480 frente a 3.040–3.140 ms). La proporción es la misma en todas las operaciones, así que la diferencia es la **máquina**, no el comportamiento.

La prueba son los **KB leídos**, que no dependen del hardware y coinciden casi exactamente:

| Op | KB leídos (míos) | KB leídos (referencia) |
|---|---:|---:|
| L1 | 498.582 | 497.136 |
| L3 | 89.386 | 89.574 |
| L4 | 230 | 234 |
| L6 | 149.387 | 149.107 |

> Por eso, en el resto del proyecto, los **KB leídos** son la métrica principal para comparar motores: el tiempo depende de mi ordenador, el trabajo que hace la base de datos no.

---

## 2. Dónde se va el tiempo en cada operación

### L1 · Ficha de producto (el 83 % del coste)

Con `MEND-000123` (146 reseñas), 1,5 s:

| Parte de la consulta | Tiempo | Qué hace |
|---|---:|---|
| **Carrusel "relacionados"** (SubPlan 5) | **696 ms** | `Seq Scan` de **toda la tabla `resena` (2,1 M filas)** para calcular la nota media de cada producto de la categoría |
| Ficha + atributos (EAV) + vendedor | < 2 ms | Índices; 17 filas del EAV |
| Contador, media, histograma, 3 últimas reseñas | < 4 ms | Índice `ix_resena_sku` |

**El carrusel lee la tabla de reseñas entera en cada visita.** Son unos 490 MB, que es exactamente lo que marca el banco (≈ 498.000 KB por ejecución). Como la tabla (668 MB) no cabe en la caché de PostgreSQL (512 MB), la vuelve a leer cada vez.

Además, el resultado del carrusel es **el mismo para todos los productos de una categoría** (solo hay 40 categorías) y el enunciado acepta que vaya con una hora de retraso. Es el candidato perfecto para precalcular.

**Con el producto atípico `MEND-000001` (43.020 reseñas): 10,9 s.**

| Parte | Tiempo | Bloques leídos de disco |
|---|---:|---:|
| `count(*)` de reseñas | 144 ms | 462 (*Index Only Scan*: le basta el índice) |
| **`avg(puntuacion)`** | **7.016 ms** | **31.491** |
| Histograma | 22 ms | 0 (ya estaba en caché por la media) |
| Carrusel | 2.811 ms | 37.686 |

Calcular la media necesita la columna `puntuacion`, que no está en el índice, así que hay que ir a la tabla. Como las reseñas se guardaron **por orden de fecha, mezcladas entre productos**, las 43.020 reseñas de este producto están repartidas en unos 31.500 bloques distintos: prácticamente **un bloque de disco por reseña**.

### L2 · Buscador con filtros (el 16 % del coste)

2,0 s para devolver **1.233** productos:

1. Busca en el EAV todos los productos negros: **80.085** filas.
2. Para cada uno, consulta `dim_producto` (80.085 búsquedas) y filtra por departamento: quedan 11.363.
3. Para cada uno, otra búsqueda en el EAV para "unisex" (11.363): quedan 2.839.
4. Otra más para el peso (2.839): quedan **1.233**.

Examina unas **65 veces más filas de las que devuelve**, porque cada filtro es un `JOIN` más contra los 7,9 M de filas del EAV y solo el primero puede usar un índice de forma directa. Las facetas son baratas (65 ms). El problema es encontrar los productos.

### L3 · Panel del vendedor

5,1 s con el vendedor 1 (32.459 productos): recorre **119.235 líneas de pedido** y luego **32.459 productos × sus reseñas** para el resumen del catálogo. Es lenta, pero se ejecuta 12.000 veces al día: **0,4 h**. No es prioritaria.

### L4 · Mis pedidos · ¿Por qué la segunda vez es mucho más rápida?

| Ejecución | Tiempo | Bloques en caché (hit) | Bloques leídos de disco (read) |
|---|---:|---:|---:|
| Primera (caché fría) | **52,6 ms** | 95 | **128** |
| Segunda (caché caliente) | **0,7 ms** | 214 | 0 |

Para montar 10 pedidos completos hacen falta unos **220 bloques**: la cabecera, 24 líneas, 24 versiones de producto y 30 estados del historial. Como los datos se guardaron en el orden en que ocurrieron, **las filas de un mismo pedido no están juntas**: sus líneas y sus estados están repartidos entre los de miles de pedidos, y cada fila cae en un bloque distinto.

La primera vez, esos bloques se leen del disco uno a uno. La segunda vez ya están en la caché de PostgreSQL (`shared_buffers`) y no hay que leer nada: **unas 70 veces más rápido**.

En producción, con millones de clientes, la mayoría de los pedidos no estarán en caché, así que la cifra realista es la primera. Es justo lo que mide el modo de poca memoria (apartado 3).

### L5 · Seguimiento de un pedido

2,2 ms y 18 bloques. Funciona bien con los índices actuales.

### L6 · Informe de ventas

4,6 s: lee completas `pedido`, `pedido_linea` y `dim_producto` y agrupa con `ROLLUP`. Pero se lanza 50 veces al día (**0,08 h**). Además, es una consulta que nadie tuvo que preparar de antemano: PostgreSQL la resuelve a partir del modelo, y mañana podría ser por marca o por provincia sin cambiar nada. **No hay que optimizarla.**

### Escrituras

| Op | Observación |
|---|---|
| **E1** | El paso 6 (recalcular el segmento) tarda **754 ms** de los ~780 ms de la transacción con el cliente 1, y además **es incorrecto**: hace `JOIN` con `pedido_linea`, así que `count(*)` cuenta **líneas** en vez de pedidos y `sum(importe_total)` suma el total de cada pedido **una vez por línea**. Se corrige en la R.2. |
| **E2** | Con el vendedor 1 crea 32.459 versiones nuevas y **borra y reinserta 537.183 atributos** (11,8 s) para cambiar solo la garantía. Se lanza 40 veces al día. |
| E3, E4 | 1–2 ms. Sin problemas. |

---

## 3. Con poca memoria (1 GB por motor)

| Op | Normal (ms) | Poca memoria (ms) | Aumento |
|---|---:|---:|---:|
| L1 | 1.110 | 1.368 | ×1,2 |
| L2 | 808 | 1.020 | ×1,3 |
| **L4** | 3,5 | 9,4 | **×2,7** |
| **L5** | 1,5 | 3,4 | **×2,3** |
| E2 | 88 | 176 | ×2,0 |
| L6 | 5.480 | 5.467 | ×1,0 |

Las operaciones que más empeoran son **L4 y L5**, las que montan un pedido a partir de filas dispersas por el disco: con poca caché, cada una de esas filas es una lectura real. L6 no cambia porque ya leía las tablas enteras de forma secuencial.

Es la primera pista de dónde puede ayudar el modelo documental: guardar el pedido completo en un solo sitio.

---

## 4. Datos para la Parte A

Las consultas de apoyo de `03_operaciones.sql` dan los máximos que decidirán el diseño:

| Relación | Media | p95 | **Máximo** |
|---|---:|---:|---:|
| Reseñas por producto | 4,6 | 11 | **43.020** |
| Productos por vendedor | 150 | 370 | **32.459** |
| Pedidos por cliente | 3,5 | 9 | **4.452** |
| Líneas por pedido | 3,0 | 4 | 40 |
| Atributos por producto | 16,6 | 17 | 17 |
| Estados por pedido | 4,7 | 5 | 5 |
| Deseos por cliente | 3,2 | 6 | 14 |
| Versiones por producto | 1,2 | 2 | 2 |

---

## 5. Resumen

1. El coste está concentrado en **L1 y L2**. Cualquier mejora que no toque esas dos operaciones apenas cambia el total.
2. **L1** lee la tabla de reseñas entera en cada visita por culpa del carrusel, y con el producto atípico, además, una reseña por bloque.
3. **L2** sufre el EAV: un `JOIN` por filtro y 65 filas examinadas por cada fila devuelta.
4. **L4 y L5** son rápidas con caché, pero muy sensibles a la memoria, porque un pedido está repartido en muchos bloques.
5. **E1** tiene un error de lógica en el cálculo del segmento.
6. **L6** es lenta pero barata, y es un buen ejemplo de lo que PostgreSQL hace bien sin preparar nada.

### Pendiente de revisar

En los dos `EXPLAIN` de L1, el primer acceso a `dim_producto` por índice tarda unos 800 ms aunque solo lee 4 bloques. Esto no aparece en el banco de pruebas de forma tan marcada. Lo revisaré en la R.2 repitiendo el `EXPLAIN` con la caché caliente.
