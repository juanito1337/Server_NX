#requires -Version 5.1
Set-StrictMode -Version Latest
function Get-NXUIRolePath {
    [CmdletBinding()]
    param([object]$UIProfile,[Parameter(Mandatory)][string]$StagingRoot,[Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Files)
    if ($null -eq $UIProfile) { return '' }
    $wanted=@('contract_version','resource_id','revision','name','nx_version','path','size','sha256','package_sha256')
    $keys=@($UIProfile.PSObject.Properties.Name | Sort-Object)
    if (($keys -join ',') -cne (($wanted|Sort-Object) -join ',') -or $UIProfile.contract_version -isnot [int] -or $UIProfile.contract_version -ne 1 -or $UIProfile.nx_version -cne '2512' -or $UIProfile.revision -isnot [int] -or $UIProfile.revision -lt 1) { throw 'Invalid UI-role test contract.' }
    $relative=$UIProfile.path
    if ($relative -isnot [string] -or $relative -cnotmatch '^UGII/menus/roles/[^\\:]+\.(?i:mtx)$' -or $relative -match '(^|/)\.\.?(/|$)' -or $relative -match '[\x00-\x1f]' -or $UIProfile.sha256 -cnotmatch '^[a-f0-9]{64}$' -or $UIProfile.package_sha256 -cnotmatch '^[a-f0-9]{64}$') { throw 'Invalid UI-role path/hash.' }
    $rows=@($Files|Where-Object { $_.RelativePath.Replace('\','/') -ceq $relative })
    if ($rows.Count -ne 1 -or $rows[0].Size -ne $UIProfile.size -or $rows[0].Sha256 -ine $UIProfile.sha256) { throw 'UI role differs from release manifest.' }
    $role=Join-Path $StagingRoot $relative.Replace('/','\')
    Assert-NXOfflinePath -Path $role
    if (-not (Test-Path -LiteralPath $role -PathType Leaf) -or (Get-Item -LiteralPath $role -Force).Length -ne $UIProfile.size -or (Get-FileHash -LiteralPath $role -Algorithm SHA256).Hash -ine $UIProfile.sha256) { throw 'Selected UI role changed.' }
    return $role
}
Export-ModuleMember -Function Get-NXUIRolePath
