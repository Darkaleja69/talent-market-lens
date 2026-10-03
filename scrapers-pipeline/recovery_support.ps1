# =============================================================================
#  recovery_support.ps1
#  Shared guard for the recovery executor contract (T-10).
#
#  Dot-source it from the supervisor and from reconcile_pending_runs.ps1:
#      . (Join-Path $PSScriptRoot "recovery_support.ps1")
#
#  Test-RecoveryScriptSupportsDate inspects the script's AST and returns $true
#  only when its param(...) declares a `Date` parameter. This matters because
#  PowerShell sends unknown `-Date X` arguments to `$args` instead of failing:
#  a legacy executor without -Date would silently run its full historical
#  sweep (uploads with --overwrite and today's _READY) while the caller
#  believed it was recovering one specific run. T-11 completes the real
#  executor with -Date/-PlanJson.
# =============================================================================

function Test-RecoveryScriptSupportsDate {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    try {
        $resolved = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).ProviderPath
        $tokens = $null
        $errors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile(
            $resolved, [ref]$tokens, [ref]$errors
        )
    } catch {
        return $false
    }
    if ($null -eq $ast) { return $false }
    if ($null -ne $errors -and $errors.Count -gt 0) { return $false }
    if ($null -eq $ast.ParamBlock) { return $false }
    foreach ($parameter in $ast.ParamBlock.Parameters) {
        if ($parameter.Name.VariablePath.UserPath -eq "Date") { return $true }
    }
    return $false
}
