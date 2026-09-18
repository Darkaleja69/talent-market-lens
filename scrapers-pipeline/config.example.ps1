# =============================================================================
#  config.example.ps1
#  Plantilla de configuracion local. Copiala y renombrala a config.local.ps1:
#
#     Copy-Item config.example.ps1 config.local.ps1
#
#  config.local.ps1 esta en .gitignore, asi que tus nombres reales de storage
#  nunca se suben al repositorio. config.ps1 la carga automaticamente si existe.
# =============================================================================

$StorageAccount = "<tu-storage-account>"
$Container      = "landing"
