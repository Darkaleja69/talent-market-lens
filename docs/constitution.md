# Constitución — Talent Market Lens

1. **Stack simple:** reutilizar el stack existente; toda dependencia nueva requiere justificación documentada.
2. **Spec y código:** cada cambio funcional debe corresponder a la spec activa, que se actualiza junto con el código.
3. **Lógica e interfaz:** las reglas de negocio viven en módulos comprobables; CLI, notebooks y conectores solo coordinan entradas y salidas.
4. **Tests:** cada cambio funcional añade o actualiza tests; antes de cerrar, se ejecutan los tests del componente afectado.
5. **Persistencia:** los datos crudos se conservan en Azure antes de la ELT y los resultados analíticos se persisten en Delta; el disco local no es la copia canónica.
6. **Idioma:** identificadores y comentarios del código se escriben en inglés; los mensajes dirigidos al usuario, en español.


