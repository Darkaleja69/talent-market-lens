-- Coste por SKU y día a partir de la tabla de sistema de facturación.
--
-- OJO: consultar system.billing.usage exige un SQL warehouse. Con el
-- `Serverless Starter Warehouse` (Small, 12 DBU/h, auto_stop 10 min) cada
-- despertar cuesta ~1,29 EUR, así que esta consulta se usa solo cuando
-- Azure Cost Analysis y la Account Console no bastan.
--
-- Devuelve DBUs, no euros. Para euros, cruzar con Azure Cost Analysis
-- (VMs, discos y networking no están en esta tabla).

SELECT
  usage_date,
  sku_name,
  billing_origin_product,
  SUM(usage_quantity) AS dbus
FROM system.billing.usage
WHERE usage_date >= current_date() - 30
GROUP BY ALL
ORDER BY usage_date DESC, dbus DESC;
